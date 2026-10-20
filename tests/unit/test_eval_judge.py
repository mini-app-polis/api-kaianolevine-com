"""The eval judge's reply is parsed into score, grounded and reasoning."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tests.evals.judge import judge_answer


class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.sent: dict[str, Any] = {}
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.sent = kwargs
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


async def test_a_grounded_verdict_is_read_with_the_score() -> None:
    client = _Client("Reasoning: Matches the page.\nGrounded: yes\nScore: 5")
    score, grounded, reasoning = await judge_answer(
        client=client,
        model="m",
        question="q",
        ideal_answer="i",
        agent_answer="a",
        cited_sources="[1] page anchor-step\n{}",
    )
    assert (score, grounded, reasoning) == (5, True, "Matches the page.")
    assert (
        "Cited sources:\n[1] page anchor-step" in client.sent["messages"][0]["content"]
    )


async def test_an_ungrounded_answer_is_read_as_such() -> None:
    client = _Client(
        "Reasoning: Claims a count no source gives.\nGrounded: no\nScore: 3"
    )
    score, grounded, _ = await judge_answer(
        client=client, model="m", question="q", ideal_answer="i", agent_answer="a"
    )
    assert (score, grounded) == (3, False)


async def test_a_reply_without_a_verdict_is_none() -> None:
    client = _Client("Reasoning: Fine.\nScore: 4")
    score, grounded, reasoning = await judge_answer(
        client=client, model="m", question="q", ideal_answer="i", agent_answer="a"
    )
    assert (score, grounded, reasoning) == (4, None, "Fine.")
