"""Cursor thread list/read/status/prompt via IDE bridge + CLI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def _cursor_executable() -> str | None:
    import shutil

    return shutil.which("cursor-agent")


def _cursor_api_key() -> str:
    env_key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if env_key:
        return env_key
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db() or {}
        return (settings.get("cursor_key") or "").strip()
    except Exception:
        return ""


def list_threads(
    *,
    project: str = "",
    project_id: int | None = None,
    cwd: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    from distr.core.external_agent_context import list_cursor_threads, list_cursor_workspaces, _project_name_from_path
    from distr.core.ide_bridge import list_ide_sessions

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    folder_hint = (cwd or "").strip()
    for row in list_cursor_threads(folder=folder_hint, limit=limit):
        tid = str(row.get("id") or "")
        key = f"transcript:{tid}"
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "surface": "cursor",
                "thread_id": tid,
                "session_id": None,
                "title": row.get("title") or row.get("preview") or f"Cursor chat {tid[:8]}",
                "project": _project_name_from_path(row.get("folder") or ""),
                "folder": row.get("folder") or "",
                "updated_at": row.get("updated_at") or "",
                "status": "transcript",
                "source": "cursor_transcript",
            }
        )

    for session in list_ide_sessions(source="cursor", project_id=project_id, limit=limit):
        tid = str(session.get("external_thread_id") or session.get("id") or "")
        key = f"session:{session.get('id')}"
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "surface": "cursor",
                "thread_id": tid,
                "session_id": session.get("id"),
                "title": session.get("title") or f"Cursor session #{session.get('id')}",
                "project": session.get("project_name") or "",
                "folder": session.get("folder") or "",
                "updated_at": session.get("updated_at") or "",
                "status": session.get("status") or "",
                "source": "decisions_ide_bridge",
            }
        )

    project_hint = (project or "").strip().lower()
    for workspace in list_cursor_workspaces(limit=limit):
        folder = str(workspace.get("folder") or "")
        name = _project_name_from_path(folder)
        if project_hint and project_hint not in name.lower() and project_hint not in folder.lower():
            continue
        key = f"folder:{folder}"
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "surface": "cursor",
                "thread_id": workspace.get("storage_id") or "",
                "session_id": None,
                "title": name or folder,
                "project": name,
                "folder": folder,
                "updated_at": workspace.get("updated_at") or "",
                "status": "workspace_open",
                "source": "cursor_workspace",
            }
        )

    if cwd:
        cwd_items = [item for item in items if cwd.rstrip("/") in str(item.get("folder") or "").rstrip("/")]
        if cwd_items:
            items = cwd_items + [item for item in items if item not in cwd_items]

    return items[: max(1, min(int(limit or 12), 30))]


def read_thread(
    *,
    session_id: int | None = None,
    thread_id: str = "",
    query: str = "",
    project: str = "",
    cwd: str = "",
    project_id: int | None = None,
    limit_events: int = 20,
) -> dict[str, Any]:
    from distr.core.external_agent_context import build_cursor_thread_context
    from distr.core.ide_bridge import get_ide_progress

    local = build_cursor_thread_context(
        thread_id=thread_id,
        query=query,
        project=project,
        folder=cwd,
        limit_messages=max(1, int(limit_events or 20)),
    )

    bridge_messages: list[dict[str, str]] = []
    session: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    progress: dict[str, Any] = {}
    try:
        progress = get_ide_progress(source="cursor", cwd=cwd, project_id=project_id, session_id=session_id)
        session = progress.get("session") if isinstance(progress.get("session"), dict) else None
        if session:
            events = session.get("events") if isinstance(session.get("events"), list) else []
            for event in events[-max(1, int(limit_events or 20)) :]:
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                input_text = str(payload.get("input") or "").strip()
                output_text = str(payload.get("output") or "").strip()
                message = str(event.get("message") or "").strip()
                if input_text:
                    bridge_messages.append({"role": "user", "source": "cursor_bridge", "content": input_text})
                body = output_text or message
                if body:
                    bridge_messages.append({"role": "assistant", "source": "cursor_bridge", "content": body})
    except Exception as exc:
        if not local.get("found"):
            return {"surface": "cursor", "found": False, "reason": str(exc), "messages": []}

    local_messages = local.get("messages") if isinstance(local.get("messages"), list) else []
    messages = local_messages or bridge_messages
    if local_messages and bridge_messages:
        messages = _merge_thread_messages(local_messages, bridge_messages, limit=max(1, int(limit_events or 20)))

    if not messages and not local.get("found") and not session:
        reason = local.get("reason") or "No Cursor IDE session or local transcript found for this project."
        return {"surface": "cursor", "found": False, "reason": reason, "messages": []}

    thread = local.get("thread") if isinstance(local.get("thread"), dict) else {}
    packet = session.get("input_packet") if isinstance(session, dict) and isinstance(session.get("input_packet"), dict) else {}
    return {
        "surface": "cursor",
        "found": True,
        "session_id": session.get("id") if session else None,
        "thread_id": thread.get("id") or packet.get("external_thread_id") or thread_id or "",
        "status": (session.get("status") if session else "") or "transcript",
        "project": local.get("project_name")
        or (
            (progress.get("project") or {}).get("name")
            if isinstance(progress.get("project"), dict)
            else ""
        ),
        "folder": thread.get("folder") or packet.get("folder") or cwd or "",
        "messages": messages,
        "events": events[-limit_events:],
        "warning": local.get("warning") or "",
        "sources": _message_sources(local_messages, bridge_messages),
    }


def _message_sources(local_messages: list[dict[str, Any]], bridge_messages: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if local_messages:
        sources.append("cursor_transcript")
    if bridge_messages:
        sources.append("decisions_ide_bridge")
    return sources


def _merge_thread_messages(
    local_messages: list[dict[str, Any]],
    bridge_messages: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, str]]:
    merged = list(local_messages)
    local_tails = {
        str(item.get("content") or item.get("text") or "")[-180:].strip().lower()
        for item in local_messages[-4:]
        if isinstance(item, dict)
    }
    for item in bridge_messages:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            continue
        tail = content[-180:].strip().lower()
        if tail and tail in local_tails:
            continue
        merged.append(item)
    return merged[-limit:]


def thread_status(
    *,
    session_id: int | None = None,
    thread_id: str = "",
    query: str = "",
    project: str = "",
    cwd: str = "",
    project_id: int | None = None,
) -> dict[str, Any]:
    context = read_thread(
        session_id=session_id,
        thread_id=thread_id,
        query=query,
        project=project,
        cwd=cwd,
        project_id=project_id,
        limit_events=5,
    )
    if not context.get("found"):
        return {
            "surface": "cursor",
            "found": False,
            "status": "not_found",
            "reason": context.get("reason") or "No Cursor session found.",
        }

    messages = context.get("messages") if isinstance(context.get("messages"), list) else []
    last_preview = ""
    last_role = ""
    if messages:
        last = messages[-1]
        last_role = str(last.get("role") or "")
        last_preview = str(last.get("content") or last.get("text") or "")[:240]

    raw_status = str(context.get("status") or "running").lower()
    if raw_status in {"completed", "failed", "cancelled"}:
        phase = "idle"
    elif last_role == "user" and not last_preview.startswith("Status:"):
        phase = "awaiting_response"
    else:
        phase = "in_progress"

    return {
        "surface": "cursor",
        "found": True,
        "status": raw_status,
        "phase": phase,
        "session_id": context.get("session_id"),
        "thread_id": context.get("thread_id") or thread_id,
        "project": context.get("project") or "",
        "folder": context.get("folder") or "",
        "last_message_role": last_role,
        "last_message_preview": last_preview,
        "event_count": len(context.get("events") or []),
    }


def prompt_thread(
    *,
    instruction: str,
    folder: str = "",
    thread_id: str = "",
    model: str = "",
    resume: bool = True,
    continue_latest: bool = False,
) -> dict[str, Any]:
    text = (instruction or "").strip()
    if not text:
        return {"success": False, "error": "instruction is required", "surface": "cursor"}

    executable = _cursor_executable()
    if not executable:
        return {"success": False, "error": "cursor-agent not found on PATH", "surface": "cursor"}

    cmd = [executable, "--trust", "-p"]
    if model and model not in ("auto", "default"):
        cmd += ["--model", model]
    tid = (thread_id or "").strip()
    if tid and resume:
        cmd += ["--resume", tid]
    elif continue_latest:
        cmd += ["--continue"]
    cmd.append(text)

    env = {**os.environ, "TERM": "dumb"}
    key = _cursor_api_key()
    if key:
        env["CURSOR_API_KEY"] = key

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=folder if folder and os.path.isdir(folder) else None,
            env=env,
        )
        output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
        return {
            "success": result.returncode == 0,
            "surface": "cursor",
            "thread_id": tid or None,
            "command": cmd[:-1] + ["<instruction>"],
            "output_preview": output[:4000],
            "error": "" if result.returncode == 0 else (output[:2000] or f"cursor-agent exited {result.returncode}"),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "surface": "cursor", "error": "cursor-agent timed out after 600s"}
    except Exception as exc:
        logger.warning("cursor prompt failed: %s", exc, exc_info=True)
        return {"success": False, "surface": "cursor", "error": str(exc)}


def amend_thread(
    *,
    amendment: str,
    thread_id: str = "",
    folder: str = "",
    session_id: int | None = None,
    model: str = "",
) -> dict[str, Any]:
    tid = (thread_id or "").strip()
    if not tid and session_id:
        status = thread_status(session_id=session_id, cwd=folder)
        tid = str(status.get("thread_id") or "").strip()
    if not tid:
        return prompt_thread(
            instruction=amendment,
            folder=folder,
            resume=False,
            continue_latest=True,
            model=model,
        )
    return prompt_thread(
        instruction=amendment,
        folder=folder,
        thread_id=tid,
        model=model,
        resume=True,
    )
