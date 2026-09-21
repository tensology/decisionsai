"""Exercise the browser fixture's process and real API without launching a browser."""
import json
from urllib.request import Request, urlopen

from tests.ui.test_plan_workspace_playwright import plan_server


def test_isolated_ui_server_lifecycle_and_linked_fixture(tmp_path):
    server = plan_server.__wrapped__(tmp_path)
    url = next(server)
    def request(path, body=None):
        req = Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    try:
        base = "/api/workflows/studio/plan-workspaces/1"
        reply = request(base + "/messages", {"message": "Add a phone field", "provider": "ollama", "model_name": "fixture-model"})
        assert reply["status"] == "complete"
        workspace = request(base)
        assert {item["item_type"] for item in workspace["items"]} == {"wireframe", "erd", "frac"}
        assert all(item["revision_count"] == 2 for item in workspace["items"])
        assert all("phone" in item["content"].lower() for item in workspace["items"])
        body = {"provider": "ollama", "model_name": "fixture-model"}
        assert request(base + "/build", body)["created"] == 3
        repeated = request(base + "/build", body)
        assert (repeated["created"], repeated["reused"]) == (0, 3)
        assert request(base + "/conversation")["messages"][-1]["build_result"]["reused"] == 3
    finally:
        server.close()
