"""Normalization for Codex CLI preferences used in ticket complexity routing."""

from __future__ import annotations

# Maps to Codex config.toml `model_reasoning_effort` (UI label: Intelligence).
VALID_CODEX_INTELLIGENCE = {"", "low", "medium", "high", "xhigh"}

# Maps to Codex config.toml `service_tier` (UI label: Speed).
VALID_CODEX_SPEED = {"", "flex", "fast"}


def normalize_codex_intelligence(value: str | None) -> str:
    raw = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if raw in ("extra_high", "extrahigh"):
        return "xhigh"
    if raw in VALID_CODEX_INTELLIGENCE:
        return raw
    return ""


def normalize_codex_speed(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in VALID_CODEX_SPEED:
        return raw
    return ""
