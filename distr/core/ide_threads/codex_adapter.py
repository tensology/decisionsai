"""Codex thread list/read/status/prompt via local state + CLI."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def list_threads(*, project: str = "", limit: int = 12) -> list[dict[str, Any]]:
    from distr.core.external_agent_context import list_codex_threads, _project_name_from_path

    threads = list_codex_threads(limit=max(1, min(int(limit or 12), 30)))
    project_hint = (project or "").strip().lower()
    items: list[dict[str, Any]] = []
    for row in threads:
        cwd = str(row.get("cwd") or "")
        project_name = _project_name_from_path(cwd)
        if project_hint and project_hint not in project_name.lower() and project_hint not in cwd.lower():
            if project_hint not in str(row.get("title") or "").lower():
                continue
        items.append(
            {
                "surface": "codex",
                "thread_id": str(row.get("id") or ""),
                "title": row.get("title") or row.get("preview") or "",
                "project": project_name,
                "folder": cwd,
                "updated_at": row.get("updated_at") or "",
                "preview": row.get("preview") or "",
                "archived": bool(row.get("archived")),
                "model": row.get("model") or "",
            }
        )
    return items[: max(1, min(int(limit or 12), 30))]


def read_thread(
    *,
    thread_id: str = "",
    query: str = "",
    project: str = "",
    limit_messages: int = 12,
) -> dict[str, Any]:
    from distr.core.external_agent_context import build_codex_thread_context

    context = build_codex_thread_context(
        query=query,
        project=project,
        thread_id=thread_id,
        limit_messages=limit_messages,
    )
    context["surface"] = "codex"
    return context


def thread_status(*, thread_id: str = "", query: str = "", project: str = "") -> dict[str, Any]:
    context = read_thread(thread_id=thread_id, query=query, project=project, limit_messages=3)
    thread = context.get("thread") if isinstance(context.get("thread"), dict) else {}
    messages = context.get("messages") if isinstance(context.get("messages"), list) else []
    last_role = ""
    last_preview = ""
    if messages:
        last = messages[-1] if isinstance(messages[-1], dict) else {}
        last_role = str(last.get("role") or "")
        last_preview = str(last.get("content") or "")[:240]

    rollout_path = str(thread.get("rollout_path") or "")
    rollout_mtime = ""
    if rollout_path:
        path = Path(rollout_path).expanduser()
        if path.exists():
            rollout_mtime = _iso_mtime(path)

    status = "unknown"
    if context.get("found"):
        status = "active" if not thread.get("archived") else "archived"
    elif thread_id or query or project:
        status = "not_found"

    return {
        "surface": "codex",
        "found": bool(context.get("found")),
        "status": status,
        "thread_id": str(thread.get("id") or thread_id or ""),
        "title": thread.get("title") or thread.get("preview") or "",
        "project": context.get("project_name") or "",
        "updated_at": thread.get("updated_at") or "",
        "rollout_updated_at": rollout_mtime,
        "last_message_role": last_role,
        "last_message_preview": last_preview,
        "message_count": len(messages),
        "warning": context.get("warning") or "",
    }


def _iso_mtime(path: Path) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _codex_executable() -> str | None:
    import shutil

    return shutil.which("codex")


def prompt_thread(
    *,
    instruction: str,
    folder: str = "",
    thread_id: str = "",
    model: str = "",
    sandbox: str = "workspace-write",
    resume: bool = True,
) -> dict[str, Any]:
    """Send a prompt to Codex (new thread or resume existing)."""
    text = (instruction or "").strip()
    if not text:
        return {"success": False, "error": "instruction is required", "surface": "codex"}

    executable = _codex_executable()
    if not executable:
        return {"success": False, "error": "codex CLI not found on PATH", "surface": "codex"}

    cmd: list[str] = [executable, "exec"]
    if sandbox:
        cmd += ["--sandbox", sandbox]
    if model and model not in ("auto", "default"):
        cmd += ["--model", model]
    if folder and os.path.isdir(folder):
        cmd += ["-C", folder]

    tid = (thread_id or "").strip()
    if tid and resume:
        cmd += ["resume", tid, text]
    else:
        cmd.append(text)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=folder if folder and os.path.isdir(folder) else None,
            env={**os.environ, "TERM": "dumb"},
        )
        output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
        return {
            "success": result.returncode == 0,
            "surface": "codex",
            "thread_id": tid or None,
            "command": cmd[:-1] + ["<instruction>"] if cmd else [],
            "output_preview": output[:4000],
            "error": "" if result.returncode == 0 else (output[:2000] or f"codex exited {result.returncode}"),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "surface": "codex", "error": "codex exec timed out after 600s"}
    except Exception as exc:
        logger.warning("codex prompt failed: %s", exc, exc_info=True)
        return {"success": False, "surface": "codex", "error": str(exc)}


def amend_thread(
    *,
    amendment: str,
    thread_id: str = "",
    folder: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Continue an existing Codex thread with a follow-up / steer message."""
    tid = (thread_id or "").strip()
    if not tid:
        return {
            "success": False,
            "surface": "codex",
            "error": "thread_id is required to amend a Codex thread",
        }
    return prompt_thread(
        instruction=amendment,
        folder=folder,
        thread_id=tid,
        model=model,
        resume=True,
    )
