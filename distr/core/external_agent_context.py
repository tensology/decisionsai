"""Read local Codex/Cursor work context for DecisionsAI agent surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import unquote, urlparse


def build_external_agent_context(*, limit: int = 8) -> dict[str, Any]:
    context = {
        "codex_threads": list_codex_threads(limit=limit),
        "codex_history": list_codex_history(limit=limit),
        "cursor_workspaces": list_cursor_workspaces(limit=limit),
    }
    try:
        record_external_agent_context_activity(context)
    except Exception:
        pass
    return context


def record_external_agent_context_activity(context: dict[str, Any] | None) -> int:
    """Record recent Codex/Cursor context as quiet Hermes machine activity."""
    if not isinstance(context, dict):
        return 0
    try:
        from distr.core.orchestrator_memory import record_machine_activity, run_weekly_machine_activity_compaction
    except Exception:
        return 0

    try:
        run_weekly_machine_activity_compaction()
    except Exception:
        pass

    count = 0
    codex_threads = [item for item in context.get("codex_threads") or [] if isinstance(item, dict)]
    for item in codex_threads:
        title = _one_line(item.get("title") or item.get("preview") or "Codex thread", 220)
        workspace = str(item.get("cwd") or "").strip()
        activity_id = record_machine_activity(
            surface="codex",
            app_name="Codex",
            window_title=title,
            workspace_path=workspace,
            summary=f"Codex thread: {title}",
            metadata={
                "thread_id": item.get("id") or "",
                "source": item.get("source") or "",
                "model": item.get("model") or "",
                "updated_at": item.get("updated_at") or "",
                "archived": bool(item.get("archived")),
            },
            at=_iso_to_ts(item.get("updated_at")),
        )
        if activity_id:
            count += 1

    cursor_workspaces = [item for item in context.get("cursor_workspaces") or [] if isinstance(item, dict)]
    for item in cursor_workspaces:
        workspace = str(item.get("folder") or "").strip()
        name = _project_name_from_path(workspace) or "workspace"
        activity_id = record_machine_activity(
            surface="cursor",
            app_name="Cursor",
            window_title=name,
            workspace_path=workspace,
            summary=f"Cursor workspace: {name}",
            metadata={
                "storage_id": item.get("storage_id") or "",
                "updated_at": item.get("updated_at") or "",
            },
            at=_iso_to_ts(item.get("updated_at")),
        )
        if activity_id:
            count += 1
    return count


def list_codex_threads(*, limit: int = 8) -> list[dict[str, Any]]:
    db_path = _codex_home() / "state_5.sqlite"
    if not db_path.exists():
        return []
    query = """
        select id, updated_at, updated_at_ms, source, cwd, title, archived,
               first_user_message, preview, model, rollout_path
        from threads
        order by coalesce(updated_at_ms, updated_at * 1000) desc
        limit ?
    """
    rows = _sqlite_rows(db_path, query, (max(1, min(int(limit or 8), 30)),))
    if not rows:
        query = """
            select id, updated_at, updated_at_ms, source, cwd, title, archived,
                   first_user_message, preview, model
            from threads
            order by coalesce(updated_at_ms, updated_at * 1000) desc
            limit ?
        """
        rows = _sqlite_rows(db_path, query, (max(1, min(int(limit or 8), 30)),))
    threads: list[dict[str, Any]] = []
    for row in rows:
        updated_ms = row.get("updated_at_ms")
        updated_s = row.get("updated_at")
        updated_at = _ts_to_iso(updated_ms, millis=True) if updated_ms else _ts_to_iso(updated_s)
        title = _one_line(row.get("title") or row.get("first_user_message") or row.get("preview") or "", 220)
        preview = _one_line(row.get("preview") or row.get("first_user_message") or "", 260)
        threads.append({
            "id": row.get("id"),
            "updated_at": updated_at,
            "source": row.get("source") or "",
            "cwd": row.get("cwd") or "",
            "title": title,
            "preview": preview,
            "archived": bool(row.get("archived")),
            "model": row.get("model") or "",
            "rollout_path": row.get("rollout_path") or "",
        })
    return threads


def build_codex_thread_context(
    *,
    query: str = "",
    project: str = "",
    thread_id: str = "",
    limit_messages: int = 12,
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Load a focused local Codex conversation from the desktop session store."""
    threads = list_codex_threads(limit=30)
    if not threads:
        return {
            "found": False,
            "reason": "No local Codex threads were found.",
            "query": query,
            "project": project,
            "candidates": [],
        }

    ranked = _rank_codex_threads(threads, query=query, project=project, thread_id=thread_id)
    if not ranked:
        return {
            "found": False,
            "reason": "No matching Codex thread was found.",
            "query": query,
            "project": project,
            "candidates": _thread_candidates_for_reference(threads[:6]),
        }

    selected = ranked[0]
    rollout_path = selected.get("rollout_path") or _find_rollout_path_for_thread(str(selected.get("id") or ""))
    messages: list[dict[str, str]] = []
    tool_calls: list[str] = []
    warning = ""
    if rollout_path:
        messages, tool_calls = _read_codex_rollout(
            Path(rollout_path).expanduser(),
            limit_messages=max(1, min(int(limit_messages or 12), 30)),
            max_chars=max(1000, int(max_chars or 6000)),
        )
        if not messages:
            warning = "The matching Codex thread exists, but its local transcript did not contain readable user or assistant messages."
    else:
        warning = "The matching Codex thread does not have a readable rollout transcript path."

    return {
        "found": True,
        "thread": selected,
        "project_name": _project_name_from_path(selected.get("cwd") or ""),
        "activity_hint": _activity_hint(selected.get("title") or selected.get("preview") or ""),
        "messages": messages,
        "tool_calls": tool_calls,
        "warning": warning,
        "alternatives": _thread_candidates_for_reference(ranked[1:5]),
    }


