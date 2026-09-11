from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from distr.core.workflow.dispatcher import StepDispatcher


def test_fresh_agent_step_does_not_reuse_the_workflow_agent(monkeypatch):
    dispatcher = StepDispatcher()
    persistent_agent = MagicMock()
    run_context = SimpleNamespace(workflow_agent=persistent_agent, event_loop=object())
    monkeypatch.setattr(dispatcher, "_get_run_context", lambda step_id, run_id: run_context)
    monkeypatch.setattr(dispatcher, "_build_agent_prompt", lambda step, run_id: "Review independently")
    record = MagicMock()
    monkeypatch.setattr(dispatcher, "_record_result_and_route", record)

    instances = []

    class FreshAgent:
        def __init__(self):
            instances.append(self)

        async def execute(self, prompt):
            assert prompt == "Review independently"
            return "Independent review passed"

        def shutdown(self):
            self.shutdown_called = True

    step = {
        "id": 41,
        "workflow_id": 7,
        "instruction": "Review the prior implementation",
        "timeout_seconds": 5,
        "config": {"agent_context_mode": "fresh"},
    }
    with patch("distr.core.workflow_agent.WorkflowAgent", FreshAgent):
        result = dispatcher._run_agent(step, 99)

    assert result == {"output": "Independent review passed", "passed": True}
    assert len(instances) == 1
    assert instances[0].shutdown_called is True
    persistent_agent.execute.assert_not_called()
    record.assert_called_once_with(
        41,
        run_id=99,
        result_text="Independent review passed",
        passed=True,
    )
