"""Memory recall for the system prompt must fail fast.

`build_developer_context()` runs on every turn and calls a MemPalace search. The
hub default timeout is 20s, so a slow hub stalled every single reply — measured
at ~21s per turn before the request even went to the model.
"""

from __future__ import annotations

import json

from distr.core.mempalace import hub as hub_module


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


def test_call_tool_passes_an_explicit_timeout(monkeypatch):
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"content": []}})

    monkeypatch.setattr(hub_module.urllib.request, "urlopen", fake_urlopen)

    hub_module.call_tool(
        "/tmp/palace",
        "mempalace_search",
        {"query": "x"},
        hub={"base_url": "http://127.0.0.1:9", "headers": {}, "pid": None},
        timeout=1.5,
    )

    assert seen["timeout"] == 1.5


def test_call_tool_defaults_to_the_module_timeout(monkeypatch):
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"content": []}})

    monkeypatch.setattr(hub_module.urllib.request, "urlopen", fake_urlopen)

    hub_module.call_tool(
        "/tmp/palace",
        "mempalace_search",
        {"query": "x"},
        hub={"base_url": "http://127.0.0.1:9", "headers": {}, "pid": None},
    )

    assert seen["timeout"] == hub_module.CALL_TIMEOUT_S


def test_prompt_recall_uses_a_short_timeout(monkeypatch):
    from distr.core.mempalace import wiring

    seen: dict = {}

    class _Adapter:
        def format_search_as_context(self, query, **kwargs):
            seen.update(kwargs)
            return "some context"

    monkeypatch.setattr(wiring, "_enabled", lambda settings=None: True)
    monkeypatch.setattr(wiring, "_adapter", lambda: _Adapter())

    wiring.prefer_read_orchestrator_context("a question")

    assert seen["timeout"] == wiring.ORCHESTRATOR_CONTEXT_TIMEOUT_S
    assert wiring.ORCHESTRATOR_CONTEXT_TIMEOUT_S <= 3.0, (
        "prompt recall must not be able to stall a turn"
    )
