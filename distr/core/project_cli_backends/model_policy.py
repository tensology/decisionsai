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


def _openrouter_hy3_route(settings: dict | None = None) -> dict[str, Any] | None:
    settings = settings or {}
    if not bool(settings.get("openrouter_enabled")) or not str(settings.get("openrouter_key") or "").strip():
        return None
    return {
        "backend": "pi",
        "model": "tencent/hy3-preview",
        "model_provider": "openrouter",
        "source": "auto_role_route",
        "policy_reason": "Selected Tencent HY3 through OpenRouter as the lower-cost cloud escalation tier.",
    }


def build_auto_fallback_chain(
    route: dict[str, Any],
    *,
    settings: dict | None = None,
) -> list[dict[str, Any]]:
    """Return the ordered, evidence-triggered escalation ladder for Auto mode."""
    current_backend = str(route.get("backend") or "pi").strip().lower()
    current_model = str(route.get("model") or "auto").strip().lower()
    current_provider = str(route.get("model_provider") or "").strip().lower()
    if current_backend == "claude_code":
        return []

    candidates: list[dict[str, Any]] = []
    if current_backend == "pi" and not (
        current_provider == "openrouter" and current_model == "tencent/hy3-preview"
    ):
        candidates.append({
            "backend": "codex",
            "model": "auto",
            "automatic": True,
            "reason": "Escalate from a local/free worker after a failed completion contract.",
        })
    if current_backend in {"pi", "codex"}:
        candidates.append({
            "backend": "cursor",
            "model": "auto",
            "automatic": False,
            "reason": "Cursor is available as an interactive IDE handoff, not a silent background retry.",
        })
    hy3 = _openrouter_hy3_route(settings)
    if hy3 and not (
        current_backend == "pi"
        and current_provider == "openrouter"
        and current_model == "tencent/hy3-preview"
    ):
        candidates.append({
            **hy3,
            "automatic": True,
            "reason": "Use the lower-cost OpenRouter cloud tier after Codex/Cursor.",
        })
    candidates.append({
        "backend": "claude_code",
        "model": "auto",
        "automatic": True,
        "reason": "Claude is the final expensive escalation after cheaper routes fail.",
    })
    return candidates


