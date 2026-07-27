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
        orchestrator_policy=json.dumps(policy or {}),
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


def test_harness_category_treats_preserved_frontend_as_backend_constraint():
    from distr.core.orchestrator_routing import _infer_harness_category

    assert _infer_harness_category(
        "Verify the Django backend. Preserve the existing frontend and TrackPlayer."
    ) == "api"


@patch("distr.core.settings.load_settings_from_db", return_value={})
@patch(
    "distr.core.project_cli_backends.models_catalog.pi_cli_models",
    return_value=[{"id": "qwen/qwen3-coder:free", "provider": "openrouter"}],
)
def test_pi_route_resolves_missing_provider_from_model_catalog(_models, _settings):
    from distr.core.orchestrator_routing import _resolved_model_provider

    assert _resolved_model_provider(
        "pi",
        {"model": "qwen/qwen3-coder:free"},
    ) == "openrouter"


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_uses_board_complexity_override(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
):
    from distr.core.orchestrator_routing import resolve_execution_route

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
    assert decision.model_provider == "cursor"
    assert decision.to_route_dict()["model_provider"] == "cursor"
    assert decision.source in {"board_override", "policy", "harness_preference"}


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.orchestrator.inspect_visual_baseline_readiness", return_value={"ready": True, "verdict": "pass"})
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_applies_harness_preference(
    mock_resolve,
    mock_get_backend,
    _mock_readiness,
    _mock_llm,
    _mock_emit,
):
    from distr.core.orchestrator_routing import resolve_execution_route

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


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm")
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_requires_approval_for_llm_override(
    mock_resolve,
    mock_get_backend,
    mock_llm,
    _mock_emit,
):
    from distr.core.orchestrator_routing import resolve_execution_route

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


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_resolve_execution_route_falls_back_when_backend_unavailable(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
):
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.side_effect = [
        {"backend": "codex", "model": "auto", "complexity": "medium"},
        {
            "backend": "pi",
            "model": "ornith:35b",
            "model_provider": "ollama",
            "complexity": "medium",
        },
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
    assert decision.model == "ornith:35b"
    assert decision.model_provider == "ollama"
    assert decision.source == "fallback"


@patch("distr.core.settings.load_settings_from_db", return_value={})
@patch(
    "distr.core.project_cli_backends.model_policy._free_eligible_model",
    return_value={"backend": "pi", "model": "kilo-auto/free", "provider": "kilocode"},
)
@patch("distr.core.qualification.ProviderCertificationStore.get")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
@pytest.mark.parametrize("certification_status", ["limited", "unavailable"])
def test_direct_route_replaces_model_that_failed_real_execution(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    mock_certification,
    _mock_free_route,
    _mock_settings,
    certification_status,
):
    from distr.core.qualification import CertificationStatus
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.return_value = {
        "backend": "codex",
        "model": "gpt-5.3-codex",
        "complexity": "medium",
    }
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)
    mock_certification.return_value = SimpleNamespace(
        status=CertificationStatus(certification_status),
        provider="openai",
        model="gpt-5.3-codex",
    )

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(),
        board=None,
        emit_event=False,
    )

    assert decision.backend_id == "pi"
    assert decision.model == "kilo-auto/free"
    assert decision.model_provider == "kilocode"
    assert decision.source == "fallback"


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_route_decided_event_includes_harness_intake_profile(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    mock_emit,
):
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.return_value = {"backend": "codex", "model": "auto", "complexity": "medium"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)

    resolve_execution_route(
        project=_project(),
        ticket=_ticket(
            title="UI critical redesign",
            description="Need screenshots and flow polish.",
        ),
        board=_board(),
        emit_event=True,
    )

    payload = mock_emit.call_args.kwargs["payload"]
    profile = payload["intake_profile"]
    assert profile["ui_heavy"] is True
    assert profile["route_pressure"] == "codex"
    assert "ui_critical" in profile["risk_flags"]


