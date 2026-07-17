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


def test_workflow_waiting_nudge_is_actionable():
    from distr.core.kanban.ticket_workflow_engagement import build_workflow_waiting_nudge

    text, voice = build_workflow_waiting_nudge(
        workflow_name="DecisionsAI dogfood 1781725452487",
        ticket_title="Dogfood smoke ticket",
        step_name="Dogfood route",
        waiting_kind="step_review",
    )
    assert "continue" in text.lower()
    assert "workflows" in text.lower()
    assert "continue" in voice.lower()
    assert "1781725452487" not in voice


def test_provider_preflight_nudge_says_no_work_started_and_offers_decision():
    from distr.core.kanban.ticket_workflow_engagement import build_workflow_waiting_nudge

    text, voice = build_workflow_waiting_nudge(
        workflow_name="Development",
        ticket_title="Fix checkout",
        waiting_kind="provider_preflight",
    )

    assert "no model work has started" in text.lower()
    assert "approve" in text.lower()
    assert "stop" in text.lower()
    assert "approve" in voice.lower()


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


def test_workflow_finished_persists_system_activity_with_run_id(monkeypatch):
    captured = []

    def _record(run_id, event_type, **kwargs):
        captured.append({"run_id": run_id, "event_type": event_type, **kwargs})

    monkeypatch.setattr(
        "distr.core.workflow.chat_trace.record_workflow_chat_event",
        _record,
    )
    monkeypatch.setattr(
        SignalBridgeMixin,
        "_workflow_report_run_metadata",
        lambda self, _text: {"run_id": 4054},
    )
    app = DummySignalApp(DummyTelegramManager())

    app._persist_workflow_system_activity(
        "That didn't work out.\nPolish step complete with evidence attached."
    )

    assert len(captured) == 1
    assert captured[0]["run_id"] == 4054
    assert captured[0]["event_type"] == "run_completed"
    assert captured[0]["status"] == "failed"
    assert "didn't work out" in captured[0]["summary"].lower()


def test_workflow_finished_persists_to_current_chat_without_run_id(monkeypatch):
    captured = []

    def _record(chat_id, event_type, **kwargs):
        captured.append({"chat_id": chat_id, "event_type": event_type, **kwargs})

    monkeypatch.setattr(
        "distr.core.workflow.chat_trace.record_chat_workflow_event",
        _record,
    )
    monkeypatch.setattr(
        SignalBridgeMixin,
        "_workflow_report_run_metadata",
        lambda self, _text: {},
    )
    app = DummySignalApp(DummyTelegramManager(), chat_id=44)
    monkeypatch.setattr(app, "_chat_id_exists", lambda chat_id: True)

    app._persist_workflow_system_activity("All done.\nValidation passed.")

    assert len(captured) == 1
    assert captured[0]["chat_id"] == 44
    assert captured[0]["event_type"] == "run_completed"
    assert captured[0]["status"] == "completed"
