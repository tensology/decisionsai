from __future__ import annotations

from types import SimpleNamespace


def test_ambient_harness_event_resolves_project_from_folder_and_records_notification(monkeypatch):
    from distr.core.harness_events import HarnessEventPayload, record_harness_event

    emitted = []
    notifications = []
    learnings = []

    monkeypatch.setattr(
        "distr.core.harness_events._resolve_project_by_folder",
        lambda folder: {"id": 42, "name": "DecisionsAI", "folder_location": folder},
    )
    monkeypatch.setattr(
        "distr.core.harness_events.emit_orchestration_event",
        lambda **kwargs: emitted.append(kwargs) or 123,
    )
    monkeypatch.setattr(
        "distr.core.harness_events.emit_user_notification",
        lambda **kwargs: notifications.append(kwargs) or 124,
    )
    monkeypatch.setattr(
        "distr.core.harness_events.record_learning_signal",
        lambda **kwargs: learnings.append(kwargs),
    )

    result = record_harness_event(
        HarnessEventPayload(
            harness="codex",
            event_type="codex_completed",
            status="completed",
            message="Refined the skill registry merge.",
            project_folder="/repo/DecisionsAI",
            source="ambient",
        )
    )

    assert result["success"] is True
    assert result["attachment"] == "project"
    assert result["project_id"] == 42
    assert emitted[0]["project_id"] == 42
    assert emitted[0]["workflow_id"] is None
    assert emitted[0]["payload"]["source"] == "ambient"
    assert notifications[0]["channel"] == "telegram"
    assert "Codex" in notifications[0]["text"]
    assert learnings[0]["scope"] == "project"
    assert learnings[0]["scope_id"] == 42


def test_unattached_harness_event_records_global_ambient_event(monkeypatch):
    from distr.core.harness_events import HarnessEventPayload, record_harness_event

    emitted = []
    notifications = []

    monkeypatch.setattr("distr.core.harness_events._resolve_project_by_folder", lambda folder: None)
    monkeypatch.setattr(
        "distr.core.harness_events.emit_orchestration_event",
        lambda **kwargs: emitted.append(kwargs) or 55,
    )
    monkeypatch.setattr(
        "distr.core.harness_events.emit_user_notification",
        lambda **kwargs: notifications.append(kwargs) or 56,
    )
    monkeypatch.setattr("distr.core.harness_events.record_learning_signal", lambda **kwargs: None)

    result = record_harness_event(
        HarnessEventPayload(
            harness="cursor",
            event_type="worker_progress",
            message="Started a standalone exploration.",
            project_folder="/unknown/repo",
            source="ambient",
        )
    )

    assert result["attachment"] == "ambient"
    assert result["project_id"] is None
    assert emitted[0]["source"] == "cursor"
    assert emitted[0]["payload"]["attachment"] == "ambient"
    assert notifications[0]["channel"] == "telegram"
    assert "outside a tracked workflow" in notifications[0]["text"]


def test_workflow_harness_event_keeps_workflow_attachment(monkeypatch):
    from distr.core.harness_events import HarnessEventPayload, record_harness_event

    emitted = []
    notifications = []

    monkeypatch.setattr(
        "distr.core.harness_events._resolve_project_by_folder",
        lambda folder: {"id": 7, "name": "Project", "folder_location": folder},
    )
    monkeypatch.setattr(
        "distr.core.harness_events.emit_orchestration_event",
        lambda **kwargs: emitted.append(kwargs) or 77,
    )
    monkeypatch.setattr(
        "distr.core.harness_events.emit_user_notification",
        lambda **kwargs: notifications.append(kwargs) or 78,
    )
    monkeypatch.setattr("distr.core.harness_events.record_learning_signal", lambda **kwargs: None)

    result = record_harness_event(
        HarnessEventPayload(
            harness="claude",
            event_type="codex_needs_input",
            message="Need approval to change the API contract.",
            workflow_id=1,
            run_id=2,
            step_id=3,
            project_folder="/repo/project",
            source="workflow",
        )
    )

    assert result["attachment"] == "workflow"
    assert emitted[0]["workflow_id"] == 1
    assert emitted[0]["run_id"] == 2
    assert emitted[0]["step_id"] == 3
    assert emitted[0]["project_id"] == 7
    assert notifications[0]["channel"] == "telegram"
