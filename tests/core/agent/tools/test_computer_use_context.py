from distr.core.agent.services.computer_use_context import (
    clear_context,
    get_context_snapshot,
    record_action,
    record_candidate_target,
    record_observation,
)
from distr.core.agent.tools.input.computer_use_context import ComputerUseContextTool


def test_context_service_records_and_reads_state():
    clear_context()
    record_observation("screenshot_analyzer", {"capture_region": "screen_1"})
    record_candidate_target(
        source="screenshot_analyzer",
        x=100,
        y=200,
        screen=1,
        description="save button",
    )
    record_action("click", "success", {"x": 100, "y": 200})

    snapshot = get_context_snapshot()
    assert snapshot["last_observation"]["source"] == "screenshot_analyzer"
    assert snapshot["last_candidate_target"]["x"] == 100
    assert snapshot["last_candidate_target"]["y"] == 200
    assert snapshot["last_action"]["action"] == "click"
    assert snapshot["last_action"]["status"] == "success"


def test_context_service_clear_resets_values():
    clear_context()
    record_observation("accessibility_tree", {"tool": "get_window_tree"})
    clear_context()

    snapshot = get_context_snapshot()
    assert snapshot["last_observation"] is None
    assert snapshot["last_candidate_target"] is None
    assert snapshot["last_action"] is None


def test_context_tool_get_and_clear():
    clear_context()
    record_candidate_target(source="accessibility_tree", x=42, y=84, screen=1, description="ok")
    tool = ComputerUseContextTool()

    text = tool._run(action="get")
    assert "(42, 84)" in text
    assert "ok" in text

    clear_result = tool._run(action="clear")
    assert "cleared" in clear_result.lower()

    text_after_clear = tool._run(action="get")
    assert "No computer-use context" in text_after_clear
