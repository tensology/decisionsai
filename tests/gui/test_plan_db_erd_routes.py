"""db-links snapshot / diff / apply routes (N5 phase 4): status codes, nothing secret in bodies, files or logs.
Temp DB, fake driver, session secret store only; no keychain and no database server."""
import logging

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.planning import db_discovery as dd, db_introspect as di, db_links as dl, db_secrets as ds, service
from distr.gui.web.routes.development.planning import register_routes
from tests.core.test_plan_db_discovery import RESOLVE
from tests.core.test_plan_db_introspect import PG_ANSWERS, REAL_ASSERT, SECRET, FakeConn, PgError
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401


@pytest.fixture
def ctx(plan_workspace_db, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    pinned = lambda host, resolver=None: REAL_ASSERT(host, resolver=RESOLVE)  # noqa: E731
    monkeypatch.setattr(dl, "assert_loopback_host", pinned)
    monkeypatch.setattr(dd, "assert_loopback_host", pinned)
    ds.set_store(ds.SecretStore(platform="linux", service="decisions-db-erd-routes"))
    state = {"error": None}
    def connector(*a):
        if state["error"]:
            raise state["error"]
        return FakeConn("postgres", PG_ANSWERS)
    monkeypatch.setitem(di.CONNECTORS, "postgres", connector)
    app = FastAPI(); router = APIRouter(prefix="/api"); register_routes(router, None); app.include_router(router)
    ws = service.ensure_workspace(board_key="decisions:68", board_provider="decisions", board_name="ERD routes", project_id=plan_workspace_db["project_id"])
    link = dl.create_link(ws["id"], {"engine": "postgres", "host": "127.0.0.1", "database": "shop", "username": "dev", "label": "Shop"})
    dl.set_secret(ws["id"], link["id"], SECRET)
    with TestClient(app) as client:
        yield client, f"/api/workflows/studio/plan-workspaces/{ws['id']}/db-links/{link['id']}", state, plan_workspace_db["folder"] / "planning"
    ds.set_store(None); ds.clear_session_secrets()


def test_snapshot_diff_apply_flow(ctx, caplog):
    client, url, state, root = ctx
    assert client.get(f"{url}/snapshot").status_code == 404
    assert client.get(f"{url}/diff").status_code == 404
    r = client.post(f"{url}/snapshot")
    assert r.status_code == 200 and len(r.json()["tables"]) == 3
    assert client.get(f"{url}/snapshot").json() == r.json()
    d = client.get(f"{url}/diff").json()
    assert d["target"] == "new_erd" and d["summary"]["added"] == 3
    r = client.post(f"{url}/apply", json={"tables": ["public.customers", "public.orders"], "item_id": None, "expected_revision": None})
    assert r.status_code == 200 and r.json()["item"]["item_type"] == "erd"
    item = r.json()["item"]
    d = client.get(f"{url}/diff").json()
    assert d["target"] == "link_erd" and d["item_id"] == item["id"] and d["revision"] == item["revision_count"]
    stale = client.post(f"{url}/apply", json={"tables": ["public.secret_stuff"], "item_id": item["id"], "expected_revision": 99})
    assert stale.status_code == 409
    ok = client.post(f"{url}/apply", json={"tables": ["public.secret_stuff"], "item_id": item["id"], "expected_revision": item["revision_count"]})
    assert ok.status_code == 200 and "public__secret_stuff" in ok.json()["item"]["content"]
    blob = "".join(p.read_text() for p in root.rglob("*") if p.is_file()) + caplog.text + r.text + ok.text
    assert SECRET not in blob


@pytest.mark.parametrize("body,status", [({}, 400), ({"tables": [], "other": 1}, 400), ([1], 400),
                                         ({"tables": []}, 422), ({"tables": "x"}, 422), ({"tables": ["a.b"]}, 422),
                                         ({"tables": ["public.orders"], "item_id": 999999, "expected_revision": 1}, 404)])
def test_apply_validation(ctx, body, status):
    client, url, state, _ = ctx
    client.post(f"{url}/snapshot")
    assert client.post(f"{url}/apply", json=body).status_code == status


def test_snapshot_errors_no_leak(ctx, caplog):
    client, url, state, root = ctx
    assert client.post(f"{url}/snapshot", json={"other": 1}).status_code == 400
    state["error"] = PgError(f"password authentication failed password={SECRET}")
    r = client.post(f"{url}/snapshot")
    assert r.status_code == 502 and r.json()["code"] == "auth_failed" and SECRET not in r.text
    assert not (root / ".db-snapshots").exists() or not any((root / ".db-snapshots").glob("*.json"))
    assert SECRET not in caplog.text
    assert client.post("/api/workflows/studio/plan-workspaces/1/db-links/99999/snapshot").status_code == 404
