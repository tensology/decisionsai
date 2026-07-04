#!/usr/bin/env python3
"""Remove DecisionsAI workflow-loop smoke-test artifacts.

This deletes only records marked with the smoke-test marker by default, plus the
project folder for those projects. Use --yes to execute; without it the script
prints what it would remove.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


SMOKE_MARKER = "[dai-smoke-loop-fixture]"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean DecisionsAI workflow loop smoke-test artifacts.")
    parser.add_argument("--marker", default=SMOKE_MARKER, help="Description marker for smoke-test projects, boards, workflows, and tickets.")
    parser.add_argument("--yes", action="store_true", help="Actually delete records and project folders.")
    args = parser.parse_args()

    from distr.core.db import get_session
    from distr.core.db.kanban import (
        KanbanBoard,
        KanbanLane,
        KanbanTicket,
        ProjectExecutionSession,
    )
    from distr.core.db.orchestrator import (
        OrchestratorCorrectionAttempt,
        OrchestratorEvent,
        OrchestratorValidationRecord,
    )
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
    from sqlalchemy import or_

    marker = str(args.marker or SMOKE_MARKER).strip()
    if not marker:
        raise SystemExit("Refusing to run with an empty marker.")

    with get_session() as db:
        marker_like = f"%{marker}%"
        projects = db.query(Project).filter(Project.description.like(marker_like)).all()
        boards = db.query(KanbanBoard).filter(KanbanBoard.description.like(marker_like)).all()
        workflows = db.query(AutoWorkflow).filter(AutoWorkflow.description.like(marker_like)).all()
        tickets = (
            db.query(KanbanTicket)
            .filter(KanbanTicket.description.like(marker_like))
            .all()
        )

        project_ids = {p.id for p in projects}
        board_ids = {b.id for b in boards}
        workflow_ids = {w.id for w in workflows}
        ticket_ids = {t.id for t in tickets}
        lane_ids = {
            lane.id
            for lane in (db.query(KanbanLane).filter(KanbanLane.board_id.in_(board_ids)).all() if board_ids else [])
        }

        run_filters = []
        if workflow_ids:
            run_filters.append(AutoWorkflowRun.workflow_id.in_(workflow_ids))
        if board_ids:
            run_filters.append(AutoWorkflowRun.board_id.in_(board_ids))
        if ticket_ids:
            run_filters.append(AutoWorkflowRun.ticket_id.in_(ticket_ids))
        run_ids = {
            r.id
            for r in (db.query(AutoWorkflowRun).filter(or_(*run_filters)).all() if run_filters else [])
        }

        session_filters = []
        if project_ids:
            session_filters.append(ProjectExecutionSession.project_id.in_(project_ids))
        if workflow_ids:
            session_filters.append(ProjectExecutionSession.workflow_id.in_(workflow_ids))
        if run_ids:
            session_filters.append(ProjectExecutionSession.run_id.in_(run_ids))
        if ticket_ids:
            session_filters.append(ProjectExecutionSession.ticket_id.in_(ticket_ids))
        session_ids = {
            s.id
            for s in (db.query(ProjectExecutionSession).filter(or_(*session_filters)).all() if session_filters else [])
        }

        folders = [
            Path(p.folder_location).expanduser()
            for p in projects
            if p.folder_location and Path(p.folder_location).expanduser().exists()
        ]

        print("Cleanup target marker:", marker)
        print("Projects:", sorted(project_ids))
        print("Boards:", sorted(board_ids))
        print("Workflows:", sorted(workflow_ids))
        print("Tickets:", sorted(ticket_ids))
        print("Lanes:", sorted(lane_ids))
        print("Runs:", sorted(run_ids))
        print("Execution sessions:", sorted(session_ids))
        print("Folders:", [str(p) for p in folders])

        if not args.yes:
            print("Dry run only. Re-run with --yes to delete.")
            return 0

        if session_ids:
            db.query(OrchestratorEvent).filter(OrchestratorEvent.execution_session_id.in_(session_ids)).delete(synchronize_session=False)
        if run_ids:
            db.query(OrchestratorCorrectionAttempt).filter(OrchestratorCorrectionAttempt.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(OrchestratorValidationRecord).filter(OrchestratorValidationRecord.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(OrchestratorEvent).filter(OrchestratorEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(AutoWorkflowStepResult).filter(AutoWorkflowStepResult.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id.in_(run_ids)).delete(synchronize_session=False)
        if ticket_ids:
            db.query(OrchestratorEvent).filter(OrchestratorEvent.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
            db.query(KanbanTicket).filter(KanbanTicket.id.in_(ticket_ids)).delete(synchronize_session=False)
        if workflow_ids:
            db.query(OrchestratorEvent).filter(OrchestratorEvent.workflow_id.in_(workflow_ids)).delete(synchronize_session=False)
            db.query(AutoWorkflowStep).filter(AutoWorkflowStep.workflow_id.in_(workflow_ids)).delete(synchronize_session=False)
        if board_ids:
            db.query(OrchestratorEvent).filter(OrchestratorEvent.board_id.in_(board_ids)).delete(synchronize_session=False)
        if lane_ids:
            db.query(KanbanLane).filter(KanbanLane.id.in_(lane_ids)).delete(synchronize_session=False)
        if project_ids:
            db.query(OrchestratorEvent).filter(OrchestratorEvent.project_id.in_(project_ids)).delete(synchronize_session=False)
        if session_ids:
            db.query(ProjectExecutionSession).filter(ProjectExecutionSession.id.in_(session_ids)).delete(synchronize_session=False)
        for workflow in workflows:
            db.delete(workflow)
        for board in boards:
            db.delete(board)
        for project in projects:
            db.delete(project)
        db.commit()

    for folder in folders:
        try:
            shutil.rmtree(folder)
            print("Deleted folder:", folder)
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"Could not delete folder {folder}: {exc}")

    print("Cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
