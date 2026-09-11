"""Versioned per-thread plans, separate from reusable workflow definitions."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func

from distr.core.db import get_session
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import DevelopmentPlanRevision


PLAN_STATUSES = {"draft", "approved", "executing", "completed", "superseded", "cancelled"}
PLAN_MODES = {"develop", "plan", "goal"}


def _payload(row: DevelopmentPlanRevision) -> dict[str, Any]:
    try:
        snapshot = json.loads(row.snapshot_json or "{}")
    except (TypeError, json.JSONDecodeError):
        snapshot = {}
    return {
        "id": int(row.id),
        "chat_id": int(row.chat_id),
        "workflow_id": int(row.workflow_id),
        "revision": int(row.revision),
        "mode": row.mode or "develop",
        "status": row.status or "draft",
        "instruction": row.instruction or "",
        "snapshot": snapshot if isinstance(snapshot, dict) else {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def create_plan_revision(
    *,
    chat_id: int,
    workflow_id: int,
    instruction: str,
    workflow: dict[str, Any] | None,
    mode: str = "develop",
    status: str = "draft",
) -> dict[str, Any]:
    clean_mode = str(mode or "develop").strip().lower()
    clean_status = str(status or "draft").strip().lower()
    if clean_mode not in PLAN_MODES:
        raise ValueError("Unknown plan mode.")
    if clean_status not in PLAN_STATUSES:
        raise ValueError("Unknown plan status.")
    snapshot = {
        "name": (workflow or {}).get("name"),
        "description": (workflow or {}).get("description"),
        "steps": list((workflow or {}).get("steps") or []),
        "run_settings": dict((workflow or {}).get("run_settings") or {}),
    }
    now = utc_now_naive()
    with get_session() as db:
        previous = (
            db.query(DevelopmentPlanRevision)
            .filter(
                DevelopmentPlanRevision.chat_id == int(chat_id),
                DevelopmentPlanRevision.status.in_(["draft", "approved", "executing"]),
            )
            .all()
        )
        for row in previous:
            row.status = "superseded"
        revision = int(
            db.query(func.coalesce(func.max(DevelopmentPlanRevision.revision), 0))
            .filter(DevelopmentPlanRevision.chat_id == int(chat_id))
            .scalar()
            or 0
        ) + 1
        row = DevelopmentPlanRevision(
            chat_id=int(chat_id),
            workflow_id=int(workflow_id),
            revision=revision,
            mode=clean_mode,
            status=clean_status,
            instruction=str(instruction or "").strip(),
            snapshot_json=json.dumps(snapshot, ensure_ascii=False, default=str),
            approved_at=now if clean_status in {"approved", "executing", "completed"} else None,
            started_at=now if clean_status in {"executing", "completed"} else None,
            completed_at=now if clean_status == "completed" else None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _payload(row)


def list_plan_revisions(chat_id: int) -> list[dict[str, Any]]:
    with get_session() as db:
        rows = (
            db.query(DevelopmentPlanRevision)
            .filter(DevelopmentPlanRevision.chat_id == int(chat_id))
            .order_by(DevelopmentPlanRevision.revision.desc())
            .all()
        )
        return [_payload(row) for row in rows]


def transition_plan_revision(revision_id: int, status: str) -> dict[str, Any] | None:
    clean_status = str(status or "").strip().lower()
    if clean_status not in PLAN_STATUSES:
        raise ValueError("Unknown plan status.")
    with get_session() as db:
        row = db.get(DevelopmentPlanRevision, int(revision_id))
        if row is None:
            return None
        allowed = {
            "draft": {"approved", "cancelled", "superseded"},
            "approved": {"executing", "cancelled", "superseded"},
            "executing": {"completed", "cancelled", "superseded"},
        }
        if clean_status != row.status and clean_status not in allowed.get(row.status, set()):
            raise ValueError(f"Plan revision cannot move from {row.status} to {clean_status}.")
        now = utc_now_naive()
        row.status = clean_status
        if clean_status == "approved" and row.approved_at is None:
            row.approved_at = now
        if clean_status == "executing":
            row.approved_at = row.approved_at or now
            row.started_at = row.started_at or now
        if clean_status == "completed":
            row.completed_at = now
        db.commit()
        db.refresh(row)
        return _payload(row)
