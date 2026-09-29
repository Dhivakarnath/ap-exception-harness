"""Turn token usage into a USD cost using the configured per-token price.

The cost is a real, product-priced number — not an invented one — derived from
the tokens actually reported by the model and the on-demand price in config
(`model_price_*_per_mtok_usd`, defaulting to Amazon Nova Lite's published rate).
It is only as current as those config numbers, which is why they live in config
and are documented as a stated assumption: a price change is an env change, and
the metric layer labels the resulting cost accordingly.
"""

from __future__ import annotations

from ap_agent.config import get_settings

_PER_MILLION = 1_000_000


def cost_usd(*, input_tokens: int, output_tokens: int) -> float:
    """USD cost of a call (or a whole run) from its input/output token counts.

    Zero tokens -> zero cost, which is the honest answer for a run that made no
    billable model call (or a scripted/offline run).
    """
    settings = get_settings()
    input_usd = (input_tokens / _PER_MILLION) * settings.model_price_input_per_mtok_usd
    output_usd = (
        output_tokens / _PER_MILLION
    ) * settings.model_price_output_per_mtok_usd
    return round(input_usd + output_usd, 6)
