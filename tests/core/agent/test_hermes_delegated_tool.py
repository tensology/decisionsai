from __future__ import annotations

import json


def test_hermes_delegated_workflow_tool_returns_plan_and_records_event(monkeypatch):
    from distr.core.agent.tools.integrations.hermes_delegated_workflow import HermesDelegatedWorkflowTool

    emitted = []
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kwargs: emitted.append(kwargs) or 456)

    result = HermesDelegatedWorkflowTool()._run(
        text="Access my email, fetch Julie's latest PDF changes file, scope what needs to be executed, and prep for Codex.",
        source_surface="telegram",
        project_id=3,
        ticket_id=4,
    )

    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["event_id"] == 456
    assert payload["plan"]["kind"] == "email_document_scope"
    assert payload["plan"]["target_backend"] == "codex"
    assert emitted[0]["event_type"] == "delegated_plan_created"


def test_hermes_delegated_workflow_tool_can_execute_with_injected_runner(monkeypatch):
    from distr.core.agent.tools.integrations.hermes_delegated_workflow import HermesDelegatedWorkflowTool
    from distr.core.hermes_delegated.models import DelegatedRunReport

    emitted = []
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kwargs: emitted.append(kwargs) or (789 + len(emitted)))

    class FakeRunner:
        def run(self, plan, context=None):
            return DelegatedRunReport(
                status="completed",
                plan=plan,
                completed_steps=["resolve_contact", "search_email"],
                evidence={"email": {"message_id": "msg-1"}},
            )

    tool = HermesDelegatedWorkflowTool(runner=FakeRunner())

    result = tool._run(
        text="Access my email, fetch Julie's latest PDF changes file, scope what needs to be executed.",
        source_surface="telegram",
        execute=True,
        project_id=3,
        ticket_id=4,
    )

    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["event_id"] == 790
    assert payload["run_event_id"] == 791
    assert payload["run_report"]["status"] == "completed"
    assert payload["run_report"]["completed_steps"] == ["resolve_contact", "search_email"]
    assert payload["telegram_report"].startswith("Delegated run")
    assert [event["event_type"] for event in emitted] == ["delegated_plan_created", "delegated_run_report"]


def test_hermes_delegated_workflow_tool_can_return_preflight_without_execution(monkeypatch):
    from distr.core.agent.tools.integrations.hermes_delegated_workflow import HermesDelegatedWorkflowTool

    emitted = []
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kwargs: emitted.append(kwargs) or (900 + len(emitted)))

    class FakeRunner:
        email_adapter = None
        document_adapter = None
        project_dispatcher = None
        desktop_adapter = None
        browser_adapter = None

        def run(self, plan, context=None):
            raise AssertionError("preflight must not execute the runner")

    tool = HermesDelegatedWorkflowTool(runner=FakeRunner())

    result = tool._run(
        text="Open https://example.com in the browser and screenshot it.",
        source_surface="telegram",
        preflight=True,
    )

    payload = json.loads(result)
    assert payload["success"] is False
    assert payload["preflight"]["ready"] is False
    assert payload["preflight"]["blockers"][0]["name"] == "browser"
    assert payload["preflight_event_id"] == 902
    assert "run_event_id" not in payload
    assert [event["event_type"] for event in emitted] == ["delegated_plan_created", "delegated_preflight_report"]
