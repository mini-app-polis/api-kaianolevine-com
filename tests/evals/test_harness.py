"""WCS Q&A eval harness — local only.

Run with:
    doppler run -- pytest tests/evals/

Skipped automatically anywhere lacking OPENAI_API_KEY + ANTHROPIC_API_KEY
(see conftest.py). For each question in questions.yaml:

  1. POST /v1/wcs/ask via in-process ASGI transport (real DB + real OpenAI +
     real Anthropic — auth is overridden so the harness runs as a configured
     user without needing a Clerk session).
  2. Compute source_recall + source_precision against ideal_source_ids.
  3. Call the Opus judge for a 1-5 score + free-form reasoning.
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

from kaianolevine_api.agents.wcs_qa.prompts import JUDGE_PROMPT
from kaianolevine_api.auth import get_current_owner
from kaianolevine_api.config import get_settings
from kaianolevine_api.database import get_sessionmaker
from kaianolevine_api.main import app

from .judge import judge_answer
from .metrics import flatten_ids, source_precision, source_recall
from .results import write_eval_run

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


_SKIP_REASONS: list[str] = []
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

    # 3. Judge.
    judge_score, judge_reasoning = await judge_answer(
        client=judge_client,
        model=settings.WCS_QA_JUDGE_MODEL,
        question=question["question"],
        ideal_answer=question.get("ideal_answer", ""),
        agent_answer=data["answer"],
    )

    # 4. Persist on the configured DATABASE_URL (production for normal runs).
    sm = get_sessionmaker(settings.DATABASE_URL)
    async with sm() as session:
        await write_eval_run(
            session=session,
            run_id=run_id,
            question_id=question["id"],
            git_sha=git_sha,
            agent_answer=data["answer"],
            cited_source_ids={
                "notes": [c["id"] for c in data["citations"] if c["type"] == "note"],
                "chunks": [c["id"] for c in data["citations"] if c["type"] == "chunk"],
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
        )