def format_codex_thread_context_for_prompt(context: dict[str, Any] | None, *, max_reference_chars: int = 7000) -> str:
    from distr.core.agent.tool_voice_format import voice_then_reference

    if not isinstance(context, dict) or not context.get("found"):
        reason = (context or {}).get("reason") if isinstance(context, dict) else ""
        candidates = (context or {}).get("candidates") if isinstance(context, dict) else []
        spoken = "I could not find the matching Codex conversation locally yet."
        if reason:
            spoken += f" {reason}"
        reference = _format_thread_candidates_reference(candidates or [])
        return voice_then_reference(spoken, reference)

    thread = context.get("thread") or {}
    project_name = context.get("project_name") or _project_name_from_path(thread.get("cwd") or "") or "that project"
    activity = context.get("activity_hint") or _clean_label(thread.get("title") or thread.get("preview") or "the recent work")
    messages = [item for item in context.get("messages") or [] if isinstance(item, dict)]
    warning = context.get("warning") or ""

    spoken = f"I found the {project_name} Codex conversation. It looks like it is about {activity}."
    if messages:
        spoken += " I can use that thread now for a ticket, plan, skill handoff, or reply without you pasting it here."
    elif warning:
        spoken += f" {warning}"

    reference_lines = [
        f"Codex thread: {thread.get('title') or thread.get('preview') or thread.get('id') or 'Untitled'}",
        f"Project: {project_name}",
    ]
    if thread.get("cwd"):
        reference_lines.append(f"CWD: {thread.get('cwd')}")
    if thread.get("id"):
        reference_lines.append(f"Thread ID: {thread.get('id')}")
    if thread.get("rollout_path"):
        reference_lines.append(f"Transcript: {thread.get('rollout_path')}")
    if warning:
        reference_lines.append(f"Warning: {warning}")
    if messages:
        reference_lines.append("")
        reference_lines.append("Recent conversation:")
        for item in messages:
            role = "User" if item.get("role") == "user" else "Assistant"
            text = _one_line(item.get("text") or "", 900)
            reference_lines.append(f"{role}: {text}")
    tool_calls = [str(name) for name in context.get("tool_calls") or [] if str(name).strip()]
    if tool_calls:
        reference_lines.append("")
        reference_lines.append(f"Recent tools: {_join_human(tool_calls[-8:])}")
    alternatives = context.get("alternatives") or []
    if alternatives:
        reference_lines.append("")
        reference_lines.append("Other possible Codex threads:")
        reference_lines.extend(_format_thread_candidates_reference(alternatives).splitlines())

    reference = "\n".join(reference_lines).strip()
    if len(reference) > max_reference_chars:
        reference = reference[: max_reference_chars - 3].rstrip() + "..."
    return voice_then_reference(spoken, reference)


