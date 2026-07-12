from __future__ import annotations

from unittest.mock import MagicMock


def test_continue_workflow_steers_running_backend(monkeypatch):
    from distr.core.agent.tools.step_runner.workflow_tools import ContinueWorkflowTool

    monkeypatch.setattr(
        "distr.core.workflow.service.get_active_runs",
        MagicMock(
            return_value=[
                {
                    "id": 42,
                    "workflow_id": 7,
                    "status": "running",
                    "steerable": True,
                }
            ]
        ),
    )
    steer = MagicMock(
        return_value={
            "success": True,
            "run_id": 42,
            "backend_id": "codex",
            "delivered": False,
            "method": "queued",
        }
    )
    monkeypatch.setattr("distr.core.workflow.service.apply_run_harness_steer", steer)
    continue_waiting = MagicMock()
    monkeypatch.setattr("distr.core.workflow.dispatcher.continue_waiting_step", continue_waiting)

    result = ContinueWorkflowTool()._run(user_input="Keep this scoped to the ticket")

    assert "Workflow run 42 steered for codex" in result
    steer.assert_called_once_with(42, "Keep this scoped to the ticket", source="agent_tool")
    continue_waiting.assert_not_called()


def test_continue_workflow_keeps_waiting_continue_behavior(monkeypatch):
    from distr.core.agent.tools.step_runner.workflow_tools import ContinueWorkflowTool

    monkeypatch.setattr(
        "distr.core.workflow.service.get_active_runs",
        MagicMock(
            return_value=[
                {
                    "id": 9,
                    "workflow_id": 3,
                    "status": "waiting",
                    "steerable": True,
                }
            ]
        ),
    )
    continue_waiting = MagicMock(return_value={"success": True})
    monkeypatch.setattr("distr.core.workflow.dispatcher.continue_waiting_step", continue_waiting)

    result = ContinueWorkflowTool()._run(user_input="approve")

    assert "Workflow run 9 resumed" in result
    continue_waiting.assert_called_once_with(9, "approve")
