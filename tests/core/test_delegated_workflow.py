from __future__ import annotations


def test_delegated_plan_for_email_attachment_scoping_prefers_api_tools():
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    plan = plan_delegated_workflow(
        "telegram",
        "Access my email, fetch Julie's latest email, download the PDF changes file, "
        "scope what needs to be executed, and prep it for Codex.",
    )

    assert plan.kind == "email_document_scope"
    assert [step.action for step in plan.steps[:5]] == [
        "resolve_contact",
        "search_email",
        "download_attachments",
        "extract_document",
        "scope_execution",
    ]
    assert plan.steps[1].preferred_route == "google_workspace"
    assert "desktop_accessibility" in plan.steps[1].fallback_routes
    assert "browser_automation" in plan.steps[1].fallback_routes
    assert plan.requires_approval_before == ["send_outbound_message", "modify_project_files"]


def test_delegated_plan_for_desktop_copy_paste_prefers_direct_and_accessibility_routes():
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    plan = plan_delegated_workflow(
        "desktop",
        "Copy this code, open Sublime, create a file in Downloads, paste it, and save it.",
    )

    assert plan.kind == "desktop_sequence"
    assert [step.action for step in plan.steps] == [
        "capture_source_content",
        "set_clipboard",
        "launch_or_focus_app",
        "create_or_open_file",
        "write_text",
        "verify_result",
    ]
    assert plan.steps[2].preferred_route == "sidecar"
    assert "desktop_accessibility" in plan.steps[2].fallback_routes
    assert plan.requires_approval_before == ["overwrite_existing_file"]


def test_delegated_plan_for_codex_cursor_handoff_includes_project_handoff_step():
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    plan = plan_delegated_workflow(
        "telegram",
        "Tell Cursor to implement the changes from the document and report back to me.",
    )

    assert plan.kind == "project_handoff"
    assert plan.steps[-1].action == "dispatch_project_handoff"
    assert plan.steps[-1].preferred_route == "project_cli_backend"
    assert plan.target_backend == "cursor"


def test_delegated_plan_for_browser_workflow_prefers_playwright():
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    plan = plan_delegated_workflow(
        "telegram",
        "Open https://example.com in the browser, click the docs link, take a screenshot, and report back.",
    )

    assert plan.kind == "browser_workflow"
    assert [step.action for step in plan.steps] == [
        "prepare_browser_task",
        "execute_browser_actions",
        "verify_browser_result",
    ]
    assert plan.steps[1].preferred_route == "playwright"
    assert "browser_use" in plan.steps[1].fallback_routes
    assert plan.requires_approval_before == ["submit_forms", "send_outbound_message"]


def test_delegated_plan_for_local_browser_file_url_prefers_playwright():
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    plan = plan_delegated_workflow(
        "desktop",
        "Open file:///tmp/hermes-browser-smoke.html in the browser and take a screenshot.",
    )

    assert plan.kind == "browser_workflow"
    assert plan.steps[1].preferred_route == "playwright"


def test_roadblock_report_formats_options_for_telegram():
    from distr.core.delegated_workflow.models import Roadblock
    from distr.core.delegated_workflow.roadblocks import format_telegram_report

    report = format_telegram_report(
        Roadblock(
            code="gmail_not_connected",
            title="Gmail is not connected",
            detail="I cannot search Julie's latest email until a Google account is connected.",
            options=[
                "Connect Gmail in Settings > Advanced.",
                "Use the current browser session as a fallback.",
                "Upload the document directly in Telegram.",
            ],
        )
    )

    assert report.startswith("Blocked: Gmail is not connected")
    assert "1. Connect Gmail" in report
    assert "2. Use the current browser session" in report
    assert "3. Upload the document directly" in report


def test_run_report_formats_completed_and_blocked_for_telegram():
    from distr.core.delegated_workflow.models import DelegatedRunReport
    from distr.core.delegated_workflow.planner import plan_delegated_workflow
    from distr.core.delegated_workflow.roadblocks import build_roadblock_report, format_run_report_for_telegram

    plan = plan_delegated_workflow("telegram", "Tell Cursor to implement the changes and report back.")
    completed = DelegatedRunReport(
        status="completed",
        plan=plan,
        completed_steps=["resolve_project_context", "dispatch_project_handoff"],
        evidence={"handoff": {"backend_id": "cursor", "output": "Status: completed"}},
    )
    blocked = DelegatedRunReport(
        status="blocked",
        plan=plan,
        completed_steps=["resolve_project_context"],
        current_step="dispatch_project_handoff",
        roadblock=build_roadblock_report("backend_not_ready"),
    )

    completed_text = format_run_report_for_telegram(completed, run_id=77)
    blocked_text = format_run_report_for_telegram(blocked, run_id=78)

    assert completed_text.startswith("Delegated run 77 completed")
    assert "dispatch_project_handoff" in completed_text
    assert "Status: completed" in completed_text
    assert blocked_text.startswith("Delegated run 78 blocked")
    assert "Blocked: The project backend is not ready" in blocked_text


def test_delegated_plan_dict_redacts_secret_like_values():
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    plan = plan_delegated_workflow(
        "telegram",
        "Use token=abc123456789012345678901234567890 to fetch my email from Julie and prep for Codex.",
    )

    payload = plan.to_safe_dict()

    assert "abc123456789012345678901234567890" not in str(payload)
    assert "[redacted]" in str(payload)


def test_record_delegated_plan_emits_redacted_hermes_event(monkeypatch):
    from distr.core.delegated_workflow.events import record_delegated_plan
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    emitted = []
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kwargs: emitted.append(kwargs) or 123)

    plan = plan_delegated_workflow(
        "telegram",
        "Use token=abc123456789012345678901234567890 to fetch Julie's latest PDF and prep it for Codex.",
    )

    event_id = record_delegated_plan(plan, project_id=7, ticket_id=8)

    assert event_id == 123
    assert emitted[0]["event_type"] == "delegated_plan_created"
    assert emitted[0]["source"] == "telegram"
    assert emitted[0]["project_id"] == 7
    assert emitted[0]["ticket_id"] == 8
    assert "abc123456789012345678901234567890" not in str(emitted[0]["payload"])
    assert "[redacted]" in str(emitted[0]["payload"])


def test_record_delegated_run_report_emits_redacted_hermes_event(monkeypatch):
    from distr.core.delegated_workflow.events import record_delegated_run_report
    from distr.core.delegated_workflow.models import DelegatedRunReport
    from distr.core.delegated_workflow.planner import plan_delegated_workflow

    emitted = []
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kwargs: emitted.append(kwargs) or 124)

    plan = plan_delegated_workflow(
        "telegram",
        "Use token=abc123456789012345678901234567890 to fetch Julie's latest PDF and prep it for Codex.",
    )
    report = DelegatedRunReport(
        status="blocked",
        plan=plan,
        completed_steps=["resolve_contact"],
        current_step="search_email",
        evidence={"secret": "abc123456789012345678901234567890"},
    )

    event_id = record_delegated_run_report(report, project_id=7, ticket_id=8)

    assert event_id == 124
    assert emitted[0]["event_type"] == "delegated_run_report"
    assert emitted[0]["status"] == "blocked"
    assert emitted[0]["source"] == "telegram"
    assert "abc123456789012345678901234567890" not in str(emitted[0]["payload"])
