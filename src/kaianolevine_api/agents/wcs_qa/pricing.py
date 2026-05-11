"""Per-token pricing for WCS Q&A cost estimates.

Used by the ``/v1/wcs/ask`` response to attach an approximate dollar cost to
each agent run. Cost = (input_tokens / 1e6) * input_per_mtok +
(output_tokens / 1e6) * output_per_mtok.

This is an *estimate*. It excludes:
  - Embedding cost on the OpenAI side (negligible, ~$0.0000004 per ask
    with text-embedding-3-small).
  - Prompt caching discounts (not used by this agent).
  - Any Anthropic billing rollup adjustments.

Real invoices may differ by a percent or two. Treat these numbers as a
"close enough" signal for whether a question is cheap or expensive, not
as accounting truth.

Pricing last verified: 2026-05-11. Anthropic occasionally adjusts
published prices — update when bumping the model in WCS_QA_AGENT_MODEL.
Source: https://www.anthropic.com/pricing
"""

from __future__ import annotations

# Maps the model id used in `WCS_QA_AGENT_MODEL` (and any other supported
# Anthropic model) to (input_dollars_per_million_tokens,
# output_dollars_per_million_tokens). Unknown models return None from
# ``compute_cost_usd`` so the UI knows to omit the cost rather than
# silently estimate at $0.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # Claude Sonnet — the default ask model. ~5x cheaper input than output.
    "claude-sonnet-4-6": (3.00, 15.00),
    # Claude Opus — the optional judge model. Larger and pricier.
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00),
    # Claude Haiku — small/fast, occasionally used for cheap classifications.
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


def compute_cost_usd(
    model: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Estimated dollar cost for a single agent run.

    Returns ``None`` for unrecognized models — the caller should surface
    "cost unknown" in that case rather than render a misleading $0.
    """
    prices = MODEL_PRICES.get(model)
    if prices is None:
        return None
    input_per_mtok, output_per_mtok = prices
    return (input_tokens / 1_000_000.0) * input_per_mtok + (
        output_tokens / 1_000_000.0
    ) * output_per_mtok
