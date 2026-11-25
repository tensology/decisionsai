import contextlib
from queue import Queue

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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

    monkeypatch.setattr(KanbanTicketTool, "_get_session", lambda self: get_session())
    return get_session


def test_move_ticket_to_another_board_defaults_to_target_source_lane_and_rebases_defaults(monkeypatch):
    from distr.app.events import EventHandlerMixin
    from distr.gui.web.routes.kanban import create_routes

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
            "source": wrong.id,
            "target": right.id,
        }

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    queue = Queue()
    with TestClient(app) as client, client.websocket_connect("/api/tickets/ws/boards") as socket:
        result = KanbanTicketTool(event_queue=queue)._run(
            action="move_ticket",
            ticket_id=ids["ticket"],
            target_board_name="Right Context",
        )
        event, data = queue.get_nowait()
        assert event == "kanban_ticket_moved"
        with get_session() as session:
            assert session.get(KanbanTicket, ids["ticket"]).lane_id == ids["right_current"]
        EventHandlerMixin()._dispatch_agent_event(event, data)
        updates = [socket.receive_json(), socket.receive_json()]
        assert {update["board_id"] for update in updates} == {ids["source"], ids["target"]}
        for update in updates:
            assert update["type"] == "kanban_updated"
            assert update["event"] == "ticket_lane_move"
            assert update["payload"]["ticket_id"] == ids["ticket"]

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


@pytest.mark.parametrize("lane_name", ["Complete", "Done", "Completed", "Closed", "Shipped"])
def test_agent_cannot_move_a_ticket_to_complete(lane_name):
    result = KanbanTicketTool()._action_move_ticket(42, lane_name)

    assert "ready for your acceptance in QA" in result
    assert "Only you can move a QA ticket to Complete" in result


@pytest.mark.parametrize("action", ["move_ticket", "update_ticket"])
def test_fuzzy_destination_cannot_bypass_human_acceptance(monkeypatch, action):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        board = KanbanBoard(name="Delivery")
        session.add(board)
        session.flush()
        qa = KanbanLane(board_id=board.id, name="QA", position=0)
        session.add_all([qa, KanbanLane(board_id=board.id, name="Complete", position=1)])
        session.flush()
        ticket = KanbanTicket(lane_id=qa.id, title="Verified", position=0)
        session.add(ticket)
        session.flush()
        ticket_id, qa_id = ticket.id, qa.id

    queue = Queue()
    result = KanbanTicketTool(event_queue=queue)._run(
        action=action, ticket_id=ticket_id, lane_name="Complet",
    )
    assert "Only you can move a QA ticket to Complete" in result
    assert queue.empty()
    with get_session() as session:
        assert session.get(KanbanTicket, ticket_id).lane_id == qa_id
