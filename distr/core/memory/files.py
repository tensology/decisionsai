"""Persistent memory markdown files (R8) — caps, atomic writes, context snippets."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

AGENT_MD: Final = "AGENT.md"
USER_MD: Final = "USER.md"
MEMORY_MD: Final = "MEMORY.md"
EVENTS_MD: Final = "EVENTS.md"

CAP_AGENT_BYTES: Final = 128 * 1024
CAP_USER_BYTES: Final = 100 * 1024
CAP_MEMORY_BYTES: Final = 200 * 1024
CAP_EVENTS_BYTES: Final = 1024 * 1024

SECTION_SPLIT_RE = re.compile(r"\n-{3,}\s*\n")
SECTION_JOIN = "\n---\n"

CTX_AGENT_MAX_BYTES: Final = 5 * 1024
CTX_AGENT_MAX_LINES: Final = 100
CTX_USER_MAX_LINES: Final = 20
CTX_MEMORY_MAX_BYTES: Final = 8 * 1024
CTX_MEMORY_MAX_SECTIONS: Final = 5

DEFAULT_AGENT_BODY = """# Agent identity

You are the Decisions assistant: concise, accurate, and respectful of user boundaries.
Follow the product safety and tooling rules from the main system prompt.
"""

DEFAULT_USER_BODY = """# User preferences

(No entries yet. Preferences will be appended as `---`-delimited sections.)
"""

DEFAULT_MEMORY_BODY = """# Long-term memory

(No distilled memories yet. Entries append below, separated by `---`.)
"""

DEFAULT_EVENTS_BODY = """# Events log

