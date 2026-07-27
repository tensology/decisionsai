"""Tests for Gmail tool exposure and request_tool Gmail routing safeguards."""

from types import SimpleNamespace

from distr.core.agent.services.llm.core_mixin import LLMSharedMixin
from distr.core.agent.services.llm.mixins.ollama_response import OllamaResponseMixin


class _CoreHarness(LLMSharedMixin):
    def __init__(self) -> None:
        self._model_name = "llama3:8b"
        self._tools = [
            SimpleNamespace(name="request_tool"),
            SimpleNamespace(name="google_workspace"),
            SimpleNamespace(name="tensology_workspace"),
            SimpleNamespace(name="delegated_workflow"),
        ]
        self._tools_dict = {t.name: t for t in self._tools}
        self._sticky_tool_names = set()


class _OllamaHarness(OllamaResponseMixin):
    def __init__(self) -> None:
        self._agent_name = "DecisionsAI"
        self._username = "User"
        self._model_name = "llama3:8b"
        self._tools = []
        self._tools_dict = {
            "google_workspace": SimpleNamespace(name="google_workspace"),
            "tensology_workspace": SimpleNamespace(name="tensology_workspace"),
        }
        self.chat_manager = None
        self._default_template_raw = (
            "Agent {agent_name} for {username}. Model {model_name}.\n"
            "{tools_description}\n"
            "{dropped_files_context}\n"
            "{desktop_path} {documents_path} {downloads_path} {pictures_path} "
            "{music_path} {videos_path} {home_path}"
        )

    def _get_dropped_files_context(self, chat_id=None):
        return ""


class _MailReadTool:
    def __init__(self, name, result):
        self.name = name
        self.result = result
        self.calls = []

    def _run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def test_get_filtered_tools_force_exposes_google_workspace_for_gmail_queries(monkeypatch):
    class _Retriever:
        def retrieve(self, _msg, _model):
            return ["request_tool"]

    monkeypatch.setattr(
        "distr.core.agent.tool_retriever.get_tool_retriever",
        lambda: _Retriever(),
    )

    h = _CoreHarness()
    out = h._get_filtered_tools("How many gmail emails do I have from snuza?")
    names = {t.name for t in out}

    assert "request_tool" in names
    assert "google_workspace" in names


def test_get_filtered_tools_routes_explicit_tensology_mail_without_google(monkeypatch):
    class _Retriever:
        def retrieve(self, _msg, _model):
            return ["request_tool"]

    monkeypatch.setattr(
        "distr.core.agent.tool_retriever.get_tool_retriever",
        lambda: _Retriever(),
    )

    h = _CoreHarness()
    out = h._get_filtered_tools("Check my Tensology mail inbox")
    names = {t.name for t in out}

    assert "tensology_workspace" in names
    assert "google_workspace" not in names


def test_get_filtered_tools_exposes_both_connected_mailboxes_for_generic_read(monkeypatch):
    class _Retriever:
        def retrieve(self, _msg, _model):
            return ["request_tool"]

    monkeypatch.setattr(
        "distr.core.agent.tool_retriever.get_tool_retriever",
        lambda: _Retriever(),
    )

    names = {t.name for t in _CoreHarness()._get_filtered_tools("Check my mail")}
    assert {"tensology_workspace", "google_workspace"}.issubset(names)


def test_request_tool_executes_both_active_mailboxes_for_generic_read():
    request_tool = SimpleNamespace(name="request_tool")
    tensology = _MailReadTool("tensology_workspace", '{"emails": []}')
    google = _MailReadTool("google_workspace", "Gmail inbox is empty")
    h = _CoreHarness()
    h._tools = [request_tool, tensology, google]
    h._tools_dict = {tool.name: tool for tool in h._tools}
    h._messages = [{"role": "user", "content": "Check my mail"}]
    h.chat_manager = None
    h.event_queue = None

    h._wire_request_tool_callback()
    success, result, injected = request_tool._on_tool_requested("email inbox")

    assert success is True
    assert injected is False
    assert "tensology_workspace" in result
    assert "google_workspace" in result
    assert tensology.calls[0]["action"] == "list_mail"
    assert google.calls[0]["action"] == "check_inbox"


