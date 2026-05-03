"""Sidecar tool availability watch (R23)."""

from __future__ import annotations

import pytest
from langchain_core.tools import BaseTool

from distr.core.agent.tools.registry import get_tool_registry, reset_tool_registry_for_tests
from distr.core.agent.tools.sidecar_tool_watch import (
    prime_sidecar_tool_availability,
    reset_sidecar_tool_watch_for_tests,
    tick_sidecar_tool_availability,
)


class _Stub(BaseTool):
    name: str = "run_python"
    description: str = "stub"

    def _run(self, **kwargs):
        return "ok"


@pytest.fixture(autouse=True)
def _reset_registry_and_watch():
    reset_tool_registry_for_tests()
    reset_sidecar_tool_watch_for_tests()
    yield
    reset_tool_registry_for_tests()
    reset_sidecar_tool_watch_for_tests()


def test_initial_tick_marks_unavailable_when_sidecar_down(monkeypatch):
    reg = get_tool_registry()
    reg.register(_Stub(), "native")
    monkeypatch.setattr(
        "distr.core.agent.tools.input.sidecar_http.is_sidecar_reachable",
        lambda timeout=2.0: False,
    )
    tick_sidecar_tool_availability()
    assert reg.get_record("run_python") is not None
    assert reg.get_record("run_python").available is False
    assert reg.get_by_name("run_python") is None


def test_prime_forces_resync_after_health_changes(monkeypatch):
    """prime_* clears memoized health so we resync immediately (startup path)."""
    reg = get_tool_registry()
    reg.register(_Stub(), "native")
    monkeypatch.setattr(
        "distr.core.agent.tools.input.sidecar_http.is_sidecar_reachable",
        lambda timeout=2.0: True,
    )
    tick_sidecar_tool_availability()
    assert reg.get_record("run_python").available is True

    monkeypatch.setattr(
        "distr.core.agent.tools.input.sidecar_http.is_sidecar_reachable",
        lambda timeout=2.0: False,
    )
    prime_sidecar_tool_availability()
    assert reg.get_record("run_python").available is False


def test_transition_up_enables_tools(monkeypatch):
    reg = get_tool_registry()
    reg.register(_Stub(), "native")
    states = iter([False, True])

    monkeypatch.setattr(
        "distr.core.agent.tools.input.sidecar_http.is_sidecar_reachable",
        lambda timeout=2.0: next(states),
    )
    tick_sidecar_tool_availability()
    assert reg.get_record("run_python").available is False
    tick_sidecar_tool_availability()
    assert reg.get_record("run_python").available is True
    assert reg.get_by_name("run_python") is not None
