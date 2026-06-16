import pytest

from distr.app.signals import SignalBridgeMixin
from distr.app.workflow import WorkflowOrchestrationMixin


@pytest.fixture(autouse=True)
def _reset_engagement_ledger():
    from distr.core.human_engagement import reset_engagement_ledger

    reset_engagement_ledger()
    yield
    reset_engagement_ledger()


class DummyTelegramManager:
    def __init__(self, connected=True):
        self.connected = connected
        self.sent = []

    def is_connected(self):
        return self.connected

    def send_to_telegram(self, text=None, *args, **kwargs):
        self.sent.append(text if text is not None else (args[0] if args else ""))


class DummyWorkflowApp(WorkflowOrchestrationMixin):
    def __init__(self, manager):
        self.telegram_manager = manager


class DummySignalApp(SignalBridgeMixin):
    def __init__(self, manager, chat_id=None):
        self.telegram_manager = manager
        self.chat_manager = DummyChatManager(chat_id)


class DummyChatManager:
    def __init__(self, chat_id):
        self.chat_id = chat_id

    def get_current_chat(self):
        return self.chat_id


def test_workflow_waiting_state_notifies_with_continue_prompt(monkeypatch):
    captured = []

    def _capture_notify(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress",
        _capture_notify,
    )
    app = DummyWorkflowApp(DummyTelegramManager())

    app._on_step_waiting_for_feedback(
        step_id=12,
        workflow_id=5,
        run_id=99,
        result_text="Validation passed. I need approval before deploying.",
    )

    assert len(captured) == 1
    message = captured[0]["body"]
    assert "needs your input" in message
    assert "adjust direction" in message
    assert captured[0]["requires_response"] is True


def test_workflow_waiting_ide_handoff_uses_plain_english(monkeypatch):
    captured = []

    def _capture_notify(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress",
        _capture_notify,
    )
    app = DummyWorkflowApp(DummyTelegramManager())

    app._on_step_waiting_for_feedback(
        step_id=1876,
        workflow_id=353,
        run_id=4054,
        result_text=(
            "Opened Cursor IDE with your work packet.\n"
            "Packet: /tmp/decisionsai_cursor_ide.md\n"
            "IDE opened: yes"
        ),
    )

    assert len(captured) == 1
    message = captured[0]["body"].lower()
    assert "opened cursor" in message
    assert "run: 4054" not in message


def test_workflow_waiting_state_still_notifies_when_telegram_disconnected(monkeypatch):
    captured = []

    def _capture_notify(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress",
        _capture_notify,
    )
    manager = DummyTelegramManager(connected=False)
    app = DummyWorkflowApp(manager)

    app._on_step_waiting_for_feedback(
        step_id=12,
        workflow_id=5,
        run_id=99,
        result_text="Needs input.",
    )

    assert len(captured) == 1


def test_workflow_completion_report_sends_concise_telegram_summary():
    manager = DummyTelegramManager()
    app = DummySignalApp(manager)

    app._send_workflow_report_to_telegram(
        5,
        "Done - the workflow finished successfully. Session 5, run 99.\n"
        "Steps (2):\n"
        "  1. Implement: updated index.html.\n"
        "  2. Validate: checks passed.",
    )

    assert len(manager.sent) == 1
    message = manager.sent[0]
    assert "Done - the workflow finished successfully." in message
    assert "Implement" in message
    assert "Validate" in message
    assert "Give a brief" not in message


def test_workflow_completion_report_does_not_read_voice_note_payload_verbatim():
    manager = DummyTelegramManager()
    app = DummySignalApp(manager)

    app._send_workflow_report_to_telegram(
        350,
        "Done - the workflow finished successfully. Session 350, run 149.\n"
        "Steps (1):\n"
        "  1. Automation Instruction: It sent a Telegram voice note with the requested message.",
    )

    assert len(manager.sent) == 1
    message = manager.sent[0]
    assert "It sent a Telegram voice note with the requested message." in message
    assert "Hey babe" not in message
    assert "That's what happened" not in message


def test_automation_workflow_report_is_not_sent_to_telegram():
    import json

    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

    with get_session() as session:
        workflow = AutoWorkflow(name="Screen Compliment", workflow_type="scheduled", status="active")
        session.add(workflow)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="failed",
            run_data=json.dumps({
                "source_type": "automation",
                "phase": "scheduled_automation",
                "message": "Automation failed.",
            }),
        )
        session.add(run)
        session.commit()
        run_id = run.id

    manager = DummyTelegramManager()
    app = DummySignalApp(manager)

    app._send_workflow_report_to_telegram(
        353,
        f"The workflow failed. Session 353, run {run_id}.\n"
        "Automation Instruction: failed to send voice note.",
    )

    assert manager.sent == []


def test_workflow_report_agent_payload_targets_current_chat(monkeypatch):
    app = DummySignalApp(DummyTelegramManager(), chat_id=44)
    monkeypatch.setattr(app, "_chat_id_exists", lambda chat_id: True)

    payload = app._workflow_report_agent_payload("Workflow finished.")

    assert payload["text"].startswith("The workflow just finished.")
    assert "Workflow finished." in payload["text"]
    assert payload["speak"] is False
    assert payload["chat_id"] == 44


def test_workflow_report_agent_payload_skips_stale_current_chat(monkeypatch):
    app = DummySignalApp(DummyTelegramManager(), chat_id=404)
    monkeypatch.setattr(app, "_chat_id_exists", lambda chat_id: False)

    payload = app._workflow_report_agent_payload("Workflow finished.")

    assert payload["text"].startswith("The workflow just finished.")
    assert "Workflow finished." in payload["text"]
    assert payload["speak"] is False
    assert "chat_id" not in payload
