"""N6 phase 1: server-side wire validation in update_item and the trailing decisions-flow sidecar (temp DB)."""
import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.planning import service
from distr.core.planning.validation import PlanValidationError, validate_flow_sidecar, validate_wireframe
from distr.gui.web.routes.development.planning import register_routes
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401

PAGE = 'screen "Customers" id=customers route=/customers\n  #| Lists every **customer**.\n  heading "Customers"\n'


def side(nodes, prefix="# decisions-flow:"):
    return f"{prefix} {json.dumps({'v': 1, 'nodes': nodes}, sort_keys=True, separators=(',', ':'))}\n"


def test_wire_parser_ignores_description_and_sidecar():
    with_meta = PAGE + side({"customers": {"shape": "diamond", "note": "Admins only"}})
    assert validate_wireframe(with_meta) == validate_wireframe(PAGE)


@pytest.mark.parametrize("content,fmt,message", [
    (PAGE + side({"customers": {}}) + side({"customers": {}}), "wire", "Only one flow metadata line"),
    (PAGE + side({"customers": {}}) + '  text "after"\n', "wire", "must be the last line"),
    (PAGE + "# decisions-flow: {nope\n", "wire", "not valid JSON"),
    (PAGE + "# decisions-flow: " + json.dumps({"v": 1, "nodes": {"customers": {"note": "x" * 500}}, "pad": "y" * 17000}) + "\n", "wire", "larger than 16 KB"),
    (PAGE + side({"customers": {"shape": "hexagon"}}), "wire", 'Unknown node shape "hexagon"'),
    ("flowchart LR\n  A{Ok?} --> B\n" + side({"A": {"shape": "diamond"}}, "%% decisions-flow:"), "mermaid", "set in the diagram itself"),
    (PAGE + side({"customers": {"image": "42"}}), "wire", "Image 42 is not an attachment"),
    (PAGE + side({"customers": {"note": "x" * 501}}), "wire", "longer than 500 characters"),
    (PAGE + side({"customers": {"colour": "red"}}), "wire", 'Unknown flow metadata field "colour"'),
    (PAGE + "# decisions-flow: " + json.dumps({"v": 1, "nodes": {}, "extra": 1}) + "\n", "wire", 'Unknown flow metadata field "extra"'),
    (PAGE + "# decisions-flow: " + json.dumps({"v": 2, "nodes": {}}) + "\n", "wire", '"v": 1'),
    (PAGE + side({"9bad": {}}), "wire", "must start with a letter"),
    (PAGE + side({"customers": {"links": {"saved": "x" * 121}}}), "wire", "at most 120"),
])
def test_sidecar_rejections(content, fmt, message):
    with pytest.raises(PlanValidationError, match=message.replace("(", r"\(").replace('"', '"')):
        validate_flow_sidecar(content, fmt, asset_ids={"7"})


def test_valid_sidecar_and_orphan_warnings_only():
    ok = PAGE + side({"customers": {"shape": "diamond", "image": "7", "note": "Admins", "links": {"saved": "on save"}}})
    assert validate_flow_sidecar(ok, "wire", asset_ids={"7"}) == []
    orphan = PAGE + side({"customers": {}, "gone": {"shape": "circle"}})
    assert validate_flow_sidecar(orphan, "wire", asset_ids=set()) == ['Flow metadata for "gone" has no matching node; it will be removed on the next edit.']
    mermaid = "flowchart LR\n  A{Ok?} -->|yes| B([Done])\n" + side({"A": {"note": "check"}, "Z": {}}, "%% decisions-flow:")
    assert validate_flow_sidecar(mermaid, "mermaid", asset_ids=set()) == ['Flow metadata for "Z" has no matching node; it will be removed on the next edit.']
    assert validate_flow_sidecar(PAGE, "wire") == []                         # absent sidecar: nothing to do


@pytest.fixture
def wire_item(plan_workspace_db):
    workspace = service.ensure_workspace(board_key="decisions:71", board_provider="decisions", board_name="Flow", project_id=plan_workspace_db["project_id"])
    return service.create_item(workspace_id=workspace["id"], item_type="wireframe", title="Customers", content=PAGE)


@pytest.fixture
def client(plan_workspace_db):
    app = FastAPI(); router = APIRouter(prefix="/api"); register_routes(router, None); app.include_router(router)
    with TestClient(app) as c:
        yield c


def test_update_item_validates_wire_and_sidecar(client, wire_item):
    url = f"/api/workflows/studio/plan-items/{wire_item['id']}"
    bad_wire = client.patch(url, json={"content": 'screen "X"\n  wobble "nope"\n', "expected_revision": 1})
    assert bad_wire.status_code == 422 and "Invalid wireframe" in bad_wire.json()["detail"]
    bad_meta = client.patch(url, json={"content": PAGE + side({"customers": {"shape": "star"}}), "expected_revision": 1})
    assert bad_meta.status_code == 422 and "Unknown node shape" in bad_meta.json()["detail"]
    no_asset = client.patch(url, json={"content": PAGE + side({"customers": {"image": "99"}}), "expected_revision": 1})
    assert no_asset.status_code == 422 and "not an attachment of this project" in no_asset.json()["detail"]
    good = client.patch(url, json={"content": PAGE + side({"customers": {"shape": "diamond"}, "old": {}}), "expected_revision": 1})
    assert good.status_code == 200
    body = good.json()
    assert body["revision_count"] == 2 and "#| Lists every **customer**." in body["content"]
    assert body["validation_warnings"] == ['Flow metadata for "old" has no matching node; it will be removed on the next edit.']
    stale = client.patch(url, json={"content": PAGE, "expected_revision": 1})
    assert stale.status_code == 409


def test_agent_edits_validate_the_sidecar(wire_item):
    detail = service.get_workspace(wire_item["workspace_id"])
    revisions = {str(i["id"]): i["revision_count"] for i in detail["items"]}
    with pytest.raises(PlanValidationError, match="Unknown node shape"):
        service.apply_agent_edits(wire_item["workspace_id"], edits=[{"id": wire_item["id"], "content": PAGE + side({"customers": {"shape": "star"}})}],
                                  expected_revisions=revisions, instruction="t")
    results = service.apply_agent_edits(wire_item["workspace_id"], edits=[{"id": wire_item["id"], "content": PAGE + side({"customers": {"shape": "oval"}})}],
                                        expected_revisions=revisions, instruction="t")
    assert results[0]["revision_count"] == 2 and "validation_warnings" not in results[0]
