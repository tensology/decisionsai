"""Tests for ticket board notes helpers and agent tool."""

from pathlib import Path

import pytest

from distr.core.kanban import board_notes as board_notes_module
from distr.core.kanban.board_notes import (
    append_board_note,
    create_board_note,
    find_board_note_by_title,
    load_board_notes,
    save_board_notes,
)


@pytest.fixture
def notes_file(monkeypatch, tmp_path):
    path = tmp_path / "kanban_board_notes.json"
    monkeypatch.setattr(board_notes_module, "BOARD_NOTES_FILE", path)
    return path


def test_append_board_note_creates_when_missing(notes_file):
    note = append_board_note("First line about billing", title="Billing")
    assert note["title"] == "Billing"
    assert "billing" in note["content"].lower()
    assert len(load_board_notes()) == 1


def test_append_board_note_adds_to_existing_by_title(notes_file):
    create_board_note(title="Week focus", content="Start with RelightSA")
    note = append_board_note("Follow up with Paul", title="Week")
    assert "RelightSA" in note["content"]
    assert "Paul" in note["content"]
    assert len(load_board_notes()) == 1


def test_find_board_note_by_title_partial_match(notes_file):
    save_board_notes([{"id": "a", "title": "Client calls", "content": "x"}])
    found = find_board_note_by_title("calls")
    assert found is not None
    assert found["id"] == "a"


def test_board_notes_tool_create_and_list(notes_file):
    from distr.core.agent.tools.system.board_notes import BoardNotesTool

    tool = BoardNotesTool()
    created = tool._run(
        action="create",
        title="Deploy plan",
        content="Ship kanban notes tool tonight.",
    )
    assert "Created ticket board note" in created
    listed = tool._run(action="list")
    assert "Deploy plan" in listed
    assert "kanban notes tool" in listed


def test_board_notes_tool_update_and_delete(notes_file):
    from distr.core.agent.tools.system.board_notes import BoardNotesTool

    tool = BoardNotesTool()
    created = tool._run(action="create", title="Billing", content="Invoice Paul")
    note_id = created.split("id=")[-1].rstrip(").")
    updated = tool._run(action="update", note_id=note_id, title="Billing v2", content="Invoice Paul tomorrow")
    assert "Updated ticket board note" in updated
    assert "Billing v2" in updated
    deleted = tool._run(action="delete", title="Billing v2")
    assert "Deleted ticket board note" in deleted
    assert tool._run(action="list") == "No ticket board notes yet."
