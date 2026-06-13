"""Regression coverage for terminal workflow audit and ticket writeback."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base


def _make_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed_terminal_run(factory):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
    from distr.core.kanban.result_packet import create_initial_result_packet_for_run

    session = factory()
    try:
        board = KanbanBoard(name="Decisions", in_use=True)
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Current", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Update docs note",
            description="Original ticket body.",
            priority="medium",
            position=0,
        )
        session.add(ticket)
        session.flush()

        workflow = AutoWorkflow(name="Docs Workflow", description="Update a docs note.")
        session.add(workflow)
        session.flush()

        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Update note",
            position=0,
            action_type="agent_instruction",
            instruction="Update the docs note.",
            status="passed",
        )
        session.add(step)
        session.flush()

        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            board_id=board.id,
            ticket_id=ticket.id,
            current_step_id=step.id,
            status="running",
            run_data=json.dumps(
                {
                    "risk_profile": {"level": "low", "signals": [], "risk_type": "standard"},
                    "result_packet": create_initial_result_packet_for_run(
                        ticket_id=ticket.id,
                        board_id=board.id,
                        board_name=board.name,
                        project_id=None,
                        project_name=None,
                        execution_lane="cursor",
                    ),
                }
            ),
        )
        session.add(run)
        session.flush()

        session.add(
            AutoWorkflowStepResult(
                step_id=step.id,
                run_id=run.id,
                agent_response="Updated the docs note and checked the output.",
                status="passed",
            )
        )
        ids = {
            "board_id": board.id,
            "ticket_id": ticket.id,
            "workflow_id": workflow.id,
            "step_id": step.id,
            "run_id": run.id,
        }
        session.commit()
        return ids
    finally:
        session.close()


def test_complete_run_persists_terminal_packet_ticket_note_and_audit_entry():
    from distr.core.db.kanban import KanbanTicket, KanbanTicketAuditEntry
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.dispatcher import complete_run

    factory = _make_factory()
    ids = _seed_terminal_run(factory)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.workflow.dispatcher.get_session", get_session), patch(
        "distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()
    ), patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()), patch(
        "distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()
    ), patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()):
        assert complete_run(ids["run_id"], "completed") is True

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).first()
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ids["ticket_id"]).first()
        audit_entries = (
            session.query(KanbanTicketAuditEntry)
            .filter(KanbanTicketAuditEntry.ticket_id == ids["ticket_id"])
            .order_by(KanbanTicketAuditEntry.id.asc())
            .all()
        )

        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]

        assert run.status == "completed"
        assert packet["status"] == "completed"
        assert packet["summary"] == f"Workflow run {ids['run_id']} finished with status: completed."
        assert packet["audit"]["final_verdict"] == "pass"
        assert f"workflow_run:{ids['run_id']}" in packet["artifacts"]["logs"]

        assert ticket.workflow_status == "completed"
        assert f"[Workflow Run #{ids['run_id']}] Status: completed" in ticket.description
        assert "Update note: passed" in ticket.description
        assert "Evidence:" in ticket.description

        assert audit_entries
        terminal = audit_entries[-1]
        assert terminal.run_id == ids["run_id"]
        assert terminal.status == "completed"
        assert terminal.final_verdict == "pass"


def test_complete_run_allows_ui_heavy_packet_with_taste_aware_validation():
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.dispatcher import complete_run

    factory = _make_factory()
    ids = _seed_terminal_run(factory)

    def get_session():
        return _session_ctx(factory)

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_data = json.loads(run.run_data or "{}")
        run_data["risk_profile"] = {
            "level": "high",
            "signals": ["ui", "flow"],
            "risk_type": "product_conversion",
        }
        packet = run_data["result_packet"]
        packet["artifacts"] = {
            "logs": [],
            "screenshots": ["/tmp/decisions/workflow_screenshots/ui-after.png"],
            "diffs_or_patches": [],
            "links": [],
            "ui_quality": {
                "before_unavailable_reason": "Terminal run did not provide a before screenshot slot.",
                "after_screenshot": "/tmp/decisions/workflow_screenshots/ui-after.png",
                "flow_summary": "Opened settings and saved the compact form.",
                "happy_path_steps": ["Save"],
                "click_count": 1,
                "layout_hierarchy_notes": "Kept the compact form hierarchy clear and the save action primary.",
            },
        }
        packet["execution"] = {
            "action_trace": [{"action_type": "click", "description": "Save"}],
            "validation_snapshots": [
                {
                    "validation_type": "ui_quality",
                    "verdict": "pass",
                    "expected": "UI work includes screenshots and flow evidence before completion.",
                    "observed": "Flow summary: opened settings and saved the compact form.",
                    "standards_context": "[VISUAL TASTE MEMORY]\n- approved: Compact operational controls.",
                }
            ],
        }
        packet["tests_and_checks"] = {
            "tests_run": ["lint", "typecheck", "build", "tests"],
            "results": [],
        }
        run.run_data = json.dumps(run_data)

    with patch("distr.core.workflow.dispatcher.get_session", get_session), patch(
        "distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()
    ), patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()), patch(
        "distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()
    ), patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()):
        assert complete_run(ids["run_id"], "completed") is True

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_status = run.status
        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]

    assert run_status == "completed"
    assert packet["status"] == "completed"
    assert packet["audit"]["final_verdict"] == "pass"
    assert "workflow_run:" + str(ids["run_id"]) in packet["artifacts"]["logs"]
    assert packet["execution"]["validation_snapshots"][0]["validation_type"] == "ui_quality"


def test_complete_run_records_ui_quality_validation_from_visual_baseline_artifacts(tmp_path):
    from distr.core.db.orchestrator import OrchestratorValidationRecord, OrchestratorVisualBaselineScreen, OrchestratorVisualBaselineSet
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.dispatcher import complete_run

    factory = _make_factory()
    ids = _seed_terminal_run(factory)
    baseline_path = tmp_path / "baseline.png"
    candidate_path = tmp_path / "candidate.png"
    Image.new("RGB", (4, 4), color=(34, 92, 160)).save(baseline_path)
    Image.new("RGB", (4, 4), color=(34, 92, 160)).save(candidate_path)

    def get_session():
        return _session_ctx(factory)

    with get_session() as session:
        baseline = OrchestratorVisualBaselineSet(
            name="Gold Admin",
            scope="board",
            scope_id=ids["board_id"],
            description="Reference dashboard.",
        )
        session.add(baseline)
        session.flush()
        session.add(
            OrchestratorVisualBaselineScreen(
                baseline_set_id=baseline.id,
                screen_name="Dashboard",
                screenshot_path=str(baseline_path),
            )
        )

        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]
        packet["artifacts"]["screenshots"] = [str(candidate_path)]
        packet["artifacts"]["ui_quality"] = {
            "before_unavailable_reason": "No before screenshot was captured for this UI step.",
            "after_screenshot": str(candidate_path),
            "flow_summary": "Opened the dashboard and confirmed the dense admin overview.",
            "happy_path_steps": ["Open dashboard"],
            "click_count": 1,
            "layout_hierarchy_notes": "Kept the dense admin overview hierarchy aligned to the reference dashboard.",
            "visual_baseline_name": "Gold Admin",
            "baseline_screen_name": "Dashboard",
            "visual_diff_threshold": 0.01,
        }
        packet["execution"]["validation_snapshots"] = []
        run.run_data = json.dumps(run_data)

    with patch("distr.core.workflow.dispatcher.get_session", get_session), patch(
        "distr.core.orchestrator.get_session", get_session
    ), patch("distr.core.db.get_session", get_session), patch(
        "distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()
    ), patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()), patch(
        "distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()
    ), patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()):
        assert complete_run(ids["run_id"], "completed") is True

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]
        validations = session.query(OrchestratorValidationRecord).all()
        validation_types = [row.validation_type for row in validations]

    assert len(validations) == 1
    assert validation_types == ["ui_quality"]
    snapshot = packet["execution"]["validation_snapshots"][0]
    assert snapshot["validation_type"] == "ui_quality"
    assert snapshot["verdict"] == "pass"
    assert snapshot["visual_baseline"]["baseline_name"] == "Gold Admin"
    assert snapshot["visual_baseline"]["screen_results"][0]["status"] == "pass"


def test_complete_run_queues_correction_for_failed_visual_baseline_validation(tmp_path):
    from distr.core.db.orchestrator import (
        OrchestratorCorrectionAttempt,
        OrchestratorValidationRecord,
        OrchestratorVisualBaselineScreen,
        OrchestratorVisualBaselineSet,
    )
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.dispatcher import complete_run

    factory = _make_factory()
    ids = _seed_terminal_run(factory)
    baseline_path = tmp_path / "baseline.png"
    candidate_path = tmp_path / "candidate.png"
    Image.new("RGB", (4, 4), color=(34, 92, 160)).save(baseline_path)
    Image.new("RGB", (4, 4), color=(210, 64, 64)).save(candidate_path)

    def get_session():
        return _session_ctx(factory)

    with get_session() as session:
        baseline = OrchestratorVisualBaselineSet(
            name="Gold Admin",
            scope="board",
            scope_id=ids["board_id"],
            description="Reference dashboard.",
        )
        session.add(baseline)
        session.flush()
        session.add(
            OrchestratorVisualBaselineScreen(
                baseline_set_id=baseline.id,
                screen_name="Dashboard",
                screenshot_path=str(baseline_path),
            )
        )

        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]
        packet["artifacts"]["screenshots"] = [str(candidate_path)]
        packet["artifacts"]["ui_quality"] = {
            "before_unavailable_reason": "No before screenshot was captured for this UI step.",
            "after_screenshot": str(candidate_path),
            "flow_summary": "Opened the dashboard and confirmed the overview.",
            "happy_path_steps": ["Open dashboard"],
            "click_count": 1,
            "visual_baseline_name": "Gold Admin",
            "baseline_screen_name": "Dashboard",
            "visual_diff_threshold": 0.01,
        }
        packet["execution"]["validation_snapshots"] = []
        run.run_data = json.dumps(run_data)

    with patch("distr.core.workflow.dispatcher.get_session", get_session), patch(
        "distr.core.orchestrator.get_session", get_session
    ), patch("distr.core.db.get_session", get_session), patch(
        "distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()
    ), patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()), patch(
        "distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()
    ), patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()):
        assert complete_run(ids["run_id"], "completed") is True

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_status = run.status
        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]
        validations = session.query(OrchestratorValidationRecord).all()
        attempts = session.query(OrchestratorCorrectionAttempt).all()
        attempt_ids = [row.id for row in attempts]
        attempt_statuses = [row.status for row in attempts]
        correction_packets = [json.loads(row.correction_packet or "{}") for row in attempts]

    assert run_status == "failed"
    assert packet["audit"]["final_verdict"] == "needs_changes"
    snapshot = packet["execution"]["validation_snapshots"][0]
    assert snapshot["validation_type"] == "ui_quality"
    assert snapshot["verdict"] == "fail"
    assert snapshot["visual_baseline"]["baseline_name"] == "Gold Admin"
    assert snapshot["visual_baseline"]["screen_results"][0]["status"] == "fail"
    assert len(validations) == 1
    assert len(attempts) == 1
    assert attempt_statuses == ["queued"]
    assert snapshot["correction_attempt_id"] == attempt_ids[0]
    assert correction_packets[0]["failed_validation"]["validation_type"] == "ui_quality"
    assert "visual baseline" in correction_packets[0]["failed_validation"]["correction_hint"].lower()


def test_complete_run_does_not_auto_dispatch_failed_visual_baseline_correction(tmp_path):
    from distr.core.db.orchestrator import (
        OrchestratorCorrectionAttempt,
        OrchestratorVisualBaselineScreen,
        OrchestratorVisualBaselineSet,
    )
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    from distr.core.workflow.dispatcher import complete_run

    factory = _make_factory()
    ids = _seed_terminal_run(factory)
    baseline_path = tmp_path / "baseline.png"
    candidate_path = tmp_path / "candidate.png"
    Image.new("RGB", (4, 4), color=(34, 92, 160)).save(baseline_path)
    Image.new("RGB", (4, 4), color=(210, 64, 64)).save(candidate_path)

    def get_session():
        return _session_ctx(factory)

    with get_session() as session:
        workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == ids["workflow_id"]).one()
        workflow.run_settings = json.dumps({
            "auto_dispatch_corrections": True,
            "max_correction_attempts": 2,
        })
        baseline = OrchestratorVisualBaselineSet(
            name="Gold Admin",
            scope="board",
            scope_id=ids["board_id"],
            description="Reference dashboard.",
        )
        session.add(baseline)
        session.flush()
        session.add(
            OrchestratorVisualBaselineScreen(
                baseline_set_id=baseline.id,
                screen_name="Dashboard",
                screenshot_path=str(baseline_path),
            )
        )

        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]
        packet["artifacts"]["screenshots"] = [str(candidate_path)]
        packet["artifacts"]["ui_quality"] = {
            "before_unavailable_reason": "No before screenshot was captured for this UI step.",
            "after_screenshot": str(candidate_path),
            "flow_summary": "Opened the dashboard and confirmed the overview.",
            "happy_path_steps": ["Open dashboard"],
            "click_count": 1,
            "visual_baseline_name": "Gold Admin",
            "baseline_screen_name": "Dashboard",
            "visual_diff_threshold": 0.01,
        }
        packet["execution"]["validation_snapshots"] = []
        run.run_data = json.dumps(run_data)

    with patch("distr.core.workflow.dispatcher.get_session", get_session), patch(
        "distr.core.orchestrator.get_session", get_session
    ), patch("distr.core.db.get_session", get_session), patch(
        "distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()
    ), patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()), patch(
        "distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()
    ), patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()), patch(
        "distr.core.workflow.dispatcher.StepDispatcher.run_in_workflow",
        return_value={"success": True},
    ) as dispatch:
        assert complete_run(ids["run_id"], "completed") is True

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        run_status = run.status
        completed_at = run.completed_at
        run_data = json.loads(run.run_data or "{}")
        attempts = session.query(OrchestratorCorrectionAttempt).all()
        attempt_ids = [row.id for row in attempts]
        attempt_statuses = [row.status for row in attempts]
        dispatch_results = [json.loads(row.dispatch_result or "{}") for row in attempts]

    assert run_status == "failed"
    assert completed_at is not None
    assert "pending_correction" not in run_data
    assert attempt_statuses == ["queued"]
    dispatch.assert_not_called()
