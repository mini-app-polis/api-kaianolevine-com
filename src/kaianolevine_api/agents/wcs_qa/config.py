"""Frozen agent-loop configuration derived from Settings.

Pulling these into a typed dataclass keeps the loop's signature small and lets
tests construct deterministic configs without the full Settings object.

Budgets come from the configured ``WCS_QA_MAX_*_DEFAULT`` settings and are
clamped to the matching ``WCS_QA_MAX_*_LIMIT`` ceilings — the limits exist
to guarantee no run can ever exceed the configured worst-case spend, even
if defaults are later raised or a future request-side override is added.
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
    """Build an ``AgentConfig`` from ``Settings``.

    Each budget is ``min(configured _DEFAULT, configured _LIMIT)``. The
    clamp is defensive: it costs nothing when defaults are below the limit
    (the common case) and protects against a future config change that
    accidentally sets a default above its ceiling.
    """
    return AgentConfig(
        model=settings.WCS_QA_AGENT_MODEL,
        max_tool_calls=min(
            settings.WCS_QA_MAX_TOOL_CALLS_DEFAULT,
            settings.WCS_QA_MAX_TOOL_CALLS_LIMIT,
        ),
        max_input_tokens=min(
            settings.WCS_QA_MAX_INPUT_TOKENS_DEFAULT,
            settings.WCS_QA_MAX_INPUT_TOKENS_LIMIT,
        ),
        max_output_tokens=min(
            settings.WCS_QA_MAX_OUTPUT_TOKENS_DEFAULT,
            settings.WCS_QA_MAX_OUTPUT_TOKENS_LIMIT,
        ),
        embedding_model=settings.WCS_QA_EMBEDDING_MODEL,
        flattener_version=settings.WCS_QA_FLATTENER_VERSION,
        chunking_version=settings.WCS_QA_CHUNKING_VERSION,
        site_url=settings.WCS_SITE_URL,
    )
