"""User-facing roadblock formatting for delegated Hermes runs."""

from __future__ import annotations

from .models import Roadblock
from .models import DelegatedRunReport


def format_telegram_report(roadblock: Roadblock) -> str:
    """Return a concise Telegram-safe blocked-state message."""
    lines = [f"Blocked: {roadblock.title}", roadblock.detail.strip()]
    if roadblock.options:
        lines.append("Options:")
        lines.extend(f"{idx}. {option}" for idx, option in enumerate(roadblock.options, start=1))
    return "\n".join(line for line in lines if line)


def format_run_report_for_telegram(report: DelegatedRunReport, *, run_id: int | None = None) -> str:
    """Return a concise Telegram-safe delegated run status."""
    run_label = f" {run_id}" if run_id is not None else ""
    if report.status == "completed":
        lines = [f"Delegated run{run_label} completed: {report.plan.kind}."]
        if report.completed_steps:
            lines.append("Completed: " + ", ".join(report.completed_steps[-6:]))
        handoff = (report.evidence or {}).get("handoff") if isinstance(report.evidence, dict) else None
        if isinstance(handoff, dict):
            backend = handoff.get("backend_id") or report.plan.target_backend
            output = (handoff.get("output") or "").strip()
            lines.append(f"Handoff: {backend or 'project backend'}")
            if output:
                lines.append(output[:700])
        elif report.evidence:
            summary = str(report.evidence.get("summary") or report.evidence.get("scope") or "") if isinstance(report.evidence, dict) else ""
            if summary:
                lines.append(summary[:700])
        return "\n".join(lines)

    lines = [f"Delegated run{run_label} {report.status}: {report.plan.kind}."]
    if report.current_step:
        lines.append(f"Current step: {report.current_step}")
    if report.completed_steps:
        lines.append("Completed: " + ", ".join(report.completed_steps[-6:]))
    if report.roadblock:
        lines.append(format_telegram_report(report.roadblock))
    return "\n".join(lines)


def build_roadblock_report(code: str, detail: str = "") -> Roadblock:
    """Build common delegated workflow roadblocks with actionable choices."""
    normalized = (code or "").strip().lower()
    if normalized == "gmail_not_connected":
        return Roadblock(
            code="gmail_not_connected",
            title="Gmail is not connected",
            detail=detail or "I cannot search email until a Google account is connected.",
            options=[
                "Connect Gmail in Settings > Advanced.",
                "Use the current browser session as a fallback.",
                "Upload the document directly in Telegram.",
            ],
        )
    if normalized == "password_protected_document":
        return Roadblock(
            code="password_protected_document",
            title="The document is password-protected",
            detail=detail or "I found the document, but cannot extract its contents without the password.",
            options=[
                "Send me the document password.",
                "Upload an unlocked copy.",
                "Summarize only the email body.",
            ],
        )
    if normalized == "backend_not_ready":
        return Roadblock(
            code="backend_not_ready",
            title="The project backend is not ready",
            detail=detail or "I cannot hand this work to Codex or Cursor until the selected backend is installed and authenticated.",
            options=[
                "Open the project backend setup screen.",
                "Use a different ready backend.",
                "Create a workflow ticket and wait for setup.",
            ],
        )
    return Roadblock(
        code=normalized or "delegated_workflow_blocked",
        title="The delegated workflow is blocked",
        detail=detail or "I need more information before I can continue.",
        options=["Clarify the target account, project, or app.", "Cancel this delegated run."],
    )
