from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.settings.workflows import register_routes


class _FakeSession:
    def __init__(self, chat):
        self.chat = chat

    def get(self, model, object_id):
        if getattr(model, "__name__", "") == "Chat":
            return self.chat
        if getattr(model, "__name__", "") == "AutoWorkflow" and int(object_id) == 44:
            return SimpleNamespace(id=44)
        return None

    def commit(self):
        return None

    def query(self, _model):
        class _Query:
            def filter(self, *_args, **_kwargs):
                return self

            def order_by(self, *_args, **_kwargs):
                return self

            def first(self):
                return SimpleNamespace(id=44)

        return _Query()


class _DetachingChat(SimpleNamespace):
    """Mimic an expired SQLAlchemy row once its request session closes."""

    detached = False

    def __init__(self, **kwargs):
        project_id = kwargs.pop("project_id", None)
        super().__init__(**kwargs)
        self._project_id = project_id

    @property
    def project_id(self):
        if self.detached:
            raise RuntimeError("project_id accessed after the session closed")
        return self._project_id

    @project_id.setter
    def project_id(self, value):
        self._project_id = value


def _client() -> TestClient:
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_studio_incoming_route_is_registered_and_returns_channel_data(monkeypatch):
    monkeypatch.setattr(
        "distr.core.incoming.service.list_development_incoming",
        lambda limit: {
            "items": [{"key": "whatsapp:1", "source": "whatsapp"}],
            "links": [{"id": 2, "source": "whatsapp"}],
            "counts": {"whatsapp": 1},
        },
    )

    response = _client().get("/api/workflows/studio/incoming?limit=3")

    assert response.status_code == 200
    assert response.json()["items"][0]["key"] == "whatsapp:1"
    assert response.json()["links"][0]["id"] == 2


