from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_kanban_global_settings_uses_complexity_routing_not_llm_rows():
    html = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text()
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban_board.js").read_text()

    assert "Ticket complexity routing" in html
    assert "LLM Configuration" not in html
    assert "kb-gs-sub-download" not in html
    assert "kb-gs-coder-download" not in html

    for level in ("low", "medium", "high"):
        assert f"project_cli_{level}_backend" in js
        assert f"project_cli_{level}_model" in js
    assert 'id="kb-gs-cli-{{ level }}-backend"' in html
    assert 'id="kb-gs-cli-{{ level }}-model"' in html
    assert 'id="kb-gs-cli-{{ level }}-codex-cog"' in html
    assert "codex-intelligence" in html

    assert "kb-gs-sub-provider" not in js
    assert "kb-gs-coder-provider" not in js
