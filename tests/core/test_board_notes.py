from pathlib import Path

from distr.core.developer_context import build_developer_context
from distr.core.initiative.context import ContextAssembler
from distr.core.kanban import board_notes as board_notes_module
from distr.core.kanban.board_notes import (
    BOARD_NOTES_FILE,
    format_board_notes_for_prompt,
    load_board_notes,
    save_board_notes,
)


def test_format_board_notes_for_prompt_includes_content():
    notes = [{"id": "a", "title": "Planning", "content": "Ship invoice flow today."}]
    text = format_board_notes_for_prompt(notes)
    assert "Ticket board notes:" in text
    assert "Planning" in text
    assert "Ship invoice flow today." in text


def test_developer_context_includes_board_notes(monkeypatch, tmp_path):
    notes_file = tmp_path / "kanban_board_notes.json"
    monkeypatch.setattr(board_notes_module, "BOARD_NOTES_FILE", notes_file)
    save_board_notes([{"id": "1", "title": "Week focus", "content": "RelightSA billing"}])
    context = build_developer_context()
    assert context.board_notes
    prompt = context.to_prompt_text(max_chars=4000)
    assert "Ticket board notes:" in prompt
    assert "Week focus" in prompt


def test_initiative_bundle_includes_board_notes(monkeypatch, tmp_path):
    notes_file = tmp_path / "kanban_board_notes.json"
    monkeypatch.setattr(board_notes_module, "BOARD_NOTES_FILE", notes_file)
    save_board_notes([{"id": "2", "title": "Calls", "content": "Follow up with Paul"}])
    bundle = ContextAssembler().build({})
    assert bundle.board_notes
    assert bundle.board_notes[0]["title"] == "Calls"


def test_save_and_load_board_notes_roundtrip(monkeypatch, tmp_path):
    notes_file = tmp_path / "kanban_board_notes.json"
    monkeypatch.setattr(board_notes_module, "BOARD_NOTES_FILE", notes_file)

    save_board_notes([{"id": "x", "title": "One", "content": "Body", "modified_at": "t"}])
    loaded = load_board_notes()
    assert loaded[0]["title"] == "One"
    assert notes_file.exists()
