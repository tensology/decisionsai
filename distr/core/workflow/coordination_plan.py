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


def _ticket_requires_visual_evidence(run_data: dict[str, Any]) -> bool:
    from distr.core.workflow.ticket_contract import classify_ticket_execution

    ticket_text = "\n".join(
        str(run_data.get(key) or "")
        for key in ("ticket_title", "ticket_workflow_brief")
    )
    return bool(classify_ticket_execution(ticket_text).get("ui_evidence_required"))


def _configured_vision_review_route(settings: dict[str, Any]) -> dict[str, Any]:
    provider = str(settings.get("vision_llm_provider") or "").strip().lower()
    model = str(settings.get("vision_llm_model") or "").strip()
    if not provider or not model:
        return {}
    return {
        "backend": "pi",
        "model_provider": provider,
        "model": model,
        "source": "run_coordination_visual_evidence",
        "step_role": "review",
        "task_profile": {"intent": "visual_review"},
        "evidence_capabilities": ["vision"],
        "rationale": (
            "The ticket explicitly requires screenshot evidence, so review uses the configured vision model "
            "to inspect image contents rather than accepting file existence."
        ),
    }


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
    if bool(workflow_settings.get("auto_route_models", False)):
        # Model discovery can probe Ollama and several hosted catalogues.  A
        # whole-run plan must do that once, not once per step/evaluator; the
        # latter made starting a seven-step workflow look frozen for minutes.
        try:
            from distr.core.project_cli_backends.models_catalog import pi_cli_models

            settings["_pi_cli_models_cache"] = pi_cli_models(settings)
        except Exception:
            settings["_pi_cli_models_cache"] = []
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
    if (
        risk_level in {"high", "critical"}
        and not base_route.get("complexity")
        and not _dict(run_data.get("ticket_execution_profile")).get("complexity")
    ):
        complexity = "high"
    # Some run creators know complexity before choosing a backend. Resolve the
    # configured complexity route here so every assignment and the run summary
    # have a concrete worker even when role-aware Auto is disabled.
    if not str(base_route.get("backend") or "").strip():
        base_route["backend"] = str(
            settings.get(f"project_cli_{complexity}_backend")
            or settings.get("project_cli_medium_backend")
            or "pi"
        ).strip() or "pi"
    if not str(base_route.get("model") or "").strip() or str(base_route.get("model")).strip().lower() == "auto":
        base_route["model"] = str(
            settings.get(f"project_cli_{complexity}_model")
            or settings.get("project_cli_medium_model")
            or "auto"
        ).strip() or "auto"
    assignments: dict[str, dict[str, Any]] = {}
    role_routes: dict[str, dict[str, Any]] = {}
    prior_step_id: int | None = None
    visual_evidence_required = _ticket_requires_visual_evidence(run_data)

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
        if role == "review" and visual_evidence_required:
            vision_route = _configured_vision_review_route(settings)
            if vision_route:
                vision_route.setdefault("complexity", complexity)
                primary = vision_route
        primary["source"] = "run_coordination_plan"
        if role == "review" and primary.get("evidence_capabilities") == ["vision"]:
            primary["source"] = "run_coordination_visual_evidence"
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
            "required_evidence_capabilities": (
                ["vision"] if role == "review" and visual_evidence_required else []
            ),
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
        constraints = _list(item.get("steering_constraints"))
        if constraints:
            lines.append(f"  Human steer: {constraints[-1][:300]}")
    return "\n".join(lines)


