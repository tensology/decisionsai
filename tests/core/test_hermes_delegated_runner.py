from __future__ import annotations

from types import SimpleNamespace


class FakeEmailAdapter:
    connected = True

    def __init__(self):
        self.queries = []

    def search_latest_email(self, *, sender_hint: str, query: str):
        self.queries.append((sender_hint, query))
        return {
            "message_id": "msg-1",
            "from": "Julie <julie@example.com>",
            "subject": "PDF changes",
            "body": "Please scope the attached changes.",
        }

    def download_attachments(self, *, message_id: str, destination_dir: str):
        assert message_id == "msg-1"
        assert "decisionsai" in destination_dir.lower()
        return [{"path": "/tmp/decisionsai-intake/changes.pdf", "name": "changes.pdf"}]


class FakeDocumentAdapter:
    def extract(self, file_path: str):
        assert file_path.endswith("changes.pdf")
        return "Change login copy. Add browser validation. Update tests."


class FakeScopeAdapter:
    def scope(self, *, instruction: str, email: dict, documents: list[dict]):
        assert "Julie" in instruction
        assert email["message_id"] == "msg-1"
        assert documents[0]["text"].startswith("Change login copy")
        return {
            "summary": "Scope the requested login copy changes.",
            "tasks": ["Update login copy", "Add browser validation", "Update tests"],
            "risks": ["Auth UI regression"],
        }


class FakeProjectDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, *, backend_id: str, instruction: str, scope: dict, context: dict):
        self.calls.append((backend_id, instruction, scope, context))
        return SimpleNamespace(success=True, backend_id=backend_id, output="handoff accepted", error="")


def test_runner_executes_email_document_scope_and_dispatches_backend():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    dispatcher = FakeProjectDispatcher()
    plan = plan_delegated_workflow(
        "telegram",
        "Access my email, fetch Julie's latest PDF changes file, scope what needs to be executed, and prep it for Codex.",
    )
    runner = DelegatedWorkflowRunner(
        email_adapter=FakeEmailAdapter(),
        document_adapter=FakeDocumentAdapter(),
        scope_adapter=FakeScopeAdapter(),
        project_dispatcher=dispatcher,
        intake_dir="/tmp/decisionsai-intake",
    )

    report = runner.run(plan, context={"project_id": 12, "ticket_id": 34})

    assert report.status == "completed"
    assert report.roadblock is None
    assert report.completed_steps == [
        "resolve_contact",
        "search_email",
        "download_attachments",
        "extract_document",
        "scope_execution",
        "dispatch_project_handoff",
    ]
    assert report.evidence["email"]["message_id"] == "msg-1"
    assert report.evidence["scope"]["tasks"] == ["Update login copy", "Add browser validation", "Update tests"]
    assert report.evidence["handoff"]["success"] is True
    assert dispatcher.calls[0][0] == "codex"


def test_runner_returns_gmail_roadblock_when_email_adapter_not_connected():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    plan = plan_delegated_workflow(
        "telegram",
        "Access my email, fetch Julie's latest PDF changes file, scope what needs to be executed.",
    )
    runner = DelegatedWorkflowRunner(email_adapter=SimpleNamespace(connected=False))

    report = runner.run(plan)

    assert report.status == "blocked"
    assert report.current_step == "search_email"
    assert report.roadblock is not None
    assert report.roadblock.code == "gmail_not_connected"
    assert "Connect Gmail" in "\n".join(report.roadblock.options)


class FakeDesktopAdapter:
    def __init__(self):
        self.calls = []
        self.content = "print('hello')"
        self.files = {}

    def capture_source_content(self, instruction: str):
        self.calls.append(("capture_source_content", instruction))
        return self.content

    def set_clipboard(self, text: str):
        self.calls.append(("set_clipboard", text))
        return True

    def launch_or_focus_app(self, instruction: str):
        self.calls.append(("launch_or_focus_app", instruction))
        return {"app": "Sublime Text", "focused": True}

    def create_or_open_file(self, instruction: str):
        self.calls.append(("create_or_open_file", instruction))
        return "/tmp/Downloads/example.py"

    def write_text(self, path: str, text: str):
        self.calls.append(("write_text", path, text))
        self.files[path] = text
        return True

    def verify_result(self, path: str, expected_text: str):
        self.calls.append(("verify_result", path, expected_text))
        return self.files.get(path) == expected_text


