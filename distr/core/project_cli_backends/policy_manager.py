"""Preview and apply editable model policies for workflows and global routing.

The policy manager deliberately separates *pinned* configuration from *auto*
preflight resolution.  Pinned routes are database configuration, never model
identifiers baked into application source.  Auto plans resolve a concrete,
auditable route for every workflow step before execution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any


VALID_MODES = {"auto", "pinned"}
VALID_PREFERENCES = {"free", "balanced", "performance"}
VALID_SCOPES = {"workflow", "global", "both"}
LEVELS = ("low", "medium", "high")


def _json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalise_route(raw: Any, *, source: str) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    backend = str(value.get("backend") or value.get("backend_id") or "").strip().lower()
    model = str(value.get("model") or "auto").strip() or "auto"
    provider = str(value.get("model_provider") or value.get("provider") or "").strip().lower()
    if not backend:
        backend = "pi" if provider in {"ollama", "openrouter", "kilocode", "nvidia", "groq", "gemini"} else "codex"
    if backend == "codex" and not provider:
        provider = "openai"
    if backend == "claude_code" and not provider:
        provider = "anthropic"
    return {
        "backend": backend,
        "model": model,
        "model_provider": provider,
        "source": source,
    }


def _route_key(route: dict[str, Any]) -> str:
    return "|".join(
        str(route.get(key) or "").strip().lower()
        for key in ("backend", "model_provider", "model")
    )


def _recent_model_failure_counts(*, hours: int = 24, limit: int = 300) -> dict[str, int]:
    """Return consecutive recent failures for each concrete model.

    Auto routing must not repeatedly choose the leaderboard winner when live
    executions show that it is rate-limited, timing out, or otherwise failing.
    A later successful completion resets the model's failure streak.  This is
    deliberately execution evidence, not a permanent blacklist; pinned routes
    remain available and a model naturally becomes eligible again after a
    successful retry.
    """
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import ProjectExecutionSession

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=max(1, int(hours)))
        with get_session() as db:
            rows = (
                db.query(ProjectExecutionSession)
                .filter(ProjectExecutionSession.started_at >= cutoff)
                .filter(ProjectExecutionSession.selected_model.isnot(None))
                .order_by(ProjectExecutionSession.id.desc())
                .limit(max(1, int(limit)))
                .all()
            )
            failures: dict[str, int] = {}
            resolved: set[str] = set()
            for row in rows:
                model = str(getattr(row, "selected_model", "") or "").strip().lower()
                if not model or model == "auto" or model in resolved:
                    continue
                status = str(getattr(row, "status", "") or "").strip().lower()
                if status == "completed":
                    resolved.add(model)
                elif status == "failed" and _counts_as_model_health_failure(
                    str(getattr(row, "error", "") or "")
                ):
                    failures[model] = failures.get(model, 0) + 1
            return failures
    except Exception:
        # Routing still works during database bootstrap.  It simply lacks the
        # temporary health demotion until execution history becomes available.
        return {}


def _counts_as_model_health_failure(error: str) -> bool:
    """Return whether an execution proves that a model route is unavailable.

    Route health is deliberately narrower than workflow quality.  A worker can
    produce an invalid handoff or use too many inspection calls while its model
    endpoint is perfectly healthy.  Those failures remain visible to workflow
    validation and learning, but must not make Auto silently remove the local
    model from the next preflight.
    """
    lowered = str(error or "").strip().lower()
    if not lowered:
        return False
    non_route_markers = (
        "cancelled",
        "canceled",
        "code 143",
        "sigterm",
        "terminated by user",
        "user stopped",
        "outlived its terminal workflow run",
        "inspection budget exceeded",
        "required 'status: completed'",
        "required status contract",
        "completion report",
        "result remains unverified",
    )
    if any(marker in lowered for marker in non_route_markers):
        return False
    route_failure_markers = (
        "429",
        "rate limit",
        "quota",
        "insufficient credit",
        "payment required",
        "timed out",
        "timeout",
        "connection refused",
        "connection error",
        "not support chat",
        "model not found",
        "provider unavailable",
        "service unavailable",
        "http 5",
        "status code 5",
    )
    return any(marker in lowered for marker in route_failure_markers)


def _openrouter_free_cooldown_active(*, minutes: int = 30) -> bool:
    """Use recent real 429 evidence as a short provider-level circuit breaker."""
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import ProjectExecutionSession

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=max(1, int(minutes))
        )
        with get_session() as db:
            return bool(
                db.query(ProjectExecutionSession.id)
                .filter(ProjectExecutionSession.started_at >= cutoff)
                .filter(ProjectExecutionSession.selected_model.like("%:free"))
                .filter(ProjectExecutionSession.error.ilike("%429%"))
                .first()
            )
    except Exception:
        return False


def _apply_provider_health(
    routes: list[dict[str, Any]], *, openrouter_free_cooldown: bool
) -> list[dict[str, Any]]:
    """Temporarily remove free OpenRouter routes after real rate-limit evidence."""
    annotated: list[dict[str, Any]] = []
    available: list[dict[str, Any]] = []
    for raw in routes:
        route = dict(raw)
        is_openrouter_free = (
            str(route.get("model_provider") or "").strip().lower() == "openrouter"
            and str(route.get("model") or "").strip().lower().endswith(":free")
        )
        cooling_down = bool(openrouter_free_cooldown and is_openrouter_free)
        route["provider_health"] = "rate_limit_cooldown" if cooling_down else "healthy"
        annotated.append(route)
        if not cooling_down:
            available.append(route)
    return available or annotated


def _route_strength(route: dict[str, Any]) -> float:
    if route.get("score") is not None:
        try:
            return float(route["score"])
        except (TypeError, ValueError):
            pass
    match = re.search(
        r"(?:^|[-_:])(\d+(?:\.\d+)?)b(?:$|[-_:])",
        str(route.get("model") or "").lower(),
    )
    return float(match.group(1)) if match else 0.0


def _apply_recent_model_health(
    routes: list[dict[str, Any]],
    failure_counts: dict[str, int] | None = None,
    *,
    failure_threshold: int = 2,
) -> list[dict[str, Any]]:
    """Annotate candidates and remove repeatedly failing models from Auto.

    If every discovered route is unhealthy, return the annotated catalogue so
    callers can still present an explicit choice instead of silently claiming
    no provider exists.
    """
    counts = failure_counts if failure_counts is not None else _recent_model_failure_counts()
    annotated: list[dict[str, Any]] = []
    healthy: list[dict[str, Any]] = []
    for raw in routes:
        route = dict(raw)
        model = str(route.get("model") or "").strip().lower()
        failures = int(counts.get(model, 0) or 0)
        route["recent_failures"] = failures
        route["health_status"] = "demoted" if failures >= failure_threshold else "healthy"
        annotated.append(route)
        if failures < failure_threshold:
            healthy.append(route)
    return healthy or annotated


def _step_role(step: Any) -> str:
    config = _json_dict(getattr(step, "config", None))
    explicit = str(config.get("step_role") or (config.get("model_policy") or {}).get("step_role") or "").strip().lower()
    if explicit:
        return explicit

    def infer(text: str) -> str:
        text = text.strip().lower()
        if any(phrase in text for phrase in ("final production polish", "release polish", "final ship audit")):
            return "final_polish"
        if any(word in text for word in ("report", "summar", "handoff", "compact memory")):
            return "reporting"
        if any(word in text for word in ("review", "audit", "quality", "critic", "self-assess")):
            return "review"
        if any(word in text for word in ("test", "validate", "validation", "verify", "playwright", " qa", "evidence")):
            return "validation"
        if text.startswith(("plan", "scope", "architect", "ingest", "understand", "define")):
            return "planning"
        if any(word in text for word in ("implement", "build", "correct", "fix", "develop", "refactor")):
            return "implementation"
        if any(word in text for word in ("plan", "scope", "architect", "acceptance criteria")):
            return "planning"
        return ""

    # A step title is the operator's declared role.  Only inspect the longer
    # instruction when the title is genuinely neutral; otherwise incidental
    # phrases such as "follow the implementation plan" misclassify builders as
    # planners and collapse independent model routing.
    role = infer(str(getattr(step, "name", "") or ""))
    if role:
        return role
    role = infer(str(getattr(step, "instruction", "") or ""))
    if role:
        return role
    return "implementation"


def _discover_routes(settings: dict[str, Any], *, complexity: str) -> list[dict[str, Any]]:
    """Return current free/local routes without exposing provider credentials."""
    from distr.core.project_cli_backends.models_catalog import installed_ollama_cli_models
    from distr.core.project_cli_backends.provider_preflight import rank_openrouter_free_models

    routes: list[dict[str, Any]] = []
    for row in installed_ollama_cli_models(settings):
        if not row.get("usable", True):
            continue
        is_local = bool(row.get("local", True))
        is_free = bool(row.get("free", is_local))
        if not is_local and not is_free:
            continue
        routes.append({
            "backend": "pi",
            "model": str(row.get("id") or "auto"),
            "model_provider": "ollama",
            "source": "live_local_catalog",
            "reason": str(row.get("reason") or "Installed local model."),
            "free": is_free,
            "local": is_local,
        })
    if settings.get("openrouter_enabled") and str(settings.get("openrouter_key") or "").strip():
        for row in rank_openrouter_free_models(
            api_key=str(settings.get("openrouter_key") or ""),
            complexity=complexity,
            required_capabilities=["tools", "files"],
            limit=5,
        ):
            routes.append({**row, "source": "live_openrouter_free_catalog", "free": True, "local": False})
    seen: set[str] = set()
    unique = [route for route in routes if not (_route_key(route) in seen or seen.add(_route_key(route)))]
    model_healthy = _apply_recent_model_health(unique)
    return _apply_provider_health(
        model_healthy,
        openrouter_free_cooldown=_openrouter_free_cooldown_active(),
    )


def _automatic_level_routes(
    settings: dict[str, Any],
    preference: str,
    *,
    prefer_local: bool = False,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    candidates = _discover_routes(settings, complexity="high")
    local = max((route for route in candidates if route.get("local")), key=_route_strength, default=None)
    free = max(
        (route for route in candidates if route.get("free") and not route.get("local")),
        key=_route_strength,
        default=None,
    )
    economical = local or free
    strongest_free = (local or free) if prefer_local else (free or local)
    codex = _normalise_route({"backend": "codex", "model": "auto", "provider": "openai"}, source="auto_policy")
    claude = _normalise_route({"backend": "claude_code", "model": "auto", "provider": "anthropic"}, source="auto_policy")

    if preference == "performance":
        levels = {"low": codex, "medium": codex, "high": claude}
    elif preference == "balanced":
        levels = {"low": economical or codex, "medium": codex, "high": codex}
    else:
        levels = {"low": economical or codex, "medium": strongest_free or codex, "high": codex}
    return {level: dict(route) for level, route in levels.items()}, candidates


def _automatic_role_routes(
    levels: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    preference: str,
    *,
    prefer_local: bool = False,
) -> dict[str, dict[str, Any]]:
    free_routes = sorted(
        (route for route in candidates if route.get("free")),
        key=_route_strength,
        reverse=True,
    )
    local_routes = sorted(
        (route for route in candidates if route.get("local")),
        key=_route_strength,
        reverse=True,
    )
    implementation = dict(local_routes[0]) if local_routes else dict(levels["medium"])
    planning = (
        dict(local_routes[0])
        if prefer_local and local_routes
        else dict(levels["high"] if preference != "free" else levels["medium"])
    )
    review = next((dict(route) for route in free_routes if _route_key(route) != _route_key(implementation)), None)
    review = review or _normalise_route({"backend": "codex", "model": "auto"}, source="auto_policy")
    validation = next(
        (dict(route) for route in free_routes if _route_key(route) not in {_route_key(implementation), _route_key(review)}),
        None,
    ) or dict(review)
    reporting = min(
        (route for route in local_routes if _route_strength(route) >= 9.0),
        key=_route_strength,
        default=None,
    )
    return {
        "planning": planning,
        "implementation": implementation,
        "review": review,
        "validation": validation,
        "final_polish": _normalise_route(
            {"backend": "codex", "model": "auto", "provider": "openai"},
            source="auto_policy",
        ),
        # Compact reporting does not need the largest local coder, but sub-8B
        # models below roughly 9B are too unreliable for durable memory/result contracts.
        "reporting": dict(reporting or levels["low"]),
    }


def build_model_policy_plan(
    *,
    scope: str = "workflow",
    workflow_id: int | None = None,
    mode: str = "auto",
    preference: str = "free",
    assignments: dict[str, Any] | None = None,
    prefer_local: bool = False,
) -> dict[str, Any]:
    """Build a non-mutating, serialisable policy plan."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
    from distr.core.settings import load_settings_from_db

    scope = str(scope or "workflow").strip().lower()
    mode = str(mode or "auto").strip().lower()
    preference = str(preference or "free").strip().lower().replace("-", "_").replace(" ", "_")
    preference = {
        "prefer_free": "free",
        "free_first": "free",
        "local_first": "free",
        "cheap": "free",
        "cheapest": "free",
        "cost": "free",
        "cost_effective": "free",
        "auto": "balanced",
        "automatic": "balanced",
        "best": "performance",
        "quality": "performance",
        "strongest": "performance",
    }.get(preference, preference)
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unknown scope: {scope}")
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode: {mode}; use auto or pinned")
    if preference not in VALID_PREFERENCES:
        raise ValueError(f"Unknown preference: {preference}")
    if scope in {"workflow", "both"} and not workflow_id:
        raise ValueError("workflow_id is required for workflow policy changes")

    settings = load_settings_from_db()
    supplied = assignments if isinstance(assignments, dict) else {}
    auto_levels, candidates = _automatic_level_routes(
        settings,
        preference,
        prefer_local=prefer_local,
    )
    if mode == "pinned":
        supplied_levels = supplied.get("complexity") if isinstance(supplied.get("complexity"), dict) else {}
        levels = {
            level: _normalise_route(
                supplied_levels.get(level) or {
                    "backend": settings.get(f"project_cli_{level}_backend"),
                    "model": settings.get(f"project_cli_{level}_model"),
                },
                source="pinned_policy",
            )
            for level in LEVELS
        }
    else:
        levels = auto_levels

    roles = _automatic_role_routes(
        levels,
        candidates,
        preference,
        prefer_local=prefer_local,
    )
    supplied_roles = supplied.get("roles") if isinstance(supplied.get("roles"), dict) else {}
    for role, route in supplied_roles.items():
        roles[str(role).strip().lower()] = _normalise_route(route, source="pinned_policy")

    workflow: dict[str, Any] | None = None
    if workflow_id:
        with get_session() as db:
            record = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
            if not record:
                raise ValueError(f"Workflow {workflow_id} was not found")
            steps = (
                db.query(AutoWorkflowStep)
                .filter(AutoWorkflowStep.workflow_id == int(workflow_id))
                .order_by(AutoWorkflowStep.position)
                .all()
            )
            supplied_steps = supplied.get("steps") if isinstance(supplied.get("steps"), dict) else {}
            planned_steps = []
            for step in steps:
                role = _step_role(step)
                explicit = supplied_steps.get(str(step.id)) or supplied_steps.get(step.id)
                route = _normalise_route(explicit, source="pinned_policy") if explicit else dict(roles.get(role) or levels["medium"])
                planned_steps.append({
                    "step_id": step.id,
                    "position": step.position,
                    "name": step.name,
                    "role": role,
                    "route": route,
                })
            workflow = {"id": record.id, "name": record.name, "steps": planned_steps}

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "mode": mode,
        "preference": preference,
        "complexity_routes": levels,
        "role_routes": roles,
        "workflow": workflow,
        "catalog": {
            "candidate_count": len(candidates),
            "candidates": [
                {key: route.get(key) for key in (
                    "backend", "model_provider", "model", "free", "local", "reason", "score",
                    "recent_failures", "health_status",
                    "provider_health",
                )}
                for route in candidates[:8]
            ],
        },
    }


