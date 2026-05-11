"""Frozen agent-loop configuration derived from Settings.

Pulling these into a typed dataclass keeps the loop's signature small and lets
tests construct deterministic configs without the full Settings object.

Budgets are resolved in two layers:
  1. A ``Depth`` preset picks the *requested* per-request budget. ``"normal"``
     mirrors the configured ``WCS_QA_MAX_*_DEFAULT`` settings; ``"deep"``
     raises them for synthesis-heavy questions ("top N across all lessons",
     etc).
  2. The requested values are then clamped to the configured
     ``WCS_QA_MAX_*_LIMIT`` ceilings so no request can exceed the absolute
     cap regardless of payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...config import Settings

Depth = Literal["normal", "deep"]


# "deep" preset — requested budgets for synthesis-heavy questions. These are
# the *desired* ceiling; effective values are ``min(preset, configured
# _LIMIT)``. Bump these (and the matching ``_LIMIT`` settings) together when
# the deep path needs more headroom.
_DEEP_PRESET = {
    "max_tool_calls": 25,
    "max_input_tokens": 160_000,
    "max_output_tokens": 8000,
}


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
    depth: Depth


def from_settings(settings: Settings, depth: Depth = "normal") -> AgentConfig:
    """Build an ``AgentConfig`` from ``Settings``, applying the ``depth`` preset.

    Effective per-budget value is ``min(requested, configured _LIMIT)``. The
    clamp against ``_LIMIT`` is the hard ceiling — no ``depth`` value or
    future per-request override can exceed it.
    """
    if depth == "deep":
        requested = _DEEP_PRESET
    else:
        requested = {
            "max_tool_calls": settings.WCS_QA_MAX_TOOL_CALLS_DEFAULT,
            "max_input_tokens": settings.WCS_QA_MAX_INPUT_TOKENS_DEFAULT,
            "max_output_tokens": settings.WCS_QA_MAX_OUTPUT_TOKENS_DEFAULT,
        }

    return AgentConfig(
        model=settings.WCS_QA_AGENT_MODEL,
        max_tool_calls=min(
            requested["max_tool_calls"],
            settings.WCS_QA_MAX_TOOL_CALLS_LIMIT,
        ),
        max_input_tokens=min(
            requested["max_input_tokens"],
            settings.WCS_QA_MAX_INPUT_TOKENS_LIMIT,
        ),
        max_output_tokens=min(
            requested["max_output_tokens"],
            settings.WCS_QA_MAX_OUTPUT_TOKENS_LIMIT,
        ),
        embedding_model=settings.WCS_QA_EMBEDDING_MODEL,
        flattener_version=settings.WCS_QA_FLATTENER_VERSION,
        chunking_version=settings.WCS_QA_CHUNKING_VERSION,
        site_url=settings.WCS_SITE_URL,
        depth=depth,
    )
