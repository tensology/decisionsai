"""Plan DB links service: validation, normalised loopback host, limits, duplicates, archive + secret cleanup (temp DB)."""
import json
import logging

import pytest

from distr.core.planning import db_discovery as dd, db_links as dl, db_secrets as ds, service
from tests.core.plan_db_keychain import temp_keychain, unique_service  # noqa: F401
from tests.core.test_plan_db_discovery import RESOLVE
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401

SECRET = "Sup3r$ecret pass"
BASE = {"engine": "postgres", "host": "127.0.0.1", "database": "app_dev", "username": "paul"}


@pytest.fixture
def ws(plan_workspace_db, monkeypatch, temp_keychain):
    monkeypatch.setattr(dl, "assert_loopback_host", lambda host: dd.assert_loopback_host(host, resolver=RESOLVE))
    store = ds.SecretStore(keychain=temp_keychain, service=unique_service())
    ds.set_store(store)
    workspace = service.ensure_workspace(board_key="decisions:61", board_provider="decisions", board_name="DB", project_id=plan_workspace_db["project_id"])
    yield workspace
    ds.set_store(None)
    ds.clear_session_secrets()


def test_create_defaults_and_payload_has_no_secret_fields(ws):
    link = dl.create_link(ws["id"], dict(BASE))
    assert link["port"] == 5432 and link["schemas"] == ["public"] and link["label"] == "postgres · app_dev"
    assert link["has_secret"] is False and link["secret_store"] is None and link["status"] == "new"
    assert not any(k in link for k in ("password", "secret", "secret_ref"))


@pytest.mark.parametrize("host,stored", [("localhost", "127.0.0.1"), ("::1", "::1"), ("[::1]", "::1"),
                                         ("::ffff:127.0.0.1", "127.0.0.1"), ("127.0.0.5", "127.0.0.5")])
def test_host_is_stored_as_literal_loopback(ws, host, stored):
    assert dl.create_link(ws["id"], {**BASE, "host": host})["host"] == stored


@pytest.mark.parametrize("bad", [
    {"host": "db.example.com"}, {"host": "rebind.test"}, {"host": "10.0.0.5"}, {"host": "0.0.0.0"}, {"host": "::"},
    {"host": "unix:relative.sock"}, {"host": "unix:/etc/../tmp/x.sock"}, {"host": "unix:/home/x.sock"},
    {"engine": "oracle"}, {"port": 0}, {"port": 70000}, {"port": "5432"}, {"database": ""}, {"database": "a b"},
    {"database": "x" * 64}, {"username": "pa'ul"}, {"label": "x" * 81}, {"label": "a\nb"}, {"schemas": ["1bad"]},
    {"colour": "red"}, {"password": "x"}, {"db_secret": "x"}, {"extra": 1},
])
def test_validation_matrix(ws, bad):
    with pytest.raises(ValueError) as info:
        dl.create_link(ws["id"], {**BASE, **bad})
    assert not isinstance(info.value, dl.LinkConflict)


def test_unix_socket_link_has_no_port(ws):
    link = dl.create_link(ws["id"], {**BASE, "engine": "mysql", "host": "unix:/tmp/mysql.sock", "port": 3306})
    assert link["host"] == "unix:/tmp/mysql.sock" and link["port"] is None


def test_duplicate_and_limit_are_conflicts(ws):
    dl.create_link(ws["id"], dict(BASE))
    with pytest.raises(dl.LinkConflict):
        dl.create_link(ws["id"], {**BASE, "host": "localhost", "label": "again"})      # same after normalising
    for i in range(dl.MAX_LINKS_PER_PROJECT - 1):
        dl.create_link(ws["id"], {**BASE, "database": f"db{i}"})
    with pytest.raises(dl.LinkConflict):
        dl.create_link(ws["id"], {**BASE, "database": "one_more"})


def test_update_validates_and_resets_status(ws):
    link = dl.create_link(ws["id"], dict(BASE))
    other = dl.create_link(ws["id"], {**BASE, "database": "other"})
    updated = dl.update_link(ws["id"], link["id"], {"label": "Main", "port": 5433})
    assert updated["label"] == "Main" and updated["port"] == 5433
    with pytest.raises(dl.LinkConflict):
        dl.update_link(ws["id"], other["id"], {"database": "app_dev", "port": 5433})
    with pytest.raises(dd.NonLoopbackHost):
        dl.update_link(ws["id"], link["id"], {"host": "db.example.com"})


def test_secret_set_archive_cleans_keychain_and_never_logs(ws, caplog):
    caplog.set_level(logging.DEBUG)
    link = dl.create_link(ws["id"], dict(BASE))
    result = dl.set_secret(ws["id"], link["id"], SECRET)
    assert result == {"store": "keychain", "has_secret": True}
    listed = dl.list_links(ws["id"])
    assert listed["secret_store"] == "keychain" and listed["items"][0]["has_secret"] is True
    assert SECRET not in json.dumps(listed) and SECRET not in caplog.text
    store = ds.get_store()
    assert store.get(link["id"]) == SECRET
    with service.get_session() as db:
        from distr.core.db.workflow import PlanDbLink
        row = db.get(PlanDbLink, link["id"])
        assert SECRET not in json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
        assert row.secret_ref == f"keychain:{store.service}/{link['id']}"
    gone = dl.archive_link(ws["id"], link["id"])
    assert gone == {"id": link["id"], "archived": True, "secret_deleted": True}
    assert store.has(link["id"]) is False
    assert dl.list_links(ws["id"])["items"] == []
    with pytest.raises(LookupError):
        dl.update_link(ws["id"], link["id"], {"label": "x"})
    dl.create_link(ws["id"], dict(BASE))                         # archived link no longer blocks a re-link
    assert all(f"id={link['id']}" in line or "id=" in line for line in caplog.text.splitlines() if "plan_db_link" in line)


def test_wrong_workspace_is_not_found(ws, plan_workspace_db):
    other = service.ensure_workspace(board_key="decisions:62", board_provider="decisions", board_name="Other", project_id=plan_workspace_db["project_id"])
    link = dl.create_link(ws["id"], dict(BASE))
    for call in (lambda: dl.update_link(other["id"], link["id"], {"label": "x"}), lambda: dl.archive_link(other["id"], link["id"]),
                 lambda: dl.set_secret(other["id"], link["id"], SECRET), lambda: dl.list_links(999999)):
        with pytest.raises(LookupError):
            call()
