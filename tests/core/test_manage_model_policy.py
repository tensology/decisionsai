from __future__ import annotations

from contextlib import contextmanager
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
from distr.core.project_cli_backends.policy_manager import (
    _apply_recent_model_health,
    _apply_provider_health,
    _counts_as_model_health_failure,
    apply_model_policy_plan,
    build_model_policy_plan,
    refresh_auto_model_policy_for_workflow,
)


def test_auto_health_demotes_repeatedly_failing_leaderboard_model():
    routes = [
        {"backend": "pi", "model_provider": "openrouter", "model": "leader:free", "score": 100},
        {"backend": "pi", "model_provider": "openrouter", "model": "runner-up:free", "score": 90},
    ]

    healthy = _apply_recent_model_health(routes, {"leader:free": 3})

    assert [route["model"] for route in healthy] == ["runner-up:free"]
    assert healthy[0]["health_status"] == "healthy"


def test_auto_health_keeps_catalogue_when_every_model_is_temporarily_unhealthy():
    routes = [{"model": "only:free"}]

    annotated = _apply_recent_model_health(routes, {"only:free": 2})

    assert annotated[0]["model"] == "only:free"
    assert annotated[0]["recent_failures"] == 2
    assert annotated[0]["health_status"] == "demoted"


@pytest.mark.parametrize("error", [
    "Pi exited with code 143.",
    "Pi execution cancelled.",
    "Provider session outlived its terminal workflow run.",
])
def test_auto_health_does_not_blame_model_for_cancelled_work(error):
    assert _counts_as_model_health_failure(error) is False


def test_auto_health_counts_only_route_readiness_failures():
    assert _counts_as_model_health_failure("429 Rate limit exceeded") is True
    assert _counts_as_model_health_failure("Provider unavailable: HTTP 503") is True
    assert _counts_as_model_health_failure("Missing required Status contract") is False
    assert _counts_as_model_health_failure(
        "Inspection budget exceeded: model used 13 tool calls; this step allows 12."
    ) is False


def test_openrouter_rate_limit_cooldown_prefers_available_local_route():
    routes = [
        {"model_provider": "openrouter", "model": "cloud:free"},
        {"model_provider": "ollama", "model": "ornith:35b", "local": True},
    ]

    available = _apply_provider_health(routes, openrouter_free_cooldown=True)

    assert [route["model"] for route in available] == ["ornith:35b"]
    assert available[0]["provider_health"] == "healthy"


@pytest.fixture()
def policy_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def get_session():
        @contextmanager
        def ctx():
            session = factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        return ctx()

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"ollama_enabled": True, "openrouter_enabled": True, "openrouter_key": "secret"},
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.installed_ollama_cli_models",
        lambda _settings: [
            {
                "id": "qwen3.5:397b-cloud",
                "provider": "ollama",
                "free": False,
                "local": False,
                "usable": True,
            },
            {
                "id": "ornith:35b",
                "provider": "ollama",
                "free": True,
                "local": True,
                "usable": True,
            },
        ],
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.rank_openrouter_free_models",
        lambda **_kwargs: [
            {
                "backend": "pi",
                "model_provider": "openrouter",
                "model": "free/reviewer:free",
                "score": 99,
                "reason": "independent free reviewer",
            },
            {
                "backend": "pi",
                "model_provider": "openrouter",
                "model": "free/validator:free",
                "score": 90,
                "reason": "independent free validator",
            },
        ],
    )
    with factory() as db:
        workflow = AutoWorkflow(name="Development", status="active", run_settings=json.dumps({"execution_mode": "sequential"}))
        db.add(workflow)
        db.flush()
        db.add_all([
            AutoWorkflowStep(workflow_id=workflow.id, position=0, name="Plan implementation", instruction="Plan it"),
            AutoWorkflowStep(workflow_id=workflow.id, position=1, name="Implement change", instruction="Build it"),
            AutoWorkflowStep(workflow_id=workflow.id, position=2, name="Independent code review", instruction="Review it"),
            AutoWorkflowStep(workflow_id=workflow.id, position=3, name="Run Playwright validation", instruction="Test it"),
        ])
        db.commit()
        workflow_id = workflow.id
    return factory, workflow_id


