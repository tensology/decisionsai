from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_list_view_sorts_tickets_by_semantic_complexity_and_priority_ranks():
    ticket_js = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text(encoding="utf-8")
    kanban_js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert "function compareTicketsForListView" in ticket_js
    assert "extra_high: 5, high: 4, medium: 3, low: 1" in ticket_js
    assert "critical: 5, high: 4, medium: 3, low: 1" in ticket_js
    assert "compareTicketsForListView: compareTicketsForListView" in ticket_js

    render_list = kanban_js[kanban_js.index("function renderTicketList") : kanban_js.index("/** Push a local ticket to CLI")]
    assert "compareTicketsForListView" in render_list
    assert ".sort(window.KanbanTicketUi.compareTicketsForListView)" in render_list
