import asyncio
from types import SimpleNamespace

from distr.core.project_cli_backends.base import BackendTaskResult


def test_run_project_task_records_backend_handoff(monkeypatch):
    from distr.core.project_cli_backends import registry

    class FakeBackend:
        id = "codex"
        name = "Codex"

        async def send_task(self, task, on_event=None):
            assert getattr(task, "handoff_event_id", None) == 111
            if on_event:
                on_event({"type": "message_end", "message": {"content": "done"}})
            return BackendTaskResult(True, "codex", "codex", output="Status: completed")

    events = []
    completed = []
    handoffs = []

    monkeypatch.setattr(registry, "get_backend", lambda backend_id: FakeBackend())
    monkeypatch.setattr(registry, "_git_status_short", lambda folder: [" M app.py"])
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.create_execution_session",
        lambda **kwargs: 77,
    )
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.append_execution_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.complete_execution_session",
        lambda *args, **kwargs: completed.append((args, kwargs)),
    )

    def fake_record_backend_handoff(**kwargs):
        handoffs.append(kwargs)
        return 111 if kwargs.get("event_type", "backend_handoff_created") == "backend_handoff_created" else 112

    monkeypatch.setattr("distr.core.orchestrator.record_backend_handoff", fake_record_backend_handoff)
    monkeypatch.setattr("distr.core.terminal.get_project_runtime_snapshot", lambda project_id: {})

    result = asyncio.run(
        registry.run_project_task(
            SimpleNamespace(id=3, name="Demo", folder_location="/tmp/demo", coding_backend="codex", coding_backend_model="auto"),
            "Fix the UI with token=abc123456789012345678901234567890.",
            workflow_id=4,
            run_id=5,
            step_id=6,
            ticket_id=7,
            backend_id_override="codex",
        )
    )

    assert result.success is True
    assert len(handoffs) == 2
    created = handoffs[0]
    updated = handoffs[1]
    assert created["status"] == "dispatched"
    assert created["packet"]["backend_id"] == "codex"
    assert created["packet"]["execution_session_id"] == 77
    assert "abc123456789012345678901234567890" not in created["packet"]["instruction"]
    assert updated["event_type"] == "backend_handoff_updated"
    assert updated["status"] == "completed"
    assert completed
