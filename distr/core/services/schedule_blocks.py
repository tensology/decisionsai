"""Schedule block business logic: serialize, naturalize, timer."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.schedule_blocks import ScheduleBlock


def _wall_now() -> datetime:
    """Current local wall-clock time stored as a naive datetime."""
    return datetime.now().replace(microsecond=0)


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Serialize naive wall-clock datetimes without a UTC suffix."""
    if not value:
        return None
    return value.replace(microsecond=0).isoformat()


def _parse_dt(value: str) -> datetime:
    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    parsed = datetime.fromisoformat(raw)
    return parsed.replace(microsecond=0)


def _board_row(session: Session, block: ScheduleBlock) -> Optional[KanbanBoard]:
    if block.board_id:
        board = session.query(KanbanBoard).filter(KanbanBoard.id == block.board_id).first()
        if board:
            return board
    provider = (block.board_provider or "").strip().lower()
    external_board_id = (block.external_board_id or "").strip()
    if provider and provider != "local" and external_board_id:
        return (
            session.query(KanbanBoard)
            .filter(KanbanBoard.source == provider, KanbanBoard.external_board_id == external_board_id)
            .first()
        )
    if block.ticket_id:
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == block.ticket_id).first()
        if ticket and ticket.lane_id:
            lane = session.query(KanbanLane).filter(KanbanLane.id == ticket.lane_id).first()
            if lane and lane.board_id:
                return session.query(KanbanBoard).filter(KanbanBoard.id == lane.board_id).first()
    return None


def _board_label(session: Session, block: ScheduleBlock) -> str:
    if block.board_provider and block.board_provider != "local":
        return block.external_board_id or block.board_provider
    board = _board_row(session, block)
    return (board.name or "").strip() if board else ""


def _default_board_color(board: KanbanBoard) -> str:
    color = str(board.color or "").strip()
    if color:
        return color
    source = (board.source or "database").strip().lower()
    if source == "trello":
        return "#0079bf"
    if source == "jira":
        return "#0052cc"
    return "#f97316"


def _board_color(session: Session, block: ScheduleBlock) -> str:
    board = _board_row(session, block)
    if not board:
        return ""
    return _default_board_color(board)


def _ticket_label(session: Session, block: ScheduleBlock) -> tuple[str, str]:
    if block.ticket_id:
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == block.ticket_id).first()
        if ticket:
            ref = (
                getattr(ticket, "ticket_key", None)
                or ticket.external_id
                or ticket.title
                or f"#{ticket.id}"
            )
            return str(ref).strip(), (ticket.title or "").strip()
    if block.external_ticket_key:
        return block.external_ticket_key, ""
    return "", ""


def serialize_block(session: Session, block: ScheduleBlock, *, extend_running_end: bool = False) -> dict[str, Any]:
    now = _wall_now()
    end_at = block.end_at
    if extend_running_end and block.is_timer_running and block.start_at:
        end_at = max(end_at, now + timedelta(minutes=1))
    ticket_ref, ticket_title = _ticket_label(session, block)
    return {
        "id": block.id,
        "title": block.title or "",
        "start_at": _iso(block.start_at),
        "end_at": _iso(end_at),
        "board_id": block.board_id,
        "board_provider": block.board_provider or "local",
        "external_board_id": block.external_board_id,
        "board_name": _board_label(session, block),
        "board_color": _board_color(session, block),
        "ticket_id": block.ticket_id,
        "external_ticket_key": block.external_ticket_key,
        "ticket_reference": ticket_ref,
        "ticket_title": ticket_title,
        "project_id": block.project_id,
        "is_timer_running": bool(block.is_timer_running),
        "is_timer_entry": bool(block.is_timer_entry),
        "created_at": _iso(block.created_date),
        "updated_at": _iso(block.modified_date),
    }


def blocks_for_range(session: Session, start: datetime, end: datetime) -> list[ScheduleBlock]:
    return (
        session.query(ScheduleBlock)
        .filter(ScheduleBlock.start_at < end, ScheduleBlock.end_at > start)
        .order_by(ScheduleBlock.start_at.asc(), ScheduleBlock.id.asc())
        .all()
    )


def running_timer(session: Session) -> Optional[ScheduleBlock]:
    return (
        session.query(ScheduleBlock)
        .filter(ScheduleBlock.is_timer_running.is_(True))
        .order_by(ScheduleBlock.start_at.desc())
        .first()
    )


def _is_round_boundary(dt: datetime) -> bool:
    return dt.second == 0 and dt.microsecond == 0 and dt.minute % 15 == 0


def _is_round_duration(duration: timedelta) -> bool:
    minutes = int(round(duration.total_seconds() / 60))
    return minutes > 0 and minutes % 15 == 0


def _auto_offset_minutes(event_id: int, is_end: bool) -> int:
    if is_end:
        return ((event_id * 11) % 18) - 7
    return (event_id % 9) + 2


def _duration_break_minutes(event_id: int) -> int:
    return ((event_id * 5) % 13) - 6