@patch(
    "distr.core.orchestrator.inspect_visual_baseline_readiness",
    return_value={
        "ready": False,
        "verdict": "fail",
        "baseline_count": 1,
        "missing_screen_count": 1,
        "missing": [{"screen_name": "Dashboard", "screenshot_path": "/missing/dashboard.png"}],
    },
)
@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_ui_route_decision_records_visual_baseline_readiness_gap(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    mock_emit,
    mock_readiness,
):
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.return_value = {"backend": "codex", "model": "auto", "complexity": "medium"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(
            title="Polish React dashboard UI",
            description="Need screenshots, flow summary, and hierarchy review.",
        ),
        board=_board(),
        emit_event=True,
    )

    mock_readiness.assert_called_once_with(board_id=10, project_id=1, include_global=True)
    payload = mock_emit.call_args.kwargs["payload"]
    assert payload["visual_baseline_readiness"]["ready"] is False
    assert payload["visual_baseline_readiness"]["missing_screen_count"] == 1
    assert "visual baseline" in decision.rationale.lower()
    assert "visual baseline" in payload["decision"]["rationale"].lower()


@patch(
    "distr.core.orchestrator.inspect_visual_baseline_readiness",
    return_value={
        "ready": False,
        "verdict": "fail",
        "baseline_count": 0,
        "missing_screen_count": 0,
        "missing": [],
    },
)
@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_low_risk_ui_without_ready_baseline_promotes_to_codex(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
    _mock_readiness,
):
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.return_value = {"backend": "cursor", "model": "auto", "complexity": "low"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(
            complexity="low",
            title="Rename UI button text",
            description="Small CSS label tweak on the dashboard button.",
        ),
        board=_board(),
        emit_event=False,
    )

    assert decision.backend_id == "codex"
    assert decision.source == "harness_preference"
    assert "visual baseline not ready" in decision.rationale.lower()


@patch(
    "distr.core.orchestrator.inspect_visual_baseline_readiness",
    return_value={"ready": False, "verdict": "fail", "baseline_count": 0, "missing_screen_count": 0},
)
@patch("distr.core.orchestrator.record_routing_override")
@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_demote_override_can_keep_low_risk_ui_on_cursor_despite_missing_baseline(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
    mock_record_override,
    _mock_readiness,
):
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.return_value = {"backend": "cursor", "model": "auto", "complexity": "low"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(
            complexity="low",
            title="Demote to Cursor",
            description="Small CSS label tweak on the dashboard button.",
        ),
        board=_board(),
        emit_event=True,
    )

    assert decision.backend_id == "cursor"
    assert "visual baseline not ready" in decision.rationale.lower()
    mock_record_override.assert_called_once()


@patch("distr.core.orchestrator.record_routing_override")
@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_explicit_promote_override_forces_codex_and_records_learning(
    mock_resolve,
    mock_get_backend,
    _mock_llm,
    _mock_emit,
    mock_record_override,
):
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.return_value = {"backend": "cursor", "model": "auto", "complexity": "low"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)

    decision = resolve_execution_route(
        project=_project(),
        ticket=_ticket(
            complexity="low",
            title="Promote to Codex",
            description="Polish the UI hierarchy and screenshot flow.",
        ),
        board=_board(),
        emit_event=True,
    )

    assert decision.backend_id == "codex"
    assert decision.source == "harness_preference"
    assert "promote to codex" in decision.rationale.lower()
    mock_record_override.assert_called_once()
    assert mock_record_override.call_args.kwargs["override"] == "promote_to_codex"
    assert mock_record_override.call_args.kwargs["original_backend"] == "cursor"
    assert mock_record_override.call_args.kwargs["final_backend"] == "codex"


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_routing._call_orchestrator_llm", return_value=None)
@patch("distr.core.orchestrator.build_visual_taste_context", return_value="[VISUAL TASTE MEMORY]\n- approved: Dense operational layouts.")
@patch("distr.core.orchestrator.build_learned_rules_context", return_value="[BOARD LEARNED RULES]\n- Validate browser flows.")
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_orchestrator_advisory_receives_visual_taste_context_for_ui_work(
    mock_resolve,
    mock_get_backend,
    _mock_learned,
    _mock_taste,
    mock_llm,
    _mock_emit,
):
    from distr.core.orchestrator_routing import resolve_execution_route

    mock_resolve.return_value = {"backend": "codex", "model": "auto", "complexity": "medium"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)

    resolve_execution_route(
        project=_project(),
        ticket=_ticket(
            title="UI critical redesign",
            description="Need screenshots, flow polish, and visual hierarchy.",
        ),
        board=_board(),
        emit_event=False,
    )

    learned_context = mock_llm.call_args.kwargs["learned_context"]
    assert "[BOARD LEARNED RULES]" in learned_context
    assert "[VISUAL TASTE MEMORY]" in learned_context
    assert "Dense operational layouts" in learned_context
