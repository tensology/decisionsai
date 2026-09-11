"""Ticket board sidebar notes — per-board companion memory with legacy global fallback."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

from distr.core.paths import DB_DIR
from distr.core.db.time import utc_now_naive

# Legacy settings key kept only for one-time import from old broken storage attempts.
KANBAN_BOARD_NOTES_SETTINGS_KEY = "kanban_sidebar_documents"
BOARD_NOTES_FILE = Path(DB_DIR) / "kanban_board_notes.json"


def normalize_board_note(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    note_id = str(item.get("id") or "").strip()
    if not note_id:
        return None
    return {
        "id": note_id,
        "title": str(item.get("title") or "Untitled").strip() or "Untitled",
        "content": str(item.get("content") or ""),
        "modified_at": item.get("modified_at"),
    }


def _import_legacy_settings_notes() -> list[dict[str, Any]]:
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        raw = settings.get(KANBAN_BOARD_NOTES_SETTINGS_KEY)
        if not isinstance(raw, list):
            return []
        cleaned = []
        for item in raw:
            note = normalize_board_note(item)
            if note:
                cleaned.append(note)
        return cleaned
    except Exception:
        return []


def _load_legacy_global_notes() -> list[dict[str, Any]]:
    if BOARD_NOTES_FILE.exists():
        try:
            raw = json.loads(BOARD_NOTES_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                cleaned = [normalize_board_note(item) for item in raw]
                return [note for note in cleaned if note]
            if isinstance(raw, dict):
                # ponytail: keyed store {board_id: [notes]}
                merged: list[dict[str, Any]] = []
                for value in raw.values():
                    if isinstance(value, list):
                        for item in value:
                            note = normalize_board_note(item)
                            if note:
                                merged.append(note)
                return merged
        except Exception:
            pass

    legacy = _import_legacy_settings_notes()
    if legacy:
        save_board_notes(legacy)
    return legacy


def _active_md_path(board_id: int) -> Path:
    from distr.core.workspace_memory.paths import companion_memory_file

    return companion_memory_file("boards", board_id, "active.md")


def _notes_from_active_md(text: str) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    if not (text or "").strip():
        return notes
    chunks = re.split(r"\n## ", text)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or chunk.startswith("# Board"):
            continue
        if chunk.startswith("## "):
            chunk = chunk[3:]
        lines = chunk.splitlines()
        title = (lines[0] if lines else "Untitled").strip() or "Untitled"
        body = "\n".join(lines[1:]).strip()
        if body == "(empty)":
            body = ""
        notes.append(
            {
                "id": secrets.token_hex(8),
                "title": title,
                "content": body,
                "modified_at": utc_now_naive().isoformat() + "Z",
            }
        )
    return notes


def _active_md_from_notes(notes: list[dict[str, Any]]) -> str:
    lines = ["# Board notes", ""]
    for note in notes:
        title = note.get("title") or "Untitled"
        body = (note.get("content") or "").strip()
        lines.append(f"## {title}")
        lines.append(body or "(empty)")
        lines.append("")
    lines.append(f"_updated: {utc_now_naive().isoformat()}Z_")
    return "\n".join(lines).strip() + "\n"


def _save_board_notes_to_companion(board_id: int, notes: list[dict[str, Any]]) -> None:
    try:
        from distr.core.workspace_memory.provision import bootstrap_board

        bootstrap_board(board_id)
    except Exception:
        pass
    path = _active_md_path(board_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_active_md_from_notes(notes), encoding="utf-8")


def load_board_notes(board_id: int | None = None) -> list[dict[str, Any]]:
    if board_id is not None:
        path = _active_md_path(int(board_id))
        if path.is_file():
            return _notes_from_active_md(path.read_text(encoding="utf-8", errors="replace"))
        legacy = _load_legacy_global_notes()
        if legacy:
            _save_board_notes_to_companion(int(board_id), legacy)
            return legacy
        return []
    return _load_legacy_global_notes()


def save_board_notes(notes: list[dict[str, Any]], board_id: int | None = None) -> None:
    cleaned = [normalize_board_note(note) for note in notes]
    cleaned = [note for note in cleaned if note]
    if board_id is not None:
        _save_board_notes_to_companion(int(board_id), cleaned)
        return
    BOARD_NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOARD_NOTES_FILE.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_board_note(*, title: str = "Untitled", content: str = "", board_id: int | None = None) -> dict[str, Any]:
    note = {
        "id": secrets.token_hex(8),
        "title": (title or "Untitled").strip() or "Untitled",
        "content": content or "",
        "modified_at": utc_now_naive().isoformat() + "Z",
    }
    notes = load_board_notes(board_id=board_id)
    notes.append(note)
    save_board_notes(notes, board_id=board_id)
    return note


def update_board_note(
    note_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    board_id: int | None = None,
) -> dict[str, Any] | None:
    notes = load_board_notes(board_id=board_id)
    idx = next((i for i, note in enumerate(notes) if note.get("id") == note_id), None)
    if idx is None:
        return None
    note = dict(notes[idx])
    if title is not None:
        note["title"] = title.strip() or "Untitled"
    if content is not None:
        note["content"] = content
    note["modified_at"] = utc_now_naive().isoformat() + "Z"
    notes[idx] = note
    save_board_notes(notes, board_id=board_id)
    return note


def delete_board_note(note_id: str, board_id: int | None = None) -> bool:
    notes = load_board_notes(board_id=board_id)
    next_notes = [note for note in notes if note.get("id") != note_id]
    if len(next_notes) == len(notes):
        return False
    save_board_notes(next_notes, board_id=board_id)
    return True


def find_board_note_by_title(title: str, board_id: int | None = None) -> dict[str, Any] | None:
    """Return the first note whose title contains the needle (case-insensitive)."""
    needle = (title or "").strip().lower()
    if not needle:
        return None
    for note in load_board_notes(board_id=board_id):
        hay = (note.get("title") or "").lower()
        if needle in hay or hay in needle:
            return note
    return None


def append_board_note(
    content: str,
    *,
    note_id: str = "",
    title: str = "",
    board_id: int | None = None,
) -> dict[str, Any]:
    """Append text to a note, or create one when no match exists."""
    text = (content or "").strip()
    if not text:
        raise ValueError("content is required")

    note: dict[str, Any] | None = None
    if note_id:
        note = next((n for n in load_board_notes(board_id=board_id) if n.get("id") == note_id), None)
    elif title:
        note = find_board_note_by_title(title, board_id=board_id)

    if note:
        existing = (note.get("content") or "").rstrip()
        combined = f"{existing}\n\n{text}".strip() if existing else text
        updated = update_board_note(note["id"], content=combined, board_id=board_id)
        return updated or note

    create_title = (title or "").strip() or _title_from_first_line(text)
    return create_board_note(title=create_title, content=text, board_id=board_id)


def _title_from_first_line(text: str) -> str:
    first_line = (text or "").strip().splitlines()[0] if text else ""
    compact = " ".join(first_line.split())
    if not compact:
        return "Untitled"
    if len(compact) <= 72:
        return compact
    return compact[:71].rstrip() + "…"


def _one_line(text: str, max_chars: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def format_board_notes_for_prompt(
    notes: list[dict[str, Any]] | None,
    *,
    max_notes: int = 12,
    max_content_chars: int = 500,
) -> str:
    """Compact text block for orchestrator and planning prompts."""
    cleaned = [normalize_board_note(note) for note in (notes or [])]
    cleaned = [note for note in cleaned if note and (note.get("title") or note.get("content"))]
    if not cleaned:
        return ""
    lines = ["Ticket board notes:"]
    for note in cleaned[:max_notes]:
        title = note.get("title") or "Untitled"
        body = _one_line(str(note.get("content") or ""), max_content_chars)
        if body:
            lines.append(f"- {title}: {body}")
        else:
            lines.append(f"- {title}: (empty)")
    return "\n".join(lines)
