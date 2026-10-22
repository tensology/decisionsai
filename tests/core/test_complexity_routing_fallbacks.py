"""Complexity routing: fallbacks, board overrides and step-time readiness (2026-10-22)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.kanban.ticket_policy import resolve_ticket_cli_route
from distr.core.workflow.coordination_plan import build_run_coordination_plan
from distr.core.workflow.step_executor import StepExecutorMixin


class _Backend:
    def __init__(self, ready: bool):
        self._ready = ready

    def setup_status(self):
        return SimpleNamespace(ready=self._ready)


def _backends(ready_ids):
    return lambda backend_id: _Backend(str(backend_id) in ready_ids)


PI_SETTINGS = {
    "project_cli_high_backend": "pi",
    "project_cli_high_model_provider": "openrouter",
    "project_cli_high_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "project_cli_high_fallback_backend": "pi",
    "project_cli_high_fallback_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "project_cli_high_fallback_model_provider": "kilocode",
}


# ── ticket_policy ────────────────────────────────────────────────────────────

def test_route_passes_configured_fallback_and_provider_through(monkeypatch):
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", _backends({"pi"}))
    route = resolve_ticket_cli_route(None, "high", settings=PI_SETTINGS)
    assert route == {
        "complexity": "high",
        "backend": "pi",
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "model_provider": "openrouter",
        "fallback_backend": "pi",
        "fallback_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "fallback_model_provider": "kilocode",
    }


def test_empty_fallback_is_not_turned_into_default_pi(monkeypatch):
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", _backends({"pi"}))
    route = resolve_ticket_cli_route(
        None, "low", settings={"project_cli_low_backend": "pi", "project_cli_low_model": "x/y:free"}
    )
    assert "fallback_backend" not in route


def test_unready_primary_uses_configured_fallback_with_provider(monkeypatch):
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", _backends({"pi"}))
    settings = {
        "project_cli_high_backend": "codex",
        "project_cli_high_model": "auto",
        "project_cli_high_fallback_backend": "pi",
        "project_cli_high_fallback_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "project_cli_high_fallback_model_provider": "openrouter",
    }
    route = resolve_ticket_cli_route(None, "high", settings=settings)
    assert route["backend"] == "pi"
    assert route["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert route["model_provider"] == "openrouter"


def test_board_override_can_set_fallback(monkeypatch):
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", _backends({"pi"}))
    board = SimpleNamespace(
        orchestrator_policy=json.dumps({
            "complexity_routing": {
                "high": {
                    "backend": "pi",
                    "provider": "kilocode",
                    "model": "poolside/laguna-s-2.1:free",
                    "fallback_backend": "pi",
                    "fallback_model": "minimax/minimax-m3.1-flash",
                    "fallback_provider": "openrouter",
                }
            }
        })
    )
    route = resolve_ticket_cli_route(None, "high", board=board, settings=PI_SETTINGS)
    assert route["model"] == "poolside/laguna-s-2.1:free"
    assert route["model_provider"] == "kilocode"
    assert route["fallback_model"] == "minimax/minimax-m3.1-flash"
    assert route["fallback_model_provider"] == "openrouter"


# ── coordination plan ────────────────────────────────────────────────────────

def _plain_workflow():
    return SimpleNamespace(
        id=9,
        name="Plain",
        run_settings=json.dumps({"auto_route_models": False}),
        steps=[
            SimpleNamespace(
                id=21, position=0, name="Implement", description="",
                config=json.dumps({"step_role": "implementation"}), validation_type="rule_based",
            )
        ],
    )


def test_coordination_plan_uses_resolved_complexity_route_with_fallback(monkeypatch):
    seen = {}

    def fake_resolve(project, complexity, board=None, *, settings=None):
        seen["board"] = board
        return {
            "complexity": complexity,
            "backend": "pi",
            "model": "board/model:free",
            "model_provider": "kilocode",
            "fallback_backend": "pi",
            "fallback_model": "fb/model:free",
            "fallback_model_provider": "openrouter",
        }

    board = SimpleNamespace(id=12, orchestrator_policy="{}")
    monkeypatch.setattr("distr.core.kanban.ticket_policy.resolve_ticket_cli_route", fake_resolve)
    monkeypatch.setattr("distr.core.workflow.coordination_plan._board_for_run", lambda run_data: board)

    plan = build_run_coordination_plan(
        _plain_workflow(),
        {"execution_route": {"complexity": "high"}, "ticket_id": 5},
        settings={"project_cli_high_backend": "codex", "project_cli_high_model": "auto"},
    )
    primary = plan["assignments"]["21"]["primary_route"] if "21" in plan["assignments"] else next(
        iter(plan["assignments"].values())
    )["primary_route"]
    assert seen["board"] is board
    assert primary["backend"] == "pi"
    assert primary["model"] == "board/model:free"
    assert primary["model_provider"] == "kilocode"
    assert primary["fallback_backend"] == "pi"
    assert primary["fallback_model_provider"] == "openrouter"


# ── step executor ────────────────────────────────────────────────────────────

def test_step_time_readiness_keeps_ready_route(monkeypatch):
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", _backends({"pi"}))
    route = {"backend": "pi", "model": "a:free", "model_provider": "openrouter"}
    assert StepExecutorMixin._ready_route_or_fallback(route) == route


def test_step_time_readiness_uses_route_fallback(monkeypatch):
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", _backends({"pi"}))
    route = {
        "backend": "codex", "model": "auto", "complexity": "high",
        "fallback_backend": "pi", "fallback_model": "b:free", "fallback_model_provider": "kilocode",
    }
    out = StepExecutorMixin._ready_route_or_fallback(route)
    assert (out["backend"], out["model"], out["model_provider"]) == ("pi", "b:free", "kilocode")
    assert out["source"] == "step_readiness_fallback"
    assert "fallback_backend" not in out


def test_step_time_readiness_falls_back_to_complexity_route(monkeypatch):
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", _backends({"pi"}))
    monkeypatch.setattr(
        "distr.core.kanban.ticket_policy.resolve_ticket_cli_route",
        lambda project, complexity, board=None, **_: {
            "complexity": complexity, "backend": "pi", "model": "c:free", "model_provider": "openrouter",
        },
    )
    out = StepExecutorMixin._ready_route_or_fallback({"backend": "cursor", "model": "grok-4.7-high", "complexity": "high"})
    assert (out["backend"], out["model"], out["model_provider"]) == ("pi", "c:free", "openrouter")


def test_runtime_failover_allows_same_backend_with_different_provider():
    route = {
        "backend": "pi", "model": "m:free", "model_provider": "openrouter",
        "fallback_backend": "pi", "fallback_model": "m:free", "fallback_model_provider": "kilocode",
    }
    assert StepExecutorMixin._runtime_provider_fallback_route(route, {}) == {
        "backend": "pi", "model": "m:free", "model_provider": "kilocode",
    }
    identical = dict(route, fallback_model_provider="openrouter")
    assert StepExecutorMixin._runtime_provider_fallback_route(identical, {}) == {}


# ── workflow_execution save ──────────────────────────────────────────────────

def _save_client(monkeypatch, settings):
    from distr.gui.web.routes.development.workflow_execution import register_routes

    saved = {}
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: dict(settings))
    monkeypatch.setattr("distr.core.settings.save_settings_to_db", lambda s: saved.update(s))
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    return TestClient(app), saved


def test_workflow_save_keeps_fallbacks_for_non_ide_backends(monkeypatch):
    client, saved = _save_client(monkeypatch, {})
    routing = {
        level: {
            "backend": "pi", "model": f"{level}:free", "model_provider": "openrouter",
            "fallback_backend": "pi", "fallback_model": f"{level}-fb:free", "fallback_model_provider": "kilocode",
        }
        for level in ("low", "medium", "high")
    }
    response = client.post("/api/workflows/orchestrator-setup", json={"routing": routing})
    assert response.status_code == 200
    for level in ("low", "medium", "high"):
        assert saved[f"project_cli_{level}_fallback_backend"] == "pi"
        assert saved[f"project_cli_{level}_fallback_model"] == f"{level}-fb:free"
        assert saved[f"project_cli_{level}_fallback_model_provider"] == "kilocode"
        assert saved[f"project_cli_{level}_model_provider"] == "openrouter"


def test_workflow_save_without_fallback_fields_preserves_existing(monkeypatch):
    existing = {
        "project_cli_high_fallback_backend": "pi",
        "project_cli_high_fallback_model": "keep:free",
        "project_cli_high_fallback_model_provider": "kilocode",
    }
    client, saved = _save_client(monkeypatch, existing)
    response = client.post(
        "/api/workflows/orchestrator-setup",
        json={"routing": {"high": {"backend": "codex", "model": "auto"}}},
    )
    assert response.status_code == 200
    assert saved["project_cli_high_fallback_model"] == "keep:free"
    # Codex with no provider gets openai; an explicit provider is never replaced.
    assert saved["project_cli_high_model_provider"] == "openai"


def test_workflow_save_does_not_force_openai_over_explicit_codex_provider(monkeypatch):
    client, saved = _save_client(monkeypatch, {})
    client.post(
        "/api/workflows/orchestrator-setup",
        json={"routing": {"high": {"backend": "codex", "model": "auto", "model_provider": "azure"}}},
    )
    assert saved["project_cli_high_model_provider"] == "azure"
