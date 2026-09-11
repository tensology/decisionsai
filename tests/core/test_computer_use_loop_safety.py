from distr.core.workflow.step_executor import StepExecutorMixin


class _ComputerUseHarness(StepExecutorMixin):
    pass


def test_computer_use_stops_after_repeated_identical_action_and_result(monkeypatch):
    harness = _ComputerUseHarness()
    calls = {"capture": 0, "decide": 0, "execute": 0}

    def capture(_width):
        calls["capture"] += 1
        return "screenshot"

    def decide(_goal, _screenshot, _history, _iteration):
        calls["decide"] += 1
        return {"type": "list_windows", "description": "listing windows"}

    def execute(_action):
        calls["execute"] += 1
        return "Terminal (pid 42)"

    monkeypatch.setattr(harness, "_cu_capture_screenshot", capture)
    monkeypatch.setattr(harness, "_cu_decide_action", decide)
    monkeypatch.setattr(harness, "_cu_execute_action", execute)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = harness._run_computer_use(
        {"id": 7, "instruction": "Move Terminal to the left screen"},
        {
            "max_iterations": 10,
            "stuck_threshold": 3,
            "escalate_on_ambiguity": False,
        },
        run_id=91,
    )

    assert result["passed"] is False
    assert "repeated" in result["output"].lower()
    assert calls == {"capture": 3, "decide": 3, "execute": 3}


def test_computer_use_ignores_cosmetic_wording_in_loop_fingerprint(monkeypatch):
    harness = _ComputerUseHarness()
    descriptions = iter(("listing windows", "checking windows again", "one more check"))
    calls = {"execute": 0}

    monkeypatch.setattr(harness, "_cu_capture_screenshot", lambda _width: "screenshot")
    monkeypatch.setattr(
        harness,
        "_cu_decide_action",
        lambda *_args: {
            "type": "list_windows",
            "description": next(descriptions),
            "reason": "wording can vary",
        },
    )

    def execute(_action):
        calls["execute"] += 1
        return "Terminal (pid 42)"

    monkeypatch.setattr(harness, "_cu_execute_action", execute)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = harness._run_computer_use(
        {"id": 7, "instruction": "Move Terminal"},
        {"max_iterations": 10, "stuck_threshold": 3, "escalate_on_ambiguity": False},
        run_id=91,
    )

    assert result["passed"] is False
    assert "repeated" in result["output"].lower()
    assert calls["execute"] == 3


def test_computer_use_checks_turn_cancellation_before_any_sidecar_call(monkeypatch):
    harness = _ComputerUseHarness()
    sidecar_calls = []
    cancellation_checks = []

    def cancelled(*, run_id, chat_id, turn_id):
        cancellation_checks.append((run_id, chat_id, turn_id))
        return True

    monkeypatch.setattr(
        "distr.core.workflow.step_executor._computer_use_cancelled",
        cancelled,
    )
    monkeypatch.setattr(
        harness,
        "_cu_capture_screenshot",
        lambda _width: sidecar_calls.append("capture") or "screenshot",
    )

    result = harness._run_computer_use(
        {"id": 7, "instruction": "Move Terminal to the left screen"},
        {"_chat_id": 4, "_turn_id": 44},
        run_id=None,
    )

    assert result["passed"] is False
    assert "cancelled" in result["output"].lower()
    assert cancellation_checks == [(None, 4, 44)]
    assert sidecar_calls == []


def test_computer_use_rechecks_cancellation_before_action_sidecar(monkeypatch):
    harness = _ComputerUseHarness()
    sidecar_calls = []
    checks = iter((False, True))

    monkeypatch.setattr(
        "distr.core.workflow.step_executor._computer_use_cancelled",
        lambda **_identity: next(checks),
    )
    monkeypatch.setattr(harness, "_cu_capture_screenshot", lambda _width: "screenshot")
    monkeypatch.setattr(
        harness,
        "_cu_decide_action",
        lambda *_args: {"type": "click", "description": "click Save"},
    )
    monkeypatch.setattr(
        harness,
        "_cu_execute_action",
        lambda _action: sidecar_calls.append("click") or "clicked",
    )

    result = harness._run_computer_use(
        {"id": 7, "instruction": "Click Save"},
        {"_chat_id": 4, "_turn_id": 44},
        run_id=None,
    )

    assert result["passed"] is False
    assert "cancelled" in result["output"].lower()
    assert sidecar_calls == []


def test_direct_computer_use_passes_chat_and_turn_identity(monkeypatch):
    from distr.core.agent.tools.input.computer_use import ComputerUseTool
    from distr.core.workflow.dispatcher import StepDispatcher

    captured = {}

    class ChatManager:
        @staticmethod
        def get_current_chat():
            return 4

    def run(_self, step_data, config, run_id=None):
        captured.update(step_data=step_data, config=config, run_id=run_id)
        return {"output": "done", "passed": True}

    monkeypatch.setattr("distr.core.chat_turns.latest_active_turn_id", lambda chat_id: 44)
    monkeypatch.setattr(StepDispatcher, "_run_computer_use", run)

    result = ComputerUseTool(chat_manager=ChatManager())._run("Click Save")

    assert result == "done"
    assert captured["config"]["_chat_id"] == 4
    assert captured["config"]["_turn_id"] == 44
