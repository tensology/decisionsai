"""User documents: item_type "document" create/update/AI-edit and the archive-on-delete route (temp DB)."""
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.planning import service
from distr.gui.web.routes.development.planning import register_routes
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401  (temp DB fixture)


@pytest.fixture
def workspace(plan_workspace_db):
    return service.ensure_workspace(board_key="decisions:77", board_provider="decisions", board_name="Docs", project_id=plan_workspace_db["project_id"])


@pytest.fixture
def client(plan_workspace_db):
    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def _url(ws, item):
    return f"/api/workflows/studio/plan-workspaces/{ws['id']}/plan-items/{item['id']}"


def test_document_is_a_known_type_with_markdown_default(workspace):
    assert service.ITEM_TYPES["document"][:2] == ("Document", "markdown")
    doc = service.create_item(workspace_id=workspace["id"], item_type="document")
    assert (doc["title"], doc["content_format"], doc["content"], doc["revision_count"]) == ("Document", "markdown", "# Document\n\n", 1)
    named = service.create_item(workspace_id=workspace["id"], item_type="document", title="Runbook", content="# Runbook\n")
    assert named["title"] == "Runbook" and Path(named["file_path"]).read_text() == "# Runbook\n"


def test_document_updates_record_revisions(workspace):
    doc = service.create_item(workspace_id=workspace["id"], item_type="document", title="Notes")
    saved = service.update_item(doc["id"], content="# Notes\n\nv2\n", expected_revision=1)
    assert saved["revision_count"] == 2
    with pytest.raises(service.PlanRevisionConflict):
        service.update_item(doc["id"], content="# stale\n", expected_revision=1)
    assert [r["revision"] for r in service.list_revisions(doc["id"])] == [2, 1]


def test_ai_edit_path_accepts_document(workspace):
    doc = service.create_item(workspace_id=workspace["id"], item_type="document", title="Spec")
    detail = service.get_workspace(workspace["id"])
    revisions = {str(i["id"]): i["revision_count"] for i in detail["items"]}
    results = service.apply_agent_edits(workspace["id"], edits=[
        {"id": doc["id"], "content": "# Spec\n\nAI text\n"},
        {"item_type": "document", "content_format": "markdown", "title": "AI doc", "content": "# AI doc\n"},
    ], expected_revisions=revisions, instruction="test")
    assert [r["item_type"] for r in results] == ["document", "document"]
    assert service.list_revisions(doc["id"])[0]["source"] == "agent"


def test_archive_happy_path_keeps_history_and_moves_file(client, workspace):
    doc = service.create_item(workspace_id=workspace["id"], item_type="document", title="Gone")
    original = Path(doc["file_path"])
    assert original.exists()
    response = client.delete(_url(workspace, doc), params={"expected_revision": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["archived"] and body["revision"] == 2
    archived = Path(body["archive_path"])
    assert not original.exists() and archived.exists() and archived.parent.name == ".archive"
    assert archived.read_text() == "# Document\n\n"
    history = service.list_revisions(doc["id"])
    assert [r["revision"] for r in history] == [2, 1] and history[0]["status"] == "archived"
    detail = service.get_workspace(workspace["id"])
    assert doc["id"] not in {i["id"] for i in detail["items"]}
    # Hidden item must not break the AI edit revision check, and cannot be edited again.
    revisions = {str(i["id"]): i["revision_count"] for i in detail["items"]}
    service.apply_agent_edits(workspace["id"], edits=[], expected_revisions=revisions, instruction="noop")
    with pytest.raises(LookupError):
        service.update_item(doc["id"], content="x", expected_revision=2)
    assert client.delete(_url(workspace, doc), params={"expected_revision": 2}).status_code == 404


def test_archive_rejects_other_types(client, workspace):
    frac = service.create_item(workspace_id=workspace["id"], item_type="frac")
    response = client.delete(_url(workspace, frac), params={"expected_revision": frac["revision_count"]})
    assert response.status_code == 403
    assert frac["id"] in {i["id"] for i in service.get_workspace(workspace["id"])["items"]}


def test_archive_stale_revision_is_409_and_missing_revision_422(client, workspace):
    doc = service.create_item(workspace_id=workspace["id"], item_type="document")
    service.update_item(doc["id"], content="# newer\n", expected_revision=1)
    stale = client.delete(_url(workspace, doc), params={"expected_revision": 1})
    assert stale.status_code == 409 and "newer revision" in stale.json()["detail"]
    assert client.delete(_url(workspace, doc)).status_code == 422
    assert Path(doc["file_path"]).exists()


def test_archive_is_scoped_to_the_workspace_in_the_url(client, workspace, plan_workspace_db):
    other = service.ensure_workspace(board_key="decisions:78", board_provider="decisions", board_name="Other", project_id=plan_workspace_db["project_id"])
    doc = service.create_item(workspace_id=workspace["id"], item_type="document")
    assert client.delete(_url(other, doc), params={"expected_revision": 1}).status_code == 404
