"""Tests for elorm loop preset application from JSON bundles."""

from __future__ import annotations

import json

import distr.core.db.workflow  # noqa: F401
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, AutoWorkflowVariable
from distr.core.workflow.loop_catalog import ELORM_LOOP_KICKOFFS
from distr.core.workflow.loop_preset_loader import (
    load_bundle_by_name,
    list_preset_summaries,
    plan_steps_from_bundle,
)
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.settings.workflows import register_routes
from distr.core.workflow.loop_presets import (
    apply_loop_preset,
    export_loop_preset_json,
    import_loop_preset_json,
    list_loop_presets,
    save_loop_preset_from_workflow,
)
from distr.core.workflow.loop_preset_loader import load_bundle_by_name


def test_loop_presets_api_route_not_shadowed_by_workflow_id():
    """GET /workflows/loop-presets must register before /workflows/{workflow_id}."""
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    resp = client.get("/api/workflows/loop-presets")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("presets"), list)
    assert len(body["presets"]) == len(list_preset_summaries())
    assert body["presets"][0]["slug"] == "development-ticket-to-implementation"


def test_complexity_route_api_persists_provider_model_as_dropdown_source_of_truth(monkeypatch):
    import distr.core.settings as settings_module

    current = {
        "project_cli_low_backend": "pi",
        "project_cli_low_model": "ornith:9b",
        "project_cli_low_model_provider": "ollama",
    }
    saved = {}
    monkeypatch.setattr(settings_module, "load_settings_from_db", lambda: dict(current))
    monkeypatch.setattr(settings_module, "save_settings_to_db", lambda value: saved.update(value))
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    response = client.post("/api/workflows/orchestrator-setup", json={
        "enabled": True,
        "routing": {
            "low": {"backend": "pi", "model_provider": "ollama", "model": "ornith:35b"},
            "medium": {"backend": "pi", "model_provider": "openrouter", "model": "vendor/coder:free"},
            "high": {"backend": "codex", "model_provider": "openai", "model": "auto"},
        },
    })

    assert response.status_code == 200
    assert saved["project_cli_low_model"] == "ornith:35b"
    assert saved["project_cli_low_model_provider"] == "ollama"
    assert saved["project_cli_medium_model"] == "vendor/coder:free"
    assert saved["project_cli_medium_model_provider"] == "openrouter"
    assert saved["project_cli_high_model"] == "auto"
    assert saved["project_cli_high_model_provider"] == "openai"


@pytest.fixture()
def db_factory(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _get_session():
        from contextlib import contextmanager

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

    monkeypatch.setattr("distr.core.workflow.loop_presets.get_session", _get_session)
    monkeypatch.setattr("distr.core.workflow.service.get_session", _get_session)
    monkeypatch.setattr("distr.core.db.get_session", _get_session)
    return factory


def test_duplicate_workflow_preserves_execution_contract_and_remaps_routes(db_factory):
    from distr.core.workflow.service import duplicate_workflow

    with db_factory() as db:
        workflow = AutoWorkflow(
            name="Configured development",
            status="active",
            run_settings=json.dumps({"execution_mode": "sequential", "chosen_models": [{"id": "model-a"}]}),
            pre_chain=json.dumps(["scope-review"]),
        )
        db.add(workflow)
        db.flush()
        first = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Implement",
            action_type="send_to_project_cli",
            step_type="send_to_project_cli",
            instruction="Implement",
            config=json.dumps({"backend_id": "codex", "model": "model-a", "skills": ["tdd-workflow"], "tools": ["cli"]}),
        )
        second = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=1,
            name="Validate",
            action_type="send_to_project_cli",
            step_type="send_to_project_cli",
            instruction="Validate independently",
            config=json.dumps({"backend_id": "claude_code", "model": "model-b", "skills": ["verification-loop"]}),
        )
        db.add_all([first, second])
        db.flush()
        first.on_pass_goto = second.id
        db.add(AutoWorkflowVariable(workflow_id=workflow.id, name="Acceptance", default_value="All tests pass"))
        db.commit()
        original_id = workflow.id
        original_second_id = second.id

    copied_id = duplicate_workflow(original_id)
    with db_factory() as db:
        copied = db.query(AutoWorkflow).filter(AutoWorkflow.id == copied_id).one()
        copied_steps = sorted(copied.steps, key=lambda step: step.position)
        assert copied.status == "draft"
        assert json.loads(copied.run_settings)["chosen_models"] == [{"id": "model-a"}]
        assert json.loads(copied_steps[0].config)["skills"] == ["tdd-workflow"]
        assert copied_steps[0].on_pass_goto == copied_steps[1].id
        assert copied_steps[0].on_pass_goto != original_second_id
        assert copied.variables[0].default_value == "All tests pass"