def test_get_filtered_tools_force_exposes_delegated_workflow_for_remote_email_document_handoff(monkeypatch):
    class _Retriever:
        def retrieve(self, _msg, _model):
            return ["request_tool", "google_workspace"]

    monkeypatch.setattr(
        "distr.core.agent.tool_retriever.get_tool_retriever",
        lambda: _Retriever(),
    )

    h = _CoreHarness()
    out = h._get_filtered_tools(
        "From Telegram, access my email, fetch Julie's latest PDF, scope the changes, and prep it for Codex."
    )
    names = {t.name for t in out}

    assert "google_workspace" in names
    assert "delegated_workflow" in names


def test_get_filtered_tools_force_exposes_delegated_workflow_for_remote_browser_tasks(monkeypatch):
    class _Retriever:
        def retrieve(self, _msg, _model):
            return ["request_tool"]

    monkeypatch.setattr(
        "distr.core.agent.tool_retriever.get_tool_retriever",
        lambda: _Retriever(),
    )

    h = _CoreHarness()
    out = h._get_filtered_tools(
        "From Telegram, open https://example.com in the browser, click the docs link, take a screenshot, and report back."
    )
    names = {t.name for t in out}

    assert "delegated_workflow" in names


def test_get_filtered_tools_preserves_sticky_injected_google_workspace(monkeypatch):
    class _Retriever:
        def retrieve(self, _msg, _model):
            return ["request_tool"]

    monkeypatch.setattr(
        "distr.core.agent.tool_retriever.get_tool_retriever",
        lambda: _Retriever(),
    )

    h = _CoreHarness()
    h._sticky_tool_names.add("google_workspace")

    out = h._get_filtered_tools("hello")
    names = {t.name for t in out}

    assert "request_tool" in names
    assert "google_workspace" in names


def test_intercept_tool_calls_rewrites_gmail_request_tool_to_google_workspace():
    h = _OllamaHarness()
    tool_calls = [{"function": {"name": "request_tool", "arguments": {"text": "gmail"}}}]

    h._intercept_tool_calls(
        tool_calls,
        "count current gmail inbox emails from snuza with django error subject",
    )

    rewritten = tool_calls[0]["function"]
    assert rewritten["name"] == "google_workspace"
    assert rewritten["arguments"]["action"] == "check_inbox"
    query = rewritten["arguments"]["params"]["query"]
    assert "in:inbox" in query
    assert "from:no-reply@snuza.com" in query
    assert 'subject:"[Django] ERROR"' in query


def test_intercept_tool_calls_rewrites_tensology_mail_to_tensology_workspace():
    h = _OllamaHarness()
    tool_calls = [{"function": {"name": "request_tool", "arguments": {"text": "mail"}}}]

    h._intercept_tool_calls(tool_calls, "check my Tensology mail inbox")

    rewritten = tool_calls[0]["function"]
    assert rewritten["name"] == "tensology_workspace"
    assert rewritten["arguments"]["action"] == "list_mail"


def test_intercept_tool_calls_checks_both_mailboxes_for_generic_read():
    h = _OllamaHarness()
    tool_calls = [{"function": {"name": "request_tool", "arguments": {"text": "mail"}}}]

    h._intercept_tool_calls(tool_calls, "check my mail")

    assert [call["function"]["name"] for call in tool_calls] == [
        "tensology_workspace",
        "google_workspace",
    ]


def test_ollama_prompt_rebuild_includes_live_developer_context(monkeypatch):
    class _Context:
        def __init__(self, text):
            self.text = text

        def to_prompt_text(self, max_chars=2200):
            return self.text

    current_text = {"value": "Developer Workflow Context\n- Active work: Cursor packet picked up"}

    monkeypatch.setattr(
        "distr.core.developer_context.build_developer_context",
        lambda chat_id=None: _Context(current_text["value"]),
    )
    h = _OllamaHarness()
    h._maybe_rebuild_system_prompt("chat-1", include_tools_description=False)

    assert "Cursor packet picked up" in h.default_template

    current_text["value"] = "Developer Workflow Context\n- Active work: Codex run completed"
    h._maybe_rebuild_system_prompt("chat-1", include_tools_description=False)

    assert "Codex run completed" in h.default_template
    assert "Cursor packet picked up" not in h.default_template
