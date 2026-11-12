"""The Automations editor stays on the Development surface."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_automations_routes_redirect_into_development():
    server = (ROOT / "distr/gui/web/server.py").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/development/automations/index.js").read_text(encoding="utf-8")
    app_js = (ROOT / "distr/gui/web/static/development/app.js").read_text(encoding="utf-8")
    workspace = (ROOT / "distr/gui/web/templates/development/automations/workspace.html").read_text(encoding="utf-8")

    assert "def _legacy_automation_destination" in server
    assert "RedirectResponse(url=_legacy_automation_destination(request), status_code=302)" in server
    assert "automations/automations.html" not in server
    assert "window.location.assign(`/automations/" not in js
    assert "function openAutomationEditor" in js
    assert "/development/automations/" in js
    assert "scheduled-automation-editor" in js
    assert "id=\"scheduled-automation-editor\"" in workspace
    assert "query.get('automation')" in app_js
    assert "function payloadFromForm" in js
    assert "function openNewAutomation" in js
    assert "Every N sec/min" in js
    assert "id=\"automation-new\"" in workspace