def revise_plan_after_step(
    plan: dict[str, Any],
    *,
    completed_step_id: int,
    next_step_id: int | None,
    passed: bool,
    reason: str,
    settings: dict[str, Any] | None = None,
    actual_route: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Replan the next assignment at a step boundary when evidence requires it."""
    updated = deepcopy(plan or {})
    assignments = _dict(updated.get("assignments"))
    completed = _dict(assignments.get(str(completed_step_id)))
    executed = _dict(actual_route)
    if completed:
        completed["status"] = "passed" if passed else "failed"
        if executed:
            planned = _dict(completed.get("primary_route"))
            if planned and _route_identity(planned) != _route_identity(executed):
                completed.setdefault("planned_route", planned)
            completed["executed_route"] = executed
            completed["primary_route"] = executed
        assignments[str(completed_step_id)] = completed
    if not next_step_id or str(next_step_id) not in assignments:
        updated["assignments"] = assignments
        return updated, None

    target = _dict(assignments[str(next_step_id)])
    previous = _dict(target.get("primary_route"))
    target_role = str(target.get("role") or "").strip().lower()
    requested_review_mode = str(
        target.get("requested_review_mode") or target.get("review_mode") or "deterministic"
    ).strip().lower()

    # A provider fallback can make the already-planned reviewer identical to
    # the worker that produced the implementation. Preserve what really ran,
    # then choose a distinct concrete reviewer before the next step starts.
    # Without this reconciliation the UI claimed an independent review while
    # silently asking the same model to review its own work.
    if (
        passed
        and executed
        and target_role == "review"
        and requested_review_mode in {"independent", "dual"}
        and _route_identity(previous) == _route_identity(executed)
    ):
        candidates: list[dict[str, Any]] = []
        for candidate in list(target.get("evaluation_routes") or []):
            route = _dict(candidate)
            if route:
                candidates.append(route)
        try:
            candidates.extend(_free_validator_candidates(settings or {}))
        except Exception:
            pass

        distinct: list[dict[str, Any]] = []
        excluded = {_route_identity(executed)}
        for candidate in candidates:
            identity = _route_identity(candidate)
            if not _validator_capable(candidate) or identity in excluded:
                continue
            excluded.add(identity)
            distinct.append(candidate)

        if distinct:
            replacement = dict(distinct.pop(0))
            replacement["source"] = "coordination_actual_route_replan"
            replacement["rationale"] = (
                "The preceding step used this reviewer's planned model after a route fallback; "
                "use a different model to preserve independent review."
            )
            target.setdefault("planned_route", previous)
            target["primary_route"] = replacement
            target["revision"] = int(target.get("revision") or 0) + 1
            target["independent_from_route"] = executed
            target.pop("independence_blocked", None)

            max_evaluators = 2 if requested_review_mode == "dual" else 1
            target["evaluation_routes"] = distinct[:max_evaluators]
            target["review_mode"] = (
                "dual" if len(target["evaluation_routes"]) > 1
                else "independent" if target["evaluation_routes"]
                else "deterministic"
            )
            assignments[str(next_step_id)] = target
            revision = {
                "from_step_id": completed_step_id,
                "target_step_id": next_step_id,
                "previous_route": previous,
                "new_route": replacement,
                "actual_completed_route": executed,
                "reason": replacement["rationale"],
                "replan_evidence_count": int(target.get("replan_evidence_count") or 0),
                "route_changed": True,
                "independence_reconciled": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            updated["assignments"] = assignments
            updated["updated_at"] = revision["created_at"]
            updated.setdefault("revisions", []).append(revision)
            return updated, revision

        target["independence_blocked"] = True
        target["independent_from_route"] = executed
        assignments[str(next_step_id)] = target

    if passed:
        updated["assignments"] = assignments
        return updated, None

    evidence_count = int(target.get("replan_evidence_count") or 0) + 1
    target["replan_evidence_count"] = evidence_count
    target["needs_replan"] = True
    if evidence_count < 2:
        target["revision"] = int(target.get("revision") or 0) + 1
        assignments[str(next_step_id)] = target
        revision = {
            "from_step_id": completed_step_id,
            "target_step_id": next_step_id,
            "previous_route": previous,
            "new_route": previous,
            "reason": (
                f"{reason or 'Validation failed.'} Retry the inexpensive worker once with correction evidence "
                "before changing providers."
            ),
            "replan_evidence_count": evidence_count,
            "route_changed": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        updated["assignments"] = assignments
        updated["updated_at"] = revision["created_at"]
        updated.setdefault("revisions", []).append(revision)
        return updated, revision
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
        "replan_evidence_count": evidence_count,
        "route_changed": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    updated["assignments"] = assignments
    updated["updated_at"] = revision["created_at"]
    updated.setdefault("revisions", []).append(revision)
    return updated, revision


def consume_step_replan(
    plan: dict[str, Any],
    *,
    step_id: int,
    run_data: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    workflow: Any = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve a deferred ``needs_replan`` flag before the step is dispatched.

    Steering and failed-validation paths mark future assignments without
    immediately reallocating them.  Dispatch must call this so the run overlay
    becomes authoritative before the worker starts.
    """
    if not isinstance(plan, dict) or not plan.get("assignments"):
        return deepcopy(plan or {}), None
    updated = deepcopy(plan)
    assignments = _dict(updated.get("assignments"))
    key = str(int(step_id))
    assignment = _dict(assignments.get(key))
    if not assignment or not assignment.get("needs_replan"):
        return updated, None

    settings = dict(settings or {})
    run_data = _dict(run_data)
    previous = _dict(assignment.get("primary_route"))
    preference = str(assignment.get("route_preference") or "").strip().lower()
    complexity = str(
        previous.get("complexity")
        or updated.get("complexity")
        or _dict(run_data.get("execution_route")).get("complexity")
        or "medium"
    ).strip().lower() or "medium"
    role = str(assignment.get("role") or "implementation")
    new_route = dict(previous)
    reason = "Cleared deferred replan marker after confirming the current allocation."

    free_local = {"free", "local", "ornith", "ollama", "local_model", "free_model"}
    if preference in free_local:
        try:
            from distr.core.project_cli_backends.model_policy import _free_eligible_model

            resolved = dict(
                _free_eligible_model(
                    settings,
                    complexity=complexity,
                    prefer_local=preference in {"local", "ornith", "ollama", "local_model"},
                )
            )
            if resolved.get("provider") and not resolved.get("model_provider"):
                resolved["model_provider"] = resolved.pop("provider")
            if resolved.get("model") or resolved.get("backend"):
                new_route = {
                    **previous,
                    **resolved,
                    "complexity": complexity,
                    "source": "coordination_replan_dispatch",
                    "rationale": (
                        f"Dispatch resolved deferred {preference} preference for this {role} step."
                    ),
                }
                reason = new_route["rationale"]
        except Exception:
            reason = (
                f"Deferred {preference} preference could not be resolved at dispatch; "
                "kept the previous route."
            )
    elif preference in {"codex", "cursor", "claude_code"}:
        new_route = {
            **previous,
            "backend": preference,
            "model": str(previous.get("model") or "auto"),
            "complexity": complexity,
            "source": "human_steering",
            "rationale": f"Dispatch confirmed explicit {preference} allocation for this {role} step.",
        }
        reason = new_route["rationale"]
    elif workflow is not None:
        try:
            from distr.core.project_cli_backends.model_policy import apply_auto_step_role_policy

            step = next(
                (
                    item for item in (getattr(workflow, "steps", None) or [])
                    if int(getattr(item, "id", 0) or 0) == int(step_id)
                ),
                None,
            )
            config = _dict(getattr(step, "config", None)) if step is not None else {}
            seeded = dict(previous)
            seeded.setdefault("complexity", complexity)
            role_routes = {
                str(_dict(item).get("role") or "execution"): _dict(item).get("primary_route") or {}
                for item in assignments.values()
                if int(_dict(item).get("step_id") or 0) != int(step_id)
            }
            refreshed = apply_auto_step_role_policy(
                seeded,
                workflow=workflow,
                config=config,
                settings=settings,
                step_role=role,
                prior_role_routes=role_routes,
            )
            refreshed["source"] = "coordination_replan_dispatch"
            constraints = _list(assignment.get("steering_constraints"))
            refreshed["rationale"] = (
                f"Dispatch reallocated this {role} step after plan-aware steering."
                + (f" Latest steer: {constraints[-1][:240]}" if constraints else "")
            )
            new_route = refreshed
            reason = refreshed["rationale"]
        except Exception:
            reason = "Dispatch could not refresh the role policy; cleared the replan marker."

    assignment["primary_route"] = new_route
    assignment["needs_replan"] = False
    assignment["revision"] = int(assignment.get("revision") or 0) + 1
    assignment["status"] = "planned"
    assignments[key] = assignment
    created_at = datetime.now(timezone.utc).isoformat()
    revision = {
        "type": "dispatch_replan",
        "target_step_id": int(step_id),
        "previous_route": previous,
        "new_route": new_route,
        "route_preference": preference,
        "reason": reason,
        "route_changed": _route_identity(previous) != _route_identity(new_route),
        "created_at": created_at,
    }
    updated["assignments"] = assignments
    updated["updated_at"] = created_at
    updated.setdefault("revisions", []).append(revision)
    return updated, revision


def apply_steering_to_plan(
    plan: dict[str, Any],
    *,
    current_step_id: int | None,
    message: str,
    impact: str,
    route_preference: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Revise only the active run overlay in response to human steering."""
    if not isinstance(plan, dict) or not plan.get("assignments"):
        return deepcopy(plan or {}), None
    clean = " ".join(str(message or "").split()).strip()
    if not clean:
        return deepcopy(plan), None

    updated = deepcopy(plan)
    assignments = _dict(updated.get("assignments"))
    ordered = sorted(
        ((_dict(value), str(key)) for key, value in assignments.items()),
        key=lambda item: int(item[0].get("position") or 0),
    )
    current_position = 0
    if current_step_id is not None:
        current = _dict(assignments.get(str(current_step_id)))
        current_position = int(current.get("position") or 0)

    affected: list[int] = []
    explicit_routes = {
        "codex": {"backend": "codex", "model": "auto"},
        "cursor": {"backend": "cursor", "model": "auto"},
        "claude_code": {"backend": "claude_code", "model": "auto"},
    }
    for assignment, key in ordered:
        position = int(assignment.get("position") or 0)
        step_id = int(assignment.get("step_id") or key or 0)
        if position < current_position:
            continue
        constraints = list(assignment.get("steering_constraints") or [])
        if clean not in constraints:
            constraints.append(clean[:1000])
        assignment["steering_constraints"] = constraints[-8:]
        assignment["revision"] = int(assignment.get("revision") or 0) + 1
        if impact in {"plan", "route"} and position > current_position:
            assignment["needs_replan"] = True
        if impact == "route" and route_preference:
            assignment["route_preference"] = route_preference
            # Concrete CLI backends can replace future allocations now.
            # Free/local preferences stay marked needs_replan until
            # consume_step_replan resolves readiness at dispatch.
            if position > current_position and route_preference in explicit_routes:
                previous = _dict(assignment.get("primary_route"))
                assignment["primary_route"] = {
                    **previous,
                    **explicit_routes[route_preference],
                    "source": "human_steering",
                    "rationale": f"The user explicitly requested {route_preference} for the remaining run.",
                }
        assignments[key] = assignment
        affected.append(step_id)

    created_at = datetime.now(timezone.utc).isoformat()
    revision = {
        "type": "human_steering",
        "impact": impact,
        "current_step_id": current_step_id,
        "affected_step_ids": affected,
        "route_preference": route_preference,
        "reason": clean[:1000],
        "created_at": created_at,
    }
    updated["assignments"] = assignments
    updated["updated_at"] = created_at
    updated.setdefault("revisions", []).append(revision)
    return updated, revision
