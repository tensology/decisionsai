"""Unit tests for Hermes hybrid orchestrator routing."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from distr.core.project_cli_backends.base import BackendStatus


def _project():
    return SimpleNamespace(id=1, name="Demo", folder_location="/tmp/demo")


def _board(policy: dict | None = None):
    return SimpleNamespace(
        id=10,
        hermes_policy=json.dumps(policy or {}),
    )


def _ticket(*, complexity: str = "medium", title: str = "Fix API bug", description: str = ""):
    return SimpleNamespace(
        id=42,
        title=title,
        description=description,
        complexity=complexity,
        lane_id=5,
    )


def _backend_ready(backend_id: str):
    backend = MagicMock()
    backend.setup_status.return_value = BackendStatus(
        id=backend_id,
        name=backend_id,
        installed=True,
        ready=True,
        state="ready",
        message="ready",
    )
    return backend


@patch("distr.core.hermes.emit_event")
@patch("distr.core.hermes_orchestrator._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_uses_board_complexity_override(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
):
    from distr.core.hermes_orchestrator import resolve_execution_route

    mock_resolve.return_value = {
        "backend": "cursor",
        "model": "auto",
        "complexity": "high",
    }
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)
    board = _board(
        {
            "routing_mode": "policy",
            "complexity_routing": {
                "high": {"backend": "cursor", "model": "auto"},
            },
        }
    )

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(complexity="high"),
        board=board,
        emit_event=False,
    )

    assert decision.backend_id == "cursor"
    assert decision.source in {"board_override", "policy", "harness_preference"}


@patch("distr.core.hermes.emit_event")
@patch("distr.core.hermes_orchestrator._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_applies_harness_preference(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
):
    from distr.core.hermes_orchestrator import resolve_execution_route

    mock_resolve.return_value = {"backend": "codex", "model": "auto", "complexity": "medium"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)
    board = _board(
        {
            "harness_preferences": {
                "frontend": {"backend": "cursor", "model": "auto", "skills": ["frontend-design"]},
            },
        }
    )
    ticket = _ticket(title="Update React button styling", description="Tailwind CSS tweak")

    decision = resolve_execution_route(
        project=_project(),
        ticket=ticket,
        board=board,
        emit_event=False,
    )

    assert decision.backend_id == "cursor"
    assert "frontend-design" in decision.skills


@patch("distr.core.hermes.emit_event")
@patch("distr.core.hermes_orchestrator._call_orchestrator_llm")
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_requires_approval_for_llm_override(
    mock_resolve,
    mock_get_backend,
    mock_llm,
    _mock_emit,
):
    from distr.core.hermes_orchestrator import resolve_execution_route

    mock_resolve.return_value = {"backend": "codex", "model": "auto", "complexity": "medium"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)
    mock_llm.return_value = {
        "backend": "pi",
        "model": "auto",
        "rationale": "Small scoped edit",
        "confidence": 0.8,
    }
    board = _board({"routing_mode": "hybrid", "require_approval_for_override": True})

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(complexity="low", title="Rename label copy"),
        board=board,
        emit_event=False,
    )

    assert decision.requires_approval is True
    assert decision.backend_id == "codex"
    assert decision.override_route is not None


@patch("distr.core.hermes.emit_event")
@patch("distr.core.hermes_orchestrator._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_falls_back_when_backend_unavailable(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
):
    from distr.core.hermes_orchestrator import resolve_execution_route

    mock_resolve.side_effect = [
        {"backend": "codex", "model": "auto", "complexity": "medium"},
        {"backend": "pi", "model": "auto", "complexity": "medium"},
    ]

    def _backend_status(bid):
        backend = MagicMock()
        ready = bid == "pi"
        backend.setup_status.return_value = BackendStatus(
            id=bid,
            name=bid,
            installed=True,
            ready=ready,
            state="ready" if ready else "missing",
            message="ready" if ready else "missing",
        )
        return backend

    mock_get_backend.side_effect = _backend_status

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(),
        board=None,
        emit_event=False,
    )

    assert decision.backend_id == "pi"
    assert decision.source == "fallback"
