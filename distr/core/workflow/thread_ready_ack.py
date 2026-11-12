"""Server-side acknowledgement that a completed Development thread was opened.

ready_acknowledged_at is an additive chats column. A thread stays "unaddressed"
while its latest run is completed, failed, or blocked and the acknowledgement
is missing or older than that run's completed_at. Opening the thread after it
became ready sets the stamp. A later run with a newer completed_at resets it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

READY_REVIEW_STATUSES = frozenset({"completed", "failed", "blocked"})
ACTIVE_STATUSES = frozenset({
    "initializing", "queued", "running", "waiting", "paused", "pending", "dispatching",
})


def _naive_utc(value: datetime | None) -> datetime | None:
    """Compare every stamp as naive UTC (utc_now_naive's convention).

    The first-startup seed and some writers stored zone-aware values
    ("...Z"); run completed_at is naive UTC. Mixing them raised
    "can't compare offset-naive and offset-aware datetimes", so opening a
    thread never cleared its folder's ready (orange) state.
    """
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _naive_utc(value)
    text_value = str(value or "").strip()
    if not text_value:
        return None
    try:
        return _naive_utc(datetime.fromisoformat(text_value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def latest_run_snapshot(db, chat) -> dict[str, Any]:
    """Return the latest known run status for a Development chat, if any."""
    from distr.core.workflow.development_threads import development_thread_metadata

    metadata = development_thread_metadata(chat)
    execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
    status = str(execution.get("status") or "").strip().lower()
    completed_at = _parse_time(execution.get("completed_at"))
    try:
        from distr.core.db.workflow import AutoWorkflowRun

        run = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.chat_id == int(chat.id))
            .order_by(AutoWorkflowRun.id.desc())
            .first()
        )
        if run is not None:
            run_status = str(run.status or "").strip().lower()
            run_completed = _parse_time(getattr(run, "completed_at", None))
            # Prefer a newer stored run over a stale params snapshot.
            if not status or run_status in ACTIVE_STATUSES or (
                run_completed and (not completed_at or run_completed >= completed_at)
            ):
                status, completed_at = run_status, run_completed or completed_at
    except Exception:
        pass
    return {"status": status, "completed_at": completed_at, "completed_at_iso": _iso(completed_at)}


def is_ready_unaddressed(status: str, completed_at: datetime | None, acknowledged_at: datetime | None) -> bool:
    if str(status or "").strip().lower() in ACTIVE_STATUSES:
        return False
    if str(status or "").strip().lower() not in READY_REVIEW_STATUSES:
        return False
    completed_at, acknowledged_at = _parse_time(completed_at), _parse_time(acknowledged_at)
    if acknowledged_at is None:
        return True
    if completed_at is None:
        return False
    return acknowledged_at < completed_at


def ready_state(db, chat) -> dict[str, Any]:
    snap = latest_run_snapshot(db, chat)
    acknowledged_at = getattr(chat, "ready_acknowledged_at", None)
    unaddressed = is_ready_unaddressed(snap["status"], snap["completed_at"], acknowledged_at)
    return {
        "status": snap["status"],
        "completed_at": snap["completed_at_iso"],
        "ready_acknowledged_at": _iso(acknowledged_at),
        "unaddressed": unaddressed,
    }


def acknowledge_ready_thread(chat_id: int, *, now: datetime | None = None, _session_provider=None) -> dict[str, Any]:
    """Mark a settled Development thread as opened after it became ready."""
    from distr.core.db import Chat, get_session
    from distr.core.workflow.development_threads import is_development_thread

    from distr.core.db.time import utc_now_naive

    session_provider = _session_provider or get_session
    stamp = now or utc_now_naive()
    with session_provider() as db:
        chat = db.get(Chat, int(chat_id))
        if chat is None or chat.parent_id is not None or not is_development_thread(db, chat):
            raise LookupError("Development thread not found.")
        snap = latest_run_snapshot(db, chat)
        if snap["status"] in ACTIVE_STATUSES or snap["status"] not in READY_REVIEW_STATUSES:
            return {
                "id": int(chat.id),
                "acknowledged": False,
                "ready_acknowledged_at": _iso(getattr(chat, "ready_acknowledged_at", None)),
                **{k: snap[k] for k in ("status", "completed_at_iso")},
                "completed_at": snap["completed_at_iso"],
            }
        current = _parse_time(getattr(chat, "ready_acknowledged_at", None))
        completed_at = _parse_time(snap["completed_at"])
        if current is None or (completed_at and current < completed_at):
            chat.ready_acknowledged_at = stamp
            db.commit()
            db.refresh(chat)
        return {
            "id": int(chat.id),
            "acknowledged": True,
            "ready_acknowledged_at": _iso(chat.ready_acknowledged_at),
            "status": snap["status"],
            "completed_at": snap["completed_at_iso"],
        }


def ensure_chat_ready_acknowledged_at(engine) -> None:
    """Add chats.ready_acknowledged_at and seed existing rows as acknowledged."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        with engine.connect() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info(chats)"))}
            if "chats" not in {name for (name,) in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}:
                return
            added = "ready_acknowledged_at" not in existing
            if added:
                conn.execute(text("ALTER TABLE chats ADD COLUMN ready_acknowledged_at DATETIME DEFAULT NULL"))
                # Historical threads must not light the folder orange after upgrade.
                conn.execute(text(
                    "UPDATE chats SET ready_acknowledged_at = CURRENT_TIMESTAMP "
                    "WHERE ready_acknowledged_at IS NULL"
                ))
                logger.info("Added and seeded chats.ready_acknowledged_at")
            conn.commit()
    except Exception as exc:
        logger.warning("Could not ensure chats.ready_acknowledged_at: %s", exc)
