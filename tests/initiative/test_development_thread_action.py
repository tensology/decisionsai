from __future__ import annotations

from distr.core.initiative.action_handlers import control_development_thread, execute_initiative_action


def test_initiative_can_steer_a_development_thread_through_durable_queue(monkeypatch):
    captured = {}

    def enqueue(chat_id, instruction, **kwargs):
        captured.update({"chat_id": chat_id, "instruction": instruction, **kwargs})
        return {"id": 91, "chat_id": chat_id, "workflow_id": 8}

    monkeypatch.setattr("distr.core.workflow.development_control.enqueue_command", enqueue)
    monkeypatch.setattr(
        "distr.core.workflow.development_control.dispatch_command",
        lambda command_id: {"dispatched": True, "command": {"id": command_id, "run_id": 77}},
    )

    result = control_development_thread({
        "operation": "steer",
        "chat_id": 17,
        "instruction": "Preserve the compact navigation and rerun Playwright",
        "action_id": "initiative-4",
    })

    assert result["success"] is True
    assert result["delivery"]["command"]["run_id"] == 77
    assert captured == {
        "chat_id": 17,
        "instruction": "Preserve the compact navigation and rerun Playwright",
        "source": "initiative",
        "source_ref": "initiative-4",
    }


def test_initiative_thread_control_requires_routine_work_authority():
    try:
        execute_initiative_action(
            action_type="development_thread_control",
            description="Steer the thread",
            payload={"operation": "steer", "chat_id": 17, "instruction": "Continue"},
            settings={"initiative_allow_routine_tasks": False},
        )
    except PermissionError as exc:
        assert "development thread control is disabled" in str(exc)
    else:
        raise AssertionError("thread control must not bypass Initiative authority")
