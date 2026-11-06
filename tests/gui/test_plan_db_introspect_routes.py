"""db-links test / introspect routes: status codes, stable error codes, nothing secret in bodies or logs (temp DB,
fake drivers, session secret store only; no keychain and no database server)."""
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
    ds.set_store(ds.SecretStore(platform="linux", service="decisions-db-link-routes"))
    state = {"error": None, "conns": []}
    def connector(target, port, database, username, secret):
        if state["error"]:
            raise state["error"]
        conn = FakeConn("postgres", PG_ANSWERS)
        state["conns"].append(conn)
        return conn
    monkeypatch.setitem(di.CONNECTORS, "postgres", connector)
    app = FastAPI(); router = APIRouter(prefix="/api"); register_routes(router, None); app.include_router(router)
    ws = service.ensure_workspace(board_key="decisions:66", board_provider="decisions", board_name="Introspect routes", project_id=plan_workspace_db["project_id"])
    link = dl.create_link(ws["id"], {"engine": "postgres", "host": "127.0.0.1", "database": "shop", "username": "dev"})
    dl.set_secret(ws["id"], link["id"], SECRET)
    with TestClient(app) as client:
        yield client, f"/api/workflows/studio/plan-workspaces/{ws['id']}/db-links/{link['id']}", state, ws
    ds.set_store(None); ds.clear_session_secrets()


def test_routes_happy_path(ctx, caplog):
    client, url, state, _ = ctx
    r = client.post(f"{url}/test")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["code"] == "ok"
    r = client.post(f"{url}/introspect")
    assert r.status_code == 200 and r.json()["total_tables"] == 3 and len(r.json()["tables"]) == 3
    r = client.post(f"{url}/introspect", json={"filter": "cust", "limit": 1})
    assert r.status_code == 200 and r.json()["returned"] == 1 and r.json()["truncated"] is True
    assert all(c.rolled_back and c.closed and not c.committed for c in state["conns"])
    assert SECRET not in caplog.text


@pytest.mark.parametrize("body,status", [({"filter": "x'; drop"}, 422), ({"limit": 301}, 422), ({"other": 1}, 400), ([1], 400)])
def test_introspect_validation(ctx, body, status):
    client, url, state, _ = ctx
    r = client.post(f"{url}/introspect", json=body)
    assert r.status_code == status
    assert "drop" not in r.text or status != 422 or "filter must" in r.text
    assert state["conns"] == []


@pytest.mark.parametrize("error,code,status", [
    (PgError(f"password authentication failed password={SECRET}"), "auth_failed", 502),
    (PgError("connection timeout expired"), "timeout", 504),
    (PgError("connection failed: Connection refused"), "unreachable", 502),
    (PgError('database "shop" does not exist'), "db_not_found", 502),
    (RuntimeError(("postgresql://" + "dev:" + SECRET + "@127.0.0.1/shop")), "failed", 502),
])
def test_error_codes_no_leak(ctx, caplog, error, code, status):
    client, url, state, ws = ctx
    state["error"] = error
    for suffix in ("test", "introspect"):
        r = client.post(f"{url}/{suffix}")
        assert r.status_code == status and r.json() == {"ok": False, "code": code, "detail": di.MESSAGES[code]}
        assert SECRET not in r.text and "postgresql://" not in r.text
    assert SECRET not in caplog.text
    listed = client.get(url.rsplit("/", 1)[0]).json()["items"][0]
    assert listed["status"] == code and SECRET not in str(listed)


def test_non_loopback_and_missing(ctx, monkeypatch):
    client, url, state, _ = ctx
    monkeypatch.setattr(dd, "assert_loopback_host", lambda host, resolver=None: REAL_ASSERT("db.example.com", resolver=RESOLVE))
    r = client.post(f"{url}/test")
    assert r.status_code == 422 and r.json()["code"] == "non_loopback" and state["conns"] == []
    assert client.post(url.rsplit("/", 1)[0] + "/99999/test").status_code == 404
    assert client.post("/api/workflows/studio/plan-workspaces/99999/db-links/1/introspect").status_code == 404
