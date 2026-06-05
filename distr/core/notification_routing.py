"""Presence-aware routing for proactive agent notifications.

This keeps "tell the user" from meaning "always Telegram".  Callers record where
the user is interacting, then proactive services choose the freshest surface.
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass
from typing import Any


SURFACES = {
    "desktop",
    "telegram",
    "remote",
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
    merged = _read_activity()
    merged.update(_activity)
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
    ctx = getattr(manager, "_pending_remote_agent_response", None) if manager is not None else None
    if not isinstance(ctx, dict):
        return False
    created = float(ctx.get("created_at") or 0)
    return bool(created and now - created <= window_s)


def choose_notification_route(
    *,
    telegram_manager: Any = None,
    allow_telegram: bool = False,
    now: float | None = None,
    active_window_s: float = 300.0,
) -> NotificationRoute | None:
    now = float(now if now is not None else time.time())

    candidates: list[tuple[float, str, str]] = []
    activity = _all_activity()
    for surface in SURFACES:
        ts = activity.get(surface)
        if ts is not None and now - ts <= active_window_s:
            if surface in LOCAL_ACTIVITY_SURFACES:
                candidates.append((ts, "desktop", surface))
            elif surface == "telegram":
                candidates.append((ts, "telegram", surface))
            elif surface == "remote":
                candidates.append((ts, "remote", surface))

    if _recent_remote_context(telegram_manager, now, active_window_s):
        return NotificationRoute("remote", "remote control is awaiting an agent response")

    if candidates:
        candidates.sort(reverse=True)
        for _ts, delivery_surface, activity_surface in candidates:
            if delivery_surface == "telegram" and not (allow_telegram and _connected_telegram(telegram_manager)):
                continue
            if delivery_surface == "desktop" and activity_surface != "desktop":
                return NotificationRoute("desktop", f"{activity_surface} was the most recent active local surface")
            return NotificationRoute(delivery_surface, f"{activity_surface} was the most recent active surface")

    if allow_telegram and _connected_telegram(telegram_manager):
        return NotificationRoute("telegram", "no local surface is active, using Telegram fallback")

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
