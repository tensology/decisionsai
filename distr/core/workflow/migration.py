"""
Workflow Migration — StepRunner → AutoWorkflow data migration.

Extracted from service.py as part of the module decomposition.
"""
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import text, inspect as sa_inspect

from distr.core.db import get_session
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun,
)

logger = logging.getLogger(__name__)


# ── Migration marker ──

MIGRATION_MARKER_KEY = "step_runner_to_workflow_v1"

# Global degraded-mode flag.  When True, all AutoWorkflow write operations
# should be blocked (API returns HTTP 503).  Set by migrate_step_runner_data()
# on failure; cleared on successful migration.
_migration_degraded_mode = False


def is_migration_degraded() -> bool:
    """Return True when the app is in degraded mode due to a failed migration."""
    return _migration_degraded_mode


# ── Status mapping helpers ──

_SESSION_STATUS_MAP: Dict[str, str] = {
    "planned": "draft",
    "in_progress": "active",
    # Other statuses pass through unchanged
}

_SESSION_TYPE_MAP: Dict[str, str] = {
    "instruction": "instruction",
    "scheduled": "scheduled",
    # Fallback to 'manual' for unknown types
}

_RUN_STATUS_MAP: Dict[str, str] = {
    "in_progress": "running",
    # Other statuses pass through unchanged
}


