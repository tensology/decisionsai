from distr.app.signals import SignalBridgeMixin
from distr.app.workflow import WorkflowOrchestrationMixin


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
    def __init__(self, manager):
        self.telegram_manager = manager


def test_workflow_waiting_state_notifies_telegram_with_continue_prompt():
    manager = DummyTelegramManager()
    app = DummyWorkflowApp(manager)

    app._on_step_waiting_for_feedback(
        step_id=12,
        workflow_id=5,
        run_id=99,
        result_text="Validation passed. I need approval before deploying.",
    )

    assert len(manager.sent) == 1
    message = manager.sent[0]
    assert "Workflow paused" in message
    assert "Run: 99" in message
    assert "Step: 12" in message
    assert "continue, retry, skip" in message


def test_workflow_waiting_state_does_not_send_when_telegram_disconnected():
    manager = DummyTelegramManager(connected=False)
    app = DummyWorkflowApp(manager)

    app._on_step_waiting_for_feedback(
        step_id=12,
        workflow_id=5,
        run_id=99,
        result_text="Needs input.",
    )

    assert manager.sent == []


def test_workflow_completion_report_sends_concise_telegram_summary():
    manager = DummyTelegramManager()
    app = DummySignalApp(manager)

    app._send_workflow_report_to_telegram(
        5,
        "Workflow run Completed successfully (session 5, run 99)\n"
        "Steps (2):\n"
        "  1. Implement: Changed files: index.html\n"
        "  2. Validate: Checks passed\n"
        "\nGive a brief spoken overview.",
    )

    assert len(manager.sent) == 1
    message = manager.sent[0]
    assert "Workflow run Completed successfully" in message
    assert "Implement" in message
    assert "Validate" in message
    assert "Give a brief" not in message
