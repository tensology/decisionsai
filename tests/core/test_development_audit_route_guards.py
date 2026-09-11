from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.development import _workflow_support, workflows, workflow_steps


def _client(monkeypatch):
    router = APIRouter()
    workflows.register_routes(router, None)
    workflow_steps.register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    monkeypatch.setattr(workflows, "_is_audit_workflow", lambda _workflow_id: True)
    monkeypatch.setattr(_workflow_support, "_is_audit_workflow", lambda _workflow_id: True)
    return TestClient(app)


def test_audit_workflow_step_mutations_are_rejected(monkeypatch):
    client = _client(monkeypatch)
    base = "/api/workflows/17"

    responses = [
        client.post(f"{base}/steps", json={}),
        client.patch(f"{base}/steps/reorder", json={"step_ids": [1]}),
        client.patch(f"{base}/steps/1", json={"name": "changed"}),
        client.delete(f"{base}/steps/1"),
        client.post(f"{base}/steps/1/start-recording"),
        client.post(f"{base}/steps/1/stop-recording"),
    ]

    assert [response.status_code for response in responses] == [403] * len(responses)
    assert all("read-only" in response.json()["detail"].lower() for response in responses)
