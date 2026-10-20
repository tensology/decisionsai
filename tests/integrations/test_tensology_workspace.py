import json
import asyncio
from unittest.mock import MagicMock, patch

from distr.core.agent.tools.integrations.tensology_workspace import TensologyWorkspaceTool
from distr.core.tensology_client import (
    TensologyClient,
    TensologyApiError,
    _create_tensology_thread,
    _execution_skill_ids,
    process_tensology_discussion_context,
    process_tensology_discussion_assessments,
    process_tensology_approved_tickets,
    send_tensology_heartbeat,
    _remote_execution_status,
)
from distr.gui.web.server import _tensology_heartbeat_loop


def test_tensology_client_sends_scoped_headers_and_unwraps_data():
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = json.dumps({"data": {"mail": True}, "error": None}).encode()
    client = TensologyClient("https://example.test", "tns_decisionsai_secret")

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        assert client.get("capabilities") == {"mail": True}

    request = urlopen.call_args.args[0]
    assert request.full_url == "https://example.test/api/integrations/v1/capabilities"
    assert request.get_header("Authorization") == "Bearer tns_decisionsai_secret"


def test_tensology_heartbeat_authenticates_once_before_waiting():
    with (
        patch("distr.core.tensology_client.send_tensology_heartbeat") as heartbeat,
        patch("distr.core.tensology_client.process_tensology_discussion_assessments") as assessments,
        patch("distr.core.tensology_client.process_tensology_discussion_context") as context,
        patch("distr.core.tensology_client.process_tensology_approved_tickets") as tickets,
        patch("asyncio.sleep", side_effect=asyncio.CancelledError),
    ):
        try:
            asyncio.run(_tensology_heartbeat_loop(interval_seconds=60))
        except asyncio.CancelledError:
            pass

    heartbeat.assert_called_once_with()
    assessments.assert_called_once_with()
    context.assert_called_once_with()
    tickets.assert_called_once_with()


def test_tensology_ticket_poll_still_runs_when_an_unrelated_operation_fails():
    with (
        patch(
            "distr.core.tensology_client.send_tensology_heartbeat",
            side_effect=TensologyApiError("presence unavailable", status=403),
        ) as heartbeat,
        patch("distr.core.tensology_client.process_tensology_discussion_assessments") as assessments,
        patch("distr.core.tensology_client.process_tensology_discussion_context") as context,
        patch("distr.core.tensology_client.process_tensology_approved_tickets") as tickets,
        patch("asyncio.sleep", side_effect=asyncio.CancelledError),
    ):
        try:
            asyncio.run(_tensology_heartbeat_loop(interval_seconds=60))
        except asyncio.CancelledError:
            pass

    heartbeat.assert_called_once_with()
    assessments.assert_called_once_with()
    context.assert_called_once_with()
    tickets.assert_called_once_with()


def test_approved_ticket_creates_visible_project_thread_and_claims_remote_ticket():
    client = MagicMock()
    client.get.return_value = [{
        "id": 17,
        "project_id": 9,
        "project_name": "Snuza",
        "reference": "PROJ-9-001",
        "title": "Fix contact form",
        "description": "Reduce spam",
        "tasks": ["Add honeypot", "Test submission"],
        "execution_status": "awaiting_decisions",
        "decisions_thread_id": "",
        "repository_ready": True,
    }]
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.tensology_client._resolve_tensology_project", return_value={
            "id": 8, "name": "Snuza", "folder_location": "/tmp/snuza", "start_time_tracker": True,
        }),
        patch("distr.core.tensology_client._existing_tensology_thread", return_value=None),
        patch("distr.core.tensology_client._create_tensology_thread", return_value=(42, {
            "complexity": "medium", "backend": "pi", "provider": "ollama", "model": "qwen3.8:27b",
        })),
    ):
        assert process_tensology_approved_tickets(force=True) == 1

    client.post.assert_called_once()
    path, body = client.post.call_args.args[:2]
    assert path == "tickets/17/execution"
    assert body["thread_id"] == "42"
    assert body["status"] == "development"
    assert body["thread_url"].endswith("/development/threads/42/")


