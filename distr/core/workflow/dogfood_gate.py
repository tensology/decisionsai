"""Dogfood workflow exit gate and failure follow-up tickets."""

from __future__ import annotations

import json
import logging
from typing import Any

from distr.core.workflow.step_iteration import HARNESS_REPORT_FIELDS

logger = logging.getLogger(__name__)

DOGFOOD_PRESET_SLUG = "decisionsai-dogfood-ticket"


def is_dogfood_workflow(workflow_id: int | None) -> bool:
    if not workflow_id:
        return False
    try:
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflow

        with get_session() as session:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
            if not wf:
                return False
            wf_input: dict[str, Any] = {}
            try:
                wf_input = json.loads(wf.workflow_input or "{}") or {}
            except Exception:
                wf_input = {}
            slug = str(wf_input.get("preset_slug") or wf_input.get("slug") or "").strip().lower()
            if slug in (
                "dogfood-e2e-smoke",
                "dogfood-spawn-e2e",
                "spotify-e2e-ideation",
                "spotify-e2e-dev",
                "spotify-e2e-polish",
            ) or wf_input.get("e2e_smoke"):
                return False
            if slug == DOGFOOD_PRESET_SLUG:
                return True
            blob = f"{wf.description or ''} {wf.workflow_input or ''}".lower()
            return DOGFOOD_PRESET_SLUG in blob
    except Exception:
        return False


def harness_report_complete(packet: dict[str, Any]) -> list[str]:
    """Return missing harness return-contract fields."""
    report = packet.get("harness_report") or packet.get("iteration_report") or {}
    if isinstance(report, str):
        missing = []
        lower = report.lower()
        for field in HARNESS_REPORT_FIELDS:
            if field.replace("_", " ") not in lower and field not in lower:
                missing.append(field)
        return missing
    if not isinstance(report, dict):
        return list(HARNESS_REPORT_FIELDS)
    missing = []
    for field in HARNESS_REPORT_FIELDS:
        if not str(report.get(field) or "").strip():
            missing.append(field)
    return missing


def enforce_dogfood_exit_gate(
    *,
    packet: dict[str, Any],
    run_status: str,
    workflow_id: int | None,
) -> tuple[str, dict[str, Any], list[str]]:
    """Require harness report + screenshot evidence for dogfood workflows."""
    updated = dict(packet or {})
    if run_status != "completed" or not is_dogfood_workflow(workflow_id):
        return run_status, updated, []

    missing: list[str] = []
    missing.extend(f"harness_{f}" for f in harness_report_complete(updated))

    artifacts = updated.get("artifacts") or {}
    screenshots = list(artifacts.get("screenshots") or [])
    if not screenshots:
        ui = updated.get("ui_quality") or {}
        if ui.get("after_screenshot"):
            screenshots = [ui["after_screenshot"]]
    if not screenshots:
        missing.append("playwright_screenshots")

    if missing:
        audit = dict(updated.get("audit") or {})
        audits = list(audit.get("audits_run") or [])
        audits.append({
            "gate": "dogfood",
            "name": "dogfood_exit_gate",
            "outcome": "needs_changes",
            "rationale": f"Missing dogfood completion evidence: {', '.join(missing)}",
        })
        audit["final_verdict"] = "needs_changes"
        updated["audit"] = audit
        updated["status"] = "partial_success"
        return "failed", updated, missing

    return run_status, updated, []


def create_playwright_failure_followup_ticket(
    *,
    run_id: int,
    workflow_id: int | None,
    ticket_id: int | None,
    board_id: int | None,
    evidence: str,
) -> int | None:
    """Open a follow-up ticket when Playwright verification fails."""
    if not board_id and not ticket_id:
        return None
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanLane, KanbanTicket

        with get_session() as session:
            lane_id = None
            if ticket_id:
                parent = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
                if parent:
                    lane_id = parent.lane_id
            if not lane_id and board_id:
                lane = (
                    session.query(KanbanLane)
                    .filter(KanbanLane.board_id == int(board_id))
                    .order_by(KanbanLane.position.asc())
                    .first()
                )
                lane_id = lane.id if lane else None
            if not lane_id:
                return None

            title = f"Fix Playwright verification — run #{run_id}"
            body = (
                f"Automated follow-up from workflow run #{run_id} "
                f"(workflow_id={workflow_id or 'unknown'}).\n\n"
                f"## Evidence\n{evidence[:4000]}\n\n"
                "## Next steps\n"
                "- Read failure output and screenshots in Runs → Executor\n"
                "- Fix the failing journey and re-run the dogfood workflow\n"
            )
            row = KanbanTicket(
                lane_id=lane_id,
                title=title[:500],
                description=body,
                priority="high",
                parent_ticket_id=int(ticket_id) if ticket_id else None,
                linked_workflow_id=int(workflow_id) if workflow_id else None,
            )
            session.add(row)
            session.commit()
            return int(row.id)
    except Exception:
        logger.debug("create_playwright_failure_followup_ticket failed", exc_info=True)
        return None