def test_loop_preset_bundles_exist():
    summaries = list_preset_summaries()
    assert len(summaries) == 1
    slugs = {row.get("slug") for row in summaries}
    assert slugs == {"development-ticket-to-implementation"}


def test_list_loop_presets_matches_catalog():
    from distr.core.workflow.loop_preset_loader import list_preset_catalog_entries

    presets = list_loop_presets()
    catalog = list_preset_catalog_entries()
    assert len(presets) == len(catalog)
    assert presets[0]["slug"] == "development-ticket-to-implementation"


def test_plan_steps_from_bundle_has_development_loop_contract():
    bundle = load_bundle_by_name("Development")
    assert bundle is not None
    planned = plan_steps_from_bundle(bundle)
    assert planned["success"] is True
    steps = planned["steps"]
    assert len(steps) == 11
    assert steps[0]["title"] == "Understand ticket and acceptance criteria"
    assert steps[1]["title"] == "Confirm ticket execution contract"
    assert steps[2]["title"] == "Implement the planned change"
    assert steps[3]["title"] == "Independently review and validate the change"
    assert steps[4]["title"] == "Correct defects found by validation"
    assert steps[5]["title"] == "Final production polish and ship audit"
    assert steps[6]["title"] == "Prepare development deployment approval"
    assert steps[7]["title"] == "Deploy approved commit to development"
    assert steps[8]["title"] == "Prepare production promotion approval"
    assert steps[9]["title"] == "Deploy approved commit to production"
    assert steps[-1]["title"] == "Report, update ticket, and compact memory"
    assert "not another validation pass" in steps[-1]["instruction"]
    assert "Do not create or edit project files during reporting" in steps[-1]["config"]["guardrail"]
    assert "ticket_result_packet" in steps[-1]["instruction"]
    assert steps[-1]["config"]["read_only"] is True
    assert any(step["action_type"] == "send_to_project_cli" for step in steps)
    assert any("playwright" in ((step.get("config") or {}).get("tools") or []) for step in steps)
    assert steps[3].get("on_fail_goto_position") == 4
    assert steps[4].get("on_pass_goto_position") == 3
    assert [step["config"].get("step_role") for step in steps] == [
        "planning",
        "planning",
        "implementation",
        "review",
        "implementation",
        "final_polish",
        "release",
        "deployment",
        "release",
        "deployment",
        "reporting",
    ]
    assert steps[6]["require_approval"] is True
    assert steps[8]["require_approval"] is True
    assert steps[7]["require_approval"] is False
    assert steps[9]["require_approval"] is False
    assert steps[7]["config"]["environment"] == "development"
    assert steps[9]["config"]["environment"] == "production"
    assert "rollback" in steps[7]["instruction"].lower()
    assert "qa" in steps[9]["instruction"].lower()
    assert "execution contract" in (planned["loop_contract"].get("exit_when") or "")
    assert "plan.md" not in (planned["loop_contract"].get("exit_when") or "")
    assert steps[1]["config"]["expected_outputs"][0] == "execution_contract"
    assert steps[1]["config"]["review_mode"] == "independent"
    assert steps[1]["config"]["disable_tools"] is True
    assert steps[1]["config"]["required_context"] == [
        "context_packet",
        "scope_inventory",
        "unknowns",
        "route_recommendation",
        "ui_design_read_if_applicable",
    ]
    assert steps[0]["max_retries"] == 1
    assert steps[1]["max_retries"] == 1
    assert "ui_design_read_if_applicable" in steps[0]["config"]["expected_outputs"]
    assert "scope_inventory" in steps[0]["config"]["expected_outputs"]
    assert "coverage_ledger" in steps[1]["config"]["expected_outputs"]
    assert "negative_space_audit" in steps[3]["config"]["expected_outputs"]
    assert "end_to_end_evidence" in steps[3]["config"]["expected_outputs"]
    assert "completion_manifest" in steps[5]["config"]["expected_outputs"]
    assert "observable visual claims" in steps[1]["instruction"]
    assert "desktop and mobile viewports" in steps[3]["instruction"]
    assert "visual_claim_verdicts" in steps[3]["config"]["expected_outputs"]
    assert "senior product designer" in steps[5]["instruction"]
    assert "visual_quality_verdict" in steps[5]["config"]["expected_outputs"]
    assert steps[0]["config"]["read_only"] is True
    assert steps[1]["config"]["read_only"] is True
    assert steps[3]["config"]["read_only"] is True
    assert steps[2]["config"]["required_context"] == [
        "execution_contract",
        "coverage_ledger",
        "negative_space_plan",
        "end_to_end_journeys",
        "dependency_status",
        "reusable_artifacts",
        "copy_manifest_if_applicable",
        "ui_acceptance_contract_if_applicable",
    ]
    assert "Reuse each named existing artifact" in steps[2]["instruction"]
    assert "Never install a dependency or browser tool solely" in steps[2]["config"]["guardrail"]



