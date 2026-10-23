"""Stop plain thread chat from silently relaunching a stopped workflow.

A Development thread bound to a workflow used to start a brand-new run for
any message (even "hello") after the previous run was cancelled. The new run
also dropped the thread's manual model route, so Auto policy picked a free
model. A cancelled/failed run now needs an explicit resume instruction, and the
relaunch inherits the previous run's route metadata.
"""

from __future__ import annotations

import json
import re
from typing import Any

_RESUME_RE = re.compile(
    r"^\s*(?:please\s+)?(?:resume|restart|re-?run|re-?start|retry|relaunch|re-?launch)"
    r"(?:\s+(?:the\s+)?(?:workflow|run|it|this|ticket|work))?\b"
    r"|^\s*(?:please\s+)?(?:start|run|launch)\s+(?:the\s+)?(?:workflow|run|it)\s+again\b"
    r"|^\s*/resume\b",
    re.IGNORECASE,
)

# Run metadata carried into an explicit relaunch so the route stays pinned.
_INHERITED_RUN_KEYS = (
    "model_route",
    "execution_route",
    "routing_assessment",
    "use_playwright",
    "skill_ids",
)

STOPPED_STATUSES = frozenset({"cancelled", "failed"})


def is_explicit_workflow_resume(message: str) -> bool:
    return bool(_RESUME_RE.search(str(message or "")))


def latest_thread_run(chat_id: int, workflow_id: int) -> dict[str, Any] | None:
    """Return ``{"id", "status", "run_data"}`` for the thread's newest run."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflowRun

    with get_session() as db:
        run = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.chat_id == int(chat_id))
            .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowRun.id.desc())
            .first()
        )
        status = str(getattr(run, "status", "") or "").strip().lower() if run is not None else ""
        if run is None or not status or getattr(run, "id", None) is None:
            return None
        try:
            run_data = json.loads(getattr(run, "run_data", None) or "{}") or {}
        except Exception:
            run_data = {}
        return {
            "id": int(run.id),
            "status": status,
            "run_data": run_data if isinstance(run_data, dict) else {},
        }


def relaunch_decision(chat_id: int, workflow_id: int, message: str) -> dict[str, Any]:
    """Decide whether a thread message may start a new workflow run.

    Returns ``{"allow": bool, "notice": str, "inherit": dict}``.
    """
    try:
        latest = latest_thread_run(int(chat_id), int(workflow_id))
    except Exception:
        # The guard must never block a thread message because its lookup failed.
        latest = None
    if latest is None or latest["status"] not in STOPPED_STATUSES:
        return {"allow": True, "notice": "", "inherit": {}, "previous_run_id": None}
    previous = latest["run_data"]
    inherit = {
        key: previous[key]
        for key in _INHERITED_RUN_KEYS
        if key in previous and previous[key] not in (None, "", {}, [])
    }
    if is_explicit_workflow_resume(message):
        return {
            "allow": True,
            "notice": "",
            "inherit": inherit,
            "previous_run_id": latest["id"],
        }
    return {
        "allow": False,
        "notice": (
            f"Workflow run #{latest['id']} is {latest['status']}, so I saved your message without "
            "starting a new run. Reply 'resume' (or 'restart workflow') to start it again "
            "with the same route."
        ),
        "inherit": inherit,
        "previous_run_id": latest["id"],
    }