def test_studio_attachment_is_bounded_and_returns_harness_readable_path():
    response = _client().post(
        "/api/workflows/studio/attachments",
        data={"project_id": "7"},
        files={"file": ("reference.png", b"fake-png", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "reference.png"
    assert payload["mime_type"] == "image/png"
    assert payload["size"] == 8
    attachment = Path(payload["path"])
    assert attachment.parent == Path.home() / ".decisions" / "workspaces" / "projects" / "7" / "attachments"
    assert attachment.read_bytes() == b"fake-png"
    attachment.unlink(missing_ok=True)


def test_studio_attachment_accepts_video_as_a_normal_file():
    response = _client().post(
        "/api/workflows/studio/attachments",
        files={"file": ("walkthrough.mp4", b"fake-video", "video/mp4")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "walkthrough.mp4"
    assert payload["mime_type"] == "video/mp4"
    attachment = Path(payload["path"])
    assert attachment.read_bytes() == b"fake-video"
    attachment.unlink(missing_ok=True)


def test_studio_auto_routing_assessment_escalates_failures_and_vision_work():
    response = _client().post(
        "/api/workflows/studio/routing-assessment",
        json={
            "instruction": "Fix the production authentication migration. The regression is failing and the agent is blocked.",
            "ticket_title": "Repair production auth migration",
            "ticket_description": "There is a traceback and a security risk.",
            "has_images": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["complexity"] == "high"
    assert payload["operational_state"] == "blocked"
    assert set(payload["signals"]) >= {"blocked", "failure", "risk", "vision"}
    assert payload["route"]["model"] == "auto"
    assert payload["sentiment"] == "blocked"
    assert payload["execution_mode"] == "workflow"


def test_studio_auto_routing_assessment_keeps_small_copy_edit_low():
    response = _client().post(
        "/api/workflows/studio/routing-assessment",
        json={"instruction": "Fix a typo in the button text."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["complexity"] == "low"
    assert payload["operational_state"] == "neutral"
    assert payload["execution_mode"] == "direct"


def test_studio_direct_execution_state_and_stop_are_thread_scoped(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.development_execution_state",
        lambda chat_id: {
            "id": "direct-17",
            "job_id": "direct-17",
            "status": "running",
            "direct": True,
            "chat_id": chat_id,
        },
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.stop_development_execution",
        lambda chat_id: calls.append(chat_id) or {
            "id": "direct-17",
            "status": "cancelled",
            "direct": True,
            "stopped": True,
        },
    )

    client = _client()
    state = client.get("/api/workflows/studio/tasks/17/execution")
    stopped = client.post("/api/workflows/studio/tasks/17/execution/stop")

    assert state.status_code == 200
    assert state.json()["chat_id"] == 17
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
    assert calls == [17]


def test_studio_development_lifecycle_routes_are_thread_scoped(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "distr.core.workflow.development_control.fork_thread",
        lambda chat_id, title=None: calls.append(("fork", chat_id, title)) or {"ok": True, "id": 23},
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_control.delete_thread",
        lambda chat_id, delete_linked_ticket=True: calls.append(("delete", chat_id, delete_linked_ticket)) or {"deleted": True, "chat_id": chat_id},
    )

    client = _client()
    forked = client.post("/api/workflows/studio/tasks/17/fork", json={"title": "Forked task"})
    deleted = client.delete("/api/workflows/studio/tasks/17?delete_linked_ticket=false")

    assert forked.status_code == 200
    assert forked.json()["id"] == 23
    assert deleted.status_code == 200
    assert deleted.json()["chat_id"] == 17
    assert calls == [("fork", 17, "Forked task"), ("delete", 17, False)]


def test_studio_change_review_and_undo_are_thread_scoped(monkeypatch):
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.development_change_review",
        lambda chat_id: {"files": [{"path": "index.html"}], "diff": "diff --git", "chat_id": chat_id},
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.undo_development_changes",
        lambda chat_id: calls.append(("undo", chat_id)) or {"ok": True, "changes": {"undone": True}},
    )

    client = _client()
    review = client.get("/api/workflows/studio/tasks/17/execution/changes")
    undo = client.post("/api/workflows/studio/tasks/17/execution/changes/undo")

    assert review.status_code == 200
    assert review.json()["chat_id"] == 17
    assert undo.status_code == 200
    assert undo.json()["changes"]["undone"] is True
    assert calls == [("undo", 17)]


def _patch_studio_dependencies(
    monkeypatch,
    *,
    autonomy="full",
    workflow_id=None,
    project_id=None,
    detach_on_exit=False,
):
    calls = {"update_workflow": [], "dispatch": [], "workflow_dispatch": []}
    chat_type = _DetachingChat if detach_on_exit else SimpleNamespace
    chat = chat_type(
        id=17,
        title="Rebuild Studio",
        project_id=project_id,
        provider="openai",
        model_name="gpt-test",
        route_mode="auto",
        execution_profile="code",
        autonomy_level=autonomy,
        created_date=None,
        modified_date=None,
        parent_id=None,
        params=json.dumps({"development": {"workflow_id": workflow_id, "ticket_id": None}}),
    )
    calls["chat"] = chat

    @contextlib.contextmanager
    def fake_session():
        try:
            yield _FakeSession(chat)
        finally:
            if detach_on_exit:
                chat.detached = True

    def fake_create_new_chat(**kwargs):
        calls["chat_kwargs"] = kwargs
        return 17, kwargs.get("starting_question")

    workflow = {
        "id": 44,
        "chat_id": None,
        "name": "Rebuild Studio",
        "run_settings": {},
        "steps": [{"id": 71, "name": "Inspect"}],
    }

    monkeypatch.setattr("distr.core.chat.ChatService.create_new_chat", fake_create_new_chat)
    monkeypatch.setattr(
        "distr.core.chat.ChatService.add_user_message",
        lambda chat_id, message, **kwargs: calls.setdefault("messages", []).append((chat_id, message)),
    )
    monkeypatch.setattr("distr.core.chat.ChatService.append_assistant_notice", lambda *args, **kwargs: True)
    monkeypatch.setattr("distr.core.workflow.developer_workflow.resolve_development_workflow", lambda **kwargs: 44)
    monkeypatch.setattr("distr.core.workflow.service.get_workflow", lambda workflow_id: workflow)
    monkeypatch.setattr(
        "distr.core.workflow.service.update_workflow",
        lambda workflow_id, **kwargs: calls["update_workflow"].append((workflow_id, kwargs)) or True,
    )
    monkeypatch.setattr("distr.core.workflow.service.update_step", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "distr.core.workflow.studio_artifacts.seed_studio_artifacts",
        lambda **kwargs: [{"id": 1, "artifact_type": "tasks", "status": "ready"}],
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_plans.create_plan_revision",
        lambda **kwargs: {"id": 3, "chat_id": kwargs["chat_id"], "workflow_id": kwargs["workflow_id"], "revision": 1, "mode": kwargs["mode"], "status": kwargs["status"]},
    )
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"conversational_llm_provider": "openai", "conversational_llm_model": "gpt-test"})
    monkeypatch.setattr("distr.core.db.get_session", fake_session)
    monkeypatch.setattr("distr.core.workflow.development_threads.get_session", fake_session)
    def fake_mark_development_thread(chat_id, **updates):
        params = json.loads(chat.params or "{}")
        development = params.setdefault("development", {})
        development["workflow_id"] = updates.get("workflow_id")
        development["ticket_id"] = updates.get("ticket_id")
        for key in (
            "board_key",
            "board_provider",
            "board_ticket_key",
            "board_ticket_title",
            "board_ticket_lane",
        ):
            if updates.get(key):
                development[key] = updates[key]
        chat.params = json.dumps(params)

    monkeypatch.setattr(
        "distr.core.workflow.development_threads.mark_development_thread",
        fake_mark_development_thread,
    )
    def fake_update_thread_controls(chat_id, **updates):
        params = json.loads(chat.params or "{}")
        params.setdefault("development", {}).update(updates)
        chat.params = json.dumps(params)
        return params["development"]

    monkeypatch.setattr("distr.core.workflow.development_control.update_thread_controls", fake_update_thread_controls)
    monkeypatch.setattr(
        "distr.core.workflow.development_control.resume_thread_time",
        lambda chat_id: calls.setdefault("time_resumed", []).append(chat_id) or {"chat_id": chat_id, "paused": False},
    )
    monkeypatch.setattr("distr.gui.web.workflow_events.increment_workflow_updated", lambda: None)
    def fake_dispatch_development_prompt(chat_id, prompt, **kwargs):
        calls["dispatch"].append((chat_id, prompt, kwargs))
        return {"id": "direct-99", "job_id": "direct-99", "status": "initializing", "direct": True}

    monkeypatch.setattr(
        "distr.core.workflow.development_harness.dispatch_development_prompt",
        fake_dispatch_development_prompt,
    )
    def fake_dispatch_work_item(**kwargs):
        calls["workflow_dispatch"].append(kwargs)
        return {"run_id": 99, "status": "running", "chat_id": kwargs["chat_id"]}

    monkeypatch.setattr("distr.core.workflow.work_dispatch.dispatch_work_item", fake_dispatch_work_item)
    return calls


def test_studio_task_creates_thread_and_starts_direct_ide_harness(monkeypatch):
    calls = _patch_studio_dependencies(monkeypatch, autonomy="full")

    response = _client().post(
        "/api/workflows/studio/tasks",
        json={
            "prompt": "Rebuild the workflow UI",
            "title": "Rebuild Studio",
            "board_key": "jira:engineering",
            "board_provider": "jira",
            "board_ticket_key": "DEV-42",
            "board_ticket_title": "Fix the development sidebar",
            "board_ticket_lane": "In progress",
            "route_mode": "auto",
            "execution_profile": "code",
            "autonomy_level": "full",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["id"] == 17
    assert payload["workflow"] is None
    assert payload["turn"]["direct"] is True
    assert "run" not in payload
    assert payload["execution"]["job_id"] == "direct-99"
    assert payload["artifacts"] == []
    assert payload["plan_revision"] is None
    assert payload["started"] is True
    assert calls["dispatch"] == [(17, "Rebuild the workflow UI", {
        "routing_assessment": {},
        "attachments": [],
        "skill_ids": [],
        "use_playwright": False,
        "dispatch_async": True,
    })]
    assert calls["chat_kwargs"]["starting_question"] == "Rebuild the workflow UI"
    assert calls["chat_kwargs"]["activate"] is False
    assert calls["update_workflow"] == []
    development = json.loads(calls["chat"].params)["development"]
    assert development["board_key"] == "jira:engineering"
    assert development["board_ticket_key"] == "DEV-42"
    assert development["board_ticket_lane"] == "In progress"


def test_studio_task_with_explicit_workflow_dispatches_the_workflow_runner(monkeypatch):
    calls = _patch_studio_dependencies(monkeypatch, autonomy="full", workflow_id=44)

    response = _client().post(
        "/api/workflows/studio/tasks",
        json={"prompt": "Implement and audit the ticket", "workflow_id": 44, "autonomy_level": "full"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow"] == {"id": 44}
    assert payload["turn"]["run_id"] == 99
    assert calls["dispatch"] == []
    assert calls["workflow_dispatch"] == [{
        "workflow_id": 44,
        "chat_id": 17,
        "context": "Implement and audit the ticket",
        "project_id": None,
        "ticket_id": None,
        "source_type": "studio_thread",
        "run_metadata": {"skill_ids": [], "routing_assessment": {}},
        "dispatch_async": True,
    }]


def test_studio_plan_only_uses_the_direct_agent_harness(monkeypatch):
    calls = _patch_studio_dependencies(monkeypatch, autonomy="plan")

    response = _client().post(
        "/api/workflows/studio/tasks",
        json={
            "prompt": "Design the new workflow UI",
            "execution_profile": "design",
            "autonomy_level": "plan",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["started"] is True
    assert payload["turn"]["direct"] is True
    assert "run" not in payload
    assert calls["dispatch"] == [(17, "Design the new workflow UI", {
        "routing_assessment": {},
        "attachments": [],
        "skill_ids": [],
        "use_playwright": False,
        "dispatch_async": True,
    })]


def test_studio_thread_edit_rebinds_ticket_scope(monkeypatch):
    captured = {}

    def fake_rebind(chat_id, **updates):
        captured.update({"chat_id": chat_id, **updates})
        return {
            "chat_id": chat_id,
            "title": updates["title"],
            "project_id": 7,
            "identity_key": "ticket:jira:engineering:dev-42",
            "board_key": "engineering",
            "board_provider": "jira",
            "ticket_id": None,
            "board_ticket_key": "DEV-42",
            "permission_profile": updates["permission_profile"],
            "remote_continuation": updates["remote_continuation"],
        }

    monkeypatch.setattr("distr.core.workflow.development_threads.rebind_development_thread", fake_rebind)
    response = _client().patch(
        "/api/workflows/studio/tasks/17",
        json={
            "title": "Implement DEV-42",
            "project_id": 7,
            "board_key": "engineering",
            "board_provider": "jira",
            "board_ticket_key": "DEV-42",
            "permission_profile": {"mode": "trusted"},
            "remote_continuation": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["identity_key"] == "ticket:jira:engineering:dev-42"
    assert captured == {
        "chat_id": 17,
        "title": "Implement DEV-42",
        "project_id": 7,
        "board_key": "engineering",
        "board_provider": "jira",
        "ticket_id": None,
        "board_ticket_key": "DEV-42",
        "board_ticket_title": None,
        "board_ticket_lane": None,
        "permission_profile": {"mode": "trusted"},
        "remote_continuation": True,
    }


def test_studio_thread_rename_preserves_ticket_scope(monkeypatch):
    captured = {}

    def fake_update_settings(chat_id, **updates):
        captured.update({"chat_id": chat_id, **updates})
        return {"chat_id": chat_id, "title": updates["title"], "board_key": "engineering", "board_provider": "jira", "board_ticket_key": "DEV-42"}

    monkeypatch.setattr("distr.core.workflow.development_threads.update_development_thread_settings", fake_update_settings)
    response = _client().patch("/api/workflows/studio/tasks/17", json={"title": "Renamed thread"})

    assert response.status_code == 200
    assert captured == {"chat_id": 17, "title": "Renamed thread", "permission_profile": None, "remote_continuation": None}


def test_existing_development_thread_message_resumes_direct_harness_not_workflow(monkeypatch):
    calls = _patch_studio_dependencies(monkeypatch, autonomy="full")

    response = _client().post(
        "/api/workflows/studio/tasks/17/messages",
        json={"message": "Implement the ticket and verify it in the browser"},
    )

    assert response.status_code == 200
    assert calls["messages"] == [(17, "Implement the ticket and verify it in the browser")]
    assert calls["dispatch"] == [(17, "Implement the ticket and verify it in the browser", {
        "routing_assessment": {},
        "attachments": [],
        "skill_ids": [],
        "use_playwright": False,
        "dispatch_async": True,
    })]


def test_existing_thread_message_uses_its_explicit_workflow(monkeypatch):
    calls = _patch_studio_dependencies(monkeypatch, autonomy="full", workflow_id=44)

    response = _client().post(
        "/api/workflows/studio/tasks/17/messages",
        json={"message": "Implement the ticket and audit the result"},
    )

    assert response.status_code == 200
    assert calls["dispatch"] == []
    assert calls["workflow_dispatch"][0] == {
        "workflow_id": 44,
        "chat_id": 17,
        "context": "Implement the ticket and audit the result",
        "project_id": None,
        "ticket_id": None,
        "source_type": "studio_thread",
        "run_metadata": {"skill_ids": [], "routing_assessment": {}},
        "dispatch_async": True,
    }


def test_workflow_thread_copies_project_id_before_its_session_closes(monkeypatch):
    calls = _patch_studio_dependencies(
        monkeypatch,
        autonomy="full",
        workflow_id=44,
        project_id=23,
        detach_on_exit=True,
    )

    response = _client().post(
        "/api/workflows/studio/tasks/17/messages",
        json={"message": "Continue the linked workflow"},
    )

    assert response.status_code == 200
    assert calls["workflow_dispatch"][0]["project_id"] == 23


def test_existing_plan_only_thread_resumes_the_direct_read_only_agent(monkeypatch):
    calls = _patch_studio_dependencies(monkeypatch, autonomy="plan")

    response = _client().post(
        "/api/workflows/studio/tasks/17/messages",
        json={"message": "Revise the architecture and produce a new implementation plan"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["started"] is True
    assert payload["turn"]["direct"] is True
    assert calls["messages"] == [(17, "Revise the architecture and produce a new implementation plan")]
    assert calls["dispatch"] == [(17, "Revise the architecture and produce a new implementation plan", {
        "routing_assessment": {},
        "attachments": [],
        "skill_ids": [],
        "use_playwright": False,
        "dispatch_async": True,
    })]
    assert calls["update_workflow"] == []


def test_ordinary_chat_cannot_start_development_harness(monkeypatch):
    calls = _patch_studio_dependencies(monkeypatch, autonomy="full")
    calls["chat"].params = "{}"

    response = _client().post(
        "/api/workflows/studio/tasks/17/messages",
        json={"message": "Do not route this through chat"},
    )

    assert response.status_code == 409
    assert calls.get("messages") is None
    assert calls["dispatch"] == []


def test_studio_task_rejects_invalid_execution_contract():
    response = _client().post(
        "/api/workflows/studio/tasks",
        json={
            "prompt": "Do something",
            "route_mode": "mystery",
            "execution_profile": "code",
            "autonomy_level": "full",
        },
    )

    assert response.status_code == 422
    assert "route_mode" in response.json()["detail"]


def test_studio_control_state_exposes_safe_telegram_and_pending_interaction(monkeypatch):
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"connected_accounts": '[{"provider":"telegram","token":"secret"}]'},
    )
    monkeypatch.setattr(
        "distr.core.workflow.interactions.pending_interactions",
        lambda: [
            {
                "token": "abc123",
                "run_id": 99,
                "step_id": 71,
                "kind": "approval",
                "allowed_actions": '["approve","stop","feedback"]',
                "telegram_chat_id": "987654321",
                "expires_at": 123456.0,
                "error": None,
            },
            {"token": "other", "run_id": 100, "allowed_actions": "[]"},
        ],
    )
    manager = SimpleNamespace(is_connected=lambda: True)
    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: manager,
    )

    response = _client().get("/api/workflows/studio/control-state?run_id=99")

    assert response.status_code == 200
    payload = response.json()
    assert payload["channels"]["telegram"] == {"configured": True, "connected": True}
    assert payload["interactions"] == [
        {
            "token": "abc123",
            "run_id": 99,
            "step_id": 71,
            "kind": "approval",
            "allowed_actions": ["approve", "stop", "feedback"],
            "expires_at": 123456.0,
            "error": None,
            "telegram_linked": True,
        }
    ]
    assert "secret" not in response.text
    assert "987654321" not in response.text


def test_studio_web_resolution_uses_the_same_durable_interaction(monkeypatch):
    captured = {}

    def fake_resolve_interaction(**kwargs):
        captured.update(kwargs)
        return {"success": True, "run_id": 99, "action": kwargs["action"], "queued": True}

    monkeypatch.setattr(
        "distr.core.workflow.interactions.resolve_interaction",
        fake_resolve_interaction,
    )

    response = _client().post(
        "/api/workflows/studio/interactions/abc123/resolve",
        json={"action": "feedback", "response_text": "Keep the existing navigation"},
    )

    assert response.status_code == 200
    assert captured == {
        "token": "abc123",
        "action": "feedback",
        "response_text": "Keep the existing navigation",
        "source": "web",
        "resolver_id": "development-ui",
        "background": True,
    }
