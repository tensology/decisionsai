from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_kanban_project_bound_card_actions_are_hidden_without_project():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text(encoding="utf-8")

    assert "if (config.hidden) return \"\";" in js
    assert 'keyClass: "kb-act-cli"' in js
    assert 'keyClass: "kb-act-project"' in js
    assert "hidden: !opts.hasProject" in js
    assert "link ticket/board to project" not in js


def test_kanban_modal_project_actions_toggle_hidden_not_disabled_state():
    js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    assert 'cliBtn.classList.toggle("hidden", !canPush)' in js
    assert 'projectBtn.classList.toggle("hidden", !canPush)' in js
    assert 'cliBtn.disabled = false' in js
    assert 'projectBtn.disabled = false' in js

