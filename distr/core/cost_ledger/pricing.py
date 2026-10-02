"""Editable USD pricing estimates for the cost ledger.

Not billing-grade invoices — static rates for operator visibility.
Edit constants below; do not hard-code rates elsewhere.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Provider token rates — USD per 1_000_000 input / output tokens
# ---------------------------------------------------------------------------
# Rough mid-tier list prices as of late 2025 / early 2026. Swap freely.
PROVIDER_RATES_USD_PER_1M: dict[str, dict[str, float]] = {
    "openai": {"input": 2.50, "output": 10.00},
    "anthropic": {"input": 3.00, "output": 15.00},
    "cursor": {"input": 5.00, "output": 15.00},
    "codex": {"input": 5.00, "output": 15.00},
    "openrouter": {"input": 3.00, "output": 10.00},
    # Local / free — provider cost is always 0; resource estimate is separate.
    "ollama": {"input": 0.0, "output": 0.0},
    "local": {"input": 0.0, "output": 0.0},
    "pi-local": {"input": 0.0, "output": 0.0},
    "pi": {"input": 0.0, "output": 0.0},
}

LOCAL_PROVIDERS = frozenset({"ollama", "local", "pi-local", "pi", ""})

# ---------------------------------------------------------------------------
# Local resource estimate (electricity + RAM opportunity)
# Assumptions (documented; easy to edit):
# - LOCAL_RAM_GB_HOUR_USD: opportunity / cloud-equivalent RAM cost per GB-hour
# - LOCAL_KWH_USD: electricity rate (South Africa residential-ish default)
# - LOCAL_MODEL_WATTS: sustained draw for a 27B/30B-class local LLM on GPU/CPU
#   (ballpark; Mac Studio / desktop GPU idle+infer mix)
# - LOCAL_ASSUMED_RAM_GB: working set attributed to the local model process
# ---------------------------------------------------------------------------
LOCAL_RAM_GB_HOUR_USD = 0.01
LOCAL_KWH_USD = 0.15
LOCAL_ASSUMED_RAM_GB = 24.0
LOCAL_MODEL_WATTS = {
    "27b": 150.0,
    "30b": 180.0,
    "default": 140.0,
}


def normalize_provider(provider: str | None) -> str:
    return str(provider or "").strip().lower()


def is_local_provider(provider: str | None) -> bool:
    p = normalize_provider(provider)
    if p in LOCAL_PROVIDERS:
        return True
    return p.startswith("ollama") or p.startswith("local") or "pi-local" in p


def rates_for(provider: str | None) -> dict[str, float]:
    p = normalize_provider(provider)
    if p in PROVIDER_RATES_USD_PER_1M:
        return dict(PROVIDER_RATES_USD_PER_1M[p])
    # Fuzzy aliases
    for key, rates in PROVIDER_RATES_USD_PER_1M.items():
        if key and key in p:
            return dict(rates)
    if is_local_provider(p):
        return {"input": 0.0, "output": 0.0}
    # Unknown cloud — use openrouter-ish middle ground rather than inventing 0.
    return dict(PROVIDER_RATES_USD_PER_1M["openrouter"])


def estimate_provider_cost_usd(
    *,
    provider: str | None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_total: int | None = None,
) -> float:
    """Estimate provider USD from token counts. Local providers return 0."""
    if is_local_provider(provider):
        return 0.0
    rates = rates_for(provider)
    tin = max(0, int(tokens_in or 0))
    tout = max(0, int(tokens_out or 0))
    if tin == 0 and tout == 0 and tokens_total:
        # Split unknown total 40/60 in/out when providers only report one number.
        total = max(0, int(tokens_total))
        tin = int(total * 0.4)
        tout = total - tin
    cost = (tin / 1_000_000.0) * float(rates["input"]) + (tout / 1_000_000.0) * float(rates["output"])
    return round(max(0.0, cost), 8)


def _watts_for_model(model: str | None) -> float:
    text = str(model or "").strip().lower()
    if "30b" in text or "32b" in text:
        return float(LOCAL_MODEL_WATTS["30b"])
    if "27b" in text or "22b" in text or "20b" in text:
        return float(LOCAL_MODEL_WATTS["27b"])
    return float(LOCAL_MODEL_WATTS["default"])


def estimate_local_resource_cost_usd(
    *,
    duration_seconds: float | None,
    model: str | None = None,
    ram_gb: float | None = None,
) -> tuple[float, str]:
    """Return (resource_cost_usd, notes) for local/ollama runs.

    Cost = electricity (kWh * rate) + RAM GB-hour * rate.
    """
    seconds = max(0.0, float(duration_seconds or 0.0))
    if seconds <= 0:
        return 0.0, "no duration; resource cost = 0"
    hours = seconds / 3600.0
    watts = _watts_for_model(model)
    kwh = (watts / 1000.0) * hours
    elec = kwh * float(LOCAL_KWH_USD)
    ram = float(ram_gb if ram_gb is not None else LOCAL_ASSUMED_RAM_GB)
    ram_cost = ram * hours * float(LOCAL_RAM_GB_HOUR_USD)
    total = round(elec + ram_cost, 8)
    notes = (
        f"local estimate: {seconds:.1f}s, {watts:.0f}W, {ram:.1f}GB RAM; "
        f"elec=${elec:.6f} ({kwh:.6f} kWh @ ${LOCAL_KWH_USD}/kWh) + "
        f"ram=${ram_cost:.6f} (@ ${LOCAL_RAM_GB_HOUR_USD}/GB-h). "
        f"Not a utility invoice."
    )
    return total, notes


def blended_cost_usd(provider_cost: float, resource_cost: float) -> float:
    return round(float(provider_cost or 0.0) + float(resource_cost or 0.0), 8)


def pricing_snapshot() -> dict[str, Any]:
    """Operator-visible copy of rates/assumptions."""
    return {
        "provider_rates_usd_per_1m": dict(PROVIDER_RATES_USD_PER_1M),
        "local_ram_gb_hour_usd": LOCAL_RAM_GB_HOUR_USD,
        "local_kwh_usd": LOCAL_KWH_USD,
        "local_assumed_ram_gb": LOCAL_ASSUMED_RAM_GB,
        "local_model_watts": dict(LOCAL_MODEL_WATTS),
        "note": "Static estimates — not provider invoices. Edit pricing.py to change.",
    }
