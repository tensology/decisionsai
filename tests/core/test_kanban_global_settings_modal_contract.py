from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_kanban_global_settings_modal_was_removed():
    html = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text()
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban_board.js").read_text()

    assert "Ticket Boards Settings" not in html
    assert "kb-global-settings-modal" not in html
    assert "kb-settings-cog" not in html
    assert "LLM Configuration" not in html
    assert "kb-gs-sub-download" not in html
    assert "kb-gs-coder-download" not in html
    assert "kanban_agent_" not in html
    assert "kanban_agent_" not in js
    assert "kb-gs-sub-provider" not in js
    assert "kb-gs-coder-provider" not in js
