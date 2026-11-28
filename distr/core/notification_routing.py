"""Presence-aware routing for proactive agent notifications.

This keeps "tell the user" from meaning "always Telegram".  Callers record where
the user is interacting, then proactive services choose the freshest surface.
"""

from __future__ import annotations

import logging
import time
import json
import math
import os
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)

# How long an interaction counts as recent.
DEFAULT_PRESENCE_WINDOW_S = 600.0

SURFACES = {
    "desktop",
    "telegram",
    "remote",
    # A voice / agent request from the remote app (not just the page being open).
    "remote_request",
    "cursor",
    "codex",
    "browser",
    "whatsapp",
    "gmail",
    "jira",
    "trello",
}
LOCAL_ACTIVITY_SURFACES = {"desktop", "cursor", "codex", "browser"}
EXTERNAL_ACTIVITY_SURFACES = {"whatsapp", "gmail", "jira", "trello"}
_activity: dict[str, float] = {}


@dataclass(frozen=True)
class NotificationRoute:
    surface: str
    reason: str


def _ensure_activity_table() -> None:
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_channel_activity (
                    surface VARCHAR PRIMARY KEY,
                    last_active_at FLOAT NOT NULL,
                    metadata TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
    except Exception:
        return


def _write_activity(surface: str, at: float, metadata: dict[str, Any] | None = None) -> None:
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        _ensure_activity_table()
        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO user_channel_activity(surface, last_active_at, metadata, updated_at)
                    VALUES (:surface, :last_active_at, :metadata, CURRENT_TIMESTAMP)
                    ON CONFLICT(surface) DO UPDATE SET
                        last_active_at = excluded.last_active_at,
                        metadata = excluded.metadata,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {
                    "surface": surface,
                    "last_active_at": float(at),
                    "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
                },
            )
            conn.commit()
    except Exception:
        return


def _read_activity() -> dict[str, float]:
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        _ensure_activity_table()
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT surface, last_active_at FROM user_channel_activity")).fetchall()
        return {
            str(row[0]).strip().lower(): float(row[1])
            for row in rows
            if str(row[0]).strip().lower() in SURFACES and row[1] is not None
        }
    except Exception:
        return {}


def _all_activity() -> dict[str, float]:
    # Several processes (GUI, agent, web) record activity: the newest stamp per surface wins.
    merged = _read_activity()
    for surface, ts in _activity.items():
        if ts > merged.get(surface, 0.0):
            merged[surface] = ts
    return merged


def record_surface_activity(surface: str, *, at: float | None = None, metadata: dict[str, Any] | None = None) -> None:
    surface = (surface or "").strip().lower()
    if surface not in SURFACES:
        return
    ts = float(at if at is not None else time.time())
    _activity[surface] = ts
    _write_activity(surface, ts, metadata=metadata)


def last_surface_activity(surface: str) -> float | None:
    surface = (surface or "").strip().lower()
    if surface in _activity:
        return _activity.get(surface)
    return _read_activity().get(surface)


def clear_notification_activity_cache() -> None:
    _activity.clear()


def _connected_telegram(manager: Any) -> bool:
    if manager is None:
        return False
    uid = getattr(manager, "telegram_user_id", None)
    try:
        has_user = bool(uid and int(uid) > 0)
    except Exception:
        has_user = False
    try:
        return has_user and bool(manager.is_connected())
    except Exception:
        return has_user


def _recent_remote_context(manager: Any, now: float, window_s: float) -> bool:
    """True when the agent still owes the remote app a reply (a pending remote request)."""
    ctx = getattr(manager, "_pending_remote_agent_response", None) if manager is not None else None
    if isinstance(ctx, dict):
        created = float(ctx.get("created_at") or 0)
        if created and now - created <= window_s:
            return True
    return False


def presence_window_s() -> float:
    """Read the deployment's presence window in seconds, defaulting to 10 minutes."""
    try:
        value = float(os.environ.get("DECISIONS_NOTIFICATION_PRESENCE_WINDOW_S", DEFAULT_PRESENCE_WINDOW_S))
    except ValueError:
        return DEFAULT_PRESENCE_WINDOW_S
    return value if math.isfinite(value) and value > 0 else DEFAULT_PRESENCE_WINDOW_S


def choose_notification_route(
    *,
    telegram_manager: Any = None,
    allow_telegram: bool = False,
    now: float | None = None,
    active_window_s: float | None = None,
    strict_presence: bool = False,
) -> NotificationRoute | None:
    """Pick the user's current surface and log the reason.

    Approval asks require both remote presence and recent remote interaction.
    Other callers can still use an open remote app or idle Telegram as a fallback.
    """
    now = float(now if now is not None else time.time())
    window = float(active_window_s) if active_window_s else presence_window_s()
    activity = _all_activity()

    def age(surface: str) -> float | None:
        ts = activity.get(surface)
        return None if ts is None else now - ts

    def recent(surface: str) -> bool:
        a = age(surface)
        return a is not None and a <= window

    ages = {k: int(v) for k in sorted(SURFACES) if (v := age(k)) is not None and v <= window * 3}

    def chosen(surface: str, reason: str) -> NotificationRoute:
        logger.info("presence routing: surface=%s reason=%s window_s=%d ages_s=%s", surface, reason, int(window), ages)
        return NotificationRoute(surface, reason)

    remote_open = recent("remote")             # the remote app is open (page presence)
    remote_command = recent("remote_request")  # he made a remote voice / agent request
    awaiting_reply = _recent_remote_context(telegram_manager, now, window)
    if strict_presence:
        if remote_open and (remote_command or awaiting_reply):
            return chosen("remote", "the remote app is open and recently used")
        if recent("telegram") and allow_telegram and _connected_telegram(telegram_manager):
            return chosen("telegram", "the user recently messaged on Telegram")
        return chosen("desktop", "no active remote app or recent Telegram message")

    if awaiting_reply:
        return chosen("remote", "remote control is awaiting an agent response")
    if remote_command:
        return chosen("remote", "he made a remote request recently")

    if recent("telegram") and allow_telegram and _connected_telegram(telegram_manager):
        return chosen("telegram", "telegram was the most recent active surface")

    local = [(activity[s], s) for s in LOCAL_ACTIVITY_SURFACES if recent(s)]
    if local:
        local.sort(reverse=True)
        return chosen("desktop", f"{local[0][1]} was the most recent active local surface")

    if remote_open:
        return chosen("remote", "the remote app is open and nothing else is active")
    if allow_telegram and _connected_telegram(telegram_manager):
        return chosen("telegram", "no remote or local surface is active, using Telegram fallback")
    logger.info("presence routing: no active surface (ages_s=%s)", ages)
    return None


def reset_notification_activity() -> None:
    _activity.clear()
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        _ensure_activity_table()
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM user_channel_activity"))
            conn.commit()
    except Exception:
        return
