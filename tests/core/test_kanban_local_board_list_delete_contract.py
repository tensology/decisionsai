from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KANBAN_JS = ROOT / "distr/gui/web/static/kanban/js/kanban.js"
KANBAN_HTML = ROOT / "distr/gui/web/templates/kanban/kanban.html"


def test_local_board_sidebar_uses_in_use_dot_and_delete_icon_only_for_database_boards():
    js = KANBAN_JS.read_text(encoding="utf-8")
    html = KANBAN_HTML.read_text(encoding="utf-8")

    assert "kb-board-item-wrapper" in html
    assert "kb-board-in-use-dot" in js
    assert "kb-board-item-delete" in html
    assert "IN USE" not in js
    assert "confirmDeleteLocalBoardById" in js
    assert "showKanbanConfirm" in js
    assert 'e.key === "Delete"' in js
    render_local = js[js.index("function renderSidebarBoards") : js.index("function renderExternalBoards")]
    assert "kb-board-item-wrapper" in render_local
    assert "kb-board-item-delete" in render_local
    assert "confirmDeleteLocalBoardById(b.id)" in render_local

    render_external = js[js.index("function renderExternalBoards") : js.index("// ── External board context menu")]
    assert "kb-board-item-delete" not in render_external
