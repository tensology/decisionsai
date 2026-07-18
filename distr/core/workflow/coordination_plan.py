"""Run-scoped whole-workflow coordination and adaptive model allocation.

The reusable workflow remains immutable while a run is executing.  This module
turns that workflow into an auditable execution overlay: every step receives a
role, primary worker, optional independent evaluator, dependencies, expected
outputs and a bounded replan policy before the first worker starts.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any


PLAN_VERSION = 1


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}") or {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _step_role(step: Any, config: dict[str, Any]) -> str:
    explicit = str(config.get("step_role") or "").strip().lower()
    if explicit:
        return explicit
    text = f"{getattr(step, 'name', '')} {getattr(step, 'description', '')}".lower()
    if any(word in text for word in ("plan", "scope", "understand", "acceptance")):
        return "planning"
    if any(word in text for word in ("review", "audit", "validate", "verify", "quality")):
        return "review"
    if any(word in text for word in ("correct", "fix", "remediate")):
        return "correction"
    if any(word in text for word in ("deploy", "release", "ship")):
        return "deployment"
    if any(word in text for word in ("report", "memory", "handoff")):
        return "reporting"
    return "implementation"


def _route_identity(route: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(route.get("backend") or "").strip().lower(),
        str(route.get("model_provider") or "").strip().lower(),
        str(route.get("model") or "auto").strip().lower(),
    )


def _validator_capable(route: dict[str, Any]) -> bool:
    provider = str(route.get("model_provider") or route.get("provider") or "").strip()
    model = str(route.get("model") or "").strip().lower()
    return bool(provider and model and model != "auto")


def _free_validator_candidates(settings: dict[str, Any]) -> list[dict[str, Any]]:
    from distr.core.project_cli_backends.model_policy import _free_eligible_model

    candidates: list[dict[str, Any]] = []
    for prefer_local in (True, False):
        route = dict(_free_eligible_model(settings, complexity="high", prefer_local=prefer_local))
        if route.get("provider") and not route.get("model_provider"):
            route["model_provider"] = route.pop("provider")
        route["source"] = "coordination_independent_evaluator"
        if _validator_capable(route) and _route_identity(route) not in {
            _route_identity(item) for item in candidates
        }:
            candidates.append(route)
    return candidates


def _distinct_evaluator(
    primary: dict[str, Any],
    candidate: dict[str, Any],
    *,
    settings: dict[str, Any],
) -> dict[str, Any]:
    if _validator_capable(candidate) and _route_identity(primary) != _route_identity(candidate):
        return candidate
    try:
        for route in _free_validator_candidates(settings):
            if _route_identity(route) != _route_identity(primary):
                return route
    except Exception:
        pass
    # Do not pretend that a provider-less CLI `auto` route is an independent
    # validator. The LLM validator can only execute concrete provider/model
    # routes; Claude also remains the last failure escalation, never a routine
    # shadow reviewer.
    return {}


def _review_mode(*, role: str, complexity: str, risk_level: str, config: dict[str, Any]) -> str:
    requested = str(config.get("review_mode") or "").strip().lower()
    if requested in {"deterministic", "independent", "dual"}:
        return requested
    high_consequence = risk_level in {"high", "critical"} or complexity == "high"
    if role in {"review", "deployment", "final_polish"}:
        return "dual" if high_consequence else "independent"
    if role in {"implementation", "correction"} and high_consequence:
        return "independent"
    return "deterministic"


def build_run_coordination_plan(
    workflow: Any,
    run_data: dict[str, Any],
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Allocate the complete workflow before execution without mutating steps."""
    from distr.core.project_cli_backends.model_policy import apply_auto_step_role_policy

    settings = dict(settings or {})
    workflow_settings = _dict(getattr(workflow, "run_settings", None))
    is_canonical_development = str(getattr(workflow, "name", None) or "").strip().lower() == "development"
    adaptive_default = is_canonical_development and bool(workflow_settings.get("auto_route_models", False))
    adaptive_multi_model = bool(
        workflow_settings.get("adaptive_multi_model_enabled", adaptive_default)
    )
    try:
        max_parallel_evaluators = max(
            1,
            min(2, int(workflow_settings.get("max_parallel_evaluators") or 2)),
        )
    except (TypeError, ValueError):
        max_parallel_evaluators = 2
    steps = sorted(list(getattr(workflow, "steps", None) or []), key=lambda item: int(item.position or 0))
    base_route = _dict(run_data.get("execution_route"))
    complexity = str(
        base_route.get("complexity")
        or _dict(run_data.get("ticket_execution_profile")).get("complexity")
        or "medium"
    ).strip().lower() or "medium"
    risk = _dict(run_data.get("risk_profile"))
    risk_level = str(risk.get("level") or "low").strip().lower() or "low"
    assignments: dict[str, dict[str, Any]] = {}
    role_routes: dict[str, dict[str, Any]] = {}
    prior_step_id: int | None = None

    for index, step in enumerate(steps):
        config = _dict(getattr(step, "config", None))
        role = _step_role(step, config)
        seeded = dict(base_route)
        seeded.setdefault("complexity", complexity)
        primary = apply_auto_step_role_policy(
            seeded,
            workflow=workflow,
            config=config,
            settings=settings,
            step_role=role,
            prior_role_routes=role_routes,
        )
        primary["source"] = "run_coordination_plan"
        primary["step_role"] = role
        primary.setdefault(
            "rationale",
            f"Whole-workflow preflight allocated this {role} step before execution began.",
        )
        requested_review_mode = (
            _review_mode(
                role=role,
                complexity=complexity,
                risk_level=risk_level,
                config=config,
            )
            if adaptive_multi_model
            else "deterministic"
        )
        evaluators: list[dict[str, Any]] = []
        if requested_review_mode in {"independent", "dual"}:
            candidate = apply_auto_step_role_policy(
                dict(base_route),
                workflow=workflow,
                config={"model_policy": config.get("model_policy") or {}},
                settings=settings,
                step_role="review",
                prior_role_routes={**role_routes, role: primary},
            )
            candidate = _distinct_evaluator(primary, candidate, settings=settings)
            if candidate:
                candidate["source"] = "coordination_independent_evaluator"
                candidate["independent_from_step"] = int(step.id)
                evaluators.append(candidate)
            if requested_review_mode == "dual" and max_parallel_evaluators > 1:
                try:
                    excluded = {_route_identity(primary), *(_route_identity(item) for item in evaluators)}
                    second = next(
                        (
                            item for item in _free_validator_candidates(settings)
                            if _route_identity(item) not in excluded
                        ),
                        None,
                    )
                except Exception:
                    second = None
                if second:
                    second["source"] = "coordination_second_evaluator"
                    second["independent_from_step"] = int(step.id)
                    evaluators.append(second)
        review_mode = (
            "dual" if len(evaluators) > 1
            else "independent" if evaluators
            else "deterministic"
        )

        assignment = {
            "step_id": int(step.id),
            "position": index,
            "step_name": str(step.name or f"Step {index + 1}"),
            "role": role,
            "depends_on": [prior_step_id] if prior_step_id else [],
            "tools": _list(config.get("tools")),
            "skills": _list(config.get("skills")),
            "required_context": _list(config.get("required_context")),
            "expected_outputs": _list(config.get("expected_outputs")),
            "validation_type": str(getattr(step, "validation_type", None) or "none"),
            "primary_route": primary,
            "review_mode": review_mode,
            "requested_review_mode": requested_review_mode,
            "evaluation_routes": evaluators,
            "status": "planned",
            "revision": 0,
        }
        assignments[str(step.id)] = assignment
        role_routes[role] = primary
        prior_step_id = int(step.id)

    strategy = "single"
    if any(item["review_mode"] == "dual" for item in assignments.values()):
        strategy = "adaptive_dual_review"
    elif any(item["review_mode"] == "independent" for item in assignments.values()):
        strategy = "adaptive_independent_review"
    now = datetime.now(timezone.utc).isoformat()
    return {
        "version": PLAN_VERSION,
        "workflow_id": int(getattr(workflow, "id", 0) or 0),
        "workflow_name": str(getattr(workflow, "name", None) or "Workflow"),
        "created_at": now,
        "updated_at": now,
        "strategy": strategy,
        "complexity": complexity,
        "risk_level": risk_level,
        "immutable_workflow": True,
        "adaptive_multi_model_enabled": adaptive_multi_model,
        "assignments": assignments,
        "revisions": [],
        "latency_policy": {
            "parallel_independent_evaluation": True,
            "max_parallel_evaluators": max_parallel_evaluators,
            "do_not_duplicate_deterministic_steps": True,
        },
    }