def test_existing_ticket_thread_syncs_completed_execution_to_done():
    client = MagicMock()
    client.get.return_value = [{
        "id": 17,
        "project_id": 9,
        "project_name": "Snuza",
        "reference": "PROJ-9-001",
        "title": "Fix contact form",
        "execution_status": "development",
        "decisions_thread_id": "42",
        "repository_ready": True,
    }]
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.tensology_client._resolve_tensology_project", return_value={
            "id": 8, "name": "Snuza", "folder_location": "/tmp/snuza", "start_time_tracker": True,
        }),
        patch("distr.core.tensology_client._existing_tensology_thread", return_value=42),
        patch("distr.core.workflow.development_harness.development_execution_state", return_value={
            "status": "completed", "summary": "Tests and final verification passed.", "job_id": "job-1",
        }),
    ):
        assert process_tensology_approved_tickets(force=True) == 1

    client.patch.assert_called_once()
    path, body = client.patch.call_args.args[:2]
    assert path == "tickets/17/execution"
    assert body["status"] == "done"


def test_execution_stage_markers_drive_visible_lifecycle_and_gate_done():
    assert _remote_execution_status("running", {"streamed_output": "[DECISIONS_STAGE: testing]"}) == "testing"
    assert _remote_execution_status("running", {"streamed_output": "[DECISIONS_STAGE: refinement]"}) == "refinement"
    assert _remote_execution_status("completed", {"summary": "Implementation completed."}) == "failed"
    assert _remote_execution_status("completed", {"summary": "[DECISIONS_STAGE: done]"}) == "done"


def test_ui_ticket_inherits_completion_and_ui_quality_skills():
    skills, use_playwright = _execution_skill_ids({
        "title": "Improve the responsive customer settings screen",
        "description": "Polish the form, table, notification states, and mobile layout.",
        "tasks": ["Verify keyboard focus and empty, loading, error, and success states"],
    })

    assert skills == ["completion-protocol", "ui-ux-principles", "ui-crud-discipline"]
    assert use_playwright is True


def test_non_ui_ticket_uses_completion_protocol_without_browser_requirement():
    skills, use_playwright = _execution_skill_ids({
        "title": "Rotate the background cache index",
        "description": "Update the deterministic maintenance command.",
    })

    assert skills == ["completion-protocol"]
    assert use_playwright is False


def test_tensology_thread_attaches_canonical_workflow_and_quality_contract():
    ticket = {
        "id": 17,
        "project_id": 9,
        "reference": "TEST-001",
        "title": "Improve the responsive settings form",
        "description": "Verify the UI states on desktop and mobile.",
        "tasks": ["Implement the form", "Run browser tests"],
        "success_criteria": ["Desktop and mobile evidence passes"],
        "execution_complexity": "auto",
    }
    project = {"id": 11, "name": "Local Model Smoke"}
    local_response = {"task": {"id": 42}}

    with (
        patch(
            "distr.core.workflow.developer_workflow.get_or_create_development_workflow",
            return_value=374,
        ),
        patch("distr.core.tensology_client._ensure_tensology_kanban_ticket", return_value=91),
        patch(
            "distr.core.kanban.ticket_policy.resolve_ticket_complexity",
            return_value="medium",
        ),
        patch(
            "distr.core.kanban.ticket_policy.resolve_ticket_cli_route",
            return_value={"backend": "cursor", "model": "grok-4.7-medium"},
        ),
        patch("distr.core.tensology_client._local_api_post", return_value=local_response) as post,
    ):
        chat_id, route = _create_tensology_thread(ticket, project)

    assert chat_id == 42
    assert route == {
        "complexity": "medium",
        "backend": "cursor",
        "provider": "cursor",
        "model": "grok-4.7-medium",
    }
    path, body = post.call_args.args
    assert path == "workflows/studio/tasks"
    assert body["workflow_id"] == 374
    assert body["ticket_id"] == 91
    assert body["skill_ids"] == ["completion-protocol", "ui-ux-principles", "ui-crud-discipline"]
    assert body["use_playwright"] is True


