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
    assert body["presets"][0]["slug"] == "ideation-brief-to-board"


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
    assert len(summaries) == 3
    slugs = {row.get("slug") for row in summaries}
    assert slugs == {
        "ideation-brief-to-board",
        "development-ticket-to-implementation",
        "polish-verify-and-ship",
    }


def test_list_loop_presets_matches_catalog():
    from distr.core.workflow.loop_preset_loader import list_preset_catalog_entries

    presets = list_loop_presets()
    catalog = list_preset_catalog_entries()
    assert len(presets) == len(catalog)
    assert presets[0]["slug"] == "ideation-brief-to-board"


def test_plan_steps_from_bundle_has_development_loop_contract():
    bundle = load_bundle_by_name("Development: Ticket to Implementation")
    assert bundle is not None
    planned = plan_steps_from_bundle(bundle)
    assert planned["success"] is True
    steps = planned["steps"]
    assert len(steps) == 6
    assert steps[0]["title"] == "Ingest ticket, memory, and acceptance context"
    assert steps[1]["title"] == "Plan the smallest implementation slice"
    assert steps[2]["title"] == "Implement the slice with project checks"
    assert steps[3]["title"] == "Capture browser evidence and self-assess"
    assert steps[4]["title"] == "Correct, re-run, or skip with reason"
    assert steps[-1]["title"] == "Report, update ticket, and compact memory"
    assert any(step["action_type"] == "send_to_project_cli" for step in steps)
    assert any("playwright" in ((step.get("config") or {}).get("tools") or []) for step in steps)
    assert any(step.get("on_fail_goto_position") == 2 for step in steps)
    assert "plan.md" in (planned["loop_contract"].get("exit_when") or "")


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
    assert steps[0].name == "Read requirements document"


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