def test_apply_loop_preset_from_bundle(db_factory):
    session = db_factory()
    wf = AutoWorkflow(name="Empty", description="", workflow_input="{}")
    session.add(wf)
    session.commit()
    session.refresh(wf)

    preset_name = ELORM_LOOP_KICKOFFS[0]["name"]
    result = apply_loop_preset(wf.id, preset_name)

    assert result["success"] is True
    assert result["step_count"] >= 3
    assert result["loop_contract"]
    assert result.get("preset_slug")

    session = db_factory()
    steps = (
        session.query(AutoWorkflowStep)
        .filter(AutoWorkflowStep.workflow_id == wf.id)
        .order_by(AutoWorkflowStep.position)
        .all()
    )
    assert len(steps) == result["step_count"]
    assert steps[0].instruction
    cfg0 = json.loads(steps[0].config or "{}")
    assert cfg0.get("guardrail")
    assert cfg0.get("skills")
    assert cfg0.get("tools")
    assert cfg0.get("failure_checklist")
    assert steps[0].validation_prompt
    wf_row = session.query(AutoWorkflow).filter(AutoWorkflow.id == wf.id).first()
    merged = json.loads(wf_row.workflow_input or "{}")
    assert merged.get("preset_name") == preset_name
    assert merged.get("preset_source") == "bundle"
    assert merged.get("loop_contract")
    run_settings = json.loads(wf_row.run_settings or "{}")
    assert run_settings["auto_route_models"] is True
    assert run_settings["adaptive_multi_model_enabled"] is True
    assert run_settings["max_parallel_evaluators"] == 2
    assert run_settings["independent_validation"] is True
    assert run_settings["max_parallel_tickets"] == 1


def test_apply_parked_ship_with_ci_preset_is_not_active(db_factory):
    session = db_factory()
    wf = AutoWorkflow(name="Deploy Gate", description="", workflow_input="{}")
    session.add(wf)
    session.commit()
    session.refresh(wf)

    result = apply_loop_preset(wf.id, "ship-pr-until-green")

    assert result["success"] is False
    assert "Unknown loop preset" in (result.get("error") or "")