def _check_migration_marker(session) -> bool:
    """Return True if the migration marker already exists (migration was done)."""
    try:
        inspector = sa_inspect(session.bind)
        if "_migration_markers" not in inspector.get_table_names():
            return False
        row = session.execute(
            text("SELECT 1 FROM _migration_markers WHERE marker_key = :key"),
            {"key": MIGRATION_MARKER_KEY},
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _write_migration_marker(session) -> None:
    """Write the migration marker row.  Must be called inside the same session
    that committed the migration data so it can be committed together or
    separately after the main transaction succeeds."""
    # Ensure the table exists
    session.execute(text(
        "CREATE TABLE IF NOT EXISTS _migration_markers ("
        "  marker_key VARCHAR PRIMARY KEY,"
        "  migrated_at DATETIME"
        ")"
    ))
    session.execute(
        text("INSERT OR IGNORE INTO _migration_markers (marker_key, migrated_at) VALUES (:key, :ts)"),
        {"key": MIGRATION_MARKER_KEY, "ts": datetime.utcnow()},
    )


def _parse_dt(val) -> Optional[datetime]:
    """Coerce a value from raw SQL into a Python datetime (or None)."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


def _resequence_positions(steps_by_workflow: Dict[int, list]) -> Dict[int, list]:
    """Re-sequence step positions within each workflow if duplicates are detected.

    Returns the same dict with positions updated in-place on each step dict.
    """
    for wf_id, steps in steps_by_workflow.items():
        positions = [s["position"] for s in steps]
        if len(positions) != len(set(positions)):
            # Duplicates detected — re-sequence starting from 0
            steps.sort(key=lambda s: (s["position"], s["_orig_id"]))
            for idx, s in enumerate(steps):
                s["position"] = idx
    return steps_by_workflow


def migrate_step_runner_data() -> bool:
    """One-time transactional migration from StepRunner tables to AutoWorkflow tables.

    Returns True on success (or if already migrated), False on failure.
    On failure, sets the global degraded-mode flag.
    """
    global _migration_degraded_mode

    with get_session() as session:
        # Idempotency check
        if _check_migration_marker(session):
            logger.info("StepRunner migration: marker found — skipping.")
            _migration_degraded_mode = False
            return True

        # Check if legacy tables exist at all
        inspector = sa_inspect(session.bind)
        existing_tables = inspector.get_table_names()
        if "step_runner_sessions" not in existing_tables:
            logger.info("StepRunner migration: no legacy tables found — writing marker and skipping.")
            _write_migration_marker(session)
            session.commit()
            _migration_degraded_mode = False
            return True

        try:
            # ── 1. Read all legacy sessions ──
            rows = session.execute(text("SELECT * FROM step_runner_sessions")).fetchall()
            columns = session.execute(text("SELECT * FROM step_runner_sessions LIMIT 0")).keys()
            sessions_data = [dict(zip(columns, r)) for r in rows]

            # ── 2. Read all legacy steps ──
            rows = session.execute(text("SELECT * FROM step_runner_steps")).fetchall()
            columns = session.execute(text("SELECT * FROM step_runner_steps LIMIT 0")).keys()
            steps_data = [dict(zip(columns, r)) for r in rows]

            # ── 3. Read all legacy runs ──
            rows = session.execute(text("SELECT * FROM step_runner_runs")).fetchall()
            columns = session.execute(text("SELECT * FROM step_runner_runs LIMIT 0")).keys()
            runs_data = [dict(zip(columns, r)) for r in rows]

            # Build session_id → new workflow_id mapping
            session_id_map: Dict[int, int] = {}

            # ── 4. Insert workflows ──
            for s in sessions_data:
                old_status = s.get("status", "planned")
                mapped_status = _SESSION_STATUS_MAP.get(old_status, old_status)

                old_type = s.get("session_type", "instruction")
                mapped_type = _SESSION_TYPE_MAP.get(old_type, "manual")

                # Convert schedule to schedule_preset + schedule_cron
                schedule_val = s.get("schedule")
                schedule_preset = None
                schedule_cron = None
                if schedule_val:
                    from distr.core.workflow.scheduler import schedule_to_cron
                    schedule_preset = schedule_val
                    schedule_cron = schedule_to_cron(
                        schedule_val,
                        s.get("schedule_time"),
                        s.get("timezone"),
                        s.get("schedule_days"),
                    )

                wf = AutoWorkflow(
                    name=s.get("instruction", "Untitled Workflow")[:200] or "Untitled Workflow",
                    description=s.get("instruction"),
                    status=mapped_status,
                    workflow_type=mapped_type,
                    chat_id=s.get("chat_id"),
                    context_rules=s.get("context_rules"),
                    workflow_input=s.get("workflow_input"),
                    schedule_enabled=bool(s.get("enabled", False)),
                    schedule_preset=schedule_preset,
                    schedule_cron=schedule_cron,
                    schedule_time=s.get("schedule_time"),
                    schedule_days=s.get("schedule_days"),
                    schedule_timezone=s.get("timezone"),
                    next_run_at=_parse_dt(s.get("next_run_at")),
                    last_run_at=_parse_dt(s.get("last_run_at")),
                    created_date=_parse_dt(s.get("created_date")) or datetime.utcnow(),
                    modified_date=_parse_dt(s.get("modified_date")) or datetime.utcnow(),
                )
                session.add(wf)
                session.flush()  # get wf.id
                session_id_map[s["id"]] = wf.id

            # ── 5. Group steps by session and re-sequence if needed ──
            steps_by_session: Dict[int, list] = defaultdict(list)
            for st in steps_data:
                sid = st.get("session_id")
                if sid not in session_id_map:
                    continue  # orphan step — skip
                steps_by_session[sid].append({
                    "_orig_id": st["id"],
                    "session_id": sid,
                    "position": st.get("position", 0),
                    "title": st.get("title", "New Step"),
                    "instruction": st.get("instruction"),
                    "verification": st.get("verification"),
                    "status": st.get("status", "pending"),
                    "result": st.get("result"),
                    "tool_used": st.get("tool_used"),
                    "step_type": st.get("step_type", "run_command"),
                    "config": st.get("config"),
                    "code": st.get("code"),
                    "created_date": _parse_dt(st.get("created_date")) or datetime.utcnow(),
                    "modified_date": _parse_dt(st.get("modified_date")) or datetime.utcnow(),
                })

            _resequence_positions(steps_by_session)

            old_step_id_map: Dict[int, int] = {}  # old step id → new step id
            for sid, step_list in steps_by_session.items():
                wf_id = session_id_map[sid]
                for st in step_list:
                    step = AutoWorkflowStep(
                        workflow_id=wf_id,
                        position=st["position"],
                        name=st["title"],
                        instruction=st["instruction"],
                        verification=st["verification"],
                        status=st["status"],
                        result=st["result"],
                        tool_used=st["tool_used"],
                        step_type=st["step_type"],
                        action_type=st["step_type"],  # mirror step_type into action_type
                        config=st["config"],
                        code=st["code"],
                        created_date=st["created_date"],
                        modified_date=st["modified_date"],
                    )
                    session.add(step)
                    session.flush()
                    old_step_id_map[st["_orig_id"]] = step.id

            # ── 6. Insert runs ──
            for r in runs_data:
                sid = r.get("session_id")
                if sid not in session_id_map:
                    continue  # orphan run — skip

                old_status = r.get("status", "running")
                mapped_status = _RUN_STATUS_MAP.get(old_status, old_status)

                run = AutoWorkflowRun(
                    workflow_id=session_id_map[sid],
                    started_at=_parse_dt(r.get("started_at")) or datetime.utcnow(),
                    completed_at=_parse_dt(r.get("completed_at")),
                    status=mapped_status,
                    step_results=r.get("step_results"),
                )
                session.add(run)

            # ── 7. Write marker and commit ──
            _write_migration_marker(session)
            session.commit()

            logger.info(
                "StepRunner migration: completed — %d sessions, %d steps, %d runs migrated.",
                len(sessions_data), len(steps_data), len(runs_data),
            )
            _migration_degraded_mode = False
            return True

        except Exception:
            session.rollback()
            logger.error(
                "StepRunner migration: FAILED — entering degraded mode. "
                "Migration will retry on next startup.",
                exc_info=True,
            )
            _migration_degraded_mode = True
            return False
