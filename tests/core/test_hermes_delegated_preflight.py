from __future__ import annotations

from types import SimpleNamespace


class ReadyEmailAdapter:
    connected = True

    def search_latest_email(self, *, sender_hint: str, query: str):
        return {}

    def download_attachments(self, *, message_id: str, destination_dir: str):
        return []


class ReadyDocumentAdapter:
    def extract(self, file_path: str):
        return ""


class ReadyProjectDispatcher:
    def check_backend_status(self, backend_id: str):
        return {
            "id": backend_id,
            "ready": True,
            "can_receive_remote_handoff": True,
            "message": "ready",
            "handoff_method": "one_shot_cli_with_callback",
        }


def test_preflight_reports_ready_email_document_handoff_stack():
    from distr.core.hermes_delegated.preflight import preflight_delegated_plan
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    plan = plan_delegated_workflow(
        "telegram",
        "Access my email, fetch Julie's latest PDF, scope it, and prep it for Codex.",
    )
    runner = DelegatedWorkflowRunner(
        email_adapter=ReadyEmailAdapter(),
        document_adapter=ReadyDocumentAdapter(),
        project_dispatcher=ReadyProjectDispatcher(),
    )

    report = preflight_delegated_plan(plan, runner, context={"project_id": 7})

    assert report["ready"] is True
    assert [check["name"] for check in report["checks"]] == [
        "email",
        "document_extractor",
        "project_handoff",
    ]
    assert report["blockers"] == []


def test_preflight_reports_missing_browser_adapter_blocker():
    from distr.core.hermes_delegated.preflight import preflight_delegated_plan
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    plan = plan_delegated_workflow("telegram", "Open https://example.com in the browser and screenshot it.")

    report = preflight_delegated_plan(plan, DelegatedWorkflowRunner(), context={})

    assert report["ready"] is False
    assert report["blockers"][0]["name"] == "browser"
    assert "browser adapter" in report["blockers"][0]["detail"].lower()


def test_preflight_uses_backend_status_for_project_handoff():
    from distr.core.hermes_delegated.preflight import preflight_delegated_plan
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    dispatcher = SimpleNamespace(
        check_backend_status=lambda backend_id: {
            "id": backend_id,
            "ready": False,
            "can_receive_remote_handoff": False,
            "message": "codex missing",
        }
    )
    plan = plan_delegated_workflow("telegram", "Tell Codex to run tests and report back.")

    report = preflight_delegated_plan(
        plan,
        DelegatedWorkflowRunner(project_dispatcher=dispatcher),
        context={"project_id": 3},
    )

    assert report["ready"] is False
    assert report["blockers"][0]["name"] == "project_handoff"
    assert "codex missing" in report["blockers"][0]["detail"]


def test_record_delegated_preflight_emits_redacted_hermes_event(monkeypatch):
    from distr.core.hermes_delegated.events import record_delegated_preflight
    from distr.core.hermes_delegated.planner import plan_delegated_workflow

    emitted = []
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kwargs: emitted.append(kwargs) or 901)

    plan = plan_delegated_workflow("telegram", "Open https://example.com in the browser and screenshot it.")
    report = {
        "ready": False,
        "plan_kind": "browser_workflow",
        "checks": [
            {
                "name": "browser",
                "ready": False,
                "detail": "missing token=abc123456789012345678901234567890",
                "evidence": {"secret": "abc123456789012345678901234567890"},
            }
        ],
        "blockers": [
            {
                "name": "browser",
                "ready": False,
                "detail": "missing token=abc123456789012345678901234567890",
            }
        ],
    }

    event_id = record_delegated_preflight(plan, report, project_id=7, ticket_id=8)

    assert event_id == 901
    assert emitted[0]["event_type"] == "delegated_preflight_report"
    assert emitted[0]["status"] == "blocked"
    assert emitted[0]["project_id"] == 7
    assert emitted[0]["ticket_id"] == 8
    assert "abc123456789012345678901234567890" not in str(emitted[0]["payload"])
    assert "[redacted]" in str(emitted[0]["payload"])
