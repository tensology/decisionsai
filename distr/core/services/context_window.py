"""
Resolve LLM context window sizes for UI pressure rings and model metadata.

Uses cached model recommendations when available, then provider-aware static
tables keyed by official model IDs (longest prefix wins — avoids gpt-5 matching gpt-3.5).
"""

from __future__ import annotations

from typing import Optional

from distr.core.services.model_recommendations import load_recommendations

# (model_id prefix, context tokens) — longest prefixes first.
_MODEL_PREFIX_WINDOWS: tuple[tuple[str, int], ...] = (
    ("gpt-4.1-nano", 1_047_576),
    ("gpt-4.1-mini", 1_047_576),
    ("gpt-4.1", 1_047_576),
    ("gpt-4o-mini", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4-32k", 32_768),
    ("gpt-4", 8_192),
    ("gpt-3.5-turbo-16k", 16_385),
    ("gpt-3.5-turbo", 16_385),
    ("gpt-3.5", 16_385),
    ("gpt-5.5", 400_000),
    ("gpt-5.3", 400_000),
    ("gpt-5.2", 400_000),
    ("gpt-5.1", 400_000),
    ("gpt-5-mini", 400_000),
    ("gpt-5-nano", 400_000),
    ("gpt-5", 400_000),
    ("o4-mini", 200_000),
    ("o3-mini", 200_000),
    ("o3-pro", 200_000),
    ("o3", 200_000),
    ("o1-mini", 128_000),
    ("o1-preview", 128_000),
    ("o1", 200_000),
    ("claude-opus-4", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-3-7-sonnet", 200_000),
    ("claude-3-5-sonnet", 200_000),
    ("claude-3-5-haiku", 200_000),
    ("claude-3-opus", 200_000),
    ("claude-3-sonnet", 200_000),
    ("claude-3-haiku", 200_000),
    ("gemini-2.5-pro", 1_048_576),
    ("gemini-2.5-flash", 1_048_576),
    ("gemini-2.0-flash", 1_048_576),
    ("gemini-1.5-pro", 2_097_152),
    ("gemini-1.5-flash", 1_048_576),
    ("gemini", 1_048_576),
)


def _normalize_model_id(model_name: Optional[str]) -> str:
    return (model_name or "").strip().lower()


def _model_matches_prefix(model: str, prefix: str) -> bool:
    if not model or not prefix:
        return False
    if model == prefix:
        return True
    return model.startswith(prefix + "-")


def _from_recommendations(model: str) -> Optional[int]:
    if not model:
        return None
    data = load_recommendations()
    providers = data.get("providers") or {}
    best: Optional[int] = None
    best_len = -1
    for prov_data in providers.values():
        if not isinstance(prov_data, dict):
            continue
        categories = prov_data.get("categories") or {}
        for cat in categories.values():
            if not isinstance(cat, dict):
                continue
            for tier in ("paid", "free"):
                entry = cat.get(tier)
                if not isinstance(entry, dict):
                    continue
                mid = _normalize_model_id(entry.get("model_id"))
                if not mid:
                    continue
                # Model must equal the catalog id or be a longer variant (gpt-5-turbo → gpt-5).
                if model != mid and not _model_matches_prefix(model, mid):
                    continue
                try:
                    window = int(entry.get("context_window") or 0)
                except (TypeError, ValueError):
                    window = 0
                if window <= 0:
                    continue
                if len(mid) > best_len:
                    best = window
                    best_len = len(mid)
    return best


def _from_static_table(model: str) -> Optional[int]:
    for prefix, window in _MODEL_PREFIX_WINDOWS:
        if _model_matches_prefix(model, prefix):
            return window
    return None


def _provider_default(provider: Optional[str]) -> int:
    key = (provider or "").strip().lower()
    if key in {"openai", "openrouter"}:
        return 128_000
    if key == "anthropic":
        return 200_000
    if key in {"google gemini", "gemini"}:
        return 1_048_576
    if key == "groq":
        return 128_000
    if key == "ollama":
        return 128_000
    return 32_000


def context_window_for_model(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
) -> int:
    """Best-effort context window for a provider + model id pair."""
    model = _normalize_model_id(model_name)
    if not model:
        return _provider_default(provider)

    from_recs = _from_recommendations(model)
    if from_recs:
        return from_recs

    from_static = _from_static_table(model)
    if from_static:
        return from_static

    return _provider_default(provider)
