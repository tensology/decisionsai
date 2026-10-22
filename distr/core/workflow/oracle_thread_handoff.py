"""Oracle chat -> Development thread hand-off (bug 2026-10-22 17:15 SAST).

The Oracle could only *create* an empty Development thread (development_control
create_thread stored a starting question but never dispatched a run) and had no
way to continue an existing thread, so a request like "put this in the Decisions
thread" fell through to new_chat. This module mirrors the composer endpoint
POST /workflows/studio/tasks/{id}/messages (gui/web/routes/development/threads.py:395)
so the run lands in the target thread with a user turn, a running execution and
a workflow_updated push (Development list/timer pick it up on the next refresh).
"""

from __future__ import annotations

import re
from typing import Any, Callable

_DEV_THREAD_RE = re.compile(
    r"\b(?:development|dev|decisions(?:\s*ai)?|project)\b[^.?!]{0,60}\bthread\b"
    r"|\bthread\b[^.?!]{0,60}\b(?:development|project|decisions(?:\s*ai)?)\b",
    re.IGNORECASE,
)


def mentions_development_thread(text: str) -> bool:
    """True when the user is talking about a Development thread, not a chat."""
    return bool(_DEV_THREAD_RE.search(str(text or "")))


def _notify() -> None:
    try:
        from distr.gui.web.workflow_events import increment_workflow_updated

        increment_workflow_updated()
    except Exception:
        pass


def send_instruction_to_thread(
    thread_id: int,
    instruction: str,
    *,
    _dispatch: Callable[..., Any] | None = None,
    _dispatch_work: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    from distr.core.chat import ChatService, is_duplicate_pending_input
    from distr.core.chat_turns import latest_active_turn_id
    from distr.core.db import Chat, get_session
    from distr.core.workflow.development_threads import development_thread_metadata

    message = str(instruction or "").strip()
    if not message:
        return {"started": False, "error": "An instruction is required."}
    with get_session() as db:
        chat = db.get(Chat, int(thread_id))
        if chat is None or chat.parent_id is not None:
            return {"started": False, "error": f"Development thread {thread_id} not found."}
        metadata = development_thread_metadata(chat)
        if not metadata:
            return {"started": False, "error": f"Chat {thread_id} is not a Development thread."}
        if metadata.get("source_type") == "plan_workspace":
            return {"started": False, "error": "Plan threads cannot run development work."}
        autonomy = str(chat.autonomy_level or "full").strip().lower()
        project_id = int(chat.project_id) if chat.project_id is not None else None
    if is_duplicate_pending_input(int(thread_id), message):
        return {"started": False, "duplicate": True, "thread_id": int(thread_id)}
    ChatService.add_user_message(int(thread_id), message)
    turn_id = latest_active_turn_id(int(thread_id))
    if autonomy == "approval":
        _notify()
        return {"started": False, "thread_id": int(thread_id), "turn_id": turn_id,
                "message": "Instruction saved in the thread and waiting for approval."}
    workflow_id = int(metadata.get("workflow_id") or 0) or None
    if workflow_id is not None:
        if _dispatch_work is None:
            from distr.core.workflow.work_dispatch import dispatch_work_item as _dispatch_work
        execution = _dispatch_work(workflow_id=workflow_id, chat_id=int(thread_id), context=message,
                                   project_id=project_id, ticket_id=metadata.get("ticket_id"),
                                   source_type="orchestrator", run_metadata={}, dispatch_async=True)
    else:
        if _dispatch is None:
            from distr.core.workflow.development_harness import dispatch_development_prompt as _dispatch
        execution = _dispatch(int(thread_id), message, dispatch_async=True)
    _notify()
    if isinstance(execution, dict) and execution.get("error"):
        return {"started": False, "thread_id": int(thread_id), "error": str(execution["error"])}
    return {"started": True, "thread_id": int(thread_id), "turn_id": turn_id, "project_id": project_id,
            "job_id": (execution or {}).get("job_id") if isinstance(execution, dict) else None,
            "message": f"Running in Development thread {thread_id}."}
