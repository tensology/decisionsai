"""User save path (PATCH plan item): Mermaid validation, size cap, and 409-vs-422 status codes (temp DB)."""
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.planning import service
from distr.gui.web.routes.development.planning import register_routes
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401  (temp DB fixture)

ERD = 'erDiagram\n    A {\n        int id PK\n    }\n    A ||--o{ B : "has"\n%% decisions-erd: {"v":1,"tables":{"A":{"x":0,"y":0,"interfaces":[]}}}\n'


@pytest.fixture
def erd_item(plan_workspace_db):
    workspace = service.ensure_workspace(board_key="decisions:31", board_provider="decisions", board_name="Guards", project_id=plan_workspace_db["project_id"])
    item = service.create_item(workspace_id=workspace["id"], item_type="erd")
    assert item["content_format"] == "mermaid"
    return item


@pytest.fixture
def client(plan_workspace_db):
    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def test_valid_erd_with_editor_sidecar_saves(erd_item):
    saved = service.update_item(erd_item["id"], content=ERD, expected_revision=erd_item["revision_count"])
    assert saved["content"] == ERD


def test_invalid_mermaid_is_rejected_on_the_user_path(erd_item):
    with pytest.raises(ValueError, match="Invalid Mermaid"):
        service.update_item(erd_item["id"], content="erDiagram\n    A ||--o{ : broken\n", expected_revision=erd_item["revision_count"])


def test_erd_item_must_stay_an_erdiagram(erd_item):
    with pytest.raises(ValueError, match="erDiagram"):
        service.update_item(erd_item["id"], content="flowchart LR\n    A --> B\n", expected_revision=erd_item["revision_count"])


def test_size_cap_matches_agent_path(erd_item):
    with pytest.raises(ValueError, match="200,000"):
        service.update_item(erd_item["id"], content="x" * 200_001)


def test_route_uses_409_only_for_revision_conflicts_and_422_for_invalid_content(client, erd_item):
    url = f"/api/workflows/studio/plan-items/{erd_item['id']}"
    ok = client.patch(url, json={"content": ERD, "expected_revision": erd_item["revision_count"]})
    assert ok.status_code == 200
    stale = client.patch(url, json={"content": ERD.replace("has", "owns"), "expected_revision": erd_item["revision_count"]})
    assert stale.status_code == 409 and "newer revision" in stale.json()["detail"]
    invalid = client.patch(url, json={"content": "erDiagram\n    A ||--o{ : broken\n", "expected_revision": ok.json()["revision_count"]})
    assert invalid.status_code == 422 and "Invalid Mermaid" in invalid.json()["detail"]
    bad_status = client.patch(url, json={"status": "nope", "expected_revision": ok.json()["revision_count"]})
    assert bad_status.status_code == 422
