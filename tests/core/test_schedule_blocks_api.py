from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.schedule_blocks import ScheduleBlock
from distr.core.services import schedule_blocks as schedule_service
from distr.gui.web.routes.schedule_blocks import create_routes


def test_schedule_blocks_crud_timer_and_naturalize():
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    start = datetime.utcnow().replace(microsecond=0, minute=0, second=0) - timedelta(days=1)
    end = start + timedelta(hours=1)

    create_resp = client.post(
        "/api/schedule-blocks",
        json={
            "title": "Board work",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "board_provider": "local",
        },
    )
    assert create_resp.status_code == 200
    block = create_resp.json()["block"]
    block_id = block["id"]

    list_resp = client.get(
        "/api/schedule-blocks",
        params={
            "start": (start - timedelta(days=1)).isoformat(),
            "end": (end + timedelta(days=1)).isoformat(),
        },
    )
    assert list_resp.status_code == 200
    assert any(row["id"] == block_id for row in list_resp.json()["blocks"])

    timer_resp = client.post(
        "/api/schedule-blocks/timer/start",
        json={"title": "Live timer"},
    )
    assert timer_resp.status_code == 200
    running_id = timer_resp.json()["block"]["id"]

    stop_resp = client.post("/api/schedule-blocks/timer/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["block"]["id"] == running_id

    with get_session() as session:
        row = session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).first()
        row.start_at = row.start_at.replace(minute=0, second=0, microsecond=0)
        row.end_at = row.start_at + timedelta(minutes=60)
        session.commit()

    naturalize_resp = client.post(f"/api/schedule-blocks/{block_id}/naturalize-time")
    assert naturalize_resp.status_code == 200

    delete_resp = client.delete(f"/api/schedule-blocks/{block_id}")
    assert delete_resp.status_code == 200

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == running_id).delete()
        session.commit()


def test_timer_start_uses_local_wall_clock(monkeypatch):
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    fixed_now = datetime(2026, 6, 11, 14, 30, 0)
    monkeypatch.setattr(schedule_service, "_wall_now", lambda: fixed_now)

    timer_resp = client.post(
        "/api/schedule-blocks/timer/start",
        json={"title": "Live timer"},
    )
    assert timer_resp.status_code == 200
    block = timer_resp.json()["block"]
    running_id = block["id"]
    assert block["start_at"] == "2026-06-11T14:30:00"
    assert block["end_at"] == "2026-06-11T14:45:00"
    assert "Z" not in block["start_at"]

    stop_resp = client.post("/api/schedule-blocks/timer/stop")
    assert stop_resp.status_code == 200
    stopped = stop_resp.json()["block"]
    assert stopped["end_at"] == "2026-06-11T14:31:00"

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == running_id).delete()
        session.commit()


def test_naturalize_nudges_block_by_minutes_not_hours():
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    start = datetime(2026, 6, 11, 9, 0, 0)
    end = start + timedelta(hours=1)

    create_resp = client.post(
        "/api/schedule-blocks",
        json={
            "title": "Round block",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
    )
    assert create_resp.status_code == 200
    block_id = create_resp.json()["block"]["id"]

    naturalize_resp = client.post(f"/api/schedule-blocks/{block_id}/naturalize-time")
    assert naturalize_resp.status_code == 200
    payload = naturalize_resp.json()
    assert payload["success"] is True
    assert "Z" not in payload["block"]["start_at"]
    assert "Z" not in payload["block"]["end_at"]

    new_start = datetime.fromisoformat(payload["block"]["start_at"])
    new_end = datetime.fromisoformat(payload["block"]["end_at"])
    assert abs((new_start - start).total_seconds()) <= 10 * 60
    assert abs((new_end - end).total_seconds()) <= 10 * 60
    assert new_end > new_start

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).delete()
        session.commit()


def test_naturalize_already_naturalized_returns_success():
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    start = datetime(2026, 6, 11, 9, 7, 0)
    end = start + timedelta(minutes=51)

    create_resp = client.post(
        "/api/schedule-blocks",
        json={
            "title": "Already natural",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
    )
    assert create_resp.status_code == 200
    block_id = create_resp.json()["block"]["id"]

    naturalize_resp = client.post(f"/api/schedule-blocks/{block_id}/naturalize-time")
    assert naturalize_resp.status_code == 200
    payload = naturalize_resp.json()
    assert payload["success"] is True
    assert payload["block"]["start_at"] == start.isoformat()
    assert payload["block"]["end_at"] == end.isoformat()

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).delete()
        session.commit()


def test_serialize_block_returns_naive_iso_without_utc_suffix():
    start = datetime(2026, 7, 4, 9, 30, 0)
    end = start + timedelta(hours=1)
    with get_session() as session:
        block = schedule_service.create_block(
            session,
            title="Morning block",
            start_at=start.isoformat(),
            end_at=end.isoformat(),
        )
        serialized = schedule_service.serialize_block(session, block)
        block_id = block.id

    assert serialized["start_at"] == "2026-07-04T09:30:00"
    assert serialized["end_at"] == "2026-07-04T10:30:00"
    assert not serialized["start_at"].endswith("Z")
    assert not serialized["end_at"].endswith("Z")

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).delete()
        session.commit()


