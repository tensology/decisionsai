"""Tests for structured tool-selection telemetry."""

from __future__ import annotations

import json
import logging

import pytest

from distr.core.agent.tool_telemetry import log_request_tool_event, log_retrieval_summary
from distr.core.agent.tools.request_tool import RequestToolTool


def test_log_request_tool_event_success_payload(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    log_request_tool_event(
        query="need database tool",
        success=True,
        matched_registry_class="DatabaseTool",
        injected_tool_name="database_access",
        fuzzy_score=92,
        model_name="test-model",
        message="Tool 'database_access' is now available.",
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert msg.startswith("TOOL_TELEMETRY ")
    payload = json.loads(msg.split(" ", 1)[1])
    assert payload["event"] == "request_tool"
    assert payload["success"] is True
    assert payload["matched_registry_class"] == "DatabaseTool"
    assert payload["injected_tool_name"] == "database_access"
    assert payload["fuzzy_score"] == 92
    assert payload["model_name"] == "test-model"
    assert "injection_performed" not in payload


def test_log_request_tool_event_injection_performed_flag(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    log_request_tool_event(
        query="tickets",
        success=True,
        injected_tool_name="create_ticket",
        injection_performed=False,
    )
    payload = json.loads(caplog.records[0].getMessage().split(" ", 1)[1])
    assert payload["injection_performed"] is False


def test_log_request_tool_event_failure_top_candidates(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="distr.core.agent.tool_telemetry")
    log_request_tool_event(
        query="foo",
        success=False,
        fuzzy_score=40,
        top_candidates=["A", "B", "C"],
        message="Tool not found.",
    )
    msg = caplog.records[0].getMessage()
    payload = json.loads(msg.split(" ", 1)[1])
    assert payload["success"] is False
    assert payload["top_candidates"] == ["A", "B", "C"]


def test_request_tool_rejects_bad_callback_tuple_length() -> None:
    def bad_cb(_q: str) -> tuple:
        return (True,)  # invalid

    rtt = RequestToolTool(on_tool_requested=bad_cb)
    out = rtt._run(text="x")
    assert "Internal error" in out


def test_log_retrieval_summary_debug_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="distr.core.agent.tool_telemetry")
    log_retrieval_summary(
        user_message_preview="hello world",
        tier="small",
        tool_count=5,
        tool_names_preview=["a", "b"],
        backend="sbert",
    )
    assert any("TOOL_TELEMETRY retrieval" in r.getMessage() for r in caplog.records)
