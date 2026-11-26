"""Persistent store for ambient suggestions.

Two jobs:
1. Remember what was suggested, so the inbox survives a restart and the assessor
   can be told what it already said (no repeats).
2. Remember what the operator muted, so "don't suggest like this again" sticks.

The mute list is checked *before* the assessor runs, not after: a muted kind
should never cost a model call.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import text as sql_text

from distr.core.db import engine

logger = logging.getLogger(__name__)

MAX_ROWS = 500
KEEP_DAYS = 30.0

STATUS_NEW = "new"
STATUS_DISMISSED = "dismissed"


def ensure_tables() -> None:
    with engine.begin() as conn:
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS initiative_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind VARCHAR NOT NULL,
                text TEXT NOT NULL,
                confidence FLOAT NOT NULL DEFAULT 0,
                rubric_json TEXT,
                provider VARCHAR,
                model VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'new',
                created_at FLOAT NOT NULL,
                dismissed_at FLOAT
            )
        """))
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS initiative_suggestion_mutes (
                kind VARCHAR PRIMARY KEY,
                muted_at FLOAT NOT NULL
            )
        """))
        conn.execute(sql_text(
            "CREATE INDEX IF NOT EXISTS ix_initiative_suggestions_created "
            "ON initiative_suggestions (created_at)"
        ))


def _normalise_kind(kind: Any) -> str:
    return " ".join(str(kind or "").split()).lower()[:40] or "idea"


def add(
    *,
    kind: Any,
    text: Any,
    confidence: Any,
    rubric: dict[str, Any] | None = None,
    provider: str = "",
    model: str = "",
) -> int | None:
    """Record a suggestion. Returns its id, or None when unusable."""
    body = " ".join(str(text or "").split())
    if not body:
        return None
    try:
        score = min(max(float(confidence), 0.0), 1.0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        ensure_tables()
        with engine.begin() as conn:
            result = conn.execute(sql_text("""
                INSERT INTO initiative_suggestions
                    (kind, text, confidence, rubric_json, provider, model, status, created_at)
                VALUES (:kind, :text, :confidence, :rubric, :provider, :model, :status, :now)
            """), {
                "kind": _normalise_kind(kind),
                "text": body,
                "confidence": score,
                "rubric": json.dumps(rubric) if isinstance(rubric, dict) else None,
                "provider": str(provider or "")[:60],
                "model": str(model or "")[:80],
                "status": STATUS_NEW,
                "now": time.time(),
            })
            return int(result.lastrowid or 0) or None
    except Exception:
        logger.debug("suggestion_store: could not record suggestion", exc_info=True)
        return None


def recent(*, limit: int = 20, include_dismissed: bool = False) -> list[dict[str, Any]]:
    """Newest first. Dismissed rows are hidden unless asked for."""
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 20
    clause = "" if include_dismissed else "WHERE status = :status"
    params: dict[str, Any] = {"limit": limit}
    if not include_dismissed:
        params["status"] = STATUS_NEW
    try:
        ensure_tables()
        with engine.begin() as conn:
            rows = conn.execute(sql_text(
                f"SELECT id, kind, text, confidence, rubric_json, provider, model, status, created_at "
                f"FROM initiative_suggestions {clause} ORDER BY created_at DESC LIMIT :limit"
            ), params).mappings().all()
    except Exception:
        logger.debug("suggestion_store: could not read suggestions", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw_rubric = item.pop("rubric_json", None)
        try:
            item["rubric"] = json.loads(raw_rubric) if raw_rubric else None
        except (TypeError, ValueError):
            item["rubric"] = None
        out.append(item)
    return out


def recent_texts(*, limit: int = 10) -> list[str]:
    """Texts already suggested, for the assessor's 'do not repeat' list."""
    return [str(row.get("text") or "") for row in recent(limit=limit)]


def dismiss(suggestion_id: Any) -> bool:
    """Mark one suggestion dismissed. True when a row changed."""
    try:
        sid = int(suggestion_id)
    except (TypeError, ValueError):
        return False
    try:
        ensure_tables()
        with engine.begin() as conn:
            result = conn.execute(sql_text(
                "UPDATE initiative_suggestions SET status = :status, dismissed_at = :now "
                "WHERE id = :id AND status != :status"
            ), {"status": STATUS_DISMISSED, "now": time.time(), "id": sid})
            return bool(result.rowcount)
    except Exception:
        logger.debug("suggestion_store: could not dismiss suggestion", exc_info=True)
        return False


def mute_kind(kind: Any) -> bool:
    """Stop this kind of suggestion being made again."""
    name = _normalise_kind(kind)
    try:
        ensure_tables()
        with engine.begin() as conn:
            conn.execute(sql_text(
                "INSERT INTO initiative_suggestion_mutes (kind, muted_at) VALUES (:kind, :now) "
                "ON CONFLICT(kind) DO UPDATE SET muted_at = :now"
            ), {"kind": name, "now": time.time()})
        return True
    except Exception:
        logger.debug("suggestion_store: could not mute kind", exc_info=True)
        return False


def unmute_kind(kind: Any) -> bool:
    try:
        ensure_tables()
        with engine.begin() as conn:
            result = conn.execute(
                sql_text("DELETE FROM initiative_suggestion_mutes WHERE kind = :kind"),
                {"kind": _normalise_kind(kind)},
            )
            return bool(result.rowcount)
    except Exception:
        logger.debug("suggestion_store: could not unmute kind", exc_info=True)
        return False


def muted_kinds() -> list[str]:
    try:
        ensure_tables()
        with engine.begin() as conn:
            rows = conn.execute(sql_text("SELECT kind FROM initiative_suggestion_mutes ORDER BY kind")).all()
        return [str(r[0]) for r in rows]
    except Exception:
        logger.debug("suggestion_store: could not read mutes", exc_info=True)
        return []


def is_muted(kind: Any) -> bool:
    """True when this kind is muted. A muted kind must not reach the assessor."""
    try:
        ensure_tables()
        with engine.begin() as conn:
            row = conn.execute(
                sql_text("SELECT 1 FROM initiative_suggestion_mutes WHERE kind = :kind LIMIT 1"),
                {"kind": _normalise_kind(kind)},
            ).first()
        return row is not None
    except Exception:
        # Fail closed: if we cannot tell, treat it as muted rather than risk nagging.
        logger.debug("suggestion_store: mute lookup failed; treating as muted", exc_info=True)
        return True


def prune(*, keep_days: float = KEEP_DAYS, now: float | None = None) -> int:
    """Drop old rows and cap the table. Returns rows removed."""
    now = time.time() if now is None else now
    cutoff = now - max(1.0, float(keep_days)) * 86400.0
    removed = 0
    try:
        ensure_tables()
        with engine.begin() as conn:
            result = conn.execute(
                sql_text("DELETE FROM initiative_suggestions WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            )
            removed += int(result.rowcount or 0)
            result = conn.execute(sql_text("""
                DELETE FROM initiative_suggestions WHERE id NOT IN (
                    SELECT id FROM initiative_suggestions ORDER BY created_at DESC LIMIT :keep
                )
            """), {"keep": MAX_ROWS})
            removed += int(result.rowcount or 0)
    except Exception:
        logger.debug("suggestion_store: prune failed", exc_info=True)
    return removed