def test_auto_preview_resolves_every_step_without_mutating(policy_db):
    factory, workflow_id = policy_db

    plan = build_model_policy_plan(workflow_id=workflow_id, mode="auto", preference="free")

    assert [item["role"] for item in plan["workflow"]["steps"]] == [
        "planning", "implementation", "review", "validation",
    ]
    assert all(item["route"]["model"] for item in plan["workflow"]["steps"])
    assert plan["catalog"]["candidate_count"] == 3
    assert plan["workflow"]["steps"][1]["route"]["model"] == "ornith:35b"
    assert plan["workflow"]["steps"][2]["route"]["model"] == "free/reviewer:free"
    assert plan["workflow"]["steps"][1]["route"]["model"] != plan["workflow"]["steps"][2]["route"]["model"]
    with factory() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        assert "resolved_model_plan" not in json.loads(workflow.run_settings)
        assert all(not step.config for step in workflow.steps)


def test_auto_preview_can_prefer_local_planning_before_remote_free(policy_db):
    _factory, workflow_id = policy_db

    plan = build_model_policy_plan(
        workflow_id=workflow_id,
        mode="auto",
        preference="free",
        prefer_local=True,
    )

    assert plan["role_routes"]["planning"]["model"] == "ornith:35b"
    assert plan["role_routes"]["implementation"]["model"] == "ornith:35b"
    assert plan["role_routes"]["review"]["model_provider"] == "openrouter"


def test_apply_auto_policy_persists_auditable_scoped_routes(policy_db):
    factory, workflow_id = policy_db
    plan = build_model_policy_plan(workflow_id=workflow_id, mode="auto", preference="free")

    changed = apply_model_policy_plan(plan)

    assert len(changed["workflow"]["steps"]) == 4
    with factory() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        settings = json.loads(workflow.run_settings)
        assert settings["model_policy_mode"] == "auto"
        assert settings["auto_route_models"] is True
        configs = [json.loads(step.config) for step in workflow.steps]
        assert all(config["execution_route"]["enabled"] for config in configs)
        assert all(config["execution_route"]["route_snapshot"]["model"] for config in configs)
        assert configs[1]["execution_route"]["scoped_model_key"] != configs[2]["execution_route"]["scoped_model_key"]


def test_pinned_policy_is_editable_configuration_not_source_code(policy_db):
    factory, workflow_id = policy_db
    plan = build_model_policy_plan(
        workflow_id=workflow_id,
        mode="pinned",
        assignments={
            "roles": {
                "planning": {"backend": "codex", "model": "gpt-current"},
                "implementation": {"backend": "pi", "provider": "ollama", "model": "ornith:35b"},
                "review": {"backend": "pi", "provider": "openrouter", "model": "vendor/new-reviewer"},
            }
        },
    )
    apply_model_policy_plan(plan)

    with factory() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        configs = [json.loads(step.config) for step in workflow.steps]
        assert configs[0]["execution_route"]["route_snapshot"]["model"] == "gpt-current"
        assert configs[1]["execution_route"]["route_snapshot"]["model"] == "ornith:35b"
        assert configs[2]["execution_route"]["route_snapshot"]["model"] == "vendor/new-reviewer"
        assert json.loads(workflow.run_settings)["auto_route_models"] is False