def list_codex_history(*, limit: int = 8) -> list[dict[str, Any]]:
    path = _codex_home() / "history.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(int(limit or 8), 30)):]
    except Exception:
        return []
    history: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        history.append({
            "session_id": item.get("session_id") or "",
            "updated_at": _ts_to_iso(item.get("ts")),
            "text": _one_line(item.get("text") or "", 220),
        })
    return history


def list_cursor_workspaces(*, limit: int = 8) -> list[dict[str, Any]]:
    root = _cursor_workspace_storage()
    if not root.exists():
        return []
    items: list[tuple[float, Path, dict[str, Any]]] = []
    try:
        workspace_files = list(root.glob("*/workspace.json"))
    except Exception:
        return []
    for workspace_file in workspace_files:
        try:
            data = json.loads(workspace_file.read_text(encoding="utf-8", errors="replace"))
            folder = _decode_workspace_folder(data.get("folder") or data.get("workspace") or "")
            mtime = max(workspace_file.stat().st_mtime, (workspace_file.parent / "state.vscdb").stat().st_mtime if (workspace_file.parent / "state.vscdb").exists() else 0)
        except Exception:
            continue
        if folder:
            items.append((mtime, workspace_file.parent, {
                "folder": folder,
                "storage_id": workspace_file.parent.name,
                "updated_at": _ts_to_iso(mtime),
            }))
    items.sort(key=lambda item: item[0], reverse=True)
    return [item[2] for item in items[: max(1, min(int(limit or 8), 30))]]


