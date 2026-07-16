"""Model routing policy helpers for workflow/project CLI execution."""

from __future__ import annotations

import json
from typing import Any


def _free_eligible_model(
    settings: dict | None = None,
    *,
    complexity: str = "medium",
    prefer_local: bool = False,
) -> dict[str, str]:
    from distr.core.project_cli_backends.models_catalog import pi_cli_models, recommend_cli_model

    settings = settings or {}
    models = pi_cli_models(settings)
    selected = recommend_cli_model(
        models,
        prefer_free=True,
        prefer_local=prefer_local,
        prefer_scoped=True,
        complexity=complexity,
    )
    if prefer_local:
        configured_provider = str(
            settings.get("coding_llm_provider") or settings.get("code_provider") or ""
        ).strip().lower()
        configured_model = str(
            settings.get("coding_llm_model") or settings.get("code_model") or ""
        ).strip()
        configured = next(
            (
                model for model in models
                if configured_provider == "ollama"
                and str(model.get("id") or "").strip() == configured_model
                and bool(model.get("local", True))
            ),
            None,
        )
        if configured:
            selected = {
                "id": configured_model,
                "provider": "ollama",
                "reason": "Selected the configured local Ollama coding model.",
            }
    provider = str(selected.get("provider") or "").strip() or "ollama"
    backend = "pi"
    return {
        "backend": backend,
        "model": str(selected.get("id") or "auto"),
        "provider": provider,
        "source": "workflow_policy_free_local" if prefer_local else "workflow_policy_free_eligible",
        "reason": str(selected.get("reason") or ""),
    }


def _workflow_run_settings(workflow: Any) -> dict[str, Any]:
    raw = getattr(workflow, "run_settings", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def apply_workflow_model_policy(
    route: dict[str, Any],
    *,
    workflow: Any = None,
    config: dict[str, Any] | None = None,
    settings: dict | None = None,
) -> dict[str, Any]:
    """Apply workflow policy after explicit step overrides.

    Explicit step backend/model values are treated as a manual override. If no
    explicit model exists, workflow policy may resolve Auto to a free/local model.
    """
    merged = dict(route or {})
    cfg = config or {}
    explicit_backend = bool(str(cfg.get("backend_id") or "").strip())
    explicit_model = bool(str(cfg.get("model") or "").strip() and str(cfg.get("model")).strip() != "auto")
    if explicit_backend and explicit_model:
        merged["policy_source"] = "step_override"
        return merged

    policy = _workflow_run_settings(workflow)
    policy.update(cfg.get("model_policy") or {})
    free_only = bool(policy.get("free_only"))
    prefer_local = bool(policy.get("prefer_local") or policy.get("prefer_free_local"))
    auto_route = bool(policy.get("auto_route_models", True))

    current_model = str(merged.get("model") or "").strip()
    route_source = str(merged.get("source") or "").strip().lower()
    current_backend = str(merged.get("backend") or "").strip().lower()
    complexity = str(merged.get("complexity") or cfg.get("complexity") or policy.get("complexity") or "medium")
    if not auto_route and not free_only:
        return merged
    scoped_model_selected = bool(
        current_model not in {"", "auto"}
        and not bool(policy.get("force_reselect"))
    )
    if scoped_model_selected:
        merged["policy_source"] = f"{route_source or 'selected_route'}_preserved"
        return merged
    # _free_eligible_model() selects from Pi's provider catalog. An explicit or
    # board-scoped non-Pi backend must therefore keep its native `auto` model;
    # pairing (for example) Codex with an OpenRouter model identifier produces a
    # route that looks valid in the UI but is rejected by the real provider.
    backend_is_scoped = explicit_backend or route_source in {
        "board_override",
        "step_execution_route",
        "active_run_route",
        "orchestrator_override",
        "harness_preference",
    }
    if backend_is_scoped and current_backend and current_backend != "pi" and not free_only:
        merged["model"] = current_model or "auto"
        merged["policy_source"] = f"{route_source or 'explicit_backend'}_native_auto_preserved"
        return merged
    if free_only or prefer_local or current_model in {"", "auto"}:
        selected = _free_eligible_model(settings, complexity=complexity, prefer_local=prefer_local)
        if free_only or not explicit_backend:
            merged["backend"] = selected["backend"]
        if current_model in {"", "auto"} or free_only:
            merged["model"] = selected["model"]
        merged["model_provider"] = selected["provider"]
        merged["policy_source"] = selected["source"]
        merged["policy_reason"] = selected.get("reason") or ""
    return merged
