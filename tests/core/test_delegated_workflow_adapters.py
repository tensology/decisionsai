from __future__ import annotations


class FakeConnector:
    def __init__(self):
        self.downloads = []

    def is_connected(self):
        return True

    def check_inbox(self, max_results=10, query="in:inbox"):
        assert max_results == 10
        assert "from:Julie" in query
        return [
            {
                "id": "msg-1",
                "from": "Julie <julie@example.com>",
                "subject": "Changes",
                "body": "See attachment.",
                "attachments": [
                    {
                        "attachment_id": "att-1",
                        "filename": "changes.pdf",
                        "mime_type": "application/pdf",
                        "size": 123,
                    }
                ],
            }
        ]

    def download_email_attachment(self, *, message_id, attachment_id, filename, destination_dir):
        self.downloads.append((message_id, attachment_id, filename, destination_dir))
        return f"{destination_dir}/{filename}"


def test_google_email_adapter_searches_and_downloads_attachments():
    from distr.core.delegated_workflow.adapters import GoogleEmailAdapter

    connector = FakeConnector()
    adapter = GoogleEmailAdapter(connector=connector)

    email = adapter.search_latest_email(sender_hint="Julie", query="in:inbox from:Julie has:attachment")
    attachments = adapter.download_attachments(message_id=email["message_id"], destination_dir="/tmp/intake")

    assert email["message_id"] == "msg-1"
    assert attachments == [{"path": "/tmp/intake/changes.pdf", "name": "changes.pdf", "mime_type": "application/pdf", "size": 123}]
    assert connector.downloads == [("msg-1", "att-1", "changes.pdf", "/tmp/intake")]


def test_project_cli_dispatcher_returns_blocker_without_project_id():
    from distr.core.delegated_workflow.adapters import ProjectCliDispatcher

    result = ProjectCliDispatcher().dispatch(
        backend_id="codex",
        instruction="Implement scoped work",
        scope={"tasks": ["Update copy"]},
        context={},
    )

    assert result.success is False
    assert result.backend_id == "codex"
    assert "project_id" in result.error


def test_project_cli_dispatcher_expunge_loaded_project_before_session_closes(monkeypatch):
    from types import SimpleNamespace

    from distr.core.delegated_workflow.adapters import ProjectCliDispatcher

    project = SimpleNamespace(id=7)
    calls = []

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return project

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def query(self, *_args, **_kwargs):
            return _Query()

        def expunge(self, value):
            calls.append(("expunge", value))

    monkeypatch.setattr("distr.core.db.get_session", lambda: _Session())

    loaded = ProjectCliDispatcher()._load_project(7)

    assert loaded is project
    assert calls == [("expunge", project)]


def test_direct_desktop_adapter_uses_clipboard_and_filesystem(tmp_path):
    from distr.core.delegated_workflow.adapters import DirectDesktopAdapter

    clipboard = {"value": "console.log('hi')"}
    adapter = DirectDesktopAdapter(
        home_dir=str(tmp_path),
        clipboard_getter=lambda: clipboard["value"],
        clipboard_setter=lambda value: clipboard.update(value=value) or True,
    )

    source = adapter.capture_source_content("Copy this code, create a file in Downloads called app.js, paste it, and save it.")
    assert source == "console.log('hi')"
    assert adapter.set_clipboard(source) is True
    focus = adapter.launch_or_focus_app("open Sublime")
    path = adapter.create_or_open_file("create a file in Downloads called app.js")
    assert focus["strategy"] == "direct_file_preferred"
    assert path.endswith("/Downloads/app.js")
    assert adapter.write_text(path, source) is True
    assert adapter.verify_result(path, source) is True
    assert (tmp_path / "Downloads" / "app.js").read_text(encoding="utf-8") == "console.log('hi')"


class FakePlaywrightTool:
    def __init__(self, output="Playwright script completed successfully.\n\nOutput:\nExample Domain"):
        self.output = output
        self.calls = []

    def _run(self, **kwargs):
        self.calls.append(kwargs)
        return self.output


def test_playwright_browser_adapter_generates_navigation_script_for_url():
    from distr.core.delegated_workflow.adapters import PlaywrightBrowserAdapter

    tool = FakePlaywrightTool()
    adapter = PlaywrightBrowserAdapter(tool=tool)

    result = adapter.execute(
        instruction="Open https://example.com in the browser and take a screenshot.",
        context={"ticket_id": 1},
    )

    assert result["success"] is True
    assert "Example Domain" in result["output"]
    assert "https://example.com" in tool.calls[0]["code"]
    assert "page.screenshot" in tool.calls[0]["code"]
    assert tool.calls[0]["analyze_screenshot"] is True


def test_playwright_browser_adapter_accepts_file_url_for_local_smoke():
    from distr.core.delegated_workflow.adapters import PlaywrightBrowserAdapter

    tool = FakePlaywrightTool()
    adapter = PlaywrightBrowserAdapter(tool=tool)

    result = adapter.execute(
        instruction="Open file:///tmp/hermes-browser-smoke.html in the browser and take a screenshot.",
        context={},
    )

    assert result["success"] is True
    assert "file:///tmp/hermes-browser-smoke.html" in tool.calls[0]["code"]


def test_playwright_browser_adapter_blocks_without_url():
    from distr.core.delegated_workflow.adapters import PlaywrightBrowserAdapter

    tool = FakePlaywrightTool()
    adapter = PlaywrightBrowserAdapter(tool=tool)

    result = adapter.execute(instruction="Click the settings button in the browser.", context={})

    assert result["success"] is False
    assert "URL" in result["error"]
    assert tool.calls == []
