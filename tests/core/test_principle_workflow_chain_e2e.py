"""Principle workflow chain E2E: ideation builds a board, development runs tickets, polish verifies."""

from __future__ import annotations

import pytest

from distr.core.db.kanban import KanbanBoard, KanbanTicket
from distr.core.db.workflow import AutoWorkflowStep
from scripts.workflow_ticket_loop_e2e import build_spotify_ticket_specs

from tests.core.spotify_workflow_chain_harness import (
    DEVELOPMENT_SLUG,
    IDEATION_SLUG,
    POLISH_SLUG,
    assert_board_cleaned_up,
    run_spotify_workflow_chain,
    start_ideation_run,
)
from tests.core.workflow_e2e_harness import (
    apply_preset_to_workflow,
    cleanup_workflow_run_context,
    make_factory,
)


@pytest.fixture(autouse=True)
def _isolate_chain_runs():
    cleanup_workflow_run_context()
    yield
    cleanup_workflow_run_context()


@pytest.fixture()
def chain_factory(tmp_path):
    return make_factory(tmp_path)


def test_ideation_preset_has_no_cursor_steps(chain_factory, tmp_path):
    applied = apply_preset_to_workflow(chain_factory, IDEATION_SLUG)
    session = chain_factory()
    try:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == applied["workflow_id"])
            .all()
        )
        assert len(steps) == 4
        assert all(step.action_type == "agent_instruction" for step in steps)
        assert not any(step.action_type == "send_to_project_cli" for step in steps)
    finally:
        session.close()


def test_development_preset_is_cursor_only_cli(chain_factory, tmp_path):
    applied = apply_preset_to_workflow(chain_factory, DEVELOPMENT_SLUG)
    session = chain_factory()
    try:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == applied["workflow_id"])
            .order_by(AutoWorkflowStep.position.asc())
            .all()
        )
        assert len(steps) == 4
        cli_steps = [s for s in steps if s.action_type == "send_to_project_cli"]
        assert len(cli_steps) == 3
        assert steps[-1].action_type == "agent_instruction"
    finally:
        session.close()


def test_polish_preset_covers_security_and_ui(chain_factory, tmp_path):
    applied = apply_preset_to_workflow(chain_factory, POLISH_SLUG)
    session = chain_factory()
    try:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == applied["workflow_id"])
            .all()
        )
        action_types = {step.action_type for step in steps}
        assert "run_command" in action_types
        assert "playwright" in action_types
    finally:
        session.close()


def test_ideation_reads_requirements_and_builds_board(chain_factory, tmp_path):
    dev_applied = apply_preset_to_workflow(chain_factory, DEVELOPMENT_SLUG)
    ideation_applied = apply_preset_to_workflow(chain_factory, IDEATION_SLUG)
    result = start_ideation_run(
        chain_factory,
        tmp_path,
        dev_workflow_id=dev_applied["workflow_id"],
        ideation_workflow_id=ideation_applied["workflow_id"],
    )
    board = result["board"]
    session = chain_factory()
    try:
        row = session.query(KanbanBoard).filter(KanbanBoard.id == board["board_id"]).one()
        tickets = (
            session.query(KanbanTicket)
            .filter(KanbanTicket.id.in_(board["ticket_ids"]))
            .order_by(KanbanTicket.position.asc())
            .all()
        )
        assert row.name.startswith("Spotify E2E")
        assert len(tickets) == 3
        expected_titles = [spec.title for spec in build_spotify_ticket_specs()[:3]]
        assert [ticket.title for ticket in tickets] == expected_titles
        assert all(ticket.linked_workflow_id for ticket in tickets)
    finally:
        session.close()


def test_principle_workflow_chain_ideation_development_polish(chain_factory, tmp_path):
    summary = run_spotify_workflow_chain(chain_factory, tmp_path)

    assert summary["ideation"]["terminal"]["run"].status == "completed"
    assert summary["development"][0]["terminal"]["run"].status == "completed"
    assert len(summary["development_run_ids"]) >= 3
    assert summary["polish"]["terminal"]["run"].status == "completed"
    assert_board_cleaned_up(chain_factory, summary["board_id"])
