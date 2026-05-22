from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_board_header_actions_are_ordered_and_icon_only_for_sync_and_configure():
    html = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text(encoding="utf-8")
    actions = html[html.index('id="kb-board-actions"') : html.index('id="kb-delete-board"')]

    refresh_idx = actions.index('id="kb-refresh-boards"')
    configure_idx = actions.index('id="kb-edit-board"')
    add_idx = actions.index('id="kb-add-ticket"')

    assert refresh_idx < configure_idx < add_idx
    assert 'id="kb-refresh-boards"' in actions
    assert 'id="kb-edit-board"' in actions
    assert 'title="Re-sync external boards"' in actions
    assert 'title="Configure board"' in actions
    assert 'aria-label="Re-sync external boards"' in actions
    assert 'aria-label="Configure board"' in actions
    assert "border border-white/20" not in actions[refresh_idx:configure_idx]
    assert "border border-white/20" not in actions[configure_idx:add_idx]
    assert "<span>Re-sync</span>" not in actions
    assert 'id="kb-edit-board-label" class="sr-only"' in actions


def test_selected_local_board_delete_uses_shared_confirm_modal_and_enter_confirm():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert "function confirmDeleteCurrentLocalBoard()" in js
    assert 'title: "Delete board"' in js
    assert 'confirmLabel: "Delete"' in js
    assert "deleteLocalBoardById(boardId)" in js
    assert "function shouldOpenSelectedBoardDeleteConfirm(e)" in js
    assert 'e.key !== "Delete"' in js
    assert "isKeyboardEditingTarget(e.target)" in js
    assert "isMessagesPanelVisible()" in js
    assert "confirmDeleteCurrentLocalBoard();" in js
    assert 'var confirmKeys = e.key === "Enter" || e.key === "Delete";' in js


def test_board_context_menu_delete_uses_same_confirmation_path():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban_board.js").read_text(encoding="utf-8")

    assert "function runDeleteBoardConfirm(boardId, boardName, errorPrefix)" in js
    assert "deps.showKanbanConfirm({" in js
    assert 'title: "Delete board"' in js
    assert 'confirmLabel: "Delete"' in js
    assert "runDeleteBoardConfirm(boardId, name, \"Delete failed\");" in js
