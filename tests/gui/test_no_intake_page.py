from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_intake_is_not_a_top_level_web_product():
    base = (ROOT / "distr/gui/web/templates/base.html").read_text()
    workflows = (ROOT / "distr/gui/web/templates/workflows/workflows.html").read_text()
    workflow_js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text()
    ticket_js = (ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js").read_text()

    assert 'href="/intake/"' not in base
    assert "Work Intake" not in base
    assert "wf-intake-status" not in workflows
    assert "/work-intake/items" not in workflow_js
    assert "buildSourceBadge(source) + buildComplexityBadge" in ticket_js


def test_observability_is_not_named_as_a_work_queue():
    base = (ROOT / "distr/gui/web/templates/base.html").read_text()
    routes = (ROOT / "distr/gui/web/routes/observability.py").read_text()

    assert "/api/diagnostics/ui-stall" in base
    assert '@router.post("/diagnostics/ui-stall")' in routes
    assert "/work-intake/items" not in routes
    assert "/work-intake/ingest" not in routes