def test_apply_loop_preset_append_mode(db_factory):
    session = db_factory()
    wf = AutoWorkflow(name="Append Target", description="", workflow_input="{}")
    session.add(wf)
    session.commit()
    session.refresh(wf)

    preset_name = ELORM_LOOP_KICKOFFS[0]["name"]
    result = apply_loop_preset(wf.id, preset_name, mode="append")

    assert result["success"] is True
    assert result["mode"] == "append"
    preset_steps = result["step_count"]
    assert result["total_steps"] == preset_steps

    session = db_factory()
    steps = (
        session.query(AutoWorkflowStep)
        .filter(AutoWorkflowStep.workflow_id == wf.id)
        .order_by(AutoWorkflowStep.position)
        .all()
    )
    assert len(steps) == preset_steps
    assert steps[0].name == "Understand ticket and acceptance criteria"


def test_apply_loop_preset_append_rejects_over_max_steps(db_factory):
    session = db_factory()
    wf = AutoWorkflow(name="Full", description="", workflow_input="{}")
    session.add(wf)
    session.commit()
    session.refresh(wf)
    for pos in range(12):
        session.add(
            AutoWorkflowStep(
                workflow_id=wf.id,
                position=pos,
                name=f"Step {pos + 1}",
                instruction="placeholder",
                action_type="agent_instruction",
                config="{}",
            )
        )
    session.commit()

    preset_name = ELORM_LOOP_KICKOFFS[0]["name"]
    result = apply_loop_preset(wf.id, preset_name, mode="append")

    assert result["success"] is False
    assert result.get("status_code") == 422
    assert "Cannot append" in (result.get("error") or "")


def test_export_and_import_loop_preset_round_trip(db_factory, tmp_path, monkeypatch):
    session = db_factory()
    wf = AutoWorkflow(name="Round Trip", description="Goal: test\nExit when: done", workflow_input='{"loop_contract":{"goal":"test"}}')
    session.add(wf)
    session.commit()
    session.refresh(wf)
    session.add(
        AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Work",
            instruction="Do work",
            action_type="send_to_project_cli",
            config=json.dumps({"skills": ["tdd-workflow"], "tools": ["cli"], "guardrail": "Stay scoped"}),
            validation_prompt="Evidence produced",
            validation_type="llm_judgment",
        )
    )
    session.commit()

    bundle = export_loop_preset_json(wf.id)
    assert bundle is not None
    assert bundle.get("format") == "decisionsai_loop_preset_v1"
    assert len(bundle.get("steps") or []) == 1
    assert bundle["steps"][0].get("instruction") == "Do work"

    wf2 = AutoWorkflow(name="Target", description="", workflow_input="{}")
    session.add(wf2)
    session.commit()
    session.refresh(wf2)

    result = import_loop_preset_json(wf2.id, bundle, mode="replace")
    assert result["success"] is True

    session = db_factory()
    steps = (
        session.query(AutoWorkflowStep)
        .filter(AutoWorkflowStep.workflow_id == wf2.id)
        .order_by(AutoWorkflowStep.position)
        .all()
    )
    assert len(steps) == 1
    assert steps[0].name == "Work"


def test_save_loop_preset_from_workflow(db_factory, tmp_path, monkeypatch):
    user_dir = tmp_path / "loop_presets"
    user_dir.mkdir()
    monkeypatch.setattr("distr.core.workflow.loop_preset_loader.user_presets_dir", lambda: user_dir)

    session = db_factory()
    wf = AutoWorkflow(name="Savable", description="", workflow_input="{}")
    session.add(wf)
    session.commit()
    session.refresh(wf)
    session.add(
        AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Step A",
            instruction="Do A",
            action_type="agent_instruction",
            config="{}",
        )
    )
    session.commit()

    preset_name = "My Saved Loop Test"
    result = save_loop_preset_from_workflow(wf.id, preset_name)
    assert result["success"] is True

    loaded = load_bundle_by_name(preset_name)
    assert loaded is not None
    assert loaded.get("name") == preset_name
    assert len(loaded.get("steps") or []) == 1