def _execution_route(route: dict[str, Any], *, mode: str, preference: str) -> dict[str, Any]:
    provider = str(route.get("model_provider") or "").strip()
    model = str(route.get("model") or "auto").strip()
    backend = str(route.get("backend") or "pi").strip()
    return {
        "enabled": True,
        "mode": "scoped",
        "scoped_model_key": f"{backend}|{provider}|{model}",
        "route_snapshot": {
            "backend_id": backend,
            "provider": provider,
            "model": model,
            "name": model if model != "auto" else f"{backend} automatic model",
            "policy_mode": mode,
            "preference": preference,
        },
    }


def apply_model_policy_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Persist a previously previewed plan and return an audit-safe summary."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
    from distr.core.settings import save_settings_to_db

    scope = str(plan.get("scope") or "workflow")
    mode = str(plan.get("mode") or "auto")
    preference = str(plan.get("preference") or "free")
    changed: dict[str, Any] = {"global": [], "workflow": None}
    if scope in {"global", "both"}:
        updates: dict[str, Any] = {}
        for level, route in (plan.get("complexity_routes") or {}).items():
            if level not in LEVELS or not isinstance(route, dict):
                continue
            updates[f"project_cli_{level}_backend"] = str(route.get("backend") or "pi")
            updates[f"project_cli_{level}_model"] = str(route.get("model") or "auto")
            updates[f"project_cli_{level}_model_provider"] = str(route.get("model_provider") or "")
        save_settings_to_db(updates)
        changed["global"] = sorted(updates)

    workflow_plan = plan.get("workflow") if isinstance(plan.get("workflow"), dict) else None
    if scope in {"workflow", "both"}:
        if not workflow_plan:
            raise ValueError("The plan does not contain a workflow")
        workflow_id = int(workflow_plan["id"])
        with get_session() as db:
            workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} was not found")
            settings = _json_dict(workflow.run_settings)
            settings.update({
                "model_policy_mode": mode,
                "model_policy_preference": preference,
                "auto_route_models": mode == "auto",
                "resolved_model_plan": {
                    "version": plan.get("version", 1),
                    "generated_at": plan.get("generated_at"),
                    "role_routes": plan.get("role_routes") or {},
                },
            })
            workflow.run_settings = json.dumps(settings)
            step_changes = []
            for item in workflow_plan.get("steps") or []:
                step = db.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.id == int(item["step_id"]),
                    AutoWorkflowStep.workflow_id == workflow_id,
                ).first()
                if not step:
                    continue
                config = _json_dict(step.config)
                route = item.get("route") if isinstance(item.get("route"), dict) else {}
                config["step_role"] = str(item.get("role") or "implementation")
                config["model_policy"] = {
                    **(config.get("model_policy") if isinstance(config.get("model_policy"), dict) else {}),
                    "mode": mode,
                    "preference": preference,
                    "auto_route_models": mode == "auto",
                }
                config["execution_route"] = _execution_route(route, mode=mode, preference=preference)
                step.config = json.dumps(config)
                step_changes.append({"step_id": step.id, "role": config["step_role"], "route": route})
            db.commit()
            changed["workflow"] = {"id": workflow.id, "name": workflow.name, "steps": step_changes}
    return changed


def refresh_auto_model_policy_for_workflow(workflow_id: int) -> dict[str, Any] | None:
    """Re-resolve an Auto policy immediately before a workflow run starts.

    Pinned workflows intentionally return without mutation.  A failed live
    catalogue refresh is allowed to bubble to the caller, which can retain the
    last known-good concrete step routes instead of making the run unusable.
    """
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    with get_session() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} was not found")
        settings = _json_dict(workflow.run_settings)
        if settings.get("model_policy_mode") != "auto":
            return None
        preference = str(settings.get("model_policy_preference") or "free")
        prefer_local = bool(settings.get("prefer_local") or settings.get("prefer_free_local"))
    plan = build_model_policy_plan(
        scope="workflow",
        workflow_id=int(workflow_id),
        mode="auto",
        preference=preference,
        prefer_local=prefer_local,
    )
    return {"plan": plan, "applied": apply_model_policy_plan(plan)}