def apply_auto_step_role_policy(
    route: dict[str, Any],
    *,
    workflow: Any = None,
    config: dict[str, Any] | None = None,
    settings: dict | None = None,
    step_role: str = "execution",
    prior_role_routes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose a fresh provider/model for one step when Auto routing is enabled."""
    merged = dict(route or {})
    cfg = config or {}
    policy = _workflow_run_settings(workflow)
    policy.update(cfg.get("model_policy") or {})
    # Role/model autodetection is opt-in. Existing workflows without the Auto
    # flag must keep their configured board/project route.
    if not bool(policy.get("auto_route_models", False)) or bool(cfg.get("route_locked")):
        return merged

    # A failed completion contract is evidence. When a prior attempt has
    # already escalated this step, preserve that recorded route on the workflow
    # loop/retry instead of sending the same work back to the cheaper model that
    # just timed out or failed.
    if str(merged.get("source") or "").strip().lower() in {
        "runtime_provider_failover",
        "run_coordination_plan",
        "coordination_replan",
    }:
        merged["auto_detected"] = True
        merged["step_role"] = step_role
        merged["policy_source"] = str(merged.get("source") or "run_coordination_plan")
        merged["fallback_chain"] = build_auto_fallback_chain(merged, settings=settings)
        return merged

    explicit_backend = bool(str(cfg.get("backend_id") or "").strip())
    explicit_model = bool(
        str(cfg.get("model") or "").strip()
        and str(cfg.get("model") or "").strip().lower() != "auto"
    )
    if explicit_backend or explicit_model:
        merged["auto_detected"] = False
        merged["step_role"] = step_role
        merged["fallback_chain"] = build_auto_fallback_chain(merged, settings=settings)
        return merged

    role = str(step_role or "execution").strip().lower()
    complexity = str(merged.get("complexity") or policy.get("complexity") or "medium").strip().lower()
    task_profile = merged.get("task_profile")
    task_profile = task_profile if isinstance(task_profile, dict) else {}
    risk_flags = {
        str(item).strip().lower()
        for item in (task_profile.get("risk_flags") or [])
        if str(item).strip()
    }
    high_consequence = bool(
        risk_flags.intersection({"auth", "payments", "migration", "cross_module", "ui_critical"})
    )
    selected: dict[str, Any]
    rationale: str

    if role == "final_polish":
        selected = {"backend": "codex", "model": "auto", "source": "auto_role_route"}
        rationale = "The final production polish uses Codex after cheaper implementation and independent review complete."
    elif role == "planning" and complexity in {"medium", "high"}:
        selected = {"backend": "codex", "model": "auto", "source": "auto_role_route"}
        rationale = (
            f"{complexity.title()}-complexity planning benefits from a stronger independent "
            "reasoning pass, so Auto selected Codex before implementation."
        )
    elif role == "implementation" and high_consequence:
        selected = {"backend": "codex", "model": "auto", "source": "auto_role_route"}
        rationale = (
            "Implementation touches a high-consequence boundary, so Auto selected Codex "
            "instead of risking an unproven local edit."
        )
    elif role == "review":
        implementation = (prior_role_routes or {}).get("implementation")
        implementation = implementation if isinstance(implementation, dict) else {}
        selected = _openrouter_hy3_route(settings) or _free_eligible_model(
            settings,
            complexity=complexity,
            prefer_local=str(implementation.get("model_provider") or "") != "ollama",
        )
        rationale = "Review uses a different free/lower-cost provider from implementation to reduce context bias."
    elif role == "deployment":
        selected = merged
        rationale = "Deployment keeps its approved route; the workflow approval gate controls execution."
    else:
        selected = _free_eligible_model(settings, complexity=complexity, prefer_local=True)
        rationale = (
            f"Auto selected the configured local/free model for {role} at {complexity} complexity."
        )

    selected = dict(selected)
    if selected.get("provider") and not selected.get("model_provider"):
        selected["model_provider"] = selected.pop("provider")
    merged.update(selected)
    merged["auto_detected"] = True
    merged["step_role"] = role
    merged["policy_source"] = "auto_step_role"
    merged["policy_reason"] = rationale
    # Mission Control and channel summaries historically read ``rationale``;
    # keep it aligned with the final role-aware choice instead of exposing the
    # generic baseline decision that Auto just replaced.
    merged["rationale"] = rationale
    merged["fallback_chain"] = build_auto_fallback_chain(merged, settings=settings)
    return merged


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

    if explicit_backend and str(merged.get("backend") or "").strip().lower() != "pi":
        merged["model"] = str(merged.get("model") or "auto").strip() or "auto"
        source = str(merged.get("source") or "step_backend").strip().lower()
        merged["policy_source"] = f"{source}_native_auto_preserved"
        return merged

    policy = _workflow_run_settings(workflow)
    policy.update(cfg.get("model_policy") or {})
    free_only = bool(policy.get("free_only"))
    prefer_local = bool(policy.get("prefer_local") or policy.get("prefer_free_local"))
    auto_route = bool(policy.get("auto_route_models", False))

    current_model = str(merged.get("model") or "").strip()
    route_source = str(merged.get("source") or "").strip().lower()
    current_backend = str(merged.get("backend") or "").strip().lower()
    complexity = str(merged.get("complexity") or cfg.get("complexity") or policy.get("complexity") or "medium")
    if not auto_route and not free_only and not prefer_local:
        merged["model"] = current_model or "auto"
        suffix = "native_auto_preserved" if merged["model"] == "auto" else "preserved"
        merged["policy_source"] = f"{route_source or 'selected_route'}_{suffix}"
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
    # A route selected by complexity settings, board policy, the orchestrator,
    # or provider failover is already a real decision even when its provider
    # uses a native ``auto`` model. Do not silently replace Codex/Claude/Cursor
    # with Pi merely because no concrete model id was needed. Explicit
    # local/free policy is the only reason to reselect from Pi's catalog.
    backend_is_scoped = bool(current_backend) or explicit_backend or route_source in {
        "board_override",
        "step_execution_route",
        "active_run_route",
        "orchestrator_override",
        "harness_preference",
        "runtime_provider_failover",
        "policy",
    }
    if (
        backend_is_scoped
        and current_backend
        and current_backend != "pi"
        and not (free_only or prefer_local)
    ):
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
