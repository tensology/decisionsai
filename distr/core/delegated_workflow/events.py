"""Hermes ledger helpers for delegated workflow plans."""

from __future__ import annotations

from .models import DelegatedPlan, DelegatedRunReport


def record_delegated_plan(
    plan: DelegatedPlan,
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
) -> int | None:
    """Record a delegated workflow plan as a redacted Hermes event."""
    from distr.core.orchestrator import emit_event

    payload = plan.to_safe_dict()
    return emit_event(
        source=plan.source_surface or "delegated_workflow",
        event_type="delegated_plan_created",
        status="planned",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        summary=f"Delegated workflow plan created: {plan.kind}.",
        payload=payload,
        evidence={
            "kind": plan.kind,
            "step_count": len(plan.steps),
            "target_backend": plan.target_backend,
            "requires_approval_before": list(plan.requires_approval_before),
        },
    )


def record_delegated_run_report(
    report: DelegatedRunReport,
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
) -> int | None:
    """Record delegated workflow execution progress as a redacted Hermes event."""
    from distr.core.orchestrator import emit_event

    payload = report.to_safe_dict()
    return emit_event(
        source=report.plan.source_surface or "delegated_workflow",
        event_type="delegated_run_report",
        status=report.status,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        summary=f"Delegated workflow {report.status}: {report.plan.kind}.",
        payload=payload,
        evidence={
            "kind": report.plan.kind,
            "completed_steps": list(report.completed_steps),
            "current_step": report.current_step,
            "roadblock_code": report.roadblock.code if report.roadblock else "",
        },
    )


def record_delegated_preflight(
    plan: DelegatedPlan,
    report: dict,
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
) -> int | None:
    """Record delegated preflight readiness as a redacted Hermes event."""
    from distr.core.orchestrator import emit_event, redact_handoff_payload

    payload = redact_handoff_payload(report or {})
    blockers = payload.get("blockers") if isinstance(payload, dict) else []
    return emit_event(
        source=plan.source_surface or "delegated_workflow",
        event_type="delegated_preflight_report",
        status="ready" if bool((report or {}).get("ready")) else "blocked",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        summary=f"Delegated preflight {'ready' if bool((report or {}).get('ready')) else 'blocked'}: {plan.kind}.",
        payload=payload,
        evidence={
            "kind": plan.kind,
            "ready": bool((report or {}).get("ready")),
            "check_count": len((report or {}).get("checks") or []),
            "blocker_count": len((report or {}).get("blockers") or []),
            "blockers": [
                str(item.get("name") or "")
                for item in blockers
                if isinstance(item, dict)
            ],
        },
    )
