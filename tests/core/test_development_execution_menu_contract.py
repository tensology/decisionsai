from pathlib import Path
from tests.development_assets import development_assets, development_template


ROOT = Path(__file__).resolve().parents[2]


def test_workflows_execution_menu_exposes_open_ticket_respond_and_kind_routed_cancel():
    source = development_assets(".js")

    assert 'aria-label="Execution actions"' in source
    assert '>Open</a>' in source
    assert '>Related ticket</a>' in source
    assert '>Continue / Respond</a>' in source
    assert "run.cancellation_target?.url" in source
    assert "api(button.dataset.activeExecutionCancel" in source


def test_kanban_execution_menu_exposes_open_ticket_respond_and_kind_routed_cancel():
    source = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")
    template = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text(encoding="utf-8")

    assert 'aria-label="Execution actions"' in source
    assert '>Open</a>' in source
    assert '>Related ticket</a>' in source
    assert '>Continue / Respond</a>' in source
    assert "data.cancellation_target" in source
    assert 'apiFetch(cancelUrl, { method: "POST" })' in source
    assert ".kb-run-popover {" in template
    assert "background: #1a2550;" in template
    assert ".kb-run-popover .kb-run-actions > div" in template


def test_kanban_list_rows_surface_active_execution_badges():
    ticket_source = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text(encoding="utf-8")
    kanban_source = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert 'workflowStatusBadgeHtml' in ticket_source
    list_block = ticket_source.split("function createTicketListRow", 1)[1].split("return row;", 1)[0]
    assert 'ticket.workflow_status' in list_block
    assert 'kb-wf-status-badge' in list_block
    assert '.kb-ticket-list-row[data-ticket-id=' in kanban_source


def test_kanban_reapplies_active_execution_badges_after_every_board_render():
    kanban_source = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    render_block = kanban_source.split("function renderBoardTickets", 1)[1].split(
        "function refreshCurrentBoardRealtime", 1
    )[0]
    assert "syncVisibleActiveRunBadges()" in render_block
    assert 'apiFetch("/api/workflows/active-runs?limit=200")' in kanban_source
    assert "setTicketWorkflowStatusOnCard(run.ticket_id, run.status)" in kanban_source


def test_kanban_updates_every_retained_ticket_view_and_guards_stale_popovers():
    kanban_source = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    status_block = kanban_source.split("function setTicketWorkflowStatusOnCard", 1)[1].split(
        "function openSendWorkflowModal", 1
    )[0]
    popover_block = kanban_source.split("function showRunPopover", 1)[1].split(
        "function cancelTicketRun", 1
    )[0]
    assert "document.querySelectorAll(" in status_block
    assert "cards.forEach(function(card)" in status_block
    assert "_runPopoverRequestGeneration" in popover_block
    assert "requestGeneration !== _runPopoverRequestGeneration" in popover_block
    assert "!badgeEl.isConnected" in popover_block
