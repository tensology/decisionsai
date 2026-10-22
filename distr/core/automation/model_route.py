"""Pick the CLI harness + model an automation runs with (2026-10-22, automodel).

Resolution order, first hit wins:
1. Per-automation override in ``action_config``:
   ``cli_route_mode == "custom"`` with ``cli_backend`` (+ ``cli_model_provider``, ``cli_model``).
   Legacy imported keys (``backend`` / ``model_provider`` / ``model``) still count as a
   per-automation override unless the user explicitly chose ``cli_route_mode == "inherit"``.
2. Global automation default in Settings: ``automation_cli_backend`` (+ provider, model).
3. Existing fallback: no route at all, so Development dispatch uses the Complexity
   routing tier ``project_cli_{complexity}_*`` (complexity defaults to medium),
   then that tier's fallback backend/model.

Pure function: no DB or app imports, so it is safe to unit test.
"""
from __future__ import annotations

from typing import Any, Mapping

ROUTE_MODES = {"inherit", "custom"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def automation_override(config: Mapping[str, Any] | None) -> dict[str, str]:
    cfg = config if isinstance(config, Mapping) else {}
    mode = _clean(cfg.get("cli_route_mode")).lower()
    if mode == "custom" and _clean(cfg.get("cli_backend")):
        return {
            "backend": _clean(cfg.get("cli_backend")).lower(),
            "model_provider": _clean(cfg.get("cli_model_provider")).lower(),
            "model": _clean(cfg.get("cli_model")),
            "source": "automation_override",
        }
    if mode != "inherit" and _clean(cfg.get("backend")):
        return {
            "backend": _clean(cfg.get("backend")).lower(),
            "model_provider": _clean(cfg.get("model_provider")).lower(),
            "model": _clean(cfg.get("model")),
            "source": "automation_import_configuration",
        }
    return {}


def automation_global_default(settings: Mapping[str, Any] | None) -> dict[str, str]:
    s = settings if isinstance(settings, Mapping) else {}
    backend = _clean(s.get("automation_cli_backend")).lower()
    if not backend:
        return {}
    return {
        "backend": backend,
        "model_provider": _clean(s.get("automation_cli_model_provider")).lower(),
        "model": _clean(s.get("automation_cli_model")),
        "source": "automation_global_default",
    }


def resolve_automation_route(
    config: Mapping[str, Any] | None,
    settings: Mapping[str, Any] | None,
) -> tuple[dict[str, str], str]:
    """Return ``(route, source)``. An empty route means the complexity-tier fallback."""
    cfg = config if isinstance(config, Mapping) else {}
    picked = automation_override(cfg) or automation_global_default(settings)
    source = picked.pop("source", "complexity_routing_fallback") if picked else "complexity_routing_fallback"
    route = {k: v for k, v in picked.items() if v}
    if route.get("backend") and not route.get("model"):
        # Keep "auto" explicit so dispatch does not borrow the complexity tier's model for another harness.
        route["model"] = "auto"
    for key in ("fallback_backend", "fallback_model_provider", "fallback_model"):
        value = _clean(cfg.get(key))
        if value:
            route[key] = value.lower() if key != "fallback_model" else value
    return route, source
