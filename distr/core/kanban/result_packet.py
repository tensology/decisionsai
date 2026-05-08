"""Canonical Result Packet builders for ticket execution lanes.

Both workflow (Cursor-style) and CLI (Pi) paths map into this shared schema so
board automation can consume one shape.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_result_packet(
    *,
    ticket_id: str,
    board_id: Optional[str] = None,
    board_name: Optional[str] = None,
    project_id: Optional[str] = None,
    project_name: Optional[str] = None,
    execution_lane: str,
    status: str,
    summary: str,
    files_changed: Optional[List[str]] = None,
    change_summary: Optional[List[str]] = None,
    commands_run: Optional[List[str]] = None,
    commands_suggested: Optional[List[str]] = None,
    tests_run: Optional[List[str]] = None,
    test_results: Optional[List[Dict[str, Any]]] = None,
    risks: Optional[List[str]] = None,
    assumptions: Optional[List[str]] = None,
    limitations: Optional[List[str]] = None,
    next_recommended: Optional[List[str]] = None,
    needs_human_review: bool = False,
    needs_human_review_reason: str = "",
    logs: Optional[List[str]] = None,
    screenshots: Optional[List[str]] = None,
    diffs_or_patches: Optional[List[str]] = None,
    links: Optional[List[str]] = None,
    audits_run: Optional[List[Dict[str, Any]]] = None,
    final_verdict: str = "cannot_determine",
    audit_rationale: str = "",
) -> Dict[str, Any]:
    """Return canonical Result Packet dict with required sections."""
    return {
        "ticket_id": str(ticket_id),
        "board_id": board_id,
        "board_name": board_name,
        "project_id": project_id,
        "project_name": project_name,
        "execution_lane": execution_lane,
        "status": status,
        "summary": (summary or "").strip(),
        "changes": {
            "files_changed": files_changed or [],
            "change_summary": change_summary or [],
        },
        "commands": {
            "commands_run": commands_run or [],
            "commands_suggested": commands_suggested or [],
        },
        "tests_and_checks": {
            "tests_run": tests_run or [],
            "results": test_results or [],
        },
        "risks_and_notes": {
            "risks": risks or [],
            "assumptions": assumptions or [],
            "limitations": limitations or [],
        },
        "next_actions": {
            "recommended": next_recommended or [],
            "needs_human_review": {
                "value": bool(needs_human_review),
                "reason": (needs_human_review_reason or "").strip(),
            },
        },
        "artifacts": {
            "logs": logs or [],
            "screenshots": screenshots or [],
            "diffs_or_patches": diffs_or_patches or [],
            "links": links or [],
        },
        "audit": {
            "audits_run": audits_run or [],
            "final_verdict": final_verdict,
            "rationale": (audit_rationale or "").strip(),
        },
    }


def format_result_packet_note(packet: Dict[str, Any], *, title: str) -> str:
    """Format a concise ticket-friendly block from a Result Packet."""
    summary = (packet.get("summary") or "").strip()
    status = (packet.get("status") or "unknown").strip()
    lane = (packet.get("execution_lane") or "unknown").strip()
    verdict = ((packet.get("audit") or {}).get("final_verdict") or "cannot_determine").strip()
    lines = [
        f"[{title}] Status: {status}",
        f"Execution lane: {lane}",
        f"Audit verdict: {verdict}",
    ]
    if summary:
        lines.append(summary)

    change_summary = ((packet.get("changes") or {}).get("change_summary") or [])[:5]
    if change_summary:
        lines.append("Change summary:")
        for item in change_summary:
            lines.append(f"- {item}")

    next_actions = ((packet.get("next_actions") or {}).get("recommended") or [])[:3]
    if next_actions:
        lines.append("Next actions:")
        for action in next_actions:
            lines.append(f"- {action}")

    return "\n".join(lines).strip()


def create_initial_result_packet_for_run(
    *,
    ticket_id: Optional[Any],
    board_id: Optional[Any],
    board_name: Optional[str],
    project_id: Optional[Any],
    project_name: Optional[str],
    execution_lane: str = "cursor",
) -> Dict[str, Any]:
    """Create the initial packet stored in workflow run_data."""
    return build_result_packet(
        ticket_id=str(ticket_id or ""),
        board_id=str(board_id) if board_id is not None else None,
        board_name=board_name,
        project_id=str(project_id) if project_id is not None else None,
        project_name=project_name,
        execution_lane=execution_lane,
        status="running",
        summary="Workflow run started.",
        final_verdict="cannot_determine",
        audit_rationale="Run started; awaiting step outcomes.",
    )


def append_workflow_step_to_packet(
    packet: Dict[str, Any],
    *,
    step_name: str,
    step_status: str,
    step_result: str,
    run_status: str,
) -> Dict[str, Any]:
    """Update packet with a single completed step outcome."""
    updated = dict(packet or {})
    changes = dict(updated.get("changes") or {})
    change_summary = list(changes.get("change_summary") or [])

    trimmed_result = (step_result or "").strip()
    snippet = trimmed_result[:220] + ("..." if len(trimmed_result) > 220 else "")
    line = f"{step_name or 'Step'}: {step_status}" + (f" ({snippet})" if snippet else "")
    change_summary.append(line)

    # Keep packet compact for run_data persistence.
    if len(change_summary) > 40:
        change_summary = change_summary[-40:]
    changes["change_summary"] = change_summary
    updated["changes"] = changes

    updated["status"] = run_status or updated.get("status") or "running"
    updated["summary"] = f"{len(change_summary)} step result(s) recorded in this run."
    updated["audit"] = dict(updated.get("audit") or {})
    if run_status in ("completed", "success"):
        updated["audit"]["final_verdict"] = "pass"
        updated["audit"]["rationale"] = "Workflow completed successfully."
    elif run_status in ("failed", "cancelled"):
        updated["audit"]["final_verdict"] = "needs_changes"
        updated["audit"]["rationale"] = f"Workflow ended with status: {run_status}."
    else:
        updated["audit"]["final_verdict"] = updated["audit"].get("final_verdict", "cannot_determine")
    return updated


def summarize_packet_for_step_context(packet: Dict[str, Any], *, max_lines: int = 8) -> str:
    """Compact packet digest for step prompts and CLI handoffs."""
    if not packet:
        return ""
    status = packet.get("status") or "unknown"
    verdict = ((packet.get("audit") or {}).get("final_verdict") or "cannot_determine")
    summary = (packet.get("summary") or "").strip()
    lines = [
        "[RESULT PACKET CONTEXT]",
        f"status: {status}",
        f"audit_verdict: {verdict}",
    ]
    if summary:
        lines.append(f"summary: {summary}")
    for item in ((packet.get("changes") or {}).get("change_summary") or [])[-max_lines:]:
        lines.append(f"- {item}")
    return "\n".join(lines).strip()
