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
    assert len(steps) == 7
    assert steps[0]["title"] == "Understand ticket and acceptance criteria"
    assert steps[1]["title"] == "Create the implementation plan"
    assert steps[2]["title"] == "Implement the planned change"
    assert steps[3]["title"] == "Independently review and validate the change"
    assert steps[4]["title"] == "Correct defects found by validation"
    assert steps[5]["title"] == "Final production polish and ship audit"
    assert steps[-1]["title"] == "Report, update ticket, and compact memory"
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
        "reporting",
    ]
    assert "plan.md" in (planned["loop_contract"].get("exit_when") or "")


def test_consolidation_leaves_one_development_workflow_and_preserves_history(
    db_factory, monkeypatch
):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStepResult
    from distr.core.workflow.developer_workflow import consolidate_development_workflows
    from distr.core.workflow.service import list_workflows

    monkeypatch.setattr(
        "distr.core.workspace_memory.pickup_handoff.append_ledger", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "distr.core.workspace_memory.provision.bootstrap_workflow", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "distr.core.workspace_memory.stages.sync_workflow_stages", lambda *args, **kwargs: None
    )

    with db_factory() as db:
        canonical = AutoWorkflow(
            name="Development: Ticket to Implementation",
            status="active",
            workflow_type="manual",
        )
        obsolete = AutoWorkflow(
            name="Independent HY3 Code Review",
            status="active",
            workflow_type="review",
        )
        db.add_all([canonical, obsolete])
        db.flush()
        old_step = AutoWorkflowStep(
            workflow_id=canonical.id, position=0, name="Old implementation step"
        )
        db.add(old_step)
        db.flush()
        old_run = AutoWorkflowRun(workflow_id=canonical.id, status="completed")
        db.add(old_run)
        db.flush()
        db.add(
            AutoWorkflowStepResult(
                step_id=old_step.id,
                run_id=old_run.id,
                status="passed",
                agent_response="historical evidence",
            )
        )
        board = KanbanBoard(name="Product", default_workflow_id=obsolete.id)
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Build the feature",
            linked_workflow_id=canonical.id,
            workflow_queue_position=1,
        )
        obsolete_ticket = KanbanTicket(
            lane_id=lane.id,
            title="Review the feature",
            linked_workflow_id=obsolete.id,
            workflow_queue_position=1,
        )
        db.add_all([ticket, obsolete_ticket])
        db.commit()
        canonical_id = canonical.id
        obsolete_id = obsolete.id
        old_run_id = old_run.id
        old_step_id = old_step.id
        board_id = board.id
        obsolete_ticket_id = obsolete_ticket.id

    result = consolidate_development_workflows(refresh_models=False)

    assert result["workflow_id"] == canonical_id
    assert result["historical_workflow_id"] is not None
    assert result["step_count"] == 7
    assert obsolete_id in result["archived_workflow_ids"]
    with db_factory() as db:
        canonical = db.query(AutoWorkflow).filter(AutoWorkflow.id == canonical_id).one()
        assert canonical.name == "Development"
        assert canonical.status == "active"
        assert len(canonical.steps) == 7
        settings = json.loads(canonical.run_settings or "{}")
        assert settings["memory_enabled"] is True
        assert settings["capture_failures_and_lessons"] is True
        assert settings["canonical_workflow_version"] == 4
        history = db.query(AutoWorkflow).filter(
            AutoWorkflow.id == result["historical_workflow_id"]
        ).one()
        assert history.status == "archived"
        assert history.workflow_type == "audit"
        assert db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.id == old_run_id,
            AutoWorkflowRun.workflow_id == history.id,
        ).count() == 1
        assert db.query(AutoWorkflowStepResult).filter(
            AutoWorkflowStepResult.step_id == old_step_id,
            AutoWorkflowStepResult.agent_response == "historical evidence",
        ).count() == 1
        assert db.query(KanbanTicket).filter(
            KanbanTicket.id == obsolete_ticket_id,
            KanbanTicket.linked_workflow_id == canonical_id,
        ).count() == 1
        assert db.query(KanbanBoard).filter(
            KanbanBoard.id == board_id,
            KanbanBoard.default_workflow_id == canonical_id,
        ).count() == 1

    visible = list_workflows()
    assert [row["name"] for row in visible] == ["Development"]


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
