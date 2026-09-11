from __future__ import annotations

from distr.core.workflow.work_dispatch import dispatch_work_item


def test_dispatch_prepares_thread_before_starting_run(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch._resolve_scope",
        lambda **kwargs: {
            "title": "Open-ended work",
            "project_id": None,
            "board_id": None,
            "board_provider": None,
            "board_key": None,
            "board_ticket_key": None,
            "board_ticket_title": None,
            "board_ticket_lane": None,
        },
    )

    def ensure(**kwargs):
        calls.append(("thread", kwargs))
        return 17

    def start(workflow_id, **kwargs):
        calls.append(("run", {"workflow_id": workflow_id, **kwargs}))
        return {"run_id": 99, "status": "running"}

    monkeypatch.setattr("distr.core.workflow.development_threads.ensure_development_thread", ensure)
    monkeypatch.setattr("distr.core.workflow.service.start_workflow_run", start)
    monkeypatch.setattr(
        "distr.core.workflow.development_control.resume_thread_time",
        lambda chat_id, **_kwargs: calls.append(("time", {"chat_id": chat_id})) or {"chat_id": chat_id},
    )

    result = dispatch_work_item(
        workflow_id=44,
        context="Implement the change",
        source_type="initiative",
        source_ref="proposal-9",
        dispatch_async=True,
    )

    assert [kind for kind, _ in calls] == ["thread", "run", "time"]
    assert calls[1][1]["run_metadata"]["chat_id"] == 17
    assert calls[1][1]["run_metadata"]["work_item_identity"] == "source:initiative:proposal-9"
    orchestration = calls[1][1]["run_metadata"]["orchestration"]
    assert orchestration["owner_chat_id"] == 17
    assert orchestration["agent_role"] == "workflow_orchestrator"
    assert orchestration["execution_kind"] == "deterministic_orchestrator_subagent"
    assert result["development_url"] == "/development/threads/17/"


def test_dispatch_never_starts_run_when_thread_preparation_fails(monkeypatch):
    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch._resolve_scope",
        lambda **kwargs: {
            "title": "Broken preparation",
            "project_id": None,
            "board_id": None,
            "board_provider": None,
            "board_key": None,
            "board_ticket_key": None,
            "board_ticket_title": None,
            "board_ticket_lane": None,
        },
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_threads.ensure_development_thread",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )
    starts = []
    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    import pytest

    with pytest.raises(RuntimeError, match="thread unavailable"):
        dispatch_work_item(workflow_id=44, context="Do not run")
    assert starts == []
