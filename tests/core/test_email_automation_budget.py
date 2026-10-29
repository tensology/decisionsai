"""The email preset is finite; Gmail failures cannot look like an empty inbox."""
import json
import asyncio
from types import SimpleNamespace

import pytest

from distr.core.workflow import dev_gmail_tools as gmail


def test_gmail_search_is_exposed_and_bounded(monkeypatch):
    from distr.core.workflow.dev_agent_tools import call_tool, exposed_tools
    calls = []
    monkeypatch.setattr(gmail.gmail_readonly, "connect", lambda: "connected")
    monkeypatch.setattr(gmail.gmail_readonly, "search", lambda service, query, **kw: calls.append((service, query, kw)) or [{"id": "m1", "subject": "Review", "from": "Sender"}])
    result = call_tool("gmail_search", {"query": "is:unread", "max_results": 200}, project_folder="/tmp/email-test")
    assert "gmail_search" in exposed_tools()
    assert result["ok"] and result["count"] == 1
    assert calls == [("connected", "is:unread", {"max_results": 20})]
    assert json.loads(result["summary"])["trust"] == "untrusted_email_data"


def test_gmail_unavailable_is_a_blocker(monkeypatch):
    def unavailable():
        raise gmail.gmail_readonly.GmailUnavailable("Reconnect Google in Settings.")
    monkeypatch.setattr(gmail.gmail_readonly, "connect", unavailable)
    result = gmail.gmail_search("is:unread")
    assert not result["ok"]
    assert "Reconnect Google" in result["summary"]
    assert "messages" not in result


def test_gmail_metadata_uses_only_bounded_gets():
    calls = []
    class Service:
        def _make_request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return {"messages": [{"id": "m1"}]} if "/messages?" in url else {"id": "m1", "payload": {"headers": []}}
    gmail.gmail_readonly.search(Service(), "is:unread", max_results=20)
    assert len(calls) == 2
    assert all(method == "GET" and kwargs["timeout"] == (5, 10) for method, _, kwargs in calls)


def test_email_dispatch_preserves_instruction_and_applies_bounds(monkeypatch):
    from distr.core import automation_orchestrator as orchestrator
    from distr.core.automation.store import create_automation
    captured = []
    automation = create_automation(name="Email action items", automation_type="scheduled_instruction", status="active", instruction="Review Gmail, create clear tickets, and never send email.", preset_id="email_action_items", schedule={"kind": "daily", "time": "10:00"}, action_config={})
    monkeypatch.setattr("distr.core.workflow.development_harness.dispatch_development_prompt", lambda chat, prompt, **kw: captured.append((prompt, kw)) or {"status": "running", "id": 77})
    result = orchestrator.dispatch_automation_to_current_chat(automation, speak=False, manual=False)
    assert result["status"] == "running"
    prompt, kwargs = captured[0]
    assert prompt.startswith(automation["instruction"])
    assert "gmail_search" in prompt and "20 distinct" in prompt
    assert "Do not retry or repeat the scan" in prompt
    assert kwargs["routing_assessment"]["bounded_automation"] == "email_action_items"
    from distr.core.automation.store import get_automation
    assert get_automation(automation["id"])["instruction"] == automation["instruction"]


def test_development_worker_receives_email_budget(tmp_path, monkeypatch):
    from tests.core.test_development_harness import _database
    from distr.core.workflow import development_harness as harness
    from distr.core.automation.execution_policy import email_triage_options
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    captured = []

    async def dispatch(context):
        captured.append(context)
        return SimpleNamespace(execution_session_id=77, result=SimpleNamespace(success=True, waits_for_human=False, output="Triage complete.", error=""))

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", dispatch)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", lambda *a, **kw: {"status": {"ready": True}, "next_step": ""})
    result = harness.dispatch_development_prompt(chat_id, "Review the bounded email batch.", routing_assessment={"bounded_automation": "email_action_items"}, dispatch_async=False)
    assert result["status"] == "completed"
    options = captured[0].adapter_options
    for key, value in email_triage_options().items():
        assert options[key] == value


