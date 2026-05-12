"""Canonical Result Packet builders for ticket execution lanes.

Both workflow (Cursor-style) and CLI (Pi) paths map into this shared schema so
board automation can consume one shape.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
_PATH_RE = re.compile(
    r"(?P<path>(?:/[\w .@+-]+)+/[\w .@+-]+\.(?:png|jpe?g|webp|gif|log|txt|json|diff|patch|md|html|mp4|mov|webm))\b",
    re.IGNORECASE,
)
_REL_PATH_RE = re.compile(
    r"\b(?P<path>[\w.@+-]+(?:/[\w .@+-]+)+\.(?:png|jpe?g|webp|gif|log|txt|json|diff|patch|md|html|mp4|mov|webm))\b",
    re.IGNORECASE,
)
_CU_ACTION_RE = re.compile(
    r"^\s*(?P<step>\d+|ESC)\.\s*\[(?P<action_type>[^\]]+)\]\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


def _append_unique(items: List[str], value: str, *, limit: int = 40) -> None:
    value = (value or "").strip().strip("`\"'.,")
    if not value or value in items:
        return
    if not value.startswith("/") and f"/{value}" in items:
        return
    items.append(value)
    if len(items) > limit:
        del items[: len(items) - limit]


def _merge_unique_artifacts(existing: Dict[str, Any], found: Dict[str, List[str]]) -> Dict[str, List[str]]:
    artifacts = {
        "logs": list(existing.get("logs") or []),
        "screenshots": list(existing.get("screenshots") or []),
        "diffs_or_patches": list(existing.get("diffs_or_patches") or []),
        "links": list(existing.get("links") or []),
    }
    for key, values in found.items():
        bucket = artifacts.setdefault(key, [])
        for value in values:
            _append_unique(bucket, value)
    return artifacts


def _merge_action_trace(existing: List[Dict[str, Any]], found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    trace = list(existing or [])
    seen = {
        (
            str(item.get("step", "")),
            str(item.get("action_type", "")),
            str(item.get("description", "")),
            str(item.get("result", "")),
        )
        for item in trace
    }
    for item in found:
        key = (
            str(item.get("step", "")),
            str(item.get("action_type", "")),
            str(item.get("description", "")),
            str(item.get("result", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        trace.append(item)
    if len(trace) > 80:
        trace = trace[-80:]
    return trace


def extract_artifacts_from_step_result(step_result: str) -> Dict[str, List[str]]:
    """Classify evidence references from workflow step output into packet artifacts."""
    text = step_result or ""
    found: Dict[str, List[str]] = {
        "logs": [],
        "screenshots": [],
        "diffs_or_patches": [],
        "links": [],
    }

    for match in _URL_RE.finditer(text):
        _append_unique(found["links"], match.group(0))

    for regex in (_PATH_RE, _REL_PATH_RE):
        for match in regex.finditer(text):
            path = match.group("path").strip()
            lowered = path.lower()
            if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm")):
                _append_unique(found["screenshots"], path)
            elif lowered.endswith((".log", ".txt", ".json", ".html", ".md")):
                _append_unique(found["logs"], path)
            elif lowered.endswith((".diff", ".patch")):
                _append_unique(found["diffs_or_patches"], path)

    return {key: values for key, values in found.items() if values}


def extract_action_trace_from_step_result(step_result: str) -> List[Dict[str, Any]]:
    """Parse computer-use style step summaries into structured action trace rows."""
    text = step_result or ""
    trace: List[Dict[str, Any]] = []
    for line in text.splitlines():
        match = _CU_ACTION_RE.match(line)
        if not match:
            continue
        rest = (match.group("rest") or "").strip()
        description = rest
        result = ""
        for separator in (" -> ", " → "):
            if separator in rest:
                description, result = rest.split(separator, 1)
                break
        trace.append(
            {
                "step": match.group("step"),
                "action_type": match.group("action_type").strip().lower(),
                "description": description.strip(),
                "result": result.strip(),
            }
        )
    return trace


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
    action_trace: Optional[List[Dict[str, Any]]] = None,
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
        "execution": {
            "action_trace": action_trace or [],
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
    updated["artifacts"] = _merge_unique_artifacts(
        dict(updated.get("artifacts") or {}),
        extract_artifacts_from_step_result(trimmed_result),
    )
    execution = dict(updated.get("execution") or {})
    execution["action_trace"] = _merge_action_trace(
        list(execution.get("action_trace") or []),
        extract_action_trace_from_step_result(trimmed_result),
    )
    updated["execution"] = execution

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
    artifacts = packet.get("artifacts") or {}
    artifact_lines: List[str] = []
    for label, key in (
        ("screenshot", "screenshots"),
        ("log", "logs"),
        ("patch", "diffs_or_patches"),
        ("link", "links"),
    ):
        for value in (artifacts.get(key) or [])[-2:]:
            artifact_lines.append(f"{label}: {value}")
    if artifact_lines:
        lines.append("artifacts:")
        lines.extend(f"- {item}" for item in artifact_lines[:6])
    action_trace = ((packet.get("execution") or {}).get("action_trace") or [])[-max_lines:]
    if action_trace:
        lines.append("recent_actions:")
        for item in action_trace:
            action_type = item.get("action_type") or "action"
            desc = (item.get("description") or "").strip()
            result = (item.get("result") or "").strip()
            line = f"{action_type}: {desc}" if desc else str(action_type)
            if result:
                line += f" -> {result}"
            lines.append(f"- {line}")
    return "\n".join(lines).strip()
