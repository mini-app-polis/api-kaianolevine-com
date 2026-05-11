"""Per-token pricing for WCS Q&A cost estimates.

Used by the ``/v1/wcs/ask`` response to attach an approximate dollar cost
to each agent run. Cost breakdown::

    fresh_input  = (input_tokens / 1e6) * input_per_mtok
    cache_write  = (cache_creation_tokens / 1e6) * input_per_mtok * 1.25
    cache_read   = (cache_read_tokens / 1e6) * input_per_mtok * 0.10
    output       = (output_tokens / 1e6) * output_per_mtok
    total = fresh_input + cache_write + cache_read + output

This is an *estimate*. It excludes:
  - Embedding cost on the OpenAI side (negligible, ~$0.0000004 per ask
    with text-embedding-3-small).
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

# Anthropic prompt-caching multipliers, applied uniformly across models.
# Cache writes are billed at 1.25x the model's normal input price; cache
# reads at 0.10x. If Anthropic ever changes these ratios, update here.
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float | None:
    """Estimated dollar cost for a single agent run, including cache pricing.

    Returns ``None`` for unrecognized models — the caller should surface
    "cost unknown" in that case rather than render a misleading $0.

    The cache parameters default to 0 so callers that don't use prompt
    caching (and existing tests) get the same answer as before.
    """
    prices = MODEL_PRICES.get(model)
    if prices is None:
        return None
    input_per_mtok, output_per_mtok = prices
    cache_write_per_mtok = input_per_mtok * _CACHE_WRITE_MULTIPLIER
    cache_read_per_mtok = input_per_mtok * _CACHE_READ_MULTIPLIER
    return (
        (input_tokens / 1_000_000.0) * input_per_mtok
        + (output_tokens / 1_000_000.0) * output_per_mtok
        + (cache_creation_tokens / 1_000_000.0) * cache_write_per_mtok
        + (cache_read_tokens / 1_000_000.0) * cache_read_per_mtok
    )