def naturalize_block_times(block: ScheduleBlock) -> tuple[Optional[datetime], Optional[datetime], str]:
    duration = block.end_at - block.start_at
    if duration <= timedelta(0):
        return None, None, "Block duration must be positive."

    start_round = _is_round_boundary(block.start_at)
    end_round = _is_round_boundary(block.end_at)
    duration_round = _is_round_duration(duration)
    if not start_round and not end_round and not duration_round:
        return None, None, "Time block is already naturalized."

    new_start = block.start_at
    new_end = block.end_at
    if start_round:
        new_start = block.start_at + timedelta(minutes=_auto_offset_minutes(block.id, is_end=False))
    elif end_round or duration_round:
        new_start = block.start_at + timedelta(minutes=(block.id % 5) + 1)
    if end_round:
        new_end = block.end_at + timedelta(minutes=_auto_offset_minutes(block.id, is_end=True))
    if _is_round_duration(new_end - new_start):
        new_end = new_end + timedelta(minutes=_duration_break_minutes(block.id))

    if new_start == block.start_at and new_end == block.end_at:
        return None, None, "Time block is already naturalized."

    if new_end <= new_start:
        new_end = new_start + timedelta(minutes=1)
    return new_start, new_end, "Time block naturalized."


def apply_naturalize(session: Session, block: ScheduleBlock) -> tuple[bool, str, Optional[ScheduleBlock]]:
    original_start = block.start_at
    original_end = block.end_at
    new_start, new_end, message = naturalize_block_times(block)
    if new_start is None or new_end is None:
        if "already naturalized" in message.lower():
            return True, message, block
        return False, message, block

    if new_end <= new_start:
        new_end = new_start + timedelta(minutes=1)
    if new_start == original_start and new_end == original_end:
        return True, "Time block is already naturalized.", block

    block.start_at = new_start
    block.end_at = new_end
    block.modified_date = _wall_now()
    session.commit()
    session.refresh(block)
    return True, message, block


def create_block(
    session: Session,
    *,
    title: str,
    start_at: str,
    end_at: str,
    board_id: Optional[int] = None,
    board_provider: str = "local",
    external_board_id: Optional[str] = None,
    ticket_id: Optional[int] = None,
    external_ticket_key: Optional[str] = None,
    project_id: Optional[int] = None,
    is_timer_entry: bool = False,
    is_timer_running: bool = False,
) -> ScheduleBlock:
    start_dt = _parse_dt(start_at)
    end_dt = _parse_dt(end_at)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=15)
    if running_timer(session) and is_timer_running:
        raise ValueError("Another timer is already running.")

    block = ScheduleBlock(
        title=(title or "Untitled block").strip() or "Untitled block",
        start_at=start_dt,
        end_at=end_dt,
        board_id=board_id,
        board_provider=(board_provider or "local").strip() or "local",
        external_board_id=(external_board_id or "").strip() or None,
        ticket_id=ticket_id,
        external_ticket_key=(external_ticket_key or "").strip() or None,
        project_id=project_id,
        is_timer_entry=bool(is_timer_entry),
        is_timer_running=bool(is_timer_running),
    )
    session.add(block)
    session.commit()
    session.refresh(block)
    return block


def update_block(
    session: Session,
    block: ScheduleBlock,
    payload: dict[str, Any],
) -> ScheduleBlock:
    if "title" in payload and payload["title"] is not None:
        block.title = str(payload["title"]).strip() or "Untitled block"
    start_dt = block.start_at
    end_dt = block.end_at
    if payload.get("start_at"):
        start_dt = _parse_dt(str(payload["start_at"]))
    if payload.get("end_at"):
        end_dt = _parse_dt(str(payload["end_at"]))
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=15)
    block.start_at = start_dt
    block.end_at = end_dt

    for field in (
        "board_id",
        "ticket_id",
        "project_id",
    ):
        if field in payload:
            block.__setattr__(field, payload[field])
    if "board_provider" in payload and payload["board_provider"] is not None:
        block.board_provider = str(payload["board_provider"]).strip() or "local"
    if "external_board_id" in payload:
        block.external_board_id = (payload.get("external_board_id") or "").strip() or None
    if "external_ticket_key" in payload:
        block.external_ticket_key = (payload.get("external_ticket_key") or "").strip() or None

    block.modified_date = _wall_now()
    session.commit()
    session.refresh(block)
    return block


def start_timer(
    session: Session,
    *,
    title: str,
    board_id: Optional[int] = None,
    board_provider: str = "local",
    external_board_id: Optional[str] = None,
    ticket_id: Optional[int] = None,
    external_ticket_key: Optional[str] = None,
    project_id: Optional[int] = None,
) -> ScheduleBlock:
    existing = running_timer(session)
    if existing:
        raise ValueError("Another timer is already running.")
    now = _wall_now()
    return create_block(
        session,
        title=title,
        start_at=_iso(now) or now.isoformat(),
        end_at=_iso(now + timedelta(minutes=15)) or (now + timedelta(minutes=15)).isoformat(),
        board_id=board_id,
        board_provider=board_provider,
        external_board_id=external_board_id,
        ticket_id=ticket_id,
        external_ticket_key=external_ticket_key,
        project_id=project_id,
        is_timer_entry=True,
        is_timer_running=True,
    )


def stop_timer(session: Session) -> Optional[ScheduleBlock]:
    block = running_timer(session)
    if not block:
        return None
    now = _wall_now()
    block.is_timer_running = False
    block.end_at = max(block.start_at + timedelta(minutes=1), now)
    block.modified_date = now
    session.commit()
    session.refresh(block)
    return block


def start_project_time_tracker(session: Session, project: Any) -> Optional[ScheduleBlock]:
    if running_timer(session):
        return running_timer(session)
    if not getattr(project, "start_time_tracker", True):
        return None
    board_id = getattr(project, "kanban_board_id", None)
    title = (getattr(project, "name", None) or "Project work").strip() or "Project work"
    return start_timer(
        session,
        title=title,
        board_id=board_id,
        board_provider="local",
        project_id=getattr(project, "id", None),
    )
