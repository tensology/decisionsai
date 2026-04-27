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
        ]
        self._tools_dict = {t.name: t for t in self._tools}
        self._sticky_tool_names = set()


class _OllamaHarness(OllamaResponseMixin):
    def __init__(self) -> None:
        self._tools_dict = {"google_workspace": SimpleNamespace(name="google_workspace")}


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