def test_tensology_heartbeat_reports_machine_account_and_development_defaults():
    client = MagicMock()
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.settings.load_settings_from_db", return_value={
            "project_cli_medium_backend": "codex",
            "project_cli_medium_model": "auto",
        }),
        patch("distr.core.tensology_client.platform.node", return_value="Paul-MacBook"),
        patch("distr.core.tensology_client.getpass.getuser", return_value="paul"),
    ):
        send_tensology_heartbeat()

    client._request.assert_called_once_with(
        "POST",
        "presence",
        body={
            "machine_name": "Paul-MacBook",
            "account_username": "paul",
            "default_provider": "codex",
            "default_model": "auto",
        },
    )


def test_tensology_heartbeat_applies_remote_development_defaults():
    client = MagicMock()
    client._request.return_value = {
        "default_provider": "cursor",
        "default_model": "auto",
    }
    settings = {
        "project_cli_medium_backend": "codex",
        "project_cli_medium_model": "auto",
    }
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.settings.load_settings_from_db", return_value=settings),
        patch("distr.core.settings.save_settings_to_db") as save,
    ):
        send_tensology_heartbeat()

    save.assert_called_once()
    assert settings["project_cli_medium_backend"] == "cursor"
    assert settings["project_cli_medium_model"] == "auto"


def test_send_mail_stops_before_api_without_explicit_approval():
    fake_client = MagicMock()
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="send_mail",
            params={"to": ["person@example.com"], "subject": "Hello", "text": "Body"},
            approved=False,
        )
    assert "Approval required" in result
    fake_client.post.assert_not_called()


def test_draft_is_written_without_send_approval():
    fake_client = MagicMock()
    fake_client.post.return_value = {"id": "draft-1"}
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="save_draft",
            params={"to": ["person@example.com"], "subject": "Hello", "text": "Body"},
            idempotency_key="draft-test",
        )
    assert json.loads(result)["id"] == "draft-1"
    fake_client.post.assert_called_once_with(
        "mail/drafts",
        {"to": ["person@example.com"], "subject": "Hello", "text": "Body"},
        idempotency_key="draft-test",
    )


def test_invoice_creation_stops_before_api_without_explicit_approval():
    fake_client = MagicMock()
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="create_invoice",
            params={"invoice_number": "TEST-001", "customer_id": 1},
            approved=False,
        )
    assert "Approval required" in result
    fake_client.post.assert_not_called()


def test_mail_aliases_use_canonical_gateway_actions():
    fake_client = MagicMock()
    fake_client.get.return_value = {"emails": []}
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(action="check_inbox", params={"limit": 3})

    assert json.loads(result) == {"emails": []}
    fake_client.get.assert_called_once_with("mail/messages", {"limit": 3})


