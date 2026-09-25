# Path: app/pricing.py
# Description: OpenAI API list prices used for the dashboard's API-equivalent value estimate.

from typing import Dict, Optional

# Standard, short-context USD prices per 1M tokens. Subscription traffic is not
# billed at these rates; the dashboard uses them only as an approximate API
# equivalent. Keep this table aligned with https://developers.openai.com/api/docs/pricing.
PRICING: Dict[str, Dict[str, float]] = {
    "gpt-6-astra": {"input": 10.0, "cached_input": 1.0, "cache_write": 12.5, "output": 50.0},
    "gpt-6-sol": {"input": 2.0, "cached_input": 0.2, "cache_write": 2.5, "output": 10.0},
    "gpt-6-luna": {"input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5},
    "gpt-5.6-sol": {"input": 5.0, "cached_input": 0.5, "cache_write": 6.25, "output": 30.0},
    "gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "cache_write": 3.125, "output": 15.0},
    "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.1, "cache_write": 1.25, "output": 6.0},
    "gpt-5.5-pro": {"input": 30.0, "cached_input": 30.0, "cache_write": 30.0, "output": 180.0},
    "gpt-5.5": {"input": 5.0, "cached_input": 0.5, "cache_write": 5.0, "output": 30.0},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "cache_write": 0.75, "output": 4.5},
    "gpt-5.4-nano": {"input": 0.2, "cached_input": 0.02, "cache_write": 0.2, "output": 1.25},
    "gpt-5.4-pro": {"input": 30.0, "cached_input": 30.0, "cache_write": 30.0, "output": 180.0},
    "gpt-5.4": {"input": 2.5, "cached_input": 0.25, "cache_write": 2.5, "output": 15.0},
}

_PER_TOKEN = 1_000_000.0


def rates_for(model: Optional[str]) -> Optional[Dict[str, float]]:
    """Return the longest matching model-id prefix, or None for an unknown model."""
    if not model:
        return None
    matches = [key for key in PRICING if model.startswith(key)]
    return PRICING[max(matches, key=len)] if matches else None


def cost_usd(
    model: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimate API cost; Responses input_tokens includes cache reads and writes."""
    rates = rates_for(model)
    if rates is None:
        return 0.0
    total_input = max(input_tokens, 0)
    cached = min(max(cached_input_tokens, 0), total_input)
    written = min(max(cache_write_tokens, 0), total_input - cached)
    uncached = max(total_input - cached - written, 0)
    return (
        uncached * rates["input"] + cached * rates["cached_input"] + written * rates["cache_write"] + max(output_tokens, 0) * rates["output"]
    ) / _PER_TOKEN


def cost_for_record(record) -> float:
    """Compute API-equivalent cost from a UsageRecordDb-like row."""
    return cost_usd(
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cached_input_tokens=record.cached_input_tokens,
        cache_write_tokens=record.cache_write_tokens,
    )
