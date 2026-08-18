from __future__ import annotations

from distr.core.agent.tools.input.computer_use import ComputerUseTool
from distr.core.workflow.dispatcher import StepDispatcher


def test_chat_computer_use_tool_calls_autonomous_loop(monkeypatch):
    seen = {}

    def fake_run(self, step_data, config, run_id=None):
        seen.update(config)
        return {"output": "Goal complete", "passed": True}

    monkeypatch.setattr(StepDispatcher, "_run_computer_use", fake_run)
    class _ChatManager:
        @staticmethod
        def get_current_chat():
            return 42

    result = ComputerUseTool(chat_manager=_ChatManager()).invoke(
        {"goal": "Open Settings, select Audio, and verify the device"}
    )
    assert result == "Goal complete"
    assert seen["max_iterations"] == 15
    assert seen["_chat_id"] == 42


def test_computer_use_decision_uses_configured_provider(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"computer_use_provider": "openai", "computer_use_model": "gpt-4o"},
    )
    monkeypatch.setattr(
        "distr.core.llm_factory.resolve_computer_use_config",
        lambda settings: ("openai", "gpt-4o"),
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.vision.vision_api.resolve_vision_llm_config",
        lambda settings: ("ollama", "qwen3-vl:2b"),
    )

    def fake_call(self, provider, model, images, prompt, is_action, image_mimes=None):
        calls.append((provider, model, image_mimes))
        return '{"type":"finished","reason":"done"}'

    monkeypatch.setattr(
        "distr.core.agent.tools.vision.screenshot_analyzer.ScreenshotAnalyzerTool._call_vision_api",
        fake_call,
    )
    action = StepDispatcher()._cu_decide_action("Open Settings", "ZmFrZQ==", [], 0)
    assert action["type"] == "finished"
    assert calls == [("openai", "gpt-4o", ["image/jpeg"])]


def test_computer_use_records_each_visible_action(monkeypatch):
    dispatcher = StepDispatcher()
    actions = iter(
        [
            {"type": "click", "description": "Open Audio", "norm_x": 0.5, "norm_y": 0.5},
            {"type": "finished", "reason": "Audio is open"},
        ]
    )
    starts = []
    finishes = []

    monkeypatch.setattr(dispatcher, "_cu_capture_screenshot", lambda width: "ZmFrZQ==")
    monkeypatch.setattr(dispatcher, "_cu_decide_action", lambda *args: next(actions))
    monkeypatch.setattr(dispatcher, "_cu_execute_action", lambda action: "Clicked at (0.500, 0.500)")
    monkeypatch.setattr(
        "distr.core.agent.tool_audit.record_tool_start",
        lambda chat_id, tool_name, **kwargs: starts.append((chat_id, tool_name, kwargs)) or f"event-{len(starts)}",
    )
    monkeypatch.setattr(
        "distr.core.chat_turns.finish_tool",
        lambda event_id, **kwargs: finishes.append((event_id, kwargs)),
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = dispatcher._run_computer_use(
        {"instruction": "Open Audio"},
        {"_chat_id": 42, "max_iterations": 3},
    )

    assert result["passed"] is True
    assert [item[1] for item in starts] == ["computer_use_step__click", "computer_use_step__finished"]
    assert "Computer use step 1: Open Audio" == starts[0][2]["instruction_hint"]
    assert [item[0] for item in finishes] == ["event-1", "event-2"]


def test_cu_execute_focus_and_snap_without_click(monkeypatch):
    """Named-window snap uses list/focus/bounds, not click_at."""
    calls = []

    def fake_sidecar(tool, params, timeout=10):
        calls.append((tool, params))
        if tool == "list_windows":
            return {
                "windows": [
                    {
                        "title": "bash",
                        "pid": 222,
                        "process_name": "Terminal",
                        "left": 100,
                        "top": 100,
                        "right": 900,
                        "bottom": 700,
                        "is_foreground": False,
                    }
                ]
            }
        if tool == "focus_window":
            return {"success": True, "pid": params["pid"]}
        if tool == "set_window_bounds":
            return {
                "success": True,
                "pid": params["pid"],
                "x": 0,
                "y": 0,
                "w": 720,
                "h": 900,
                "snap": params.get("snap", ""),
            }
        raise AssertionError(f"unexpected sidecar tool {tool}")

    monkeypatch.setattr(
        "distr.core.agent.tools.input.sidecar_http.call_sidecar_tool",
        fake_sidecar,
    )
    dispatcher = StepDispatcher()
    focused = dispatcher._cu_execute_action({"type": "focus", "process_name": "Terminal"})
    moved = dispatcher._cu_execute_action({"type": "set_bounds", "process_name": "Terminal", "snap": "left"})
    assert "222" in focused
    assert "720" in moved
    tools = [t for t, _ in calls]
    assert "focus_window" in tools
    assert "set_window_bounds" in tools
    assert "click_at" not in tools


def test_cu_decide_prompt_includes_window_ops():
    prompt = StepDispatcher._CU_DECIDE_PROMPT
    assert "list_windows" in prompt
    assert "set_bounds" in prompt
    assert "process_name" in prompt
