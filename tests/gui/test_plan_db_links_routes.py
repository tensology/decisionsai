"""db-links routes: status codes, no secret in any response body or log, delete cleans the temp keychain item."""
import json
import logging

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.planning import db_discovery as dd, db_links as dl, db_secrets as ds, service
from distr.gui.web.routes.development.planning import register_routes
from tests.core.plan_db_keychain import temp_keychain, unique_service  # noqa: F401
from tests.core.test_plan_db_discovery import RESOLVE
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401

SECRET = "R0ute-secret value!"
BASE = {"engine": "mysql", "host": "127.0.0.1", "database": "shop", "username": "dev"}


@pytest.fixture
def ctx(plan_workspace_db, monkeypatch, temp_keychain, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(dl, "assert_loopback_host", lambda host: dd.assert_loopback_host(host, resolver=RESOLVE))
    ds.set_store(ds.SecretStore(keychain=temp_keychain, service=unique_service()))
    app = FastAPI(); router = APIRouter(prefix="/api"); register_routes(router, None); app.include_router(router)
    ws = service.ensure_workspace(board_key="decisions:63", board_provider="decisions", board_name="Routes", project_id=plan_workspace_db["project_id"])
    with TestClient(app) as client:
        yield client, f"/api/workflows/studio/plan-workspaces/{ws['id']}/db-links", plan_workspace_db
    ds.set_store(None); ds.clear_session_secrets()


def test_crud_status_codes_and_secret_never_echoed(ctx, caplog):
    client, url, _ = ctx
    bodies = []
    r = client.get(url); bodies.append(r.text); assert r.status_code == 200 and r.json()["items"] == []
    r = client.post(url, json=BASE); bodies.append(r.text); assert r.status_code == 201
    link = r.json()
    assert link["port"] == 3306 and link["has_secret"] is False
    r = client.post(url, json=BASE); bodies.append(r.text); assert r.status_code == 409
    r = client.post(url, json={**BASE, "host": "db.example.com"}); bodies.append(r.text); assert r.status_code == 422
    r = client.post(url, json={**BASE, "host": "::1", "database": "shop6"}); bodies.append(r.text); assert r.status_code == 201 and r.json()["host"] == "::1"
    r = client.post(url, json={**BASE, "password": SECRET}); bodies.append(r.text); assert r.status_code == 422
    r = client.post(url, content="not json", headers={"content-type": "application/json"}); assert r.status_code == 400
    r = client.patch(f"{url}/{link['id']}", json={"label": "Shop DB"}); bodies.append(r.text); assert r.status_code == 200 and r.json()["label"] == "Shop DB"
    r = client.patch(f"{url}/{link['id']}", json={"port": 99999}); bodies.append(r.text); assert r.status_code == 422
    r = client.put(f"{url}/{link['id']}/secret", json={"secret": SECRET}); bodies.append(r.text)
    assert r.status_code == 200 and r.json() == {"store": "keychain", "has_secret": True}
    r = client.put(f"{url}/{link['id']}/secret", json={"secret": SECRET, "extra": 1}); bodies.append(r.text); assert r.status_code == 400
    r = client.put(f"{url}/{link['id']}/secret", json={"secret": 'bad"' + SECRET}); bodies.append(r.text); assert r.status_code == 422
    r = client.put(f"{url}/{link['id']}/secret", json={"secret": 12345}); bodies.append(r.text); assert r.status_code == 422
    r = client.get(url); bodies.append(r.text)
    item = next(i for i in r.json()["items"] if i["id"] == link["id"])
    assert item["has_secret"] is True and item["secret_store"] == "keychain" and r.json()["secret_store"] == "keychain"
    assert all(SECRET not in body for body in bodies)
    assert all("secret_ref" not in body and "password\"" not in body for body in bodies)
    assert SECRET not in caplog.text
    store = ds.get_store()
    assert store.has(link["id"])
    r = client.delete(f"{url}/{link['id']}"); assert r.status_code == 200 and r.json()["secret_deleted"] is True
    assert store.has(link["id"]) is False                       # the temp keychain item is gone
    assert client.delete(f"{url}/{link['id']}").status_code == 404


def test_wrong_workspace_and_unknown_link_are_404(ctx):
    client, url, db = ctx
    other = service.ensure_workspace(board_key="decisions:64", board_provider="decisions", board_name="O", project_id=db["project_id"])
    link = client.post(url, json=BASE).json()
    other_url = f"/api/workflows/studio/plan-workspaces/{other['id']}/db-links"
    assert client.patch(f"{other_url}/{link['id']}", json={"label": "x"}).status_code == 404
    assert client.delete(f"{other_url}/{link['id']}").status_code == 404
    assert client.put(f"{other_url}/{link['id']}/secret", json={"secret": SECRET}).status_code == 404
    assert client.get("/api/workflows/studio/plan-workspaces/999999/db-links").status_code == 404
    assert client.patch(f"{url}/424242", json={"label": "x"}).status_code == 404
