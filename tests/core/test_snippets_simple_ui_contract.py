from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REMOTE_ROOT = ROOT.parent / "www.decisionsai.net" / "remote-app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_main_snippets_ui_uses_simple_editable_snippet_field():
    template = read(ROOT / "distr/gui/web/templates/snippets/snippets.html")
    js = read(ROOT / "distr/gui/web/static/snippets/js/snippets.js")

    assert "<textarea" not in js
    assert "type=\\\"text\\\"" in js
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in template
    assert "grid-template-columns: 28px minmax(0, 1fr) minmax(88px, 108px) 66px 38px" in template
    assert "key-badge" in template
    assert "formatHotkeyBadges" in js
    assert "ctrl+shift+f\" + (idx + 1)" in js
    assert "nextDefaultHotkey" in js
    assert "Backspace" in js
    assert "trashIcon" in js
    assert "deleteSnippet(id)" in js
    assert "No hotkey" in js
    assert "Title" not in template
    assert "Description" not in template


def test_remote_snippets_ui_does_not_use_textareas_for_snippets():
    if not REMOTE_ROOT.is_dir():
        pytest.skip("optional www.decisionsai.net sibling checkout is not present")
    snippets_tab = read(REMOTE_ROOT / "src/components/tabs/SnippetsTab.jsx")
    remote_view = read(REMOTE_ROOT / "src/components/RemoteView.jsx")

    assert "<textarea" not in snippets_tab
    assert "SnippetEditor" in snippets_tab
    assert "RemoteSnippetEditor" in remote_view
    assert "id=\"remote-snippet-text\"" in remote_view


def test_new_snippets_default_to_first_free_control_shift_number_hotkey():
    from distr.gui.web.routes.settings.snippets import _next_default_remote_hotkey

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self, rows):
            self._rows = rows

        def query(self, _field):
            return FakeQuery(self._rows)

    class FakeSnippet:
        remote_hotkey = object()

    assert _next_default_remote_hotkey(FakeSession([]), FakeSnippet) == "ctrl+shift+1"
    assert _next_default_remote_hotkey(
        FakeSession([("ctrl+shift+1",), ("ctrl+shift+2",)]),
        FakeSnippet,
    ) == "ctrl+shift+3"
