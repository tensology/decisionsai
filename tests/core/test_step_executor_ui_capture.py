import json
from unittest.mock import patch

from distr.core.workflow.step_executor import StepExecutorMixin
from distr.core.kanban.result_packet import append_workflow_step_to_packet, create_initial_result_packet_for_run


class _FakeExecutor(StepExecutorMixin):
    def __init__(self, output="Step output.", passed=True):
        self.output = output
        self.passed = passed

    def _build_config(self, step_data):
        config = step_data.get("config") or {}
        if isinstance(config, str):
            return json.loads(config)
        return config

    def _run_computer_use(self, step_data, config, run_id=None):
        return {"output": self.output, "passed": self.passed}

    def _run_command(self, config, run_id=None):
        return {"output": self.output, "passed": self.passed}


def test_execute_adds_before_after_screenshot_lines_for_ui_steps():
    executor = _FakeExecutor(output="Flow summary: Open settings, save.")

    with patch(
        "distr.core.workflow.step_executor.capture_ui_screenshot",
        side_effect=["/tmp/before.png", "/tmp/after.png"],
    ):
        result = executor._execute(
            {
                "id": 42,
                "action_type": "computer_use",
                "instruction": "Click settings and save.",
                "config": {},
            },
            run_id=9,
        )

    assert result["passed"] is True
    assert "Before screenshot: /tmp/before.png" in result["output"]
    assert "After screenshot: /tmp/after.png" in result["output"]
    assert "Flow summary: Open settings, save." in result["output"]


def test_execute_leaves_non_ui_steps_unwrapped():
    executor = _FakeExecutor(output="Unit tests passed.")

    with patch("distr.core.workflow.step_executor.capture_ui_screenshot") as capture:
        result = executor._execute(
            {
                "id": 43,
                "action_type": "run_command",
                "instruction": "Run unit tests.",
                "config": {"command": "pytest -q"},
            },
            run_id=9,
        )

    assert result["output"] == "Unit tests passed."
    capture.assert_not_called()


def test_execute_honors_ui_capture_flag_from_parsed_config():
    executor = _FakeExecutor(output="Generated preview.")

    with patch(
        "distr.core.workflow.step_executor.capture_ui_screenshot",
        side_effect=["/tmp/before.png", "/tmp/after.png"],
    ):
        result = executor._execute(
            {
                "id": 47,
                "action_type": "run_command",
                "instruction": "Generate static preview.",
                "config": json.dumps({"ui_quality_capture": True}),
            },
            run_id=9,
        )

    assert "Before screenshot: /tmp/before.png" in result["output"]
    assert "After screenshot: /tmp/after.png" in result["output"]


def test_execute_records_unavailable_before_screenshot_for_ui_steps():
    executor = _FakeExecutor(output="Flow summary: Open settings, save.")

    with patch(
        "distr.core.workflow.step_executor.capture_ui_screenshot",
        side_effect=[None, "/tmp/after.png"],
    ):
        result = executor._execute(
            {
                "id": 44,
                "action_type": "computer_use",
                "instruction": "Click settings and save.",
                "config": {},
            },
            run_id=9,
        )

    assert "Before screenshot unavailable: automatic capture failed before step execution." in result["output"]
    assert "After screenshot: /tmp/after.png" in result["output"]


def test_execute_adds_configured_visual_baseline_lines_for_ui_steps():
    executor = _FakeExecutor(output="Flow summary: Open dashboard.")

    with patch(
        "distr.core.workflow.step_executor.capture_ui_screenshot",
        side_effect=["/tmp/before.png", "/tmp/after.png"],
    ):
        result = executor._execute(
            {
                "id": 46,
                "action_type": "computer_use",
                "instruction": "Open dashboard.",
                "config": {
                    "visual_baseline_name": "Gold Admin",
                    "baseline_screen_name": "Dashboard",
                    "visual_diff_threshold": 0.1,
                },
            },
            run_id=9,
        )

    assert "Visual baseline: Gold Admin" in result["output"]
    assert "Baseline screen: Dashboard" in result["output"]
    assert "Visual diff threshold: 0.1" in result["output"]


def test_executor_screenshot_output_populates_result_packet_ui_quality():
    executor = _FakeExecutor(
        output=(
            "Flow summary: Open settings and save.\n"
            "1. [click] save -> Clicked at (0.80, 0.90): True"
        )
    )

    with patch(
        "distr.core.workflow.step_executor.capture_ui_screenshot",
        side_effect=["/tmp/before.png", "/tmp/after.png"],
    ):
        result = executor._execute(
            {
                "id": 45,
                "action_type": "computer_use",
                "instruction": "Click save.",
                "config": {},
            },
            run_id=9,
        )

    packet = create_initial_result_packet_for_run(
        ticket_id="9",
        board_id="2",
        board_name="Main",
        project_id="4",
        project_name="DecisionsAI",
        execution_lane="codex",
    )
    packet = append_workflow_step_to_packet(
        packet,
        step_name="Validate UI",
        step_status="passed",
        step_result=result["output"],
        run_status="running",
    )

    ui_quality = packet["artifacts"]["ui_quality"]
    assert ui_quality["before_screenshot"] == "/tmp/before.png"
    assert ui_quality["after_screenshot"] == "/tmp/after.png"
    assert ui_quality["click_count"] == 1
