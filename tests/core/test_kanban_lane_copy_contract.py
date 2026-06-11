from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KANBAN_JS = ROOT / "distr/gui/web/static/kanban/js/kanban.js"
KANBAN_TICKET_JS = ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js"
KANBAN_HTML = ROOT / "distr/gui/web/templates/kanban/kanban.html"
KANBAN_PY = ROOT / "distr/gui/web/routes/kanban.py"


def test_lane_copy_modal_has_board_and_lane_selects():
    html = KANBAN_HTML.read_text(encoding="utf-8")
    ticket_js = KANBAN_TICKET_JS.read_text(encoding="utf-8")

    assert 'id="kb-copy-board-select"' in html
    assert 'id="kb-copy-lane-select"' in html
    assert 'id="kb-copy-modal-title"' in html
    assert "refreshCopyLaneSelect" in ticket_js
    assert "openCopyLaneModal" in ticket_js


def test_lane_headers_expose_copy_all_action_on_external_boards():
    js = KANBAN_JS.read_text(encoding="utf-8")
    ticket_js = KANBAN_TICKET_JS.read_text(encoding="utf-8")
    html = KANBAN_HTML.read_text(encoding="utf-8")

    assert "kb-lane-copy-all" in js
    assert "laneHeaderToolsHtml" in js
    assert "openCopyLaneModal" in js
    assert "laneCopyAllButtonHtml" in ticket_js
    assert "kb-lane-whatsapp-snapshot" in html
    assert "laneWhatsappIntakeLink" in js
    assert "laneWhatsappSnapshotButtonHtml" in ticket_js
    assert "openBoardWhatsappSnapshotTicket" in ticket_js
    assert "wf-board-whatsapp-snapshot" not in js


def test_bulk_copy_lane_api_exists():
    py = KANBAN_PY.read_text(encoding="utf-8")

    assert "class BulkCopyLaneToBoard" in py
    assert '"/tickets/tickets/bulk-copy-to-board"' in py
    assert "lane_id: Optional[int]" in py
    assert "_resolve_local_destination_lane" in py
