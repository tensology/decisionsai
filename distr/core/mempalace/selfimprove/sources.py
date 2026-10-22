"""Incremental readers for harness transcripts (read-only, byte-offset cursors).

Each reader yields normalized turns:
``{"harness", "session", "project", "cwd", "role", "text", "ts", "is_error", "tool", "ref"}``
where role is ``user`` | ``assistant`` | ``tool``. Unknown lines are skipped.
"""

from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

MAX_TEXT = 4000


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _project(cwd: str | None, fallback: str) -> str:
    name = os.path.basename((cwd or "").rstrip("/")) or fallback
    return name.lower()


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(c.get("text") or ""))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    return ""


_DELEGATION = re.compile(r"<realtime_delegation>.*?<input>(.*?)</input>", re.S)
_DROP_SECTIONS = re.compile(r"^# Files mentioned by the user:.*?(?=^## My request|\Z)", re.S | re.M)
_TAGS_ONLY = re.compile(r"^\s*(<[a-zA-Z_][\w-]*[^>]*>.*?</[\w-]+>\s*)+$", re.S)
_INLINE_TAGS = re.compile(r"</?(image|user_query|attached_files|user_info)[^>]*>")
_SYSTEM_PREFIXES = ("<environment_context", "<user_instructions", "# AGENTS.md", "<permissions", "<system-reminder",
                    "<turn_aborted", "<subagent_notification", "<command-", "<local-command", "Caveat: ",
                    "[Request interrupted", "<external_", "<skill", "<collaboration_mode")


def clean_user_text(text: str) -> str:
    """Strip harness-injected wrappers so only what the person typed or said is classified."""
    if not text:
        return ""
    m = _DELEGATION.search(text)
    if m:
        text = m.group(1)
    text = _DROP_SECTIONS.sub("", text)
    text = text.replace("## My request for Codex:", "")
    stripped = text.lstrip()
    if stripped.startswith(_SYSTEM_PREFIXES) or _TAGS_ONLY.match(stripped):
        return ""
    return _INLINE_TAGS.sub("", text).strip()


def _turn(**kw: Any) -> dict[str, Any]:
    text = kw.get("text") or ""
    kw["raw_len"] = len(text)
    kw["text"] = text[:MAX_TEXT]
    kw.setdefault("is_error", False)
    kw.setdefault("tool", "")
    return kw