def test_repository_read_is_limited_to_registered_root(tmp_path):
    repository = tmp_path / "site"
    repository.mkdir()
    (repository / "app.py").write_text("def checkout():\n    return 'safe'\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    fake_client = MagicMock()
    fake_client.get.return_value = [{"id": 7, "project_id": 2, "local_path_hint": str(repository)}]

    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        tool = TensologyWorkspaceTool()
        allowed = tool._run(
            action="repository_read_file",
            params={"project_id": 2, "repository_id": 7, "path": "app.py", "context_token": "signed"},
        )
        denied = tool._run(
            action="repository_read_file",
            params={"project_id": 2, "repository_id": 7, "path": "../secret.txt", "context_token": "signed-2"},
        )

    assert json.loads(allowed)["content"].startswith("def checkout")
    assert "Only relative paths" in denied


def test_repository_search_skips_dependency_and_git_directories(tmp_path):
    repository = tmp_path / "site"
    repository.mkdir()
    (repository / "checkout.py").write_text("datapost voucher balance update", encoding="utf-8")
    (repository / "notes.txt").write_text("update", encoding="utf-8")
    (repository / "image.jpg").write_bytes(b"update\0datapost voucher balance")
    dependency = repository / "node_modules"
    dependency.mkdir()
    (dependency / "checkout.js").write_text("datapost voucher balance update", encoding="utf-8")
    fake_client = MagicMock()
    fake_client.get.return_value = [{"id": 7, "project_id": 2, "local_path_hint": str(repository)}]

    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="repository_search",
            params={
                "project_id": 2,
                "repository_id": 7,
                "query": "Which files handle the datapost voucher balance update?",
                "context_token": "signed",
            },
        )

    assert [item["path"] for item in json.loads(result)["matches"]] == ["checkout.py", "notes.txt"]


def test_repository_content_requires_signed_ticket_context(tmp_path):
    repository = tmp_path / "site"
    repository.mkdir()
    (repository / "app.py").write_text("safe", encoding="utf-8")
    fake_client = MagicMock()
    fake_client.get.return_value = [{"id": 7, "project_id": 2, "local_path_hint": str(repository)}]
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="repository_read_file",
            params={"project_id": 2, "repository_id": 7, "path": "app.py"},
        )

    assert "signed ticket context token is required" in result


def test_laya_discussion_assessment_is_advisory():
    fake_client = MagicMock()
    with (
        patch(
            "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
            return_value=fake_client,
        ),
        patch(
            "distr.core.laya_runtime.assess_request",
            return_value={"complexity": "low", "confidence": 0.92},
        ),
    ):
        result = TensologyWorkspaceTool()._run(
            action="laya_assess_discussion",
            params={
                "message": "Update checkout payment validation",
                "ticket_context": "Fix checkout payment validation errors",
            },
        )

    payload = json.loads(result)
    assert payload["scope_signal"] == "related"
    assert payload["laya"]["confidence"] == 0.92
    assert "advisory" in payload["note"]


def test_discussion_shadow_poller_posts_laya_result_without_approval():
    client = MagicMock()
    client.get.return_value = [{"id": 41, "message": {"content": "Add subscription billing"}}]
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.laya_runtime.assess_discussion", return_value={
            "status": "complete", "version": "0.3.23", "scope": "new_ticket",
        }),
    ):
        count = process_tensology_discussion_assessments(force=True)

    assert count == 1
    client.post.assert_called_once_with(
        "discussion-assessments/41",
        {"status": "complete", "version": "0.3.23", "scope": "new_ticket"},
        idempotency_key="laya-shadow-41",
    )


def test_discussion_context_poller_uses_signed_search_and_posts_bounded_evidence():
    client = MagicMock()
    client.get.return_value = [{
        "assessment_id": 42,
        "project_id": 3,
        "tasks": [{
            "repository_id": 7,
            "repository_name": "API",
            "query": "webhook signature validation",
            "context_token": "signed-context",
        }],
    }]
    tool = MagicMock()
    tool._run.return_value = json.dumps({
        "matches": [{"path": "webhooks/verify.py", "snippets": [{"line": 12, "text": "verify_signature()"}]}],
    })
    with (
        patch("distr.core.tensology_client.configured_tensology_client", return_value=client),
        patch("distr.core.agent.tools.integrations.tensology_workspace.TensologyWorkspaceTool", return_value=tool),
    ):
        count = process_tensology_discussion_context(force=True)

    assert count == 1
    tool._run.assert_called_once()
    assert tool._run.call_args.kwargs["params"]["context_token"] == "signed-context"
    body = client.post.call_args.args[1]
    assert body["status"] == "complete"
    assert body["evidence"][0]["path"] == "webhooks/verify.py"