def format_external_agent_context_for_prompt(context: dict[str, Any] | None, *, max_chars: int = 1400) -> str:
    if not isinstance(context, dict):
        return ""
    lines: list[str] = []
    codex_threads = [item for item in context.get("codex_threads") or [] if isinstance(item, dict)]
    cursor_workspaces = [item for item in context.get("cursor_workspaces") or [] if isinstance(item, dict)]
    history = [item for item in context.get("codex_history") or [] if isinstance(item, dict)]

    if codex_threads:
        lines.append("- codex_threads:")
        for item in codex_threads[:6]:
            title = _one_line(item.get("title") or item.get("preview") or "", 140)
            cwd = _short_path(item.get("cwd") or "")
            updated = item.get("updated_at") or ""
            lines.append(f"  - {updated} {cwd}: {title}")
    if cursor_workspaces:
        lines.append("- cursor_workspaces:")
        for item in cursor_workspaces[:6]:
            folder = _short_path(item.get("folder") or "")
            updated = item.get("updated_at") or ""
            lines.append(f"  - {updated} {folder}")
    if history:
        lines.append("- recent_codex_inputs:")
        for item in history[-4:]:
            lines.append(f"  - {_one_line(item.get('text') or '', 150)}")
    if not lines:
        return ""
    text = "- external_agent_context:\n" + "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def build_agent_visibility_answer(user_request: str = "", *, max_chars: int = 1800) -> str:
    from distr.core.developer_context import build_developer_context

    context = build_developer_context(user_request=user_request)
    external = context.external_agent_context or {}
    codex_threads = [item for item in external.get("codex_threads") or [] if isinstance(item, dict)]
    cursor_workspaces = [item for item in external.get("cursor_workspaces") or [] if isinstance(item, dict)]

    active_name = context.active_project.name if context.active_project else ""
    if active_name:
        lines = [f"Yes. I can see recent Codex and Cursor activity. Right now, the active project is {active_name}."]
    else:
        lines = ["Yes. I can see recent Codex and Cursor activity."]

    codex_summary = _summarize_codex_threads(codex_threads)
    if codex_summary:
        lines.append(f"On the Codex side, I can see recent work around {codex_summary}.")

    cursor_names = _workspace_names(cursor_workspaces)
    if cursor_names:
        lines.append(f"Cursor has recently had {_join_human(cursor_names[:5])} open.")

    if context.active_project:
        active_folder_name = _project_name_from_path(context.active_project.folder_location)
        if active_folder_name and active_folder_name != active_name:
            lines.append(f"The active workspace folder looks like {active_folder_name}.")
    if context.active_workflows:
        workflow_names = [
            _clean_label(workflow.name or workflow.current_step_name or f"workflow {workflow.id}")
            for workflow in context.active_workflows[:3]
        ]
        lines.append(f"There are active workflow runs for {_join_human(workflow_names)}.")
    if context.active_executions:
        backend_names = _unique(
            _clean_label(execution.backend or "project agent")
            for execution in context.active_executions[:4]
        )
        lines.append(f"I can also see recent project-agent runs through {_join_human(backend_names)}.")
    if not codex_threads and not cursor_workspaces and not context.active_executions and not context.active_workflows:
        lines.append("I do not see any recorded Codex or Cursor work yet, so the local history bridge has not produced readable state.")
    else:
        lines.append("I will use that as context instead of asking you to paste what is on screen.")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _rank_codex_threads(
    threads: list[dict[str, Any]],
    *,
    query: str = "",
    project: str = "",
    thread_id: str = "",
) -> list[dict[str, Any]]:
    query_tokens = _search_tokens(query)
    project_tokens = _search_tokens(project)
    wanted_id = str(thread_id or "").strip().lower()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, item in enumerate(threads):
        haystack = " ".join(
            str(part or "")
            for part in (
                item.get("id"),
                item.get("title"),
                item.get("preview"),
                item.get("cwd"),
                _project_name_from_path(item.get("cwd") or ""),
            )
        ).lower()
        score = 0
        if wanted_id:
            item_id = str(item.get("id") or "").lower()
            if item_id == wanted_id:
                score += 1000
            elif item_id.startswith(wanted_id):
                score += 800
            elif wanted_id in item_id:
                score += 500
        for token in project_tokens:
            if token in haystack:
                score += 60
        for token in query_tokens:
            if token in haystack:
                score += 20
        if not wanted_id and not query_tokens and not project_tokens:
            score = 1
        if score > 0:
            scored.append((score, -index, item))
    scored.sort(key=lambda value: (value[0], value[1]), reverse=True)
    return [item for _, _, item in scored]


def _read_codex_rollout(path: Path, *, limit_messages: int, max_chars: int) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists() or not path.is_file():
        return [], []
    messages: list[dict[str, str]] = []
    tool_calls: list[str] = []
    total_chars = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                payload = item.get("payload") if isinstance(item, dict) else {}
                if not isinstance(payload, dict):
                    continue
                payload_type = payload.get("type")
                if payload_type == "function_call":
                    name = str(payload.get("name") or "").strip()
                    if name:
                        tool_calls.append(name)
                    continue
                if payload_type != "message":
                    continue
                role = str(payload.get("role") or "").strip().lower()
                if role not in {"user", "assistant"}:
                    continue
                if role == "assistant" and str(payload.get("phase") or "").lower() == "commentary":
                    continue
                text = _extract_rollout_message_text(payload.get("content"))
                if _skip_rollout_message(text):
                    continue
                if len(text) > 1500:
                    text = text[:1497].rstrip() + "..."
                messages.append({"role": role, "text": text})
                total_chars += len(text)
                while len(messages) > limit_messages or total_chars > max_chars:
                    removed = messages.pop(0)
                    total_chars -= len(removed.get("text") or "")
    except Exception:
        return [], tool_calls[-12:]
    return messages[-limit_messages:], _unique(tool_calls[-12:])


