"""Tests for elorm loop preset application from JSON bundles."""

from __future__ import annotations

import json

import distr.core.db.workflow  # noqa: F401
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
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
    assert len(body["presets"]) >= 12


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


def test_loop_preset_bundles_exist():
    summaries = list_preset_summaries()
    assert len(summaries) >= 12
    assert summaries[0].get("source") == "bundle"
    bundle = load_bundle_by_name(summaries[0]["name"])
    assert bundle is not None
    assert bundle.get("format") == "decisionsai_loop_preset_v1"
    assert isinstance(bundle.get("steps"), list)
    assert len(bundle["steps"]) >= 3


def test_list_loop_presets_matches_catalog():
    presets = list_loop_presets()
    assert len(presets) == len(ELORM_LOOP_KICKOFFS)
    assert presets[0]["name"] == ELORM_LOOP_KICKOFFS[0]["name"]
    assert presets[0].get("step_count", 0) >= 5


def test_plan_steps_from_bundle_has_harness_fields():
    bundle = load_bundle_by_name("De-Sloppify Pass")
    assert bundle is not None
    planned = plan_steps_from_bundle(bundle)
    assert planned["success"] is True
    steps = planned["steps"]
    assert len(steps) == 5
    cfg = steps[0]["config"]
    assert cfg.get("guardrail")
    assert cfg.get("skills")
    assert cfg.get("tools")
    assert cfg.get("failure_checklist")
    assert steps[0]["validation_prompt"]
    assert "requesting-code-review" in cfg.get("skills", [])
    assert steps[2].get("on_fail_goto_position") == 0


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


def test_apply_loop_preset_append_mode(db_factory):
    session = db_factory()
    wf = AutoWorkflow(name="Partial", description="", workflow_input="{}")
    session.add(wf)
    session.commit()
    session.refresh(wf)
    for pos in range(3):
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

    assert result["success"] is True
    assert result["mode"] == "append"
    preset_steps = result["step_count"]
    assert result["total_steps"] == 3 + preset_steps

    session = db_factory()
    steps = (
        session.query(AutoWorkflowStep)
        .filter(AutoWorkflowStep.workflow_id == wf.id)
        .order_by(AutoWorkflowStep.position)
        .all()
    )
    assert len(steps) == 3 + preset_steps
    assert steps[0].name == "Step 1"
    assert steps[3].name == "Ingest ticket"


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
