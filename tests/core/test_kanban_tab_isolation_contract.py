from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_kanban_has_explicit_tab_isolation_resets():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert "function hideKanbanFloatingUi()" in js
    assert "function resetMessagesSurfaceForBoardMode()" in js
    assert "function resetBoardSurfaceForMessagesMode()" in js
    assert '"kb-wa-media-lightbox"' in js
    assert "waSelectionMode = false" in js
    assert "waSidebarChatListMode = false" in js
    assert "hideAllKanbanModals()" in js


def test_sidebar_switch_calls_isolation_resets_before_showing_next_panel():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban_board.js").read_text(encoding="utf-8")

    messages_idx = js.index("deps.resetBoardSurfaceForMessagesMode")
    show_messages_idx = js.index('messagesPanel.classList.remove("hidden")')
    boards_idx = js.index("deps.resetMessagesSurfaceForBoardMode")
    show_boards_idx = js.index('ticketsPanel.classList.remove("hidden")')

    assert messages_idx < show_messages_idx
    assert boards_idx < show_boards_idx

