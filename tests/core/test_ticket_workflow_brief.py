import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import (
    KanbanBoard,
    KanbanLane,
    KanbanTicket,
    KanbanTicketFile,
    KanbanTicketLink,
    KanbanTicketTodo,
)
from distr.core.kanban.ticket_workflow_brief import (
    build_ticket_workflow_brief,
    render_ticket_workflow_brief,
)


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_ticket_workflow_brief_extracts_actionable_context():
    s = _memory_session()
    try:
        board = KanbanBoard(name="Decisions")
        s.add(board)
        s.flush()
        lane = KanbanLane(board_id=board.id, name="Current", position=0)
        s.add(lane)
        s.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Fix workflow validation",
            description=(
                "Workflow validation gets stuck after computer-use steps.\n\n"
                "## Recommended Skills\n"
                "- `webapp-testing` - Needs UI regression coverage\n"
                "- `verification-before-completion` - Needs truthful validation"
            ),
            priority="high",
            position=0,
            external_source="jira",
            external_id="DAI-42",
            external_url="https://jira.example/browse/DAI-42",
        )
        s.add(ticket)
        s.flush()
        s.add(KanbanTicketTodo(ticket_id=ticket.id, text="Add regression test", done=False, position=0))
        s.add(KanbanTicketLink(ticket_id=ticket.id, title="Run log", url="https://logs.example/run"))
        s.add(KanbanTicketFile(ticket_id=ticket.id, filename="screen.png", file_path="/tmp/screen.png"))
        s.commit()

        brief = build_ticket_workflow_brief(
            s,
            ticket.id,
            project_id=7,
            project_name="DecisionsAI",
            project_folder="/repo",
        )
        rendered = render_ticket_workflow_brief(brief)

        assert brief["objective"] == "Fix workflow validation"
        assert brief["board_name"] == "Decisions"
        assert brief["lane_name"] == "Current"
        assert brief["project_name"] == "DecisionsAI"
        assert brief["external"]["id"] == "DAI-42"
        assert "webapp-testing" in brief["recommended_skills"]
        assert "verification-before-completion" in brief["recommended_skills"]
        assert brief["acceptance_criteria"][0] == "Add regression test"
        assert brief["attachments"][0]["path"] == "/tmp/screen.png"
        assert "[TICKET WORKFLOW BRIEF]" in rendered
        assert "Workflow validation gets stuck" in rendered
        assert "Acceptance criteria:" in rendered
    finally:
        s.close()


def test_generated_plan_brief_shows_source_revisions_and_actual_acceptance_criteria():
    with _memory_session() as session:
        board = KanbanBoard(name="Northstar")
        lane = KanbanLane(board=board, name="Backlog", position=0)
        ticket = KanbanTicket(lane=lane, source_provider="plan", title="Deliver Northstar",
                              description="Build the site and verify the journey.")
        ticket.context_notes = json.dumps({"original_digest": "internal-digest", "plan_build": {
            "version": 1, "workspace_id": 4, "expected_revisions": {"7": 2, "8": 1},
            "task": {"source_item_ids": [7, 8], "acceptance_criteria": ["A booking survives reload"]},
        }})
        session.add(ticket)
        session.flush()
        brief = build_ticket_workflow_brief(session, ticket.id)
        prompt = render_ticket_workflow_brief(brief)
        assert brief["acceptance_criteria"] == ["A booking survives reload"]
        assert "Plan #4 sources: item #7 (revision 2), item #8 (revision 1)." in prompt
        assert "internal-digest" not in prompt