def _extract_rollout_message_text(content: Any) -> str:
    if isinstance(content, str):
        return _one_line(content, 4000)
    parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
    elif isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            parts.append(text)
    return _one_line("\n".join(parts), 4000)


def _skip_rollout_message(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    noise_prefixes = (
        "<environment_context>",
        "<permissions instructions>",
        "<app-context>",
        "<apps_instructions>",
        "<skills_instructions>",
        "<plugins_instructions>",
        "<collaboration_mode>",
    )
    return any(lowered.startswith(prefix) for prefix in noise_prefixes)


def _find_rollout_path_for_thread(thread_id: str) -> str:
    thread_id = str(thread_id or "").strip()
    if not thread_id:
        return ""
    root = _codex_home() / "sessions"
    if not root.exists():
        return ""
    try:
        for path in root.rglob(f"*{thread_id}.jsonl"):
            if path.is_file():
                return str(path)
    except Exception:
        return ""
    return ""


def _search_tokens(value: str) -> list[str]:
    stopwords = {
        "about",
        "able",
        "actual",
        "actually",
        "and",
        "agent",
        "answer",
        "are",
        "ask",
        "bring",
        "can",
        "chat",
        "chats",
        "codex",
        "codecs",
        "conversation",
        "conversations",
        "cursor",
        "happened",
        "histories",
        "history",
        "if",
        "inside",
        "into",
        "load",
        "one",
        "please",
        "plan",
        "project",
        "pull",
        "session",
        "sessions",
        "skill",
        "summarise",
        "summarize",
        "ticket",
        "that",
        "the",
        "this",
        "thread",
        "threads",
        "transcript",
        "transcripts",
        "try",
        "trying",
        "turn",
        "use",
        "using",
        "want",
        "wanted",
        "were",
        "what",
        "with",
        "work",
        "working",
        "would",
        "you",
    }
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 2 and token not in stopwords
    ]
    return _unique(tokens)