def parse_claude(line: dict, meta: dict) -> Iterator[dict]:
    t = line.get("type")
    if t not in ("user", "assistant") or line.get("isSidechain"):
        return
    msg = line.get("message") or {}
    cwd = line.get("cwd") or meta.get("cwd")
    meta["cwd"] = cwd
    base = dict(harness="claude", session=line.get("sessionId") or meta["session"], cwd=cwd,
                project=_project(cwd, meta["fallback"]), ts=line.get("timestamp"))
    content = msg.get("content")
    if t == "user":
        if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    yield _turn(**base, role="tool", text=_text_of(c.get("content")), is_error=bool(c.get("is_error")))
            return
        text = clean_user_text(_text_of(content))
        if text and line.get("userType", "external") == "external":
            yield _turn(**base, role="user", text=text)
    else:
        text = _text_of(content)
        tools = [c.get("name", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "tool_use"]
        skill_refs = [json.dumps(c.get("input"))[:300] for c in content or []
                      if isinstance(c, dict) and c.get("type") == "tool_use"]
        yield _turn(**base, role="assistant", text=(text + "\n" + "\n".join(skill_refs)).strip(), tool=",".join(tools))


def parse_codex(line: dict, meta: dict) -> Iterator[dict]:
    p = line.get("payload") if isinstance(line.get("payload"), dict) else {}
    if line.get("type") == "session_meta":
        meta["cwd"] = p.get("cwd")
        meta["session_id"] = p.get("id") or p.get("session_id")
        return
    if line.get("type") != "response_item":
        return
    base = dict(harness="codex", session=meta.get("session_id") or meta["session"], cwd=meta.get("cwd"),
                project=_project(meta.get("cwd"), meta["fallback"]), ts=line.get("timestamp"))
    pt = p.get("type")
    if pt == "message" and p.get("role") == "user":
        text = clean_user_text(_text_of(p.get("content")))
        if text:
            yield _turn(**base, role="user", text=text)
    elif pt == "message" and p.get("role") == "assistant":
        yield _turn(**base, role="assistant", text=_text_of(p.get("content")))
    elif pt in ("function_call", "custom_tool_call"):
        yield _turn(**base, role="assistant", text=str(p.get("arguments") or p.get("input") or "")[:600], tool=str(p.get("name") or ""))
    elif pt in ("function_call_output", "custom_tool_call_output"):
        out = p.get("output")
        text = _text_of(out) if isinstance(out, list) else str(out or "")
        err = False
        try:
            j = json.loads(text) if text.startswith("{") else {}
            code = (j.get("metadata") or {}).get("exit_code", j.get("exit_code"))
            err = bool(code) and code != 0
        except ValueError:
            pass
        if "Exit code: " in text and "Exit code: 0" not in text:
            err = True
        yield _turn(**base, role="tool", text=text, is_error=err)


def parse_pi(line: dict, meta: dict) -> Iterator[dict]:
    if line.get("type") == "session":
        meta["cwd"] = line.get("cwd")
        meta["session_id"] = line.get("id")
        return
    if line.get("type") != "message":
        return
    m = line.get("message") or {}
    role = m.get("role")
    base = dict(harness="pi", session=meta.get("session_id") or meta["session"], cwd=meta.get("cwd"),
                project=_project(meta.get("cwd"), meta["fallback"]), ts=line.get("timestamp"))
    if role == "user":
        text = clean_user_text(_text_of(m.get("content")))
        if text:
            yield _turn(**base, role="user", text=text)
    elif role == "assistant":
        calls = [c for c in m.get("content") or [] if isinstance(c, dict) and c.get("type") == "toolCall"]
        extra = "\n".join(json.dumps(c.get("arguments"))[:300] for c in calls)
        yield _turn(**base, role="assistant", text=(_text_of(m.get("content")) + "\n" + extra).strip(),
                    tool=",".join(str(c.get("name")) for c in calls))
    elif role == "toolResult":
        yield _turn(**base, role="tool", text=_text_of(m.get("content")), is_error=bool(m.get("isError")),
                    tool=str(m.get("toolName") or ""))


def parse_cursor(line: dict, meta: dict) -> Iterator[dict]:
    role = line.get("role")
    m = line.get("message") or {}
    base = dict(harness="cursor", session=meta["session"], cwd=meta.get("cwd"),
                project=meta["fallback"], ts=None)
    if role == "user":
        raw = _text_of(m.get("content"))
        q = re.search(r"<user_query>(.*?)</user_query>", raw, re.S)
        text = q.group(1).strip() if q else clean_user_text(raw)
        if text:
            yield _turn(**base, role="user", text=text)
    elif role == "assistant":
        yield _turn(**base, role="assistant", text=_text_of(m.get("content")))


def _cursor_project(path: str) -> str:
    # ~/.cursor/projects/Users-paul-development-X-repo/agent-transcripts/<id>/<id>.jsonl
    proj = Path(path).parts[-4] if "agent-transcripts" in path else "cursor"
    return proj.split("-")[-1].lower() or "cursor"


def _claude_project(path: str) -> str:
    return Path(path).parent.name.split("-")[-1].lower() or "claude"


def _pi_project(path: str) -> str:
    return Path(path).parent.name.strip("-").split("-")[-1].lower() or "pi"


SOURCES = {
    "claude": ("~/.claude/projects/*/*.jsonl", parse_claude, _claude_project),
    "codex": ("~/.codex/sessions/**/*.jsonl", parse_codex, lambda p: "codex"),
    "pi": ("~/.pi/agent/sessions/*/*.jsonl", parse_pi, _pi_project),
    "cursor": ("~/.cursor/projects/*/agent-transcripts/**/*.jsonl", parse_cursor, _cursor_project),
}


def files(name: str) -> list[str]:
    pattern = SOURCES[name][0].replace("~", str(_home()), 1)
    return glob.glob(pattern, recursive=True)


def iter_new(name: str, path: str, offset: int, meta: dict, *,
             max_line_bytes: int = 2_000_000) -> Iterator[tuple[list[dict], int, int]]:
    """Stream complete lines after ``offset``, one at a time.

    Yields ``(turns, end_offset, line_bytes)`` per line, where ``end_offset`` is the byte position just
    after that line. Only one line is in memory at a time. A trailing partial line is not
    consumed. Lines longer than ``max_line_bytes`` (base64 images, huge tool dumps) are skipped
    without being parsed, so they cost one read and nothing else.
    """
    _, parser, proj = SOURCES[name]
    meta.setdefault("session", Path(path).stem)
    meta.setdefault("fallback", proj(path))
    with open(path, "rb") as fh:
        fh.seek(offset)
        pos = offset
        while True:
            raw = fh.readline(max_line_bytes + 1)
            if not raw:
                return
            if not raw.endswith(b"\n"):
                if len(raw) <= max_line_bytes:
                    return  # partial last line: leave it for the next run
                # oversized line: skip to its end without keeping it
                skipped = len(raw)
                while raw and not raw.endswith(b"\n"):
                    raw = fh.readline(max_line_bytes + 1)
                    skipped += len(raw)
                if not raw:
                    return  # oversized line still being written
                pos += skipped
                yield [], pos, skipped
                continue
            pos += len(raw)
            if len(raw) > max_line_bytes:
                yield [], pos, len(raw)
                continue
            turns: list[dict] = []
            try:
                line = json.loads(raw)
            except ValueError:
                line = None
            if isinstance(line, dict):
                for t in parser(line, meta):
                    t["ref"] = f"{path}@{pos - len(raw)}"
                    turns.append(t)
            yield turns, pos, len(raw)


def read_new(name: str, path: str, offset: int, meta: dict) -> tuple[list[dict], int]:
    """Read complete lines after ``offset``; return (turns, new_offset). Small files / tests only."""
    turns: list[dict] = []
    end = offset
    for ts, end, _ in iter_new(name, path, offset, meta):
        turns.extend(ts)
    return turns, end


SOURCES = {
    "claude": ("~/.claude/projects/*/*.jsonl", parse_claude, _claude_project),
    "codex": ("~/.codex/sessions/**/*.jsonl", parse_codex, lambda p: "codex"),
    "pi": ("~/.pi/agent/sessions/*/*.jsonl", parse_pi, _pi_project),
    "cursor": ("~/.cursor/projects/*/agent-transcripts/**/*.jsonl", parse_cursor, _cursor_project),
}


def files(name: str) -> list[str]:
    pattern = SOURCES[name][0].replace("~", str(_home()), 1)
    return glob.glob(pattern, recursive=True)


def read_new(name: str, path: str, offset: int, meta: dict) -> tuple[list[dict], int]:
    """Read complete lines after ``offset``; return (turns, new_offset)."""
    _, parser, proj = SOURCES[name]
    meta.setdefault("session", Path(path).stem)
    meta.setdefault("fallback", proj(path))
    turns: list[dict] = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = data[: end + 1]
    lineno = None
    for raw in chunk.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        for t in parser(line, meta):
            t["ref"] = f"{path}@{offset}"
            turns.append(t)
    return turns, offset + len(chunk)
