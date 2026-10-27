"""WCS Q&A eval harness — local only, never against production.

Run with:
    RUN_EVALS=1 doppler run -- pytest tests/evals/

doppler.yaml pins this repo to its own project's `dev` config, so that command
targets the development database and ENVIRONMENT=development. The harness
still writes rows and spends on real OpenAI and Anthropic calls, so it is
opt-in (CD-033): skipped unless RUN_EVALS=1 is set — having the keys in the
environment is not enough, or a plain `doppler run -- pytest` would run it.
Also skipped anywhere lacking OPENAI_API_KEY + ANTHROPIC_API_KEY.

It refuses outright when the process resolves as production (the fleet's
environment resolver: ENVIRONMENT, then Railway's environment name). The
opt-in decides whether it runs; the guard decides where it may not. A
collection error rather than a skip, so pointing it at production by
mistake is loud. tests/evals/ is also outside pytest's testpaths, so a bare
`pytest` never collects it.

For each question in questions.yaml:

  1. POST /v1/wcs/ask via in-process ASGI transport (real DB + real OpenAI +
     real Anthropic — auth is overridden so the harness runs as a configured
     user without needing a Clerk session).
  2. Compute source_recall + source_precision against ideal_source_ids.
  3. Call the Opus judge for a 1-5 score, a grounded yes/no (is every claim
     backed by a cited source, given those sources' text) and reasoning.
  4. Append a row to wcs_qa_eval_runs on the configured DATABASE_URL.

Each pytest run uses a single fresh run_id; one row per question is written
under that run_id, keyed by (run_id, question_id).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import yaml
from anthropic import AsyncAnthropic
from mini_app_polis.environment import Environment, resolve

from kaianolevine_api.agents.wcs_qa.prompts import JUDGE_PROMPT
from kaianolevine_api.auth import get_current_owner
from kaianolevine_api.config import get_settings
from kaianolevine_api.database import get_sessionmaker
from kaianolevine_api.main import app

from .judge import judge_answer
from .metrics import flatten_ids, source_precision, source_recall
from .results import write_eval_run
from .sources import cited_sources_text

QUESTIONS_PATH = Path(__file__).parent / "questions.yaml"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_questions() -> list[dict]:
    if not QUESTIONS_PATH.exists():
        return []
    raw = yaml.safe_load(QUESTIONS_PATH.read_text()) or []
    return [q for q in raw if isinstance(q, dict) and q.get("id")]


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=REPO_ROOT
        ).strip()
    except Exception:
        return "unknown"


_QUESTIONS = _load_questions()


@pytest.fixture(scope="session")
def run_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="session")
def git_sha() -> str:
    return _git_sha()


@pytest.fixture(scope="session")
def judge_prompt_sha() -> str:
    return hashlib.sha256(JUDGE_PROMPT.encode("utf-8")).hexdigest()


@pytest.fixture(scope="session")
def judge_client() -> AsyncAnthropic:
    settings = get_settings()
    return AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


@pytest.fixture
async def eval_client() -> AsyncIterator[httpx.AsyncClient]:
    """In-process ASGI client for the agent. Auth is dep-overridden."""
    user_id = os.environ.get("WCS_QA_EVAL_USER_ID", "dev-owner")
    app.dependency_overrides[get_current_owner] = lambda: user_id
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=120.0,
    ) as client:
        yield client
    app.dependency_overrides.pop(get_current_owner, None)


_ENVIRONMENT, _ENVIRONMENT_SOURCE = resolve()
if _ENVIRONMENT is Environment.PRODUCTION:
    # Raised at import, so collection fails rather than the suite skipping
    # quietly. Checked before the opt-in: a production process should not
    # even report "skipped" as though RUN_EVALS were the only thing missing.
    raise RuntimeError(
        "Refusing to run the WCS Q&A eval harness in production "
        f"({_ENVIRONMENT_SOURCE} resolves to {_ENVIRONMENT.value}). It writes "
        "eval rows and calls paid models; run it against the dev config: "
        "RUN_EVALS=1 doppler run -- pytest tests/evals/"
    )

_SKIP_REASONS: list[str] = []
if os.environ.get("RUN_EVALS") != "1":
    _SKIP_REASONS.append(
        "eval harness writes eval rows and calls paid models; set RUN_EVALS=1 to run it"
    )
if not (os.environ.get("OPENAI_API_KEY") and os.environ.get("ANTHROPIC_API_KEY")):
    _SKIP_REASONS.append("eval harness requires OPENAI_API_KEY and ANTHROPIC_API_KEY")
if not _QUESTIONS:
    _SKIP_REASONS.append("no questions in tests/evals/questions.yaml")

if _SKIP_REASONS:
    pytestmark = pytest.mark.skip(reason="; ".join(_SKIP_REASONS))


@pytest.mark.parametrize(
    "question",
    _QUESTIONS or [None],
    ids=[q["id"] for q in _QUESTIONS] if _QUESTIONS else ["no_questions"],
)
async def test_eval_question(
    question: dict,
    eval_client: httpx.AsyncClient,
    run_id: uuid.UUID,
    git_sha: str,
    judge_client: AsyncAnthropic,
    judge_prompt_sha: str,
) -> None:
    settings = get_settings()

    # 1. Hit the agent.
    resp = await eval_client.post(
        "/v1/wcs/ask", json={"question": question["question"]}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    # 2. Metrics.
    cited_ids = {c["id"] for c in data["citations"]}
    ideal_ids = flatten_ids(question.get("ideal_source_ids") or {})
    recall = source_recall(ideal_ids, cited_ids)
    precision = source_precision(ideal_ids, cited_ids)

    # 3. Judge, with the text of what the answer cites for the grounding check.
    sm = get_sessionmaker(settings.DATABASE_URL)
    async with sm() as session:
        sources = await cited_sources_text(
            session=session,
            citations=data["citations"],
            viewer_id=os.environ.get("WCS_QA_EVAL_USER_ID", "dev-owner"),
            settings=settings,
        )
    judge_score, judge_grounded, judge_reasoning = await judge_answer(
        client=judge_client,
        model=settings.WCS_QA_JUDGE_MODEL,
        question=question["question"],
        ideal_answer=question.get("ideal_answer", ""),
        agent_answer=data["answer"],
        cited_sources=sources,
    )

    # 4. Persist on the configured DATABASE_URL (development; see the guard above).
    async with sm() as session:
        await write_eval_run(
            session=session,
            run_id=run_id,
            question_id=question["id"],
            git_sha=git_sha,
            agent_answer=data["answer"],
            cited_source_ids={
                "lessons": [
                    c["id"] for c in data["citations"] if c["type"] == "lesson"
                ],
                "chunks": [c["id"] for c in data["citations"] if c["type"] == "chunk"],
                "pages": [c["id"] for c in data["citations"] if c["type"] == "page"],
            },
            tool_trace={
                "tool_trace_id": data["tool_trace_id"],
                "budget_exhausted": data["budget_exhausted"],
            },
            source_recall=recall,
            source_precision=precision,
            judge_score=judge_score,
            judge_reasoning=judge_reasoning,
            judge_model=settings.WCS_QA_JUDGE_MODEL,
            judge_prompt_sha=judge_prompt_sha,
            judge_grounded=judge_grounded,
        )