@pytest.mark.parametrize("stop", ["tool_budget", "time_budget", "normal"])
@pytest.mark.parametrize("completed", [True, False])
def test_pi_email_returns_a_report_without_replaying_scan(stop, completed, monkeypatch, tmp_path):
    from tests.core.test_execution_contracts import _FakeAsyncProcess
    from distr.core.project_cli_backends import registry
    from distr.core.project_cli_backends.base import ProjectTask
    from distr.core.automation.execution_policy import email_triage_options
    monkeypatch.setattr("distr.core.pi_rpc.PiRpcSession.find_pi", lambda: "/test/pi")
    options = email_triage_options()
    options["disable_dev_agent_tools"] = True
    events = [json.dumps({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "I will check Gmail."}]}})]
    if stop == "tool_budget":
        for _ in range(24):
            events.extend(json.dumps({"type": kind, "toolName": "gmail_search"}) for kind in ("tool_execution_start", "tool_execution_end"))
        events.append(json.dumps({"type": "tool_execution_start", "toolName": "must_not_run"}))
    else:
        events.append(json.dumps({"type": "agent_end"}))
    initial = _FakeAsyncProcess(events)
    report = (
        "action_items: 1. Sender, Subject: review this request.\n"
        "tickets_created: none\ncoverage: One bounded batch, 20 messages.\n"
        f"Status: {'completed' if completed else 'needs_input'}\n"
        "Summary: Email triage report.\nFiles changed: none\nCommands run: gmail_search\n"
        f"Blockers: {'none' if completed else 'Gmail is disconnected; reconnect in Settings.'}"
    )
    final = _FakeAsyncProcess([
        json.dumps({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": report}]}}),
        json.dumps({"type": "agent_end"}),
    ])
    processes, commands, observed = iter([initial, final]), [], []
    async def spawn(*args, **kwargs):
        commands.append(list(args))
        return next(processes)
    monkeypatch.setattr(registry.asyncio, "create_subprocess_exec", spawn)
    if stop == "time_budget":
        clock = iter([0, 0, 301])
        monkeypatch.setattr(registry, "time", SimpleNamespace(monotonic=lambda: next(clock, 301)))
    task = ProjectTask(project_id=-118, project_name="Email action items", folder=str(tmp_path), instruction="Review Gmail.", origin="development", execution_session_id=771, model="test-model", adapter_options=options)
    result = asyncio.run(registry.PiBackend().send_task(task, on_event=observed.append))
    assert result.success is completed, result.error
    assert result.output == report
    assert len(commands) == 2
    assert "--no-tools" in commands[1]
    assert commands[0][commands[0].index("--session-id") + 1] == commands[1][commands[1].index("--session-id") + 1]
    assert not any(event.get("toolName") == "must_not_run" for event in observed)


def test_failed_metadata_read_cannot_be_reported_as_empty_inbox(monkeypatch):
    class Service:
        def _make_request(self, method, url, **kwargs):
            return {"messages": [{"id": "m1"}]} if "/messages?" in url else None
    monkeypatch.setattr(gmail.gmail_readonly, "connect", lambda: Service())
    result = gmail.gmail_search("is:unread")
    assert result["ok"] is False
    assert "incomplete" in result["summary"]


def test_report_timeout_is_one_deadline_even_while_events_arrive(monkeypatch, tmp_path):
    from tests.core.test_execution_contracts import _FakeAsyncProcess
    from distr.core.project_cli_backends import registry
    from distr.core.project_cli_backends.base import ProjectTask
    process = _FakeAsyncProcess([json.dumps({"type": "heartbeat"})] * 100)
    commands = []
    async def spawn(*args, **kwargs):
        commands.append(args)
        return process
    clock = iter([0, 20, 40, 61])
    monkeypatch.setattr(registry, "time", SimpleNamespace(monotonic=lambda: next(clock, 61)))
    monkeypatch.setattr(registry.asyncio, "create_subprocess_exec", spawn)
    task = ProjectTask(project_id=-118, project_name="Email", folder=str(tmp_path), instruction="Review Gmail.", origin="development", execution_session_id=771, adapter_options={"report_timeout_seconds": 60})
    output, errors = asyncio.run(registry._finalize_pi_workflow_session(pi_path="/test/pi", task=task, on_event=None))
    assert output == ""
    assert len(commands) == 1
    assert process.returncode == -15
    assert any("timed out after 60 seconds" in error for error in errors)


def test_cron_api_save_keeps_schedule_and_tool_type():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.automations import create_routes
    from distr.core.automation.store import create_automation
    schedule = {"kind": "cron", "cron": "1-51/10 7-19 * * *", "timezone": "Africa/Johannesburg"}
    automation = create_automation(name="Cron API regression", automation_type="tool_action", status="paused", instruction="", preset_id="", schedule=schedule, action_config={"tool": "support_monitor"})
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    response = TestClient(app).put("/api/automations/" + automation["id"], json={"name": "Renamed monitor", "automation_type": "tool_action", "schedule": automation["schedule"]})
    assert response.status_code == 200, response.text
    saved = response.json()["automation"]
    assert saved["schedule"] == automation["schedule"]
    assert saved["automation_type"] == "tool_action"
    assert saved["action_config"]["tool"] == "support_monitor"
