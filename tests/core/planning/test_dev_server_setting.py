"""P2b: the dev-server URL is what points the rendered-DOM enrichment somewhere."""

import tempfile

import pytest


@pytest.fixture()
def fresh_db(monkeypatch):
    monkeypatch.setenv("DECISIONS_DB_DIR", tempfile.mkdtemp(prefix="devserver-"))
    from distr.core.db import init_db

    init_db()


def test_the_setting_defaults_to_empty(fresh_db):
    from distr.core.utils import load_settings_from_db

    assert load_settings_from_db().get("planning_dev_server_url") == ""


def test_the_setting_round_trips(fresh_db):
    from distr.core.utils import load_settings_from_db, save_settings_to_db

    save_settings_to_db({"planning_dev_server_url": "http://127.0.0.1:5173"})
    assert load_settings_from_db().get("planning_dev_server_url") == "http://127.0.0.1:5173"


def test_an_empty_setting_means_the_scan_never_renders(monkeypatch):
    """Off unless configured: the expensive path must not run by accident."""
    from distr.core.planning import dom_cues, project_scan as ps

    called = []
    monkeypatch.setattr(dom_cues, "available", lambda: True)
    monkeypatch.setattr(dom_cues, "read_routes", lambda *a, **k: called.append(a) or {})
    assert ps.enrich_cues_from_dom({"template_cues": {"/x": {}}}, "") == 0
    assert called == []
