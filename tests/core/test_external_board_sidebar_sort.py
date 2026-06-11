from distr.gui.web.routes.kanban import _sort_external_board_list


def test_external_board_sidebar_sort_puts_configured_boards_first_by_recent_activity():
    boards = [
        {"id": "1", "name": "Zeta"},
        {"id": "2", "name": "Alpha", "local_id": 10, "modified_date": "2026-06-10T12:00:00Z"},
        {"id": "3", "name": "Beta", "local_id": 11, "modified_date": "2026-06-11T08:00:00Z"},
        {"id": "4", "name": "Gamma"},
    ]

    ordered = _sort_external_board_list(boards)

    assert [b["id"] for b in ordered] == ["3", "2", "4", "1"]


def test_external_board_sidebar_sort_contract_is_wired_in_ui():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = (root / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")
    py = (root / "distr/gui/web/routes/kanban.py").read_text(encoding="utf-8")

    assert "function sortExternalBoardsForSidebar" in js
    assert "touchExternalBoardActivity" in js
    assert "/touch" in js
    assert "_sort_external_board_list" in py
    assert '"/tickets/external-boards/{provider}/{ext_board_id}/touch"' in py
