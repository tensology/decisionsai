"""Chained Spotify E2E: ideation → development → polish with board cleanup."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from unittest.mock import patch

from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflowRun
from scripts.workflow_ticket_loop_e2e import SPOTIFY_LANES, build_spotify_ticket_specs

from tests.core.workflow_e2e_harness import (
    FIXTURES_DIR,
    MatrixFakeBackend,
    apply_preset_to_workflow,
    assert_run_terminal,
    cleanup_workflow_run_context,
    disable_step_wait_gates,
    get_session_factory,
    make_factory,
    seed_board_ticket_project,
    wait_for_terminal_run,
    workflow_patch_stack,
)

SPOTIFY_REQUIREMENTS_FIXTURE = FIXTURES_DIR / "spotify_remake_requirements.md"
IDEATION_SLUG = "ideation-brief-to-board"
DEVELOPMENT_SLUG = "development-ticket-to-implementation"
POLISH_SLUG = "polish-verify-and-ship"
SPOTIFY_BOARD_PREFIX = "Spotify E2E "


class IdeationMatrixAgent:
    """Orchestrator stand-in that provisions the board during ideation steps."""

    factory = None
    tmp_path: Path | None = None
    dev_workflow_id: int | None = None
    last_board: dict[str, Any] | None = None

    def __init__(self, event_queue=None):
        self.event_queue = event_queue

    def enable_computer_use(self, goal: str = "") -> None:
        return None

    async def execute(self, prompt: str) -> str:
        lower = (prompt or "").lower()
        if "create board" in lower or "board, lanes, and tickets" in lower:
            assert self.factory is not None and self.tmp_path is not None
            IdeationMatrixAgent.last_board = provision_spotify_board(
                self.factory,
                self.tmp_path,
            )
            count = len(IdeationMatrixAgent.last_board.get("ticket_ids") or [])
            return f"Created board {IdeationMatrixAgent.last_board['board_id']} with {count} development tickets."
        if "queue tickets for development" in lower or "queue tickets" in lower:
            assert self.factory is not None and IdeationMatrixAgent.last_board
            assert self.dev_workflow_id is not None
            queue_board_for_development(
                self.factory,
                board_id=int(IdeationMatrixAgent.last_board["board_id"]),
                workflow_id=int(self.dev_workflow_id),
                project_id=int(IdeationMatrixAgent.last_board["project_id"]),
            )
            return "Queued development tickets on the board with workflow linkage."
        return "Ideation step complete with evidence attached."


class PolishMatrixAgent:
    """May file a polish ticket when polish workflow finds gaps."""

    factory = None
    last_polish_ticket_id: int | None = None

    def __init__(self, event_queue=None):
        self.event_queue = event_queue

    def enable_computer_use(self, goal: str = "") -> None:
        return None

    async def execute(self, prompt: str) -> str:
        lower = (prompt or "").lower()
        if "file polish tickets" in lower or "polish tickets" in lower:
            return "No polish tickets required; all gates are green."
        if "release evidence" in lower or "close with release" in lower:
            return (
                "Release evidence attached. Spotify remake is ready to ship with "
                "security audit, UI evidence, and release notes."
            )
        return "Polish step complete with evidence attached."


def provision_spotify_board(factory, tmp_path: Path, *, stamp: str = "matrix") -> dict[str, Any]:
    """Create board, lanes, project, and development tickets from Spotify specs."""
    project_dir = tmp_path / "spotify-remake"
    project_dir.mkdir(parents=True, exist_ok=True)
    specs = build_spotify_ticket_specs()[:3]

    session = factory()
    try:
        project = Project(
            name=f"Spotify remake {stamp}",
            folder_location=str(project_dir),
            coding_backend="pi",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board = KanbanBoard(
            name=f"{SPOTIFY_BOARD_PREFIX}{stamp}",
            in_use=True,
            default_project_id=project.id,
        )
        session.add(board)
        session.flush()

        lane_by_name: dict[str, KanbanLane] = {}
        for position, lane_name in enumerate(SPOTIFY_LANES):
            lane = KanbanLane(board_id=board.id, name=lane_name, position=position)
            session.add(lane)
            lane_by_name[lane_name] = lane
        session.flush()

        backlog = lane_by_name["Backlog"]
        ticket_ids: list[int] = []
        for index, spec in enumerate(specs):
            acceptance = "\n".join(f"- {item}" for item in spec.acceptance)
            description = f"{spec.description}\n\nAcceptance criteria:\n{acceptance}"
            ticket = KanbanTicket(
                lane_id=backlog.id,
                title=spec.title,
                description=description,
                complexity=spec.complexity,
                priority=spec.priority,
                time_estimate=spec.time_estimate,
                linked_project_id=project.id,
                position=index,
            )
            session.add(ticket)
            session.flush()
            ticket_ids.append(int(ticket.id))

        session.commit()
        return {
            "board_id": int(board.id),
            "project_id": int(project.id),
            "ticket_ids": ticket_ids,
            "lane_ids": {name: int(lane.id) for name, lane in lane_by_name.items()},
        }
    finally:
        session.close()


def queue_board_for_development(
    factory,
    *,
    board_id: int,
    workflow_id: int,
    project_id: int,
) -> None:
    session = factory()
    try:
        board = session.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).one()
        board.default_workflow_id = int(workflow_id)
        board.default_project_id = int(project_id)
        tickets = (
            session.query(KanbanTicket)
            .join(KanbanLane, KanbanTicket.lane_id == KanbanLane.id)
            .filter(KanbanLane.board_id == int(board_id))
            .order_by(KanbanTicket.position.asc(), KanbanTicket.id.asc())
            .all()
        )
        ready_lane = (
            session.query(KanbanLane)
            .filter(KanbanLane.board_id == int(board_id), KanbanLane.name == "Ready")
            .one()
        )
        for index, ticket in enumerate(tickets, start=1):
            ticket.linked_workflow_id = int(workflow_id)
            ticket.linked_project_id = int(project_id)
            ticket.workflow_queue_position = index
            ticket.lane_id = ready_lane.id
        session.commit()
    finally:
        session.close()


def delete_spotify_board(factory, board_id: int) -> None:
    """Remove a disposable Spotify E2E board and its tickets (in-memory tests)."""
    session = factory()
    try:
        board = session.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).first()
        if not board:
            return
        if not str(board.name or "").startswith(SPOTIFY_BOARD_PREFIX):
            raise ValueError(f"Refusing to delete non-test board: {board.name}")
        lanes = session.query(KanbanLane).filter(KanbanLane.board_id == board.id).all()
        lane_ids = [lane.id for lane in lanes]
        if lane_ids:
            session.query(KanbanTicket).filter(KanbanTicket.lane_id.in_(lane_ids)).delete(
                synchronize_session=False
            )
            session.query(KanbanLane).filter(KanbanLane.id.in_(lane_ids)).delete(
                synchronize_session=False
            )
        session.delete(board)
        session.commit()
    finally:
        session.close()


def assert_board_cleaned_up(factory, board_id: int) -> None:
    session = factory()
    try:
        assert session.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).first() is None
    finally:
        session.close()


def _sync_ideation_agent_step(self, step_data, run_id=None) -> dict[str, Any]:
    instruction = (step_data.get("instruction") or "").lower()
    name = (step_data.get("name") or "").lower()
    if "create board" in instruction or "create board" in name:
        assert IdeationMatrixAgent.factory is not None and IdeationMatrixAgent.tmp_path is not None
        IdeationMatrixAgent.last_board = provision_spotify_board(
            IdeationMatrixAgent.factory,
            IdeationMatrixAgent.tmp_path,
        )
        board = IdeationMatrixAgent.last_board
        return {
            "output": f"Created board {board['board_id']} with {len(board['ticket_ids'])} tickets.",
            "passed": True,
        }
    if "queue tickets" in instruction or "queue tickets" in name:
        assert IdeationMatrixAgent.last_board and IdeationMatrixAgent.dev_workflow_id
        queue_board_for_development(
            IdeationMatrixAgent.factory,
            board_id=int(IdeationMatrixAgent.last_board["board_id"]),
            workflow_id=int(IdeationMatrixAgent.dev_workflow_id),
            project_id=int(IdeationMatrixAgent.last_board["project_id"]),
        )
        return {"output": "Queued development tickets on the board.", "passed": True}
    return {"output": "Ideation step complete with evidence attached.", "passed": True}


def _sync_polish_agent_step(self, step_data, run_id=None) -> dict[str, Any]:
    instruction = (step_data.get("instruction") or "").lower()
    if "release evidence" in instruction or "close with release" in instruction:
        return {
            "output": "Release evidence attached. Spotify remake is ready to ship.",
            "passed": True,
        }
    return {"output": "Polish step complete with evidence attached.", "passed": True}


def _sync_dev_agent_step(self, step_data, run_id=None) -> dict[str, Any]:
    return {
        "output": (
            "Development slice closed with evidence attached. "
            "Ticket contains development evidence and a clear slice completion summary."
        ),
        "passed": True,
    }


def _workflow_patch_stack_with_agent(factory, tmp_path, agent_cls, *, mode: str = "ideation"):
    patches = workflow_patch_stack(factory, tmp_path)
    if mode == "ideation":
        patches.append(
            patch(
                "distr.core.workflow.step_executor.StepExecutorMixin._run_agent",
                _sync_ideation_agent_step,
            )
        )
    elif mode == "polish":
        patches.append(
            patch(
                "distr.core.workflow.step_executor.StepExecutorMixin._run_agent",
                _sync_polish_agent_step,
            )
        )
    else:
        patches.append(patch("distr.core.workflow_agent.WorkflowAgent", agent_cls))
    return patches


def start_ideation_run(
    factory,
    tmp_path: Path,
    *,
    dev_workflow_id: int,
    ideation_workflow_id: int | None = None,
    timeout: float = 90.0,
) -> dict[str, Any]:
    from distr.core.project_cli_backends import registry as backend_registry
    from distr.core.workflow.dispatcher import _active_runs, _runs_lock, start_workflow_run

    requirements_path = tmp_path / "spotify_remake_requirements.md"
    requirements_path.write_text(
        SPOTIFY_REQUIREMENTS_FIXTURE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    IdeationMatrixAgent.factory = factory
    IdeationMatrixAgent.tmp_path = tmp_path
    IdeationMatrixAgent.dev_workflow_id = dev_workflow_id
    IdeationMatrixAgent.last_board = None

    if ideation_workflow_id is None:
        applied = apply_preset_to_workflow(factory, IDEATION_SLUG)
        workflow_id = applied["workflow_id"]
    else:
        workflow_id = int(ideation_workflow_id)
        disable_step_wait_gates(factory, workflow_id)

    original_pi = backend_registry._BACKENDS.get("pi")
    backend_registry._BACKENDS["pi"] = MatrixFakeBackend()
    run_id = None
    try:
        with contextlib.ExitStack() as stack:
            for patcher in _workflow_patch_stack_with_agent(
                factory, tmp_path, IdeationMatrixAgent, mode="ideation"
            ):
                stack.enter_context(patcher)

            result = start_workflow_run(
                workflow_id,
                run_metadata={
                    "requirements_path": str(requirements_path),
                    "skip_run_briefing": True,
                    "skip_human_checkpoints": True,
                    "product_brief": "Spotify remake from requirements fixture",
                },
            )
            assert "error" not in result, result
            run_id = result["run_id"]

            wait_for_terminal_run(
                factory,
                run_id,
                timeout=timeout,
                auto_continue=False,
                workflow_id=workflow_id,
            )
            terminal = assert_run_terminal(factory, run_id, expect_status="completed")

            with _runs_lock:
                ctx = _active_runs.pop(run_id, None)
            if ctx:
                ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)

        assert IdeationMatrixAgent.last_board is not None, "ideation did not create a board"
        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "board": IdeationMatrixAgent.last_board,
            "terminal": terminal,
        }
    finally:
        cleanup_workflow_run_context(*( [run_id] if run_id is not None else [] ))
        if original_pi is not None:
            backend_registry._BACKENDS["pi"] = original_pi


def start_preset_run_for_ticket(
    factory,
    tmp_path: Path,
    preset_slug: str,
    *,
    board_id: int,
    ticket_id: int,
    project_id: int,
    title: str,
    workflow_id: int | None = None,
    timeout: float = 90.0,
    drain_queued: bool = False,
) -> dict[str, Any]:
    from distr.core.project_cli_backends import registry as backend_registry
    from distr.core.workflow.dispatcher import _active_runs, _runs_lock, start_workflow_run

    MatrixFakeBackend.calls = 0
    MatrixFakeBackend.last_loop_context = False

    if workflow_id is None:
        applied = apply_preset_to_workflow(factory, preset_slug)
        workflow_id = applied["workflow_id"]
    else:
        disable_step_wait_gates(factory, int(workflow_id))

    original_pi = backend_registry._BACKENDS.get("pi")
    backend_registry._BACKENDS["pi"] = MatrixFakeBackend()
    run_id = None
    try:
        with contextlib.ExitStack() as stack:
            for patcher in workflow_patch_stack(factory, tmp_path):
                stack.enter_context(patcher)
            stack.enter_context(
                patch(
                    "distr.core.workflow.step_executor.StepExecutorMixin._run_agent",
                    _sync_dev_agent_step,
                )
            )

            result = start_workflow_run(
                workflow_id,
                board_id=board_id,
                ticket_id=ticket_id,
                run_metadata={
                    "project_id": project_id,
                    "project_name": "Spotify remake",
                    "project_folder": str(tmp_path / "spotify-remake"),
                    "ticket_title": title,
                    "skip_run_briefing": True,
                    "skip_human_checkpoints": True,
                },
            )
            assert "error" not in result, result
            run_id = result["run_id"]

            wait_for_terminal_run(
                factory,
                run_id,
                timeout=timeout,
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                project_id=project_id,
            )
            terminal = assert_run_terminal(
                factory,
                run_id,
                expect_status="completed",
                ticket_id=ticket_id,
            )

            if drain_queued:
                drain_active_workflow_runs(
                    factory,
                    int(workflow_id),
                    timeout=timeout,
                    board_id=board_id,
                    project_id=project_id,
                )

            with _runs_lock:
                ctx = _active_runs.pop(run_id, None)
            if ctx:
                ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)

        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "ticket_id": ticket_id,
            "terminal": terminal,
            "cli_calls": MatrixFakeBackend.calls,
            "loop_context_seen": MatrixFakeBackend.last_loop_context,
        }
    finally:
        cleanup_workflow_run_context(*( [run_id] if run_id is not None else [] ))
        if original_pi is not None:
            backend_registry._BACKENDS["pi"] = original_pi


def start_polish_run(
    factory,
    tmp_path: Path,
    *,
    board_id: int,
    ticket_id: int,
    project_id: int,
    polish_workflow_id: int | None = None,
    timeout: float = 90.0,
) -> dict[str, Any]:
    PolishMatrixAgent.factory = factory
    original_pi = None
    from distr.core.project_cli_backends import registry as backend_registry

    if polish_workflow_id is None:
        applied = apply_preset_to_workflow(factory, POLISH_SLUG)
        workflow_id = applied["workflow_id"]
    else:
        workflow_id = int(polish_workflow_id)
        disable_step_wait_gates(factory, workflow_id)

    original_pi = backend_registry._BACKENDS.get("pi")
    backend_registry._BACKENDS["pi"] = MatrixFakeBackend()
    run_id = None
    try:
        with contextlib.ExitStack() as stack:
            for patcher in _workflow_patch_stack_with_agent(
                factory, tmp_path, PolishMatrixAgent, mode="polish"
            ):
                stack.enter_context(patcher)

            from distr.core.workflow.dispatcher import _active_runs, _runs_lock, start_workflow_run

            result = start_workflow_run(
                workflow_id,
                board_id=board_id,
                ticket_id=ticket_id,
                run_metadata={
                    "project_id": project_id,
                    "project_name": "Spotify remake",
                    "project_folder": str(tmp_path / "spotify-remake"),
                    "ticket_title": "Polish Spotify remake",
                    "skip_run_briefing": True,
                    "skip_human_checkpoints": True,
                },
            )
            assert "error" not in result, result
            run_id = result["run_id"]

            wait_for_terminal_run(
                factory,
                run_id,
                timeout=timeout,
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                project_id=project_id,
            )
            terminal = assert_run_terminal(factory, run_id, expect_status="completed", ticket_id=ticket_id)

            with _runs_lock:
                ctx = _active_runs.pop(run_id, None)
            if ctx:
                ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)

        return {"workflow_id": workflow_id, "run_id": run_id, "terminal": terminal}
    finally:
        cleanup_workflow_run_context(*( [run_id] if run_id is not None else [] ))
        if original_pi is not None:
            backend_registry._BACKENDS["pi"] = original_pi


def drain_active_workflow_runs(
    factory,
    workflow_id: int,
    *,
    timeout: float = 120.0,
    board_id: int | None = None,
    project_id: int | None = None,
) -> list[int]:
    """Wait until no runs on this workflow remain in running/waiting."""
    from distr.core.workflow.dispatcher import continue_waiting_step

    deadline = time.time() + timeout
    completed_run_ids: list[int] = []
    while time.time() < deadline:
        session = factory()
        try:
            active = (
                session.query(AutoWorkflowRun)
                .filter(
                    AutoWorkflowRun.workflow_id == int(workflow_id),
                    AutoWorkflowRun.status.in_(["running", "waiting"]),
                )
                .order_by(AutoWorkflowRun.id.asc())
                .all()
            )
            if not active:
                return completed_run_ids
            for run in active:
                if run.status == "waiting":
                    try:
                        run_data = json.loads(run.run_data or "{}")
                    except Exception:
                        run_data = {}
                    waiting_kind = str(run_data.get("waiting_kind") or "")
                    if waiting_kind == "run_briefing":
                        continue_waiting_step(int(run.id), "yes, go ahead")
                    elif waiting_kind == "step_review":
                        continue_waiting_step(int(run.id), "looks good, continue")
                    else:
                        continue_waiting_step(int(run.id), "matrix auto-continue")
                elif run.status == "completed" and int(run.id) not in completed_run_ids:
                    completed_run_ids.append(int(run.id))
        finally:
            session.close()
        time.sleep(0.15)

    raise AssertionError(f"workflow {workflow_id} still has active runs after {timeout}s")


def run_spotify_workflow_chain(factory, tmp_path: Path) -> dict[str, Any]:
    """Run ideation → development (each ticket) → polish; return summary for assertions."""
    ideation_wf = apply_preset_to_workflow(factory, IDEATION_SLUG)
    dev_wf = apply_preset_to_workflow(factory, DEVELOPMENT_SLUG)
    polish_wf = apply_preset_to_workflow(factory, POLISH_SLUG)

    ideation = start_ideation_run(
        factory,
        tmp_path,
        dev_workflow_id=int(dev_wf["workflow_id"]),
        ideation_workflow_id=int(ideation_wf["workflow_id"]),
    )
    board = ideation["board"]
    board_id = int(board["board_id"])
    project_id = int(board["project_id"])
    ticket_ids = list(board["ticket_ids"])

    dev_results = []
    specs = build_spotify_ticket_specs()[:3]
    dev_results.append(
        start_preset_run_for_ticket(
            factory,
            tmp_path,
            DEVELOPMENT_SLUG,
            board_id=board_id,
            ticket_id=int(ticket_ids[0]),
            project_id=project_id,
            title=specs[0].title,
            workflow_id=int(dev_wf["workflow_id"]),
            drain_queued=True,
            timeout=120.0,
        )
    )
    session = factory()
    try:
        dev_run_ids = [
            int(row.id)
            for row in session.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == int(dev_wf["workflow_id"]),
                AutoWorkflowRun.status == "completed",
            )
            .order_by(AutoWorkflowRun.id.asc())
            .all()
        ]
        assert len(dev_run_ids) >= 3, dev_run_ids
    finally:
        session.close()

    polish = start_polish_run(
        factory,
        tmp_path,
        board_id=board_id,
        ticket_id=int(ticket_ids[0]),
        project_id=project_id,
        polish_workflow_id=int(polish_wf["workflow_id"]),
    )

    delete_spotify_board(factory, board_id)
    assert_board_cleaned_up(factory, board_id)

    return {
        "ideation": ideation,
        "development": dev_results,
        "development_run_ids": dev_run_ids,
        "polish": polish,
        "board_id": board_id,
        "ticket_ids": ticket_ids,
    }
