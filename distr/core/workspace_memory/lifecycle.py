"""Workspace lifecycle hooks — ensure + automatic handoff triggers."""

from __future__ import annotations

import logging
import shutil
from typing import Any

logger = logging.getLogger(__name__)


def hook_remove_workspace(entity_type: str, entity_id: int | str) -> bool:
    """Remove one deleted entity's companion memory without touching repo files."""
    try:
        from .paths import companion_root, workspaces_root

        root = companion_root(entity_type, entity_id)  # type: ignore[arg-type]
        boundary = workspaces_root().expanduser().resolve()
        resolved = root.expanduser().resolve()
        if resolved == boundary or boundary not in resolved.parents:
            raise ValueError(f"Unsafe workspace removal path: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)
        return True
    except Exception:
        logger.warning(
            "hook_remove_workspace failed %s/%s",
            entity_type,
            entity_id,
            exc_info=True,
        )
        return False


def hook_ensure_workspace(
    entity_type: str,
    entity_id: int | str,
    *,
    force: bool = True,
    reason: str = "",
    run_kwargs: dict[str, Any] | None = None,
) -> str | None:
    """Safe wrapper for mutation/read paths."""
    try:
        from .provision import ensure_workspace

        return ensure_workspace(
            entity_type,
            entity_id,
            force=force,
            reason=reason,
            run_kwargs=run_kwargs,
        )
    except Exception:
        logger.debug("hook_ensure_workspace failed %s/%s", entity_type, entity_id, exc_info=True)
        return None


def handoff_workflow_step(
    *,
    run_id: int,
    ticket_id: int | None = None,
    project_id: int | None = None,
    step_name: str = "",
    summary: str = "",
    status: str = "",
) -> None:
    """Persist run/ticket continuity after a workflow step completes."""
    from .pickup_handoff import perform_handoff

    body = "\n".join(
        part
        for part in (
            f"Step: {step_name}".strip() if step_name else "",
            f"Status: {status}".strip() if status else "",
            (summary or "").strip(),
        )
        if part
    ).strip() or "Workflow step completed."
    try:
        perform_handoff(
            "runs",
            run_id,
            summary=body,
            source="workflow_step",
            extra={"ticket_id": ticket_id, "project_id": project_id},
        )
    except Exception:
        logger.debug("handoff_workflow_step: run handoff failed", exc_info=True)
    if ticket_id:
        try:
            perform_handoff(
                "tickets",
                int(ticket_id),
                summary=body,
                source="workflow_step",
                extra={"run_id": run_id, "project_id": project_id},
            )
        except Exception:
            logger.debug("handoff_workflow_step: ticket handoff failed", exc_info=True)


def handoff_cli_session(
    *,
    ticket_id: int | None = None,
    project_id: int | None = None,
    summary: str = "",
    source: str = "cli_completed",
) -> None:
    """Persist continuity when an external CLI/IDE session ends."""
    from .pickup_handoff import perform_handoff

    body = (summary or "").strip() or "CLI session completed."
    if ticket_id:
        try:
            perform_handoff(
                "tickets",
                int(ticket_id),
                summary=body,
                source=source,
                extra={"project_id": project_id},
            )
        except Exception:
            logger.debug("handoff_cli_session: ticket failed", exc_info=True)
    if project_id:
        try:
            perform_handoff(
                "projects",
                int(project_id),
                summary=body,
                source=source,
                extra={"ticket_id": ticket_id},
            )
        except Exception:
            logger.debug("handoff_cli_session: project failed", exc_info=True)


def handoff_ticket_lane_done(
    *,
    ticket_id: int,
    lane_name: str = "",
    summary: str = "",
) -> None:
    """Record ticket closure when moved to a done lane."""
    from .pickup_handoff import perform_handoff

    body = (summary or "").strip() or f"Ticket moved to {lane_name or 'done'}."
    try:
        perform_handoff(
            "tickets",
            int(ticket_id),
            summary=body,
            source="lane_done",
            extra={"lane_name": lane_name},
        )
    except Exception:
        logger.debug("handoff_ticket_lane_done failed", exc_info=True)