def test_runner_executes_desktop_sequence_with_adapter():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    adapter = FakeDesktopAdapter()
    plan = plan_delegated_workflow(
        "telegram",
        "Copy this code, open Sublime, create a file in Downloads, paste it, and save it.",
    )

    report = DelegatedWorkflowRunner(desktop_adapter=adapter).run(plan)

    assert report.status == "completed"
    assert report.completed_steps == [
        "capture_source_content",
        "set_clipboard",
        "launch_or_focus_app",
        "create_or_open_file",
        "write_text",
        "verify_result",
    ]
    assert report.evidence["destination_path"] == "/tmp/Downloads/example.py"
    assert adapter.files["/tmp/Downloads/example.py"] == "print('hello')"


def test_runner_blocks_desktop_sequence_when_no_adapter():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    plan = plan_delegated_workflow(
        "telegram",
        "Copy this code, open Sublime, create a file in Downloads, paste it, and save it.",
    )

    report = DelegatedWorkflowRunner().run(plan)

    assert report.status == "blocked"
    assert report.current_step == "capture_source_content"
    assert report.roadblock is not None
    assert report.roadblock.code == "desktop_adapter_unavailable"


class FakeBrowserAdapter:
    def __init__(self):
        self.calls = []

    def execute(self, *, instruction: str, context: dict):
        self.calls.append((instruction, context))
        return {
            "success": True,
            "output": "Opened example.com and captured screenshot.",
            "screenshot_path": "/tmp/example.png",
            "console_errors": [],
        }


def test_runner_executes_browser_workflow_with_adapter():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    adapter = FakeBrowserAdapter()
    plan = plan_delegated_workflow(
        "telegram",
        "Open https://example.com in the browser, take a screenshot, and report back.",
    )

    report = DelegatedWorkflowRunner(browser_adapter=adapter).run(plan, context={"ticket_id": 22})

    assert report.status == "completed"
    assert report.completed_steps == [
        "prepare_browser_task",
        "execute_browser_actions",
        "verify_browser_result",
    ]
    assert adapter.calls[0][1]["ticket_id"] == 22
    assert report.evidence["browser"]["screenshot_path"] == "/tmp/example.png"


def test_runner_blocks_browser_workflow_when_no_adapter():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    plan = plan_delegated_workflow(
        "telegram",
        "Open https://example.com in the browser, take a screenshot, and report back.",
    )

    report = DelegatedWorkflowRunner().run(plan)

    assert report.status == "blocked"
    assert report.current_step == "execute_browser_actions"
    assert report.roadblock is not None
    assert report.roadblock.code == "browser_adapter_unavailable"


def test_runner_executes_project_handoff_with_dispatcher():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    dispatcher = FakeProjectDispatcher()
    plan = plan_delegated_workflow(
        "telegram",
        "Tell Cursor to update the settings panel and report back.",
    )

    report = DelegatedWorkflowRunner(project_dispatcher=dispatcher).run(plan, context={"project_id": 44})

    assert report.status == "completed"
    assert report.completed_steps == [
        "resolve_project_context",
        "prepare_handoff_packet",
        "dispatch_project_handoff",
    ]
    assert dispatcher.calls[0][0] == "cursor"
    assert "Tell Cursor to update the settings panel" in dispatcher.calls[0][1]
    assert dispatcher.calls[0][3]["project_id"] == 44
    assert report.evidence["handoff"]["success"] is True


def test_runner_blocks_project_handoff_without_dispatcher():
    from distr.core.hermes_delegated.planner import plan_delegated_workflow
    from distr.core.hermes_delegated.runner import DelegatedWorkflowRunner

    plan = plan_delegated_workflow(
        "telegram",
        "Tell Codex to run the test suite and report back.",
    )

    report = DelegatedWorkflowRunner().run(plan, context={"project_id": 44})

    assert report.status == "blocked"
    assert report.current_step == "dispatch_project_handoff"
    assert report.roadblock is not None
    assert report.roadblock.code == "backend_not_ready"
