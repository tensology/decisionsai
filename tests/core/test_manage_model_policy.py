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
    apply_model_policy_plan,
    build_model_policy_plan,
    refresh_auto_model_policy_for_workflow,
)


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
        lambda _settings: [{"id": "ornith:35b", "provider": "ollama", "free": True, "usable": True}],
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
    assert plan["workflow"]["steps"][2]["route"]["model"] == "free/validator:free"
    assert plan["workflow"]["steps"][1]["route"]["model"] != plan["workflow"]["steps"][2]["route"]["model"]
    with factory() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        assert "resolved_model_plan" not in json.loads(workflow.run_settings)
        assert all(not step.config for step in workflow.steps)


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
