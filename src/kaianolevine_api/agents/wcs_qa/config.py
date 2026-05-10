"""Frozen agent-loop configuration derived from Settings.

Pulling these into a typed dataclass keeps the loop's signature small and lets
tests construct deterministic configs without the full Settings object.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...config import Settings


@dataclass(frozen=True)
class AgentConfig:
    """Frozen agent-loop configuration.

    Captures the subset of ``Settings`` the WCS Q&A loop reads. Frozen so
    it can be safely shared across the request lifecycle and asserted on
    in tests without worrying about mutation.
    """

    model: str
    max_tool_calls: int
    max_input_tokens: int
    max_output_tokens: int
    embedding_model: str
    flattener_version: int
    chunking_version: int
    site_url: str


def from_settings(settings: Settings) -> AgentConfig:
    """Build an ``AgentConfig`` from the global ``Settings`` object.

    Centralizes the mapping from environment-driven settings to the
    typed agent config so the loop's signature stays small and tests can
    construct deterministic configs without the full Settings stack.
    """
    return AgentConfig(
        model=settings.WCS_QA_AGENT_MODEL,
        max_tool_calls=settings.WCS_QA_MAX_TOOL_CALLS,
        max_input_tokens=settings.WCS_QA_MAX_INPUT_TOKENS,
        max_output_tokens=settings.WCS_QA_MAX_OUTPUT_TOKENS,
        embedding_model=settings.WCS_QA_EMBEDDING_MODEL,
        flattener_version=settings.WCS_QA_FLATTENER_VERSION,
        chunking_version=settings.WCS_QA_CHUNKING_VERSION,
        site_url=settings.WCS_SITE_URL,
    )
