import json

from distr.core.agent.tools.integrations.development_control import DevelopmentControlTool
from distr.core.db import Settings, get_session


def test_development_control_creates_and_lists_without_activating_chat():
    with get_session() as db:
        settings = db.query(Settings).first()
        before = (settings.agent_current_chat_id, settings.last_chat_id)

    tool = DevelopmentControlTool()
    created = json.loads(tool._run(
        action="create_thread",
        title="Explicit orchestrator thread",
        instruction="Prepare the implementation scope",
    ))
    listed = json.loads(tool._run(action="list_threads"))

    assert created["surface"] == "development"
    assert any(row["thread_id"] == created["thread_id"] for row in listed["threads"])
    with get_session() as db:
        settings = db.query(Settings).first()
        assert (settings.agent_current_chat_id, settings.last_chat_id) == before


def test_development_delete_requires_explicit_confirmation():
    tool = DevelopmentControlTool()
    created = json.loads(tool._run(action="create_thread", title="Protected thread"))

    response = json.loads(tool._run(
        action="delete_thread",
        thread_id=created["thread_id"],
        confirm=False,
    ))

    assert response["confirmation_required"] is True
    assert response["thread_id"] == created["thread_id"]


def test_development_control_exposes_incoming_automations_and_workflows(monkeypatch):
    monkeypatch.setattr(
        "distr.core.incoming.service.list_development_incoming",
        lambda limit: {"items": [{"source": "gmail"}], "counts": {"gmail": 1}},
    )
    monkeypatch.setattr(
        "distr.core.automation.store.list_automations",
        lambda: [{"id": "auto_1", "name": "Inbox triage"}],
    )
    monkeypatch.setattr(
        "distr.core.workflow.service.list_workflows",
        lambda limit: [{"id": 4, "name": "Delivery"}],
    )
    tool = DevelopmentControlTool()

    incoming = json.loads(tool._run(action="check_incoming"))
    automations = json.loads(tool._run(action="list_automations"))
    workflows = json.loads(tool._run(action="list_workflows"))

    assert incoming["items"][0]["source"] == "gmail"
    assert automations["automations"][0]["id"] == "auto_1"
    assert workflows["workflows"][0]["id"] == 4


def test_development_control_manages_and_imports_scheduled_automations(monkeypatch):
    saved = {
        "id": "auto_9",
        "name": "Clockwork check",
        "status": "active",
        "action_config": {"run_in_new_thread": True},
    }

    def fake_create(**payload):
        saved.update(payload)
        return dict(saved)

    def fake_update(automation_id, **fields):
        assert automation_id == "auto_9"
        saved.update(fields)
        return dict(saved)

    prepared = []

    monkeypatch.setattr("distr.core.automation.store.create_automation", fake_create)
    monkeypatch.setattr("distr.core.automation.store.get_automation", lambda automation_id: dict(saved) if automation_id == "auto_9" else None)
    monkeypatch.setattr("distr.core.automation.store.update_automation", fake_update)
    monkeypatch.setattr("distr.core.automation.store.delete_automation", lambda automation_id: automation_id == "auto_9")
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.ensure_automation_thread",
        lambda automation: prepared.append(automation["id"]) or 73,
    )
    monkeypatch.setattr(
        "distr.core.automation.imports.import_automations",
        lambda sources: {"success": True, "imported_count": 1, "skipped_count": 2, "sources": sources},
    )
    tool = DevelopmentControlTool()

    created = json.loads(tool._run(
        action="create_automation",
        title="Clockwork check",
        instruction="Inspect the deployment.",
        schedule={"kind": "daily", "time": "09:00"},
        provider="openai",
        model="gpt-test",
        reasoning_effort="high",
        fallback_provider="anthropic",
        fallback_model="claude-test",
    ))
    fetched = json.loads(tool._run(action="get_automation", automation_id="auto_9"))
    updated = json.loads(tool._run(
        action="update_automation",
        automation_id="auto_9",
        title="Clockwork verification",
        schedule={"kind": "weekly", "time": "08:30", "days": "1,3,5"},
    ))
    paused = json.loads(tool._run(action="pause_automation", automation_id="auto_9"))
    protected = json.loads(tool._run(action="delete_automation", automation_id="auto_9"))
    imported = json.loads(tool._run(action="import_automations", sources=["codex", "cursor"]))
    deleted = json.loads(tool._run(action="delete_automation", automation_id="auto_9", confirm=True))

    assert created["automation"]["action_config"]["model"] == "gpt-test"
    assert created["automation"]["action_config"]["fallback_model"] == "claude-test"
    assert created["thread_id"] == 73
    assert prepared == ["auto_9", "auto_9"]
    assert fetched["automation"]["id"] == "auto_9"
    assert updated["thread_id"] == 73
    assert updated["automation"]["name"] == "Clockwork verification"
    assert updated["automation"]["action_config"]["reasoning_effort"] == "high"
    assert paused["automation"]["status"] == "paused"
    assert protected["confirmation_required"] is True
    assert protected["ui_confirmation_required"] is True
    assert imported["imported_count"] == 1
    assert imported["skipped_count"] == 2
    assert deleted["confirmation_required"] is True


def test_conversational_automation_creation_rolls_back_when_thread_fails(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        "distr.core.automation.store.create_automation",
        lambda **kwargs: {"id": "auto_77", "action_config": kwargs.get("action_config", {})},
    )
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.ensure_automation_thread",
        lambda automation: (_ for _ in ()).throw(ValueError("Thread preparation failed")),
    )
    monkeypatch.setattr(
        "distr.core.automation.store.delete_automation",
        lambda automation_id: deleted.append(automation_id) or True,
    )

    result = json.loads(DevelopmentControlTool()._run(
        action="create_automation",
        title="Must roll back",
        instruction="Run daily.",
        schedule={"kind": "daily", "time": "09:00"},
    ))

    assert result["error"] == "Thread preparation failed"
    assert deleted == ["auto_77"]
