"""E2E: sequential queue auto-advances to the next ticket after a successful run."""

from __future__ import annotations

import contextlib
import json

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.project_cli_backends import registry as backend_registry

from tests.core.workflow_e2e_harness import (
    MatrixFakeBackend,
    apply_preset_to_workflow,
    make_factory,
    wait_for_terminal_run,
    workflow_patch_stack,
)


def _seed_two_queued_tickets(factory, tmp_path, workflow_id: int) -> dict:
    session = factory()
    try:
        project = Project(
            name="Queue Handoff Project",
            folder_location=str(tmp_path),
            coding_backend="pi",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board = KanbanBoard(
            name="Queue Board",
            in_use=True,
            default_project_id=project.id,
            default_workflow_id=workflow_id,
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Queue", position=0)
        session.add(lane)
        session.flush()

        ticket_a = KanbanTicket(
            lane_id=lane.id,
            title="First queued ticket",
            description="First in queue.",
            linked_project_id=project.id,
            linked_workflow_id=workflow_id,
            workflow_queue_position=1,
            position=0,
        )
        ticket_b = KanbanTicket(
            lane_id=lane.id,
            title="Second queued ticket",
            description="Should auto-start after first completes.",
            linked_project_id=project.id,
            linked_workflow_id=workflow_id,
            workflow_queue_position=2,
            position=1,
        )
        session.add(ticket_a)
        session.add(ticket_b)
        session.flush()

        ids = {
            "project_id": project.id,
            "board_id": board.id,
            "ticket_a_id": ticket_a.id,
            "ticket_b_id": ticket_b.id,
        }
        session.commit()
        return ids
    finally:
        session.close()


def _set_sequential_run_settings(factory, workflow_id: int) -> None:
    session = factory()
    try:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        wf.run_settings = json.dumps(
            {
                "execution_mode": "sequential",
                "concurrency_scope": "workflow",
                "max_parallel_tickets": 1,
                "branch_per_ticket": True,
            }
        )
        session.commit()
    finally:
        session.close()


def test_sequential_queue_auto_starts_next_ticket(tmp_path):
    from distr.core.workflow.dispatcher import _active_runs, _runs_lock, start_workflow_run

    # Queue handoff crosses worker threads.  A StaticPool in-memory SQLite
    # connection cannot safely serve concurrent cursors and causes intermittent
    # SQLAlchemy row-decoding failures unrelated to workflow behavior.
    factory = make_factory(tmp_path, memory=False)
    applied = apply_preset_to_workflow(factory, "development-ticket-to-implementation")
    workflow_id = applied["workflow_id"]
    _set_sequential_run_settings(factory, workflow_id)
    ids = _seed_two_queued_tickets(factory, tmp_path, workflow_id)

    MatrixFakeBackend.calls = 0
    original_pi = backend_registry._BACKENDS.get("pi")
    backend_registry._BACKENDS["pi"] = MatrixFakeBackend()

    first_run_id = None
    second_run_id = None
    try:
        with contextlib.ExitStack() as stack:
            for patcher in workflow_patch_stack(factory, tmp_path):
                stack.enter_context(patcher)

            result = start_workflow_run(
                workflow_id,
                board_id=ids["board_id"],
                ticket_id=ids["ticket_a_id"],
                run_metadata={
                    "project_id": ids["project_id"],
                    "project_name": "Queue Handoff Project",
                    "project_folder": str(tmp_path),
                    "ticket_title": "First queued ticket",
                },
            )
            assert "error" not in result, result
            first_run_id = result["run_id"]

            wait_for_terminal_run(factory, first_run_id, timeout=90.0)

            session = factory()
            try:
                first_run = session.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.id == first_run_id
                ).one()
                assert first_run.status == "completed"

                second_run = (
                    session.query(AutoWorkflowRun)
                    .filter(
                        AutoWorkflowRun.workflow_id == workflow_id,
                        AutoWorkflowRun.ticket_id == ids["ticket_b_id"],
                    )
                    .order_by(AutoWorkflowRun.id.desc())
                    .first()
                )
                assert second_run is not None, "expected auto-started run for second ticket"
                second_run_id = second_run.id
                assert second_run.status in ("running", "waiting", "completed")
            finally:
                session.close()

            if second_run_id:
                wait_for_terminal_run(factory, second_run_id, timeout=90.0)

            with _runs_lock:
                for run_id in list(_active_runs.keys()):
                    ctx = _active_runs.pop(run_id, None)
                    if ctx:
                        ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
    finally:
        if original_pi is not None:
            backend_registry._BACKENDS["pi"] = original_pi
