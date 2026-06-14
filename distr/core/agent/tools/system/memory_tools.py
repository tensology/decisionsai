"""R10 — Cross-chat memory file tools (AGENT.md, USER.md, MEMORY.md, EVENTS.md)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Literal, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.memory.files import (
    AGENT_MD,
    CAP_AGENT_BYTES,
    CAP_MEMORY_BYTES,
    CAP_USER_BYTES,
    MEMORY_MD,
    SECTION_SPLIT_RE,
    USER_MD,
    append_markdown_section,
    atomic_write_text,
    enforce_cap,
    ensure_memory_files,
    memory_paths,
)

logger = logging.getLogger(__name__)

FileKey = Literal["agent", "user", "memory", "events"]

_MAX_READ_CHARS = 48_000
_MAX_SEARCH_SECTIONS = 40


def _normalize_file_key(raw: str) -> FileKey:
    k = (raw or "").strip().lower()
    if k in ("agent", "user", "memory", "events"):
        return k  # type: ignore[return-value]
    raise ValueError(
        "file must be one of: agent, user, memory, events (got %r)" % (raw,)
    )


def _path_for(key: FileKey):
    ensure_memory_files()
    paths = memory_paths()
    return {
        "agent": paths["agent"],
        "user": paths["user"],
        "memory": paths["memory"],
        "events": paths["events"],
    }[key]


def _read_text(path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _split_sections(text: str) -> list[str]:
    s = text.strip()
    if not s:
        return []
    parts = SECTION_SPLIT_RE.split(s)
    return [p.strip() for p in parts if p.strip()]


def _score_section(query: str, section: str) -> float:
    if not query.strip():
        return 0.0
    qtok = {t.lower() for t in re.findall(r"\w+", query) if len(t) > 1}
    if not qtok:
        return 0.0
    st = section.lower()
    hits = sum(1 for t in qtok if t in st)
    return hits + 0.01 * len(section) / max(len(st), 1)


def _confirm_mutation(
    *,
    event_queue: Any,
    confirmation_results_dict: Any,
    title: str,
    message: str,
    timeout_s: float = 120.0,
) -> bool:
    """Return True if allowed. Missing queues → allow (headless / tests)."""
    if event_queue is None:
        return True
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        if not settings.get("initiative_ask_file_changes", True):
            return True
    except Exception:
        pass

    if confirmation_results_dict is None:
        return True

    try:
        cid = str(uuid.uuid4())
        event_queue.put(
            {
                "type": "confirmation_request",
                "confirmation_id": cid,
                "title": title,
                "message": message,
            }
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if cid in confirmation_results_dict:
                result = confirmation_results_dict.pop(cid)
                return bool(result.get("approved"))
            time.sleep(0.2)
        return False
    except Exception as e:
        logger.warning("memory tool confirmation failed: %s", e)
        return True


def _publish_file_changed(path_hint: str) -> None:
    try:
        from distr.core.events import MEMORY_FILE_CHANGED, get_event_bus

        get_event_bus().publish(MEMORY_FILE_CHANGED, {"path": path_hint})
    except Exception:
        logger.debug("MEMORY_FILE_CHANGED publish failed", exc_info=True)


# ---------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------


class MemorySearchInput(BaseModel):
    query: str = Field(description="Natural language query to match against MEMORY.md sections.")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of best-matching sections to return.")


class MemorySearchTool(BaseTool):
    """Keyword-ranked search over --- sections in MEMORY.md (LlamaIndex optional later)."""

    name: str = "memory_search"
    description: str = (
        "Search long-term MEMORY.md for facts and preferences using keyword relevance over "
        "distilled sections (--- separated). Read-only. Use when the user asks what was saved, "
        "recall a preference, or find prior notes in persistent memory."
    )
    args_schema: type[BaseModel] = MemorySearchInput

    def _run(self, query: str = "", top_k: int = 5, **kwargs) -> str:
        ensure_memory_files()
        path = _path_for("memory")
        text = _read_text(path)
        sections = _split_sections(text)
        if not sections:
            return "MEMORY.md is empty or has no sections yet."
        scored = sorted(
            ((_score_section(query, sec), sec) for sec in sections[:_MAX_SEARCH_SECTIONS]),
            key=lambda x: -x[0],
        )
        top = [sec for _, sec in scored[:top_k]]
        parts = []
        for i, sec in enumerate(top, 1):
            preview = sec if len(sec) <= 6000 else sec[:6000] + "\n…"
            parts.append(f"### Match {i}\n{preview}")
        return "\n\n".join(parts) if parts else "No matching sections found."


# ---------------------------------------------------------------------------
# memory_read
# ---------------------------------------------------------------------------


class MemoryReadInput(BaseModel):
    file: str = Field(
        description="Which memory file: agent, user, memory, or events.",
    )
    start_line: int = Field(default=1, ge=1, description="First line (1-based).")
    end_line: int = Field(
        default=0,
        ge=0,
        description="Last line (1-based). Use 0 for ‘through end of file’.",
    )


class MemoryReadTool(BaseTool):
    name: str = "memory_read"
    description: str = (
        "Read lines from AGENT.md, USER.md, MEMORY.md, or EVENTS.md (cross-chat persistent files). "
        "Read-only. Use for inspecting preferences, identity, distilled memory, or raw events log."
    )
    args_schema: type[BaseModel] = MemoryReadInput

    def _run(
        self,
        file: str = "memory",
        start_line: int = 1,
        end_line: int = 0,
        **kwargs,
    ) -> str:
        key = _normalize_file_key(file)
        path = _path_for(key)
        raw = _read_text(path)
        lines = raw.splitlines()
        if not lines:
            return f"({key} file is empty)"
        n = len(lines)
        s = max(1, min(start_line, n))
        e = n if end_line <= 0 else min(end_line, n)
        if end_line > 0 and end_line < start_line:
            return "Invalid line range (end_line < start_line)."
        if e < s:
            return "Invalid line range."
        chunk = "\n".join(lines[s - 1 : e])
        if len(chunk) > _MAX_READ_CHARS:
            chunk = chunk[:_MAX_READ_CHARS] + "\n… [truncated]"
        header = f"Lines {s}-{e} of {key} ({path.name}):\n\n"
        return header + chunk


# ---------------------------------------------------------------------------
# memory_add
# ---------------------------------------------------------------------------


class MemoryAddInput(BaseModel):
    section: str = Field(description="Short section title or heading text for this memory block.")
    content: str = Field(description="Body text to store (facts, preferences, decisions).")
    target: Literal["memory", "user"] = Field(
        default="memory",
        description="Append to MEMORY.md (default) or USER.md preferences.",
    )


class MemoryAddTool(BaseTool):
    name: str = "memory_add"
    description: str = (
        "Append a new --- delimited section to MEMORY.md or USER.md. "
        "Requires user confirmation when file-change confirmations are enabled. "
        "Use to persist durable facts or preferences the user asked to remember."
    )
    args_schema: type[BaseModel] = MemoryAddInput

    event_queue: Optional[Any] = Field(default=None, exclude=True)
    command_queue: Optional[Any] = Field(default=None, exclude=True)
    confirmation_results_dict: Optional[Any] = Field(default=None, exclude=True)

    def __init__(
        self,
        event_queue=None,
        command_queue=None,
        confirmation_results_dict=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.event_queue = event_queue
        self.command_queue = command_queue
        self.confirmation_results_dict = confirmation_results_dict

    def _run(
        self,
        section: str = "",
        content: str = "",
        target: str = "memory",
        **kwargs,
    ) -> str:
        if not (section or "").strip() or not (content or "").strip():
            return "Error: section and content are required."
        block = f"### {section.strip()}\n\n{content.strip()}\n"
        title = "Append to memory file"
        msg = f"Append to {target.upper()}?\n\n{block[:800]}"
        if not _confirm_mutation(
            event_queue=self.event_queue,
            confirmation_results_dict=self.confirmation_results_dict,
            title=title,
            message=msg,
        ):
            return "Cancelled — memory not modified."

        ensure_memory_files()
        if target == "user":
            append_markdown_section("user", block)
            _publish_file_changed("user")
            return f"Appended section to {USER_MD}."
        append_markdown_section("memory", block)
        _publish_file_changed("memory")
        return f"Appended section to {MEMORY_MD}."


# ---------------------------------------------------------------------------
# memory_edit
# ---------------------------------------------------------------------------


class MemoryEditInput(BaseModel):
    file: str = Field(description="agent, user, memory, or events.")
    section_header: str = Field(
        default="",
        description="For replace: identify the section (first line or distinctive title). "
        "For append on memory: used as heading line.",
    )
    new_content: str = Field(description="New body text (or full replacement block for user replace).")
    mode: Literal["replace", "append"] = Field(
        default="append",
        description="replace = swap an existing section (not allowed for MEMORY.md). "
        "append = add a new section (memory) or append block (user).",
    )


class MemoryEditTool(BaseTool):
    name: str = "memory_edit"
    description: str = (
        "Edit USER.md or AGENT.md sections, or append to MEMORY.md. "
        "MEMORY.md is append-only: mode 'replace' is rejected; use append or memory_add. "
        "EVENTS.md cannot be edited with this tool. "
        "Requires confirmation for writes when enabled in settings."
    )
    args_schema: type[BaseModel] = MemoryEditInput

    event_queue: Optional[Any] = Field(default=None, exclude=True)
    command_queue: Optional[Any] = Field(default=None, exclude=True)
    confirmation_results_dict: Optional[Any] = Field(default=None, exclude=True)

    def __init__(
        self,
        event_queue=None,
        command_queue=None,
        confirmation_results_dict=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.event_queue = event_queue
        self.command_queue = command_queue
        self.confirmation_results_dict = confirmation_results_dict

    def _run(
        self,
        file: str = "memory",
        section_header: str = "",
        new_content: str = "",
        mode: str = "append",
        **kwargs,
    ) -> str:
        key = _normalize_file_key(file)
        if key == "events":
            return "Error: EVENTS.md is managed by the events log and distiller; do not edit with this tool."

        if key == "memory" and mode == "replace":
            return (
                "Error: MEMORY.md is append-only. Use mode 'append' or the memory_add tool "
                "instead of replace."
            )

        if not (new_content or "").strip():
            return "Error: new_content is required."

        path = _path_for(key)
        rel = path.name

        if not _confirm_mutation(
            event_queue=self.event_queue,
            confirmation_results_dict=self.confirmation_results_dict,
            title=f"Memory edit ({rel})",
            message=f"mode={mode}\n\n{new_content[:1200]}",
        ):
            return "Cancelled — file not modified."

        if key == "memory" and mode == "append":
            block = (
                f"### {section_header.strip()}\n\n{new_content.strip()}\n"
                if section_header.strip()
                else f"{new_content.strip()}\n"
            )
            append_markdown_section("memory", block)
            _publish_file_changed("memory")
            return f"Appended to {MEMORY_MD}."

        # user or agent: replace or append sections
        raw = _read_text(path)
        cap = (
            CAP_USER_BYTES
            if key == "user"
            else CAP_AGENT_BYTES
            if key == "agent"
            else CAP_MEMORY_BYTES
        )
        mode_cap = "sections" if key in ("user", "agent") else "suffix"

        if mode == "append":
            block = (
                f"### {section_header}\n\n{new_content.strip()}\n"
                if section_header.strip()
                else f"{new_content.strip()}\n"
            )
            if key == "user":
                append_markdown_section("user", block)
                _publish_file_changed("user")
                return f"Appended section to {USER_MD}."
            # agent
            new_text = raw.rstrip() + ("\n---\n" if raw.strip() else "") + block
            new_text = enforce_cap(new_text, CAP_AGENT_BYTES, "sections")
            atomic_write_text(path, new_text)
            _publish_file_changed("agent")
            return f"Appended section to {AGENT_MD}."

        # replace
        if not section_header.strip():
            return "Error: section_header is required for replace mode."

        needle = section_header.strip().lower()
        parts = SECTION_SPLIT_RE.split(raw)
        if len(parts) <= 1:
            parts = [raw]

        idx = -1
        for i, p in enumerate(parts):
            first = p.strip().split("\n", 1)[0].strip().lower()
            if needle in first or first in needle:
                idx = i
                break
        if idx < 0:
            return f"Error: no section matching {section_header!r} found in {rel}."

        parts[idx] = new_content.strip()
        new_text = "\n---\n".join(parts)
        new_text = enforce_cap(new_text.strip() + "\n", cap, mode_cap)
        atomic_write_text(path, new_text)
        _publish_file_changed(key)
        return f"Replaced section in {rel}."
