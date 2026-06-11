from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_board_header_actions_are_ordered_and_icon_only_for_sync_and_configure():
    html = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text(encoding="utf-8")
    actions = html[html.index('id="kb-board-actions"') : html.index('id="kb-delete-board"')]

    refresh_idx = actions.index('id="kb-refresh-boards"')
    configure_idx = actions.index('id="kb-edit-board"')
    add_idx = actions.index('id="kb-add-ticket"')
    divider_idx = actions.index('aria-hidden="true"')
    kanban_idx = actions.index('id="kb-view-kanban"')
    list_idx = actions.index('id="kb-view-list"')

    assert list_idx < kanban_idx < divider_idx < refresh_idx < configure_idx < add_idx
    assert 'id="kb-refresh-boards"' in actions
    assert 'id="kb-refresh-boards" class="hidden inline-flex' in actions
    assert 'id="kb-edit-board"' in actions
    assert 'id="kb-add-ticket"' in actions
    assert 'title="Re-sync external boards"' in actions
    assert 'title="Edit board"' in actions
    assert 'title="Add ticket"' in actions
    assert 'title="Kanban view"' in actions
    assert 'title="List view"' in actions
    assert "padding: 8px 16px" in html
    assert "kb-view-toggle svg" in html
    assert 'aria-label="Re-sync external boards"' in actions
    assert 'aria-label="Edit board"' in actions
    assert 'aria-label="Add ticket"' in actions
    assert "border border-white/20" not in actions[refresh_idx:configure_idx]
    assert "border border-white/20" not in actions[configure_idx:add_idx]
    assert "+ Add Ticket" not in actions
    assert "<span>Re-sync</span>" not in actions
    assert 'id="kb-edit-board-label" class="sr-only">Edit</span>' in actions
    assert 'id="kb-add-ticket-label" class="sr-only">Add ticket</span>' in actions
    assert ".kb-view-toggle + .kb-view-toggle" not in html


def test_board_has_list_view_surface_and_realtime_lane_move_refresh_contract():
    html = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")
    ticket_js = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text(encoding="utf-8")
    actions_py = (ROOT / "distr/core/initiative/action_handlers.py").read_text(encoding="utf-8")

    assert 'id="kb-ticket-list"' in html
    assert "function setBoardViewMode(mode)" in js
    assert "function renderTicketList(lanes, isLocal, boardData)" in js
    assert "ticketUi.createTicketListRow(ticket, isLocal, boardData)" in js
    assert "createTicketListRow: createTicketListRow" in ticket_js
    assert 'event_type="ticket_lane_move"' in actions_py
    assert "increment_kanban_updated(" in actions_py


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