def test_auto_policy_is_refreshed_at_preflight_but_pinned_policy_is_not(policy_db, monkeypatch):
    factory, workflow_id = policy_db
    apply_model_policy_plan(build_model_policy_plan(workflow_id=workflow_id, mode="auto"))
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.rank_openrouter_free_models",
        lambda **_kwargs: [{
            "backend": "pi",
            "model_provider": "openrouter",
            "model": "free/new-today:free",
            "score": 120,
            "reason": "new live catalogue leader",
        }],
    )

    refreshed = refresh_auto_model_policy_for_workflow(workflow_id)

    assert refreshed is not None
    assert refreshed["plan"]["catalog"]["candidates"][1]["model"] == "free/new-today:free"
    with factory() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        settings = json.loads(workflow.run_settings)
        assert settings["resolved_model_plan"]["generated_at"] == refreshed["plan"]["generated_at"]

    apply_model_policy_plan(build_model_policy_plan(workflow_id=workflow_id, mode="pinned"))
    assert refresh_auto_model_policy_for_workflow(workflow_id) is None


def test_global_policy_apply_updates_all_complexity_routes(policy_db, monkeypatch):
    _factory, _workflow_id = policy_db
    saved = {}
    monkeypatch.setattr("distr.core.settings.save_settings_to_db", lambda updates: saved.update(updates))

    plan = build_model_policy_plan(scope="global", mode="auto", preference="balanced")
    changed = apply_model_policy_plan(plan)

    assert changed["global"]
    assert {f"project_cli_{level}_backend" for level in ("low", "medium", "high")} <= saved.keys()
    assert {f"project_cli_{level}_model" for level in ("low", "medium", "high")} <= saved.keys()
    assert {f"project_cli_{level}_model_provider" for level in ("low", "medium", "high")} <= saved.keys()


@pytest.mark.parametrize("natural_preference", ["prefer_free", "free-first", "local first", "cheap", "cheapest"])
def test_natural_cost_preference_aliases_resolve_to_free(policy_db, natural_preference):
    _factory, workflow_id = policy_db

    plan = build_model_policy_plan(workflow_id=workflow_id, mode="auto", preference=natural_preference)

    assert plan["preference"] == "free"


@pytest.mark.parametrize("natural_preference", ["auto", "automatic"])
def test_automatic_preference_aliases_resolve_to_balanced(policy_db, natural_preference):
    _factory, workflow_id = policy_db

    plan = build_model_policy_plan(workflow_id=workflow_id, mode="auto", preference=natural_preference)

    assert plan["preference"] == "balanced"


def test_tool_is_registered_for_the_conversational_orchestrator():
    from distr.core.agent.tools.loader import TOOL_REGISTRY

    assert TOOL_REGISTRY["ManageModelPolicyTool"] == (
        "system.manage_model_policy",
        "ManageModelPolicyTool",
    )


def test_step_title_wins_over_incidental_plan_words_in_instruction():
    from types import SimpleNamespace
    from distr.core.project_cli_backends.policy_manager import _step_role

    assert _step_role(SimpleNamespace(
        name="Implement the slice with project checks",
        instruction="Follow the approved implementation plan and acceptance criteria.",
        config=None,
    )) == "implementation"
    assert _step_role(SimpleNamespace(
        name="Capture browser evidence and self-assess",
        instruction="Check the implementation plan in a browser.",
        config=None,
    )) == "review"
    assert _step_role(SimpleNamespace(
        name="Correct, re-run, or skip with reason",
        instruction="Correct defects from the plan.",
        config=None,
    )) == "implementation"
    assert _step_role(SimpleNamespace(
        name="Final production polish and ship audit",
        instruction="Fix only remaining release blockers.",
        config=None,
    )) == "final_polish"


def test_auto_role_policy_reserves_codex_for_final_polish(monkeypatch):
    from distr.core.project_cli_backends.model_policy import apply_auto_step_role_policy

    workflow = type("Workflow", (), {"run_settings": json.dumps({"auto_route_models": True})})()
    route = apply_auto_step_role_policy(
        {"backend": "pi", "model": "ornith:35b", "model_provider": "ollama", "complexity": "high"},
        workflow=workflow,
        config={},
        settings={},
        step_role="final_polish",
    )

    assert route["backend"] == "codex"
    assert route["model"] == "auto"
    assert "final production polish" in route["policy_reason"].lower()
