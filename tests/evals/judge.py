"""LLM-as-judge wrapper for the WCS Q&A eval harness.

Calls Claude Opus with the JUDGE_PROMPT system prompt and a user message
containing the question, agent answer, and ideal answer. Parses out the
"Reasoning: ..." and "Score: N" lines.

The judge runs cross-model from the agent (Opus vs Sonnet) so the same
model class isn't grading itself. The prompt requires reasoning before the
score (chain-of-thought) to discourage rubber-stamping.
"""

from __future__ import annotations

import re
from typing import Any

from kaianolevine_api.agents.wcs_qa.prompts import JUDGE_PROMPT

_SCORE_RE = re.compile(r"Score:\s*(\d+)", re.IGNORECASE)
_REASONING_RE = re.compile(
    r"Reasoning:\s*(.+?)(?=\n\s*Score:)", re.IGNORECASE | re.DOTALL
)


async def judge_answer(
    *,
    client: Any,
    model: str,
    question: str,
    ideal_answer: str,
    agent_answer: str,
    max_tokens: int = 1024,
) -> tuple[int | None, str]:
    """Call Opus, return (score, reasoning).

    score is None if parsing failed; reasoning is the full response text in
    that case so a human can inspect what went wrong.
    """
    user_message = (
        f"Question:\n{question}\n\n"
        f"Agent answer:\n{agent_answer}\n\n"
        f"Ideal answer:\n{ideal_answer}\n"
    )
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=JUDGE_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    text = "".join(
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    )

    score: int | None = None
    score_match = _SCORE_RE.search(text)
    if score_match:
        try:
            score = int(score_match.group(1))
        except ValueError:
            score = None

    reasoning_match = _REASONING_RE.search(text)
    reasoning = reasoning_match.group(1).strip() if reasoning_match else text.strip()

    return score, reasoning
