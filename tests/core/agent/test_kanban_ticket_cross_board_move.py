import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


def _memory_session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    return get_session


def test_move_ticket_to_another_board_defaults_to_target_source_lane_and_rebases_defaults(monkeypatch):
    get_session = _memory_session(monkeypatch)

    with get_session() as session:
        wrong = KanbanBoard(
            name="Wrong Board",
            default_project_id=10,
            default_workflow_id=20,
            default_action_id=30,
            send_to_cli=False,
        )
        right = KanbanBoard(
            name="Right Context",
            agent_source_lane="Current",
            default_project_id=101,
            default_workflow_id=202,
            default_action_id=303,
            send_to_cli=True,
        )
        session.add_all([wrong, right])
        session.flush()

        wrong_lane = KanbanLane(board_id=wrong.id, name="Backlog", position=0)
        right_inbox = KanbanLane(board_id=right.id, name="Inbox", position=0)
        right_current = KanbanLane(board_id=right.id, name="Current", position=1)
        session.add_all([wrong_lane, right_inbox, right_current])
        session.flush()

        ticket = KanbanTicket(
            lane_id=wrong_lane.id,
            title="Move me",
            description="Created on the wrong board",
            linked_project_id=wrong.default_project_id,
            linked_workflow_id=wrong.default_workflow_id,
            linked_action_id=wrong.default_action_id,
            send_to_cli=wrong.send_to_cli,
            position=0,
        )
        session.add(ticket)
        session.flush()
        ids = {
            "ticket": ticket.id,
            "right_current": right_current.id,
        }

    result = KanbanTicketTool()._run(
        action="move_ticket",
        ticket_id=ids["ticket"],
        target_board_name="Right Context",
    )

    assert "Moved ticket" in result
    assert "'Wrong Board' / 'Backlog'" in result
    assert "'Right Context' / 'Current'" in result

    with get_session() as session:
        moved = session.query(KanbanTicket).filter(KanbanTicket.id == ids["ticket"]).one()
        assert moved.lane_id == ids["right_current"]
        assert moved.linked_project_id == 101
        assert moved.linked_workflow_id == 202
        assert moved.linked_action_id == 303
        assert moved.send_to_cli is True


def test_move_ticket_to_board_named_in_text_without_lane(monkeypatch):
    get_session = _memory_session(monkeypatch)

    with get_session() as session:
        source = KanbanBoard(name="Source Board")
        destination = KanbanBoard(name="ThatShirtShow")
        session.add_all([source, destination])
        session.flush()

        source_lane = KanbanLane(board_id=source.id, name="Backlog", position=0)
        destination_lane = KanbanLane(board_id=destination.id, name="Ready", position=0)
        session.add_all([source_lane, destination_lane])
        session.flush()

        ticket = KanbanTicket(lane_id=source_lane.id, title="Wrong context", position=0)
        session.add(ticket)
        session.flush()
        ticket_id = ticket.id

    result = KanbanTicketTool()._run(
        action="move_ticket",
        ticket_id=ticket_id,
        text="Move this ticket to the ThatShirtShow board because it is in the wrong context.",
    )

    assert "'ThatShirtShow' / 'Ready'" in result


def test_agent_cannot_move_a_ticket_to_complete():
    result = KanbanTicketTool()._action_move_ticket(42, "Complete")

    assert "ready for your acceptance in QA" in result
    assert "Only you can move a QA ticket to Complete" in result
