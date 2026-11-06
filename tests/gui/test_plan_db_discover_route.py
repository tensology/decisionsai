"""GET .../db-links/discover: read-only discovery route (temp DB; discovery itself is stubbed, no real ports)."""
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.planning import db_discovery, service
from distr.gui.web.routes.development.planning import register_routes
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401


def test_discover_route(plan_workspace_db, monkeypatch):
    monkeypatch.setattr(db_discovery, "discover", lambda: {"candidates": [{"engine": "postgres", "host": "127.0.0.1", "port": 5999,
                                                                        "socket": None, "reachable": False, "source": "tcp"}],
                                                       "brew_services": [], "note": "n"})
    app = FastAPI(); router = APIRouter(prefix="/api"); register_routes(router, None); app.include_router(router)
    ws = service.ensure_workspace(board_key="decisions:91", board_provider="decisions", board_name="DB", project_id=plan_workspace_db["project_id"])
    with TestClient(app) as client:
        ok = client.get(f"/api/workflows/studio/plan-workspaces/{ws['id']}/db-links/discover")
        assert ok.status_code == 200 and ok.json()["candidates"][0]["port"] == 5999
        assert client.get("/api/workflows/studio/plan-workspaces/999999/db-links/discover").status_code == 404
        assert client.post(f"/api/workflows/studio/plan-workspaces/{ws['id']}/db-links/discover").status_code == 405
