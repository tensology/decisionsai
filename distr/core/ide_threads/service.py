"""Dispatch unified IDE thread operations across Codex and Cursor."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from distr.core.agent.tool_voice_format import voice_then_reference

from . import codex_adapter, cursor_adapter

logger = logging.getLogger(__name__)

IdeSurface = Literal["codex", "cursor", "auto"]
IdeAction = Literal["list", "read", "status", "prompt", "amend"]


def _normalize_surface(surface: str) -> IdeSurface:
    value = (surface or "auto").strip().lower()
    if value in ("codex", "cursor"):
        return value  # type: ignore[return-value]
    return "auto"


def _resolve_surface(surface: IdeSurface, *, project: str = "", query: str = "") -> str:
    if surface != "auto":
        return surface
    text = f"{project} {query}".lower()
    if "codex" in text or "codecs" in text:
        return "codex"
    if "cursor" in text:
        return "cursor"
    return "codex"


def _resolve_project(project: str = "", project_id: int | None = None, cwd: str = "") -> tuple[dict[str, Any] | None, str]:
    from distr.core.ide_bridge import find_project_for_folder

    project_row = find_project_for_folder(cwd, project_id=project_id)
    if project_row:
        return project_row, str(project_row.get("folder_location") or cwd or "")

    hint = (project or "").strip()
    if not hint:
        return None, cwd

    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as session:
            row = session.query(Project).filter(Project.name.ilike(f"%{hint}%")).first()
            if row:
                folder = str(row.folder_location or cwd or "")
                return {
                    "id": row.id,
                    "name": row.name,
                    "folder_location": folder,
                }, folder
    except Exception:
        logger.debug("project resolve failed", exc_info=True)
    return None, cwd


def ide_thread_action(
    *,
    action: str,
    surface: str = "auto",
    instruction: str = "",
    amendment: str = "",
    thread_id: str = "",
    session_id: int | None = None,
    query: str = "",
    project: str = "",
    project_id: int | None = None,
    cwd: str = "",
    limit: int = 12,
    limit_messages: int = 12,
    model: str = "",
    new_thread: bool = False,
) -> dict[str, Any]:
    act = (action or "").strip().lower()
    if act not in ("list", "read", "status", "prompt", "amend"):
        return {"success": False, "error": f"Unknown action: {action}"}

    resolved_surface = _resolve_surface(_normalize_surface(surface), project=project, query=query)
    project_row, folder = _resolve_project(project=project, project_id=project_id, cwd=cwd)

    if act == "list":
        if resolved_surface == "cursor":
            threads = cursor_adapter.list_threads(
                project=project,
                project_id=project_id or (int(project_row["id"]) if project_row else None),
                cwd=folder or cwd,
                limit=limit,
            )
        else:
            threads = codex_adapter.list_threads(project=project, limit=limit)
        return {"success": True, "action": act, "surface": resolved_surface, "threads": threads}

    if act == "read":
        if resolved_surface == "cursor":
            data = cursor_adapter.read_thread(
                session_id=session_id,
                thread_id=thread_id,
                query=query,
                project=project,
                cwd=folder or cwd,
                project_id=project_id or (int(project_row["id"]) if project_row else None),
                limit_events=limit_messages,
            )
        else:
            data = codex_adapter.read_thread(
                thread_id=thread_id,
                query=query,
                project=project,
                limit_messages=limit_messages,
            )
        return {"success": bool(data.get("found", True)), "action": act, "surface": resolved_surface, **data}

    if act == "status":
        if resolved_surface == "cursor":
            data = cursor_adapter.thread_status(
                session_id=session_id,
                thread_id=thread_id,
                query=query,
                project=project,
                cwd=folder or cwd,
                project_id=project_id or (int(project_row["id"]) if project_row else None),
            )
        else:
            data = codex_adapter.thread_status(thread_id=thread_id, query=query, project=project)
        return {"success": bool(data.get("found", True)), "action": act, **data}

    if act == "prompt":
        text = (instruction or "").strip()
        if not text:
            return {"success": False, "error": "instruction is required for prompt"}
        if resolved_surface == "cursor":
            result = cursor_adapter.prompt_thread(
                instruction=text,
                folder=folder or cwd,
                thread_id=thread_id,
                model=model,
                resume=not new_thread,
                continue_latest=not new_thread and not thread_id,
            )
        else:
            result = codex_adapter.prompt_thread(
                instruction=text,
                folder=folder or cwd,
                thread_id=thread_id,
                model=model,
                resume=not new_thread,
            )
        _record_prompt_session(
            surface=resolved_surface,
            folder=folder or cwd,
            project_row=project_row,
            instruction=text,
            thread_id=thread_id,
            result=result,
        )
        return {"success": bool(result.get("success")), "action": act, **result}

    # amend
    text = (amendment or instruction or "").strip()
    if not text:
        return {"success": False, "error": "amendment is required for amend"}
    if resolved_surface == "cursor":
        result = cursor_adapter.amend_thread(
            amendment=text,
            thread_id=thread_id,
            folder=folder or cwd,
            session_id=session_id,
            model=model,
        )
    else:
        result = codex_adapter.amend_thread(
            amendment=text,
            thread_id=thread_id,
            folder=folder or cwd,
            model=model,
        )
    _record_prompt_session(
        surface=resolved_surface,
        folder=folder or cwd,
        project_row=project_row,
        instruction=text,
        thread_id=thread_id,
        result=result,
        event_type="user_steer",
    )
    return {"success": bool(result.get("success")), "action": act, **result}


def _record_prompt_session(
    *,
    surface: str,
    folder: str,
    project_row: dict[str, Any] | None,
    instruction: str,
    thread_id: str,
    result: dict[str, Any],
    event_type: str = "prompt_submitted",
) -> None:
    if not folder and not project_row:
        return
    try:
        from distr.core.ide_bridge import record_ide_event

        status = "running" if result.get("success") else "failed"
        output_preview = str(result.get("output_preview") or "")[:500]
        record_ide_event(
            source=surface,
            cwd=folder,
            project_id=int(project_row["id"]) if project_row and project_row.get("id") else None,
            event_type=f"{surface}_{event_type}",
            status=status,
            message=instruction[:500],
            input_text=instruction,
            output_text=output_preview,
            payload={
                "thread_id": thread_id or result.get("thread_id") or "",
                "external_thread_id": thread_id or result.get("thread_id") or "",
                "orchestrator_prompt": True,
            },
        )
    except Exception:
        logger.debug("failed to record orchestrator IDE prompt", exc_info=True)


def format_ide_thread_result(result: dict[str, Any]) -> str:
    """Format tool output for voice-first agent consumption."""
    if not isinstance(result, dict):
        return voice_then_reference("I could not read the IDE thread state.", str(result))

    if not result.get("success", True) and result.get("error"):
        return voice_then_reference(f"That IDE request did not work. {result['error']}", json.dumps(result, indent=2)[:6000])

    action = str(result.get("action") or "")
    surface = str(result.get("surface") or "ide")

    if action == "list":
        threads = result.get("threads") if isinstance(result.get("threads"), list) else []
        if not threads:
            return voice_then_reference(
                f"I did not find any recent {surface} threads for that project.",
                json.dumps(result, indent=2)[:6000],
            )
        titles = [str(t.get("title") or t.get("project") or "untitled")[:80] for t in threads[:5]]
        voice = f"I found {len(threads)} recent {surface} thread(s), including {', '.join(titles)}."
        return voice_then_reference(voice, json.dumps(result, indent=2)[:6000])

    if action == "read":
        messages = result.get("messages") if isinstance(result.get("messages"), list) else []
        if not result.get("found") or not messages:
            reason = result.get("reason") or result.get("warning") or "No transcript was available."
            return voice_then_reference(f"I could not load that {surface} thread. {reason}", json.dumps(result, indent=2)[:6000])
        last = messages[-1] if isinstance(messages[-1], dict) else {}
        preview = str(last.get("content") or last.get("text") or "")[:220]
        voice = f"I loaded the {surface} thread. The latest message is: {preview}"
        return voice_then_reference(voice, json.dumps(result, indent=2)[:7000])

    if action == "status":
        if not result.get("found"):
            return voice_then_reference(
                str(result.get("reason") or f"No matching {surface} session was found."),
                json.dumps(result, indent=2)[:6000],
            )
        status = result.get("status") or result.get("phase") or "unknown"
        preview = result.get("last_message_preview") or ""
        voice = f"The {surface} session looks {status}."
        if preview:
            voice += f" Latest update: {preview[:180]}"
        return voice_then_reference(voice, json.dumps(result, indent=2)[:6000])

    if action in ("prompt", "amend"):
        if result.get("success"):
            voice = f"I sent that to {surface}."
            preview = str(result.get("output_preview") or "")[:220]
            if preview:
                voice += f" It replied: {preview}"
        else:
            voice = f"I could not send that to {surface}. {result.get('error') or 'The CLI returned an error.'}"
        return voice_then_reference(voice, json.dumps(result, indent=2)[:6000])

    return voice_then_reference("Done.", json.dumps(result, indent=2)[:6000])