(Raw timeline for distillation. One event per line or short block.)
"""


def default_memory_dir() -> Path:
    from distr.core.paths import MEMORY_FILES_DIR

    p = Path(MEMORY_FILES_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def memory_paths(root: Path | None = None) -> dict[str, Path]:
    base = root if root is not None else default_memory_dir()
    return {
        "agent": base / AGENT_MD,
        "user": base / USER_MD,
        "memory": base / MEMORY_MD,
        "events": base / EVENTS_MD,
    }


def _read_utf8(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.warning("memory file read failed: %s", path, exc_info=True)
        return ""


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write *text* via temp file + os.replace (atomic on same volume)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode(encoding)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mem_", suffix=".tmp")
    try:
        os.write(fd, data)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if os.path.isfile(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def prune_sections_drop_oldest(text: str, max_bytes: int) -> str:
    """Keep newest `---`-delimited sections until under *max_bytes* (UTF-8)."""
    s = text.strip()
    if not s:
        return s
    parts = SECTION_SPLIT_RE.split(s)
    if len(parts) <= 1:
        return prune_suffix_bytes(parts[0], max_bytes) if parts else ""
    while len(parts) > 1 and _utf8_len(SECTION_JOIN.join(parts)) > max_bytes:
        parts.pop(0)
    joined = SECTION_JOIN.join(parts)
    return prune_suffix_bytes(joined, max_bytes) if _utf8_len(joined) > max_bytes else joined


def prune_suffix_bytes(text: str, max_bytes: int) -> str:
    """Keep the tail of *text* up to *max_bytes* UTF-8 octets."""
    b = text.encode("utf-8")
    if len(b) <= max_bytes:
        return text
    return b[-max_bytes:].decode("utf-8", errors="replace")


def prune_lines_drop_oldest(text: str, max_bytes: int) -> str:
    """Drop oldest lines until UTF-8 size <= max_bytes."""
    lines = text.splitlines()
    body = "\n".join(lines)
    while lines and _utf8_len(body) > max_bytes:
        lines.pop(0)
        body = "\n".join(lines)
    return body + ("\n" if text.endswith("\n") and body else "")


def enforce_cap(text: str, max_bytes: int, mode: str) -> str:
    if _utf8_len(text) <= max_bytes:
        return text
    if mode == "sections":
        return prune_sections_drop_oldest(text, max_bytes)
    if mode == "lines":
        return prune_lines_drop_oldest(text, max_bytes)
    if mode == "suffix":
        return prune_suffix_bytes(text, max_bytes)
    return prune_suffix_bytes(text, max_bytes)


def extract_agent_seed_from_template(template: str) -> str:
    """Best-effort identity slice for AGENT.md; never raises."""
    if not template or not template.strip():
        return DEFAULT_AGENT_BODY
    m = re.search(
        r"(?is)^#{1,6}\s*(identity|agent identity|who you are|your role)\b.*?(?=^#{1,6}\s|\Z)",
        template,
        re.MULTILINE,
    )
    if m:
        chunk = m.group(0).strip()
        return chunk[:32000] if chunk else DEFAULT_AGENT_BODY
    lines = template.splitlines()
    head = "\n".join(lines[:120]).strip()
    return head[:32000] if head else DEFAULT_AGENT_BODY


def ensure_memory_files(
    *,
    root: Path | None = None,
    system_prompt_template: str | None = None,
) -> Path:
    """
    Ensure directory and four files exist with safe defaults.

    AGENT.md is seeded from *system_prompt_template* when provided and the file
    is missing; otherwise a minimal default. Never fails fatally.
    """
    paths = memory_paths(root)
    base = paths["agent"].parent
    base.mkdir(parents=True, exist_ok=True)

    agent_path = paths["agent"]
    if not agent_path.is_file():
        body = DEFAULT_AGENT_BODY
        if system_prompt_template:
            try:
                body = extract_agent_seed_from_template(system_prompt_template)
            except Exception:
                logger.warning("ensure_memory_files: agent seed extraction failed", exc_info=True)
        atomic_write_text(agent_path, enforce_cap(body, CAP_AGENT_BYTES, "suffix"))

    if not paths["user"].is_file():
        atomic_write_text(paths["user"], DEFAULT_USER_BODY.strip())
    if not paths["memory"].is_file():
        atomic_write_text(paths["memory"], DEFAULT_MEMORY_BODY.strip())
    if not paths["events"].is_file():
        atomic_write_text(paths["events"], DEFAULT_EVENTS_BODY.strip())

    return base


def append_markdown_section(
    key: str,
    section_body: str,
    *,
    root: Path | None = None,
) -> None:
    """Append a `---`-delimited section to USER.md or MEMORY.md with caps."""
    paths = memory_paths(root)
    if key == "user":
        path, cap, mode = paths["user"], CAP_USER_BYTES, "sections"
    elif key == "memory":
        path, cap, mode = paths["memory"], CAP_MEMORY_BYTES, "sections"
    else:
        raise ValueError("append_markdown_section key must be 'user' or 'memory'")

    ensure_memory_files(root=root)
    block = section_body.strip()
    if not block:
        return
    cur = _read_utf8(path)
    sep = "" if not cur.strip() else "\n---\n"
    new_text = f"{cur.rstrip()}{sep}{block}\n"
    new_text = enforce_cap(new_text, cap, mode)
    atomic_write_text(path, new_text)


def append_events_text(chunk: str, *, root: Path | None = None) -> None:
    """Append text to EVENTS.md with line-oriented oldest pruning."""
    paths = memory_paths(root)
    ensure_memory_files(root=root)
    cur = _read_utf8(paths["events"])
    addition = chunk.strip()
    if not addition:
        return
    new_text = cur.rstrip() + ("\n\n" if cur.strip() else "") + addition + "\n"
    new_text = enforce_cap(new_text, CAP_EVENTS_BYTES, "lines")
    atomic_write_text(paths["events"], new_text)


def _tail_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


def _last_sections(text: str, max_sections: int) -> str:
    s = text.strip()
    if not s:
        return ""
    parts = SECTION_SPLIT_RE.split(s)
    if len(parts) <= max_sections:
        return s
    tail = parts[-max_sections:]
    return SECTION_JOIN.join(tail)


def load_context_snippets_for_llm(*, root: Path | None = None) -> dict[str, str]:
    """
    Trimmed snippets for initiative / supplemental context (not full files).

    MEMORY.md uses last sections / byte cap until LlamaIndex search exists (R10).
    """
    paths = memory_paths(root)
    ensure_memory_files(root=root)

    agent_raw = _read_utf8(paths["agent"])
    agent = _tail_lines(agent_raw, CTX_AGENT_MAX_LINES)
    agent = prune_suffix_bytes(agent, CTX_AGENT_MAX_BYTES)

    user_raw = _read_utf8(paths["user"])
    user = _tail_lines(user_raw, CTX_USER_MAX_LINES)

    mem_raw = _read_utf8(paths["memory"])
    mem = _last_sections(mem_raw, CTX_MEMORY_MAX_SECTIONS)
    mem = prune_suffix_bytes(mem, CTX_MEMORY_MAX_BYTES)

    return {"agent": agent.strip(), "user": user.strip(), "memory": mem.strip()}


def try_load_system_prompt_template() -> str | None:
    try:
        from distr.core.agent.services.llm.prompt import load_system_prompt_template

        return load_system_prompt_template()
    except Exception:
        logger.debug("try_load_system_prompt_template: unavailable", exc_info=True)
        return None
