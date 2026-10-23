"""Model routing policy helpers for workflow/project CLI execution."""

from __future__ import annotations

import json
from typing import Any


def _drop_retired_openrouter_free_models(models: list[dict]) -> list[dict]:
    """Remove OpenRouter ``:free`` ids the live catalogue no longer lists.

    Pi's local catalogue can lag OpenRouter (``openai/gpt-oss-120b:free`` was
    withdrawn but still offered). When the live catalogue is unavailable the
    list is returned unchanged rather than guessing.
    """
    try:
        from distr.core.project_cli_backends.provider_preflight import openrouter_catalogue_ids

        live = openrouter_catalogue_ids()
    except Exception:
        live = None
    if not live:
        return models
    kept = []
    for model in models:
        model_id = str(model.get("id") or model.get("model") or "").strip()
        provider = str(model.get("provider") or model.get("model_provider") or "").strip().lower()
        if (
            provider == "openrouter"
            and model_id.lower().endswith(":free")
            and model_id not in live
        ):
            continue
        kept.append(model)
    return kept


def route_disallows_free_preference(
    route: dict[str, Any] | None,
    *,
    config: dict[str, Any] | None = None,
    run_data: dict[str, Any] | None = None,
) -> bool:
    """Return True for routes where free/local models must not be preferred.

    Manually pinned thread routes, locked routes, and Tensology-approved
    tickets run on the configured (paid) route; recovery must offer that
    route's configured paid fallback before any free candidate.
    """
    route = route or {}
    config = config or {}
    run_data = run_data or {}
    if (
        bool(config.get("route_locked"))
        or bool(route.get("route_locked"))
        or bool(route.get("paid_first"))
    ):
        return True
    if str(route.get("route_mode") or "").strip().lower() == "manual":
        return True
    if str(route.get("source") or "").strip().lower() == "thread_model_route":
        return True
    model_route = run_data.get("model_route") if isinstance(run_data.get("model_route"), dict) else {}
    if str(model_route.get("route_mode") or "").strip().lower() == "manual":
        return True
    assessment = run_data.get("routing_assessment")
    if not isinstance(assessment, dict):
        assessment = route.get("routing_assessment") if isinstance(route.get("routing_assessment"), dict) else {}
    if str(assessment.get("source") or "").strip().lower() == "tensology_approval":
        return True
    if str(run_data.get("board_provider") or "").strip().lower() == "tensology":
        return True
    return False


