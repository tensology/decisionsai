from pathlib import Path
from tests.development_assets import development_assets, development_template

from fastapi.testclient import TestClient

from distr.gui.web.server import create_app


ROOT = Path(__file__).resolve().parents[2]


def test_development_kanban_exposes_legacy_board_and_ticket_controls():
    html = development_template()
    js = development_assets(".js")

    for control in (
        'id="development-kanban-view-board"',
        'id="development-kanban-view-list"',
        'id="development-kanban-refresh"',
        'id="board-context-archive"',
        'id="board-context-open-folder"',
        'data-ticket-menu-action="copy"',
        'data-ticket-menu-action="workflow"',
        'data-ticket-menu-action="agent"',
        'id="kanban-ticket-notes-tab"',
        'id="kanban-ticket-attachments-tab"',
        'id="kanban-ticket-todos-tab"',
    ):
        assert control in html

    for behavior in (
        "setKanbanViewMode('list')",
        "compareKanbanListTickets",
        "forceRefreshKanbanBoard",
        "copyKanbanTicketDetails",
        "/active-run",
        "/send-to-workflow",
        "/send-to-cli",
        "/send-to-project",
        "closeBoardContextMenu();",
        "/open-folder",
    ):
        assert behavior in js

    assert "return 'Finder'" in js
    assert "return 'Explorer'" in js
    assert "showSource = source && String(source).toLowerCase() !== String(board.provider || '').toLowerCase()" in js
    assert "decisions-development-collapsed-boards" in js
    assert "collapsedBoardKeys: new Set()" in js
    assert "function initializeKanbanListMarquees" in js
    assert "track.scrollWidth <= description.clientWidth + 1" in js
    assert "function formatKanbanDuration" in js
    assert "kanban-list-time" in js
    assert "development-kanban-card-title" in js
    assert "kanban-run-status" in js
    assert "kanban-run-status-placeholder" in js
    assert "label.toLowerCase()" in js
    assert '${escapeHtml(status)}</button>' not in js
    assert "layout === 'list' ? '' : kanbanTicketMetaHtml(ticket)" in js
    assert "highest: 6" in js
    assert js.index("const priorityDiff") < js.index("const complexityDiff", js.index("function compareKanbanListTickets"))
    assert "expandedKanbanLanes: new Set()" in js
    assert "['done', 'complete', 'completed'].includes(laneName)" in js
    assert "collapsedByDefault && !state.expandedKanbanLanes.has(laneKey)" in js


def test_legacy_ticket_pages_redirect_into_development():
    client = TestClient(create_app(), follow_redirects=False)

    local = client.get("/tickets/?board_id=42&view=list")
    assert local.status_code == 302
    assert local.headers["location"] == "/development/boards/decisions/42/kanban/?view=list"

    jira = client.get("/kanban/?source=jira&board_id=OPS")
    assert jira.status_code == 302
    assert jira.headers["location"] == "/development/boards/jira/OPS/kanban/"

    intake = client.get("/intake/")
    assert intake.status_code == 302
    assert intake.headers["location"] == "/development/incoming/"