def coordination_plan_routes(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    assignments = _dict(plan.get("assignments"))
    step_routes: dict[str, Any] = {}
    role_routes: dict[str, Any] = {}
    for step_id, assignment in assignments.items():
        assignment = _dict(assignment)
        route = _dict(assignment.get("primary_route"))
        if route:
            step_routes[str(step_id)] = route
            role = str(assignment.get("role") or "execution")
            role_routes[role] = route
    return step_routes, role_routes


def render_coordination_map(plan: dict[str, Any], *, current_step_id: int | None = None) -> str:
    """Return a compact whole-run map suitable for each isolated worker."""
    assignments = sorted(
        (_dict(item) for item in _dict(plan.get("assignments")).values()),
        key=lambda item: int(item.get("position") or 0),
    )
    lines: list[str] = []
    for item in assignments:
        route = _dict(item.get("primary_route"))
        worker = " / ".join(
            value for value in (
                str(route.get("backend") or "auto"),
                str(route.get("model_provider") or ""),
                str(route.get("model") or "auto"),
            ) if value
        )
        marker = "→" if current_step_id and int(item.get("step_id") or 0) == int(current_step_id) else "·"
        lines.append(
            f"{marker} {int(item.get('position') or 0) + 1}. {item.get('step_name')} "
            f"[{item.get('role')}; {worker}; review={item.get('review_mode')}]"
        )
    return "\n".join(lines)


def revise_plan_after_step(
    plan: dict[str, Any],
    *,
    completed_step_id: int,
    next_step_id: int | None,
    passed: bool,
    reason: str,
    settings: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Replan the next assignment at a step boundary when evidence requires it."""
    updated = deepcopy(plan or {})
    assignments = _dict(updated.get("assignments"))
    completed = _dict(assignments.get(str(completed_step_id)))
    if completed:
        completed["status"] = "passed" if passed else "failed"
        assignments[str(completed_step_id)] = completed
    if passed or not next_step_id or str(next_step_id) not in assignments:
        updated["assignments"] = assignments
        return updated, None

    target = _dict(assignments[str(next_step_id)])
    previous = _dict(target.get("primary_route"))
    fallback = None
    try:
        from distr.core.project_cli_backends.model_policy import build_auto_fallback_chain

        fallback = next(
            (
                dict(item) for item in build_auto_fallback_chain(previous, settings=settings or {})
                if _route_identity(item) != _route_identity(previous) and bool(item.get("automatic", True))
            ),
            None,
        )
    except Exception:
        fallback = None
    if not fallback:
        return updated, None
    fallback["source"] = "coordination_replan"
    fallback["rationale"] = reason or "The previous step failed; use the next viable worker for correction."
    target["primary_route"] = fallback
    target["revision"] = int(target.get("revision") or 0) + 1
    assignments[str(next_step_id)] = target
    revision = {
        "from_step_id": completed_step_id,
        "target_step_id": next_step_id,
        "previous_route": previous,
        "new_route": fallback,
        "reason": fallback["rationale"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    updated["assignments"] = assignments
    updated["updated_at"] = revision["created_at"]
    updated.setdefault("revisions", []).append(revision)
    return updated, revision