def _free_eligible_model(
    settings: dict | None = None,
    *,
    complexity: str = "medium",
    prefer_local: bool = False,
    avoid_proven_limited: bool = False,
) -> dict[str, str]:
    from distr.core.project_cli_backends.models_catalog import pi_cli_models, recommend_cli_model

    settings = settings or {}
    cached_models = settings.get("_pi_cli_models_cache")
    models = (
        list(cached_models)
        if isinstance(cached_models, list)
        else pi_cli_models(settings)
    )
    # This helper is the free/local tier, not a soft preference. A paid scoped
    # model can otherwise outscore a free route and silently spend money while
    # the orchestrator is explicitly trying cheaper recovery candidates.
    free_or_local = [
        model
        for model in models
        if bool(model.get("free"))
        or bool(model.get("local"))
        or str(model.get("id") or model.get("model") or "").strip().lower().endswith(":free")
    ]
    models = _drop_retired_openrouter_free_models(free_or_local)
    if avoid_proven_limited and models:
        try:
            from distr.core.qualification import (
                CertificationStatus,
                ProviderCertificationStore,
            )

            store = ProviderCertificationStore()
            capable = []
            for model in models:
                provider = str(
                    model.get("provider") or model.get("model_provider") or ""
                ).strip().lower()
                model_id = str(model.get("id") or model.get("model") or "").strip()
                status = store.get(provider, model_id, "project_execution").status
                blocking = store.blocking_unavailable(
                    provider, model_id, "project_execution"
                )
                if blocking is not None:
                    status = blocking.status
                if status not in {
                    CertificationStatus.LIMITED,
                    CertificationStatus.UNAVAILABLE,
                }:
                    capable.append(model)
            # Keep the full catalogue when every option is known-bad so the
            # caller can surface a truthful blocker rather than inventing a
            # route. Otherwise Auto learns from observed execution quality and
            # does not keep selecting the same underperforming model.
            if capable:
                models = capable
        except Exception:
            pass
        try:
            from distr.core.project_cli_backends.policy_manager import (
                _openrouter_free_cooldown_active,
            )

            if _openrouter_free_cooldown_active():
                outside_cooldown = [
                    model
                    for model in models
                    if not (
                        str(model.get("provider") or model.get("model_provider") or "")
                        .strip()
                        .lower()
                        == "openrouter"
                        and str(model.get("id") or model.get("model") or "")
                        .strip()
                        .lower()
                        .endswith(":free")
                    )
                ]
                if outside_cooldown:
                    models = outside_cooldown
        except Exception:
            pass
    if (
        avoid_proven_limited
        and prefer_local
        and str(complexity or "medium").strip().lower()
        in {"medium", "high", "complex", "critical"}
    ):
        try:
            from distr.core.project_cli_backends.models_catalog import enrich_model_entry

            enriched = [enrich_model_entry(model) for model in models]
            strong_local = [
                model
                for model in enriched
                if bool(model.get("local"))
                and str(model.get("tier") or "").strip().lower() == "high"
            ]
            # Auto may use a small configured model for lightweight work, but
            # a medium/high unattended project step should use an installed
            # high-tier local worker when one is available. This prevents a
            # failed 9B route from being replaced by an even smaller 7B model.
            if strong_local:
                models = strong_local
        except Exception:
            pass
    effective_prefer_local = bool(
        prefer_local and any(bool(model.get("local")) for model in models)
    )
    selected = recommend_cli_model(
        models,
        prefer_free=True,
        prefer_local=effective_prefer_local,
        prefer_scoped=True,
        complexity=complexity,
    )
    if prefer_local:
        from distr.core.project_cli_backends.models_catalog import enrich_model_entry

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
        complexity_id = str(complexity or "medium").strip().lower()
        configured_tier = (
            str(enrich_model_entry(configured).get("tier") or "standard").lower()
            if configured
            else ""
        )
        # In Auto, a configured 9B conversational/coding default is a
        # preference, not a complexity-blind pin. High-complexity work must use
        # the strongest eligible installed local model (for example Ornith
        # 35B) when the configured default is below the high tier. Manual mode
        # and the step dropdown remain the actual pinning mechanisms.
        configured_is_sufficient = (
            complexity_id not in {"high", "complex", "critical"}
            or configured_tier == "high"
        )
        if configured and configured_is_sufficient:
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
    hy3 = _openrouter_hy3_route(settings)
    if hy3 and not (
        current_backend == "pi"
        and current_provider == "openrouter"
        and current_model == "tencent/hy3-preview"
    ):
        candidates.append({
            **hy3,
            "automatic": True,
            "reason": "Try the lower-cost OpenRouter cloud tier before paid coding agents.",
        })
    if current_backend == "pi" and not (
        current_provider == "openrouter" and current_model == "tencent/hy3-preview"
    ):
        candidates.append({
            "backend": "codex",
            "model": "auto",
            "automatic": True,
            "reason": "Escalate from free workers after their completion contracts fail.",
        })
    if current_backend in {"pi", "codex"}:
        candidates.append({
            "backend": "cursor",
            "model": "auto",
            "automatic": False,
            "reason": "Cursor is available as an interactive IDE handoff, not a silent background retry.",
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
    # Manual/Tensology-approved routes are pinned: never swap them for a
    # free/local role model.
    if route_disallows_free_preference(merged, config=cfg):
        merged["policy_source"] = f"{str(merged.get('source') or 'manual_route')}_locked_preserved"
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
    task_profile = dict(task_profile) if isinstance(task_profile, dict) else {}
    # The baseline route is inferred from the original request, so its intent
    # can still say ``planning`` while this function is allocating an
    # implementation or review step. The run-scoped role is more specific and
    # must win or every assignment inherits planning-oriented behavior.
    task_profile["intent"] = role
    merged["task_profile"] = task_profile
    risk_flags = {
        str(item).strip().lower()
        for item in (task_profile.get("risk_flags") or [])
        if str(item).strip()
    }
    high_consequence = bool(
        risk_flags.intersection({"auth", "payments", "migration", "cross_module", "ui_critical"})
    )
    prefer_free_local = bool(
        policy.get("prefer_free_local")
        or policy.get("prefer_local")
        or str(policy.get("preference") or policy.get("model_policy_preference") or "").strip().lower()
        in {"free", "local", "free_local"}
    )
    selected: dict[str, Any]
    rationale: str

    ui_local_first = bool(policy.get("ui_local_first") or cfg.get("ui_local_first"))
    quality_failures = int(
        cfg.get("quality_gate_failures")
        or task_profile.get("quality_gate_failures")
        or 0
    )
    hosted_threshold = max(1, int(policy.get("hosted_ui_escalation_after_failures") or 2))

    if role == "final_polish" and bool(task_profile.get("read_only")):
        selected = _free_eligible_model(
            settings,
            complexity=complexity,
            prefer_local=prefer_free_local,
            avoid_proven_limited=True,
        )
        rationale = (
            "This is a read-only evidence pass, so Auto kept final verification on a "
            "healthy free route instead of paying Codex to make no implementation change."
        )
    elif role == "final_polish" and ui_local_first and quality_failures < hosted_threshold:
        selected = _free_eligible_model(
            settings,
            complexity=complexity,
            prefer_local=True,
            avoid_proven_limited=True,
        )
        rationale = (
            "The UI production pass stays on a healthy local model until executable "
            f"quality gates fail {hosted_threshold} times."
        )
    elif role == "final_polish":
        selected = {"backend": "codex", "model": "auto", "source": "auto_role_route"}
        rationale = (
            "Hosted final polish was unlocked by repeated local quality-gate failures "
            "or by a workflow that does not opt into local-first UI execution."
        )
    elif role == "planning" and complexity in {"medium", "high"} and not prefer_free_local:
        selected = {"backend": "codex", "model": "auto", "source": "auto_role_route"}
        rationale = (
            f"{complexity.title()}-complexity planning benefits from a stronger independent "
            "reasoning pass, so Auto selected Codex before implementation."
        )
    elif role == "planning":
        selected = _free_eligible_model(
            settings,
            complexity=complexity,
            prefer_local=prefer_free_local,
            avoid_proven_limited=True,
        )
        rationale = (
            f"Auto honored the workflow free/local preference and selected a free planning model "
            f"for {complexity} complexity."
        )
    elif role == "implementation" and high_consequence and not prefer_free_local:
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
            avoid_proven_limited=True,
        )
        rationale = "Review uses a different free/lower-cost provider from implementation to reduce context bias."
    elif role == "deployment":
        selected = merged
        rationale = "Deployment keeps its approved route; the workflow approval gate controls execution."
    else:
        selected = _free_eligible_model(
            settings,
            complexity=complexity,
            prefer_local=True,
            avoid_proven_limited=True,
        )
        rationale = (
            f"Auto selected the configured local/free model for {role} at {complexity} complexity"
            + (", honoring the workflow preference before paid escalation." if prefer_free_local else ".")
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
    if is_thread_route(merged) and not cfg.get("route_locked"):
        # fix2: the thread composer route is a higher-level selection; step
        # pins and workflow free/local policies never replace it.
        merged["policy_source"] = "thread_model_route_inherited"
        return merged
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

    if route_disallows_free_preference(merged, config=cfg):
        # Free-first workflow defaults (``prefer_free_local``) do not apply to
        # manually pinned or Tensology-approved routes.
        merged["model"] = str(merged.get("model") or "auto").strip() or "auto"
        merged["policy_source"] = f"{str(merged.get('source') or 'manual_route')}_locked_preserved"
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


# --- fix2 (2026-10-23): thread route precedence + free-model guard ---
_FREE_PROVIDERS = {"ollama", "lmstudio", "lm_studio", "local", "llamacpp", "llama_cpp"}


def is_thread_route(route: Any) -> bool:
    route = route if isinstance(route, dict) else {}
    return (
        str(route.get("source") or "").strip().lower() == "thread_model_route"
        and str(route.get("route_mode") or "").strip().lower() == "manual"
        and bool(str(route.get("backend") or "").strip())
    )


def thread_route_from_run_data(run_data: Any) -> dict[str, Any]:
    """The run's inherited thread composer route (stable across steps)."""
    data = run_data if isinstance(run_data, dict) else {}
    stored = data.get("thread_route")
    if is_thread_route(stored):
        return dict(stored)
    try:
        from distr.core.workflow.development_threads import execution_route_from_model_route

        return execution_route_from_model_route(data.get("model_route"))
    except Exception:
        return {}


def route_is_free_model(route: Any) -> bool:
    route = route if isinstance(route, dict) else {}
    model = str(route.get("model") or "").strip().lower()
    if not model or model in {"auto", "default"}:
        return False
    if model.endswith(":free"):
        return True
    if route.get("free") is True or route.get("is_free") is True:
        return True
    provider = str(route.get("model_provider") or route.get("provider") or "").strip().lower()
    backend = str(route.get("backend") or "").strip().lower()
    if provider in _FREE_PROVIDERS or backend in {"ollama", "local"}:
        return True
    sources = " ".join(str(route.get(k) or "") for k in ("source", "policy_source")).lower()
    return "free_local" in sources or "free_model" in sources


def _configured_complexity_route(complexity: str, settings: dict | None) -> dict[str, Any]:
    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = dict(load_settings_from_db() or {})
        except Exception:
            settings = {}
    level = str(complexity or "medium").strip().lower() or "medium"
    for prefix in ("", "fallback_"):
        backend = str(settings.get(f"project_cli_{level}_{prefix}backend") or "").strip().lower()
        model = str(settings.get(f"project_cli_{level}_{prefix}model") or "").strip()
        provider = str(
            settings.get(f"project_cli_{level}_{prefix}model_provider")
            or settings.get(f"project_cli_{level}_{prefix}provider")
            or ""
        ).strip()
        candidate = {"backend": backend, "model": model or "auto", "model_provider": provider}
        if backend and not route_is_free_model(candidate):
            return candidate
    return {}


def enforce_paid_route(
    route: dict[str, Any],
    *,
    run_data: dict | None = None,
    config: dict | None = None,
    settings: dict | None = None,
) -> dict[str, Any]:
    """Block free/local models on paid-first runs (thread routes, Tensology tickets).

    Replacement precedence: the inherited thread route, then the configured
    complexity route (primary, then its fallback). Returns the route unchanged
    when nothing paid is available so the caller's preflight reports it.
    """
    merged = dict(route or {})
    if not route_is_free_model(merged):
        return merged
    thread_route = thread_route_from_run_data(run_data)
    if not thread_route and not route_disallows_free_preference(merged, config=config, run_data=run_data):
        return merged
    complexity = str(merged.get("complexity") or "medium")
    replacement = thread_route or _configured_complexity_route(complexity, settings)
    if not replacement:
        return merged
    blocked = f"{merged.get('backend')}/{merged.get('model_provider') or ''}/{merged.get('model')}"
    result = {**merged, **replacement}
    for key in ("free", "is_free", "local", "local_harness_preset"):
        result.pop(key, None)
    result["complexity"] = complexity
    result["paid_first"] = True
    result["blocked_free_route"] = blocked
    if thread_route:
        result["source"] = "thread_model_route"
        result["route_mode"] = "manual"
    else:
        result["source"] = "configured_complexity_route"
    result["policy_source"] = "paid_first_free_model_blocked"
    result["rationale"] = f"Paid-first: blocked free model {blocked}; using {result['backend']}/{result.get('model_provider') or ''}/{result['model']}."
    return result


def route_selection_reason(route: Any) -> str:
    route = route if isinstance(route, dict) else {}
    ident = f"{route.get('backend') or '?'} / {route.get('model_provider') or 'default'} / {route.get('model') or 'auto'}"
    source = str(route.get("source") or "").strip().lower()
    prefix = ""
    if route.get("blocked_free_route"):
        prefix = f"blocked free model {route['blocked_free_route']}; "
    if route.get("model_tier_applied"):
        return f"{prefix}{route.get('model_tier')} tier model {ident} for a light step (inherited {route.get('inherited_route')})"
    if source == "thread_model_route":
        return f"{prefix}inherited thread route {ident} (thread_model_route)"
    if source == "configured_complexity_route":
        return f"{prefix}configured {route.get('complexity') or 'medium'} complexity route {ident}"
    return f"{prefix}workflow route {ident} ({source or 'unspecified'})"


# --- parallel (2026-10-23): per-step model tier hint ---------------------
# A deliberate, narrow exception to "steps inherit the thread model": light
# steps (understand / contract / report) may carry ``config.model_tier`` and
# run on the configured tier route model, in the SAME backend/provider family
# as the inherited route.  Heavy steps omit the hint (or use "inherit") and
# keep the thread model.  Tier models come from the existing
# project_cli_{low,medium,high}_* route settings; nothing is hardcoded here.
STEP_MODEL_TIERS = ("low", "medium", "high")
_STEP_TIER_ALIASES = {"fast": "medium", "light": "medium", "cheap": "low", "strong": "high"}
_TIER_RANK = {"low": 0, "medium": 1, "high": 2}
_LIGHT_MODEL_MARKERS = ("flash", "mini", "lite", "nano", "haiku", "-fast", "lightning")


def step_model_tier(config: Any) -> str:
    """Return the step's requested tier (low/medium/high) or "" for inherit."""
    cfg = config if isinstance(config, dict) else {}
    raw = str(cfg.get("model_tier") or "").strip().lower()
    raw = _STEP_TIER_ALIASES.get(raw, raw)
    return raw if raw in STEP_MODEL_TIERS else ""


def _tier_settings(settings: dict | None) -> dict:
    if settings:
        return settings
    try:
        from distr.core.settings import load_settings_from_db

        return dict(load_settings_from_db() or {})
    except Exception:
        return {}


def _model_tier_rank(model: str, settings: dict) -> int | None:
    """Rank a model by the configured tier routes (primary first, then fallback)."""
    clean = str(model or "").strip().lower()
    if not clean or clean == "auto":
        return None
    for prefix in ("", "fallback_"):
        for level in STEP_MODEL_TIERS:
            configured = str(settings.get(f"project_cli_{level}_{prefix}model") or "").strip().lower()
            if configured and configured == clean:
                return _TIER_RANK[level]
    return None


def apply_step_model_tier(
    route: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    settings: dict | None = None,
) -> dict[str, Any]:
    """Downgrade a light step to its configured tier model within the route family.

    Never upgrades: if the inherited model already ranks at or below the
    requested tier (or looks like a flash/mini model) the route is unchanged.
    Free/local tier models are never selected (paid-first guard).  The
    inherited route is kept as the fallback so a failing fast model escalates
    back to the thread model.
    """
    merged = dict(route or {})
    cfg = config if isinstance(config, dict) else {}
    tier = step_model_tier(cfg)
    if not tier or cfg.get("route_locked"):
        return merged
    settings = _tier_settings(settings)
    level_backend = str(settings.get(f"project_cli_{tier}_backend") or "").strip().lower()
    level_model = str(settings.get(f"project_cli_{tier}_model") or "").strip()
    level_provider = str(
        settings.get(f"project_cli_{tier}_model_provider")
        or settings.get(f"project_cli_{tier}_provider")
        or ""
    ).strip()
    tier_route = {"backend": level_backend, "model": level_model or "auto", "model_provider": level_provider}

    def _skip(reason: str) -> dict[str, Any]:
        merged["model_tier"] = tier
        merged["model_tier_skipped"] = reason
        return merged

    if not level_backend or not level_model or level_model.lower() == "auto":
        return _skip("tier_route_not_configured")
    if route_is_free_model(tier_route):
        return _skip("tier_model_is_free")
    current_backend = str(merged.get("backend") or "").strip().lower()
    current_provider = str(merged.get("model_provider") or "").strip().lower()
    current_model = str(merged.get("model") or "").strip()
    if current_backend and current_backend != level_backend:
        return _skip("tier_backend_differs_from_route_family")
    if current_provider and level_provider and current_provider != level_provider.lower():
        return _skip("tier_provider_differs_from_route_family")
    if current_model.lower() == level_model.lower():
        return _skip("already_on_tier_model")
    current_rank = _model_tier_rank(current_model, settings)
    if current_rank is not None and current_rank <= _TIER_RANK[tier]:
        return _skip("inherited_model_already_at_or_below_tier")
    if current_rank is None and any(m in current_model.lower() for m in _LIGHT_MODEL_MARKERS):
        return _skip("inherited_model_already_light")
    if current_model.lower() in {"", "auto"}:
        return _skip("inherited_model_unresolved")

    inherited = f"{merged.get('backend') or '?'}/{merged.get('model_provider') or ''}/{current_model}"
    result = dict(merged)
    result["backend"] = level_backend or current_backend
    result["model"] = level_model
    result["model_provider"] = level_provider or merged.get("model_provider") or ""
    result["fallback_backend"] = merged.get("backend") or level_backend
    result["fallback_model"] = current_model
    result["fallback_model_provider"] = merged.get("model_provider") or ""
    result["model_tier"] = tier
    result["model_tier_applied"] = True
    result["inherited_route"] = inherited
    result.pop("model_tier_skipped", None)
    result["policy_source"] = "step_model_tier"
    result["rationale"] = (
        f"Light step uses the {tier} tier model {level_model} in the inherited route family; "
        f"falls back to the inherited {inherited}."
    )
    return result
