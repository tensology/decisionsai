"""Settings that are JSON lists must actually persist.

'incoming_proposals_auto_accept_projects' was read by proposals.py and had a
default in settings.py, but no DB column - so save_settings_to_db logged
"Settings model does not have attribute" and dropped it. It could only ever be
the hardcoded default.
"""

import os
import tempfile

import pytest


@pytest.fixture()
def fresh_db(monkeypatch):
    monkeypatch.setenv("DECISIONS_DB_DIR", tempfile.mkdtemp(prefix="settings-list-"))
    yield


def test_auto_accept_projects_round_trips(fresh_db):
    from distr.core.db import init_db

    init_db()
    # distr.core.settings is stubbed by the test conftest; the real persistence
    # lives in distr.core.utils.
    from distr.core.utils import load_settings_from_db, save_settings_to_db

    save_settings_to_db({"incoming_proposals_auto_accept_projects": ["OnlyThisOne"]})
    assert load_settings_from_db().get("incoming_proposals_auto_accept_projects") == ["OnlyThisOne"]


def test_an_empty_list_falls_back_to_the_builtin_default(fresh_db):
    """Empty must keep the old behaviour, not disable auto-accept silently."""
    from distr.core.db import init_db

    init_db()
    from distr.core.incoming.proposals import AUTO_ACCEPT_DEFAULT_PROJECTS, auto_accept_skip_reason

    proposal = {"origin": "local", "project_id": 1, "project_name": AUTO_ACCEPT_DEFAULT_PROJECTS[0]}
    assert auto_accept_skip_reason(proposal, {"incoming_proposals_auto_accept_projects": []}) == ""