def _thread_candidates_for_reference(threads: list[dict[str, Any]]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for item in threads:
        candidates.append(
            {
                "project": _project_name_from_path(item.get("cwd") or ""),
                "title": _one_line(item.get("title") or item.get("preview") or "", 160),
                "updated_at": str(item.get("updated_at") or ""),
                "id": str(item.get("id") or ""),
            }
        )
    return candidates


def _format_thread_candidates_reference(candidates: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in candidates:
        project = item.get("project") or "Unknown project"
        title = item.get("title") or "Untitled"
        updated = item.get("updated_at") or ""
        thread_id = item.get("id") or ""
        bits = [str(project), str(title)]
        if updated:
            bits.append(str(updated))
        if thread_id:
            bits.append(str(thread_id))
        lines.append(" - " + " | ".join(bits))
    return "\n".join(lines)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()


def _cursor_workspace_storage() -> Path:
    return Path(
        os.environ.get("CURSOR_WORKSPACE_STORAGE")
        or (Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage")
    ).expanduser()


def _sqlite_rows(db_path: Path, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=0.2)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(query, params).fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def _decode_workspace_folder(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return unquote(value)


def _ts_to_iso(value: Any, *, millis: bool = False) -> str:
    try:
        ts = float(value)
        if millis:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _iso_to_ts(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _one_line(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _short_path(value: str) -> str:
    path = str(value or "")
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _summarize_codex_threads(threads: list[dict[str, Any]], *, max_projects: int = 2) -> str:
    grouped: dict[str, list[str]] = {}
    for item in threads[:8]:
        name = _project_name_from_path(item.get("cwd") or "") or "a Codex workspace"
        hint = _activity_hint(item.get("title") or item.get("preview") or "")
        if not hint:
            continue
        grouped.setdefault(name, [])
        if hint not in grouped[name]:
            grouped[name].append(hint)

    project_bits: list[str] = []
    for name, hints in grouped.items():
        if hints:
            project_bits.append(f"{name} ({_join_human(hints[:2])})")
        else:
            project_bits.append(name)
        if len(project_bits) >= max_projects:
            break
    return _join_human(project_bits)


def _workspace_names(workspaces: list[dict[str, Any]]) -> list[str]:
    return _unique(
        name
        for item in workspaces
        for name in [_project_name_from_path(item.get("folder") or "")]
        if name
    )


def _project_name_from_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    try:
        leaf = Path(path).name or path.rstrip("/").split("/")[-1]
    except Exception:
        leaf = path.rstrip("/").split("/")[-1]
    return _project_name_from_token(leaf)


_DOMAIN_PREFIXES = {"www", "app", "api", "admin", "dev", "stage", "staging", "prod", "dpp"}
_DOMAIN_SUFFIXES = {"com", "co", "za", "net", "org", "io", "ai", "uk", "us", "dev", "app"}
_SPECIAL_PROJECT_NAMES = {
    "crystallogic": "Crystallogic",
    "decisions": "DecisionsAI",
    "decisionsai": "DecisionsAI",
    "merrypak": "Merrypak",
    "multisnack": "Multisnack",
    "player1sport": "Player1Sport",
    "relightsa": "RelightSA",
    "tensology": "Tensology",
}


def _project_name_from_token(token: str) -> str:
    raw = str(token or "").strip().strip(".")
    if not raw:
        return ""
    labels = [part for part in re.split(r"[.\s]+", raw.lower()) if part]
    if len(labels) > 1:
        candidates = [part for part in labels if part not in _DOMAIN_PREFIXES and part not in _DOMAIN_SUFFIXES]
        if candidates:
            raw = candidates[0]
    key = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if key in _SPECIAL_PROJECT_NAMES:
        return _SPECIAL_PROJECT_NAMES[key]
    parts = [part for part in re.split(r"[-_.\s]+", raw) if part]
    return " ".join(_format_name_part(part) for part in parts)


def _format_name_part(part: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", part.lower())
    if key in _SPECIAL_PROJECT_NAMES:
        return _SPECIAL_PROJECT_NAMES[key]
    if not part:
        return ""
    return part[:1].upper() + part[1:]


def _activity_hint(title: Any) -> str:
    text = _one_line(title, 180).strip(" -:.,")
    if not text:
        return ""
    lower = text.lower()
    if "elevenlabs" in lower and "crackle" in lower:
        return "ElevenLabs voice crackle"
    if (
        ("codex" in lower or "codecs" in lower)
        and "cursor" in lower
        and ("see" in lower or "context" in lower or "project" in lower or "extension" in lower or "plugin" in lower)
    ):
        return "Codex and Cursor visibility"
    if "loophole" in lower or "glitch" in lower or "edge case" in lower:
        return "bug and edge-case review"
    if "division" in lower and ("alphabet" in lower or "order" in lower or "sort" in lower):
        return "division ordering"
    if "branch" in lower and ("crud" in lower or "copy" in lower):
        return "branches CRUD"
    if "page" in lower and "running" in lower:
        return "pages not running properly"
    if text.isupper():
        text = text.lower()
    return _clean_label(text, limit=90)


def _clean_label(value: Any, *, limit: int = 80) -> str:
    text = _one_line(value, limit)
    text = re.sub(r"[/\\]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:.,")
    return text


def _join_human(items: list[str]) -> str:
    cleaned = [item for item in _unique(items) if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _unique(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out