def test_list_schedule_blocks_serializes_local_kanban_ticket():
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    start = datetime.utcnow().replace(microsecond=0, minute=0, second=0) - timedelta(days=1)
    end = start + timedelta(hours=2)

    with get_session() as session:
        board = KanbanBoard(name="Schedule board")
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Todo", position=0)
        session.add(lane)
        session.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Fix calendar", position=0)
        session.add(ticket)
        session.flush()
        board_id = board.id
        lane_id = lane.id
        ticket_id = ticket.id
        block = schedule_service.create_block(
            session,
            title="Linked block",
            start_at=start.isoformat(),
            end_at=end.isoformat(),
            ticket_id=ticket_id,
            board_id=board_id,
        )
        block_id = block.id

    list_resp = client.get(
        "/api/schedule-blocks",
        params={
            "start": (start - timedelta(days=1)).isoformat(),
            "end": (end + timedelta(days=1)).isoformat(),
        },
    )
    assert list_resp.status_code == 200
    rows = list_resp.json()["blocks"]
    matched = [row for row in rows if row["id"] == block_id]
    assert matched
    assert matched[0]["ticket_reference"] == "Fix calendar"
    assert matched[0]["ticket_title"] == "Fix calendar"

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).delete()
        session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).delete()
        session.query(KanbanLane).filter(KanbanLane.id == lane_id).delete()
        session.query(KanbanBoard).filter(KanbanBoard.id == board_id).delete()
        session.commit()


def test_serialize_block_includes_board_color_from_ticket_board():
    start = datetime(2026, 7, 4, 9, 30, 0)
    end = start + timedelta(hours=1)
    with get_session() as session:
        board = KanbanBoard(name="Ticket board", color="#22c55e")
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Todo", position=0)
        session.add(lane)
        session.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Color via ticket", position=0)
        session.add(ticket)
        session.flush()
        block = schedule_service.create_block(
            session,
            title="Ticket-only block",
            start_at=start.isoformat(),
            end_at=end.isoformat(),
            ticket_id=ticket.id,
        )
        serialized = schedule_service.serialize_block(session, block)
        block_id = block.id
        ticket_id = ticket.id
        lane_id = lane.id
        board_id = board.id

    assert serialized["board_color"] == "#22c55e"
    assert serialized["board_name"] == "Ticket board"

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).delete()
        session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).delete()
        session.query(KanbanLane).filter(KanbanLane.id == lane_id).delete()
        session.query(KanbanBoard).filter(KanbanBoard.id == board_id).delete()
        session.commit()


def test_overlapping_schedule_blocks_are_allowed():
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    start = datetime(2026, 7, 4, 10, 0, 0)
    end = start + timedelta(hours=1)

    first = client.post(
        "/api/schedule-blocks",
        json={
            "title": "Workflow A",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
    )
    assert first.status_code == 200
    first_id = first.json()["block"]["id"]

    second = client.post(
        "/api/schedule-blocks",
        json={
            "title": "Workflow B",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
    )
    assert second.status_code == 200
    second_id = second.json()["block"]["id"]
    assert second_id != first_id

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id.in_([first_id, second_id])).delete()
        session.commit()


def test_serialize_block_uses_orange_default_when_board_has_no_color():
    start = datetime(2026, 7, 4, 9, 30, 0)
    end = start + timedelta(hours=1)
    with get_session() as session:
        board = KanbanBoard(name="Default color board")
        session.add(board)
        session.flush()
        block = schedule_service.create_block(
            session,
            title="Default color block",
            start_at=start.isoformat(),
            end_at=end.isoformat(),
            board_id=board.id,
            board_provider="local",
        )
        serialized = schedule_service.serialize_block(session, block)
        block_id = block.id
        board_id = board.id

    assert serialized["board_color"] == "#f97316"

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).delete()
        session.query(KanbanBoard).filter(KanbanBoard.id == board_id).delete()
        session.commit()


def test_serialize_block_includes_board_color_from_linked_board():
    start = datetime(2026, 7, 4, 9, 30, 0)
    end = start + timedelta(hours=1)
    with get_session() as session:
        board = KanbanBoard(name="Colored board", color="#3b82f6")
        session.add(board)
        session.flush()
        block = schedule_service.create_block(
            session,
            title="Colored block",
            start_at=start.isoformat(),
            end_at=end.isoformat(),
            board_id=board.id,
            board_provider="local",
        )
        serialized = schedule_service.serialize_block(session, block)
        block_id = block.id
        board_id = board.id

    assert serialized["board_color"] == "#3b82f6"
    assert serialized["board_name"] == "Colored board"

    with get_session() as session:
        session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).delete()
        session.query(KanbanBoard).filter(KanbanBoard.id == board_id).delete()
        session.commit()
