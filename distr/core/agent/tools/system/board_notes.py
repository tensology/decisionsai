"""Agent tool for ticket board scratchpad notes (kanban notes tab)."""

from __future__ import annotations

from typing import Literal

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class BoardNotesInput(BaseModel):
    action: Literal["list", "create", "update", "append", "delete"] = Field(
        default="list",
        description=(
            "list: read all ticket board notes. "
            "create: new note with title and content. "
            "update: change title and/or content on an existing note (note_id or title). "
            "append: add text to an existing note by note_id or title, or create if missing. "
            "delete: remove a note by note_id or title."
        ),
    )
    title: str = Field(
        default="",
        description="Title for create, or title match for append/update/delete when note_id is omitted.",
    )
    content: str = Field(
        default="",
        description="Note body. Required for create and update; optional for append (append still needs content).",
    )
    note_id: str = Field(
        default="",
        description="Existing note id for update, append, or delete.",
    )


class BoardNotesTool(BaseTool):
    name: str = "board_notes"
    description: str = (
        "Read, create, edit, or delete ticket board notes — the scratchpad tabs in the "
        "kanban/ticket board area. Not MEMORY.md and not per-ticket context notes. "
        "Actions: list (read all), create (new note), update (edit title/body), "
        "append (add to existing note), delete (remove a note). "
        "Use note_id from list, or match by title when the user names a note."
    )
    args_schema: type[BaseModel] = BoardNotesInput

    def _run(
        self,
        action: str = "list",
        title: str = "",
        content: str = "",
        note_id: str = "",
        **kwargs,
    ) -> str:
        from distr.core.kanban.board_notes import (
            append_board_note,
            create_board_note,
            delete_board_note,
            find_board_note_by_title,
            load_board_notes,
            update_board_note,
        )

        action_name = (action or "list").strip().lower()

        if action_name == "list":
            notes = load_board_notes()
            if not notes:
                return "No ticket board notes yet."
            lines = ["Ticket board notes:"]
            for note in notes[:20]:
                title_text = note.get("title") or "Untitled"
                body = (note.get("content") or "").strip()
                preview = " ".join(body.split())
                if len(preview) > 400:
                    preview = preview[:399].rstrip() + "…"
                lines.append(f"- [{note.get('id')}] {title_text}: {preview or '(empty)'}")
            return "\n".join(lines)

        if action_name == "delete":
            note = _resolve_note_target(
                note_id=(note_id or "").strip(),
                title=(title or "").strip(),
                find_board_note_by_title=find_board_note_by_title,
            )
            if not note:
                return "Error: note_id or title is required for delete."
            if delete_board_note(note["id"]):
                return f"Deleted ticket board note '{note.get('title')}' (id={note['id']})."
            return f"Error: no ticket board note with id '{note['id']}'."

        body = (content or "").strip()
        if action_name in {"create", "append"} and not body:
            return "Error: content is required for create and append."

        if action_name == "create":
            note_title = (title or "").strip() or _title_from_content(body)
            note = create_board_note(title=note_title, content=body)
            return (
                f"Created ticket board note '{note.get('title')}' "
                f"(id={note.get('id')})."
            )

        if action_name == "update":
            lookup_title = (title or "").strip()
            explicit_id = (note_id or "").strip()
            note = _resolve_note_target(
                note_id=explicit_id,
                title=lookup_title,
                find_board_note_by_title=find_board_note_by_title,
            )
            if not note:
                return "Error: note_id or title is required for update."
            rename = lookup_title if explicit_id else None
            if not body and not rename:
                return "Error: provide content and/or a new title to update."
            updated = update_board_note(
                note["id"],
                title=rename,
                content=body if body else None,
            )
            if not updated:
                return f"Error: no ticket board note with id '{note['id']}'."
            return f"Updated ticket board note '{updated.get('title')}' (id={note['id']})."

        if action_name == "append":
            note = append_board_note(
                body,
                note_id=(note_id or "").strip(),
                title=(title or "").strip(),
            )
            return (
                f"Saved to ticket board note '{note.get('title')}' "
                f"(id={note.get('id')})."
            )

        return f"Error: unknown action '{action_name}'. Use list, create, update, append, or delete."

    async def _arun(
        self,
        action: str = "list",
        title: str = "",
        content: str = "",
        note_id: str = "",
        **kwargs,
    ) -> str:
        return self._run(
            action=action,
            title=title,
            content=content,
            note_id=note_id,
            **kwargs,
        )


def _resolve_note_target(
    *,
    note_id: str,
    title: str,
    find_board_note_by_title,
):
    from distr.core.kanban.board_notes import load_board_notes

    if note_id:
        for note in load_board_notes():
            if note.get("id") == note_id:
                return note
        return None
    if title:
        return find_board_note_by_title(title)
    return None


def _title_from_content(content: str) -> str:
    first_line = (content or "").strip().splitlines()[0] if content else ""
    compact = " ".join(first_line.split())
    if not compact:
        return "Untitled"
    if len(compact) <= 72:
        return compact
    return compact[:71].rstrip() + "…"
