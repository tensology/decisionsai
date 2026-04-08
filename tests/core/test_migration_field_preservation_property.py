# Feature: workflow-step-runner-unification, Property 1: Migration preserves all field values
"""
Property-based test verifying that `migrate_step_runner_data()` preserves all
field values when migrating from StepRunnerSession/Step/Run tables to
AutoWorkflow/AutoWorkflowStep/AutoWorkflowRun tables, according to the
design's field mapping tables.

**Validates: Requirements 1.3**
"""

import contextlib
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine():
    """Create an in-memory SQLite engine with both legacy and unified tables."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    # Create unified tables via SQLAlchemy metadata
    Base.metadata.create_all(engine)

    # Create legacy StepRunner tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS step_runner_sessions (
                id INTEGER PRIMARY KEY,
                instruction TEXT NOT NULL,
                status VARCHAR DEFAULT 'planned',
                chat_id INTEGER,
                created_date DATETIME,
                modified_date DATETIME,
                session_type VARCHAR DEFAULT 'instruction',
                schedule VARCHAR,
                next_run_at DATETIME,
                last_run_at DATETIME,
                enabled BOOLEAN DEFAULT 1,
                timezone VARCHAR,
                schedule_time VARCHAR,
                schedule_days VARCHAR,
                context_rules TEXT,
                workflow_input TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS step_runner_steps (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                position INTEGER DEFAULT 0,
                title VARCHAR NOT NULL,
                instruction TEXT NOT NULL,
                verification TEXT,
                status VARCHAR DEFAULT 'pending',
                result TEXT,
                tool_used VARCHAR,
                step_type VARCHAR DEFAULT 'run_command',
                config TEXT,
                code TEXT,
                created_date DATETIME,
                modified_date DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS step_runner_runs (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                started_at DATETIME,
                completed_at DATETIME,
                status VARCHAR DEFAULT 'running',
                step_results TEXT
            )
        """))
        conn.commit()

    return engine


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _insert_session(conn, **kwargs):
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(f":{k}" for k in kwargs.keys())
    conn.execute(
        text(f"INSERT INTO step_runner_sessions ({cols}) VALUES ({placeholders})"),
        kwargs,
    )


def _insert_step(conn, **kwargs):
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(f":{k}" for k in kwargs.keys())
    conn.execute(
        text(f"INSERT INTO step_runner_steps ({cols}) VALUES ({placeholders})"),
        kwargs,
    )


def _insert_run(conn, **kwargs):
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(f":{k}" for k in kwargs.keys())
    conn.execute(
        text(f"INSERT INTO step_runner_runs ({cols}) VALUES ({placeholders})"),
        kwargs,
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_REFERENCE_DT = datetime(2025, 6, 15, 12, 0, 0)

# Printable text that avoids NUL bytes (SQLite-safe)
_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=200,
)

_optional_safe_text = st.one_of(st.none(), _safe_text)

_session_statuses = st.sampled_from(["planned", "in_progress", "completed"])
_session_types = st.sampled_from(["instruction", "scheduled"])

_step_statuses = st.sampled_from(["pending", "running", "passed", "failed", "cancelled"])
_step_types = st.sampled_from([
    "agent_instruction", "run_command", "http_request",
    "execute_code", "playwright", "play_recording",
])

_run_statuses = st.sampled_from(["running", "in_progress", "completed"])

_dt_strategy = st.integers(min_value=0, max_value=365 * 24 * 60).map(
    lambda mins: _REFERENCE_DT - timedelta(minutes=mins)
)

_step_results_strategy = st.one_of(
    st.none(),
    st.lists(
        st.fixed_dictionaries({
            "step_id": st.integers(min_value=1, max_value=100),
            "status": st.sampled_from(["completed", "failed", "pending"]),
        }),
        min_size=0,
        max_size=5,
    ).map(json.dumps),
)

_session_strategy = st.fixed_dictionaries({
    "instruction": _safe_text,
    "status": _session_statuses,
    "session_type": _session_types,
    "chat_id": st.one_of(st.none(), st.integers(min_value=1, max_value=9999)),
    "context_rules": _optional_safe_text,
    "workflow_input": _optional_safe_text,
})

_step_strategy = st.fixed_dictionaries({
    "title": _safe_text,
    "instruction": _safe_text,
    "verification": _optional_safe_text,
    "status": _step_statuses,
    "result": _optional_safe_text,
    "tool_used": st.one_of(st.none(), _safe_text),
    "step_type": _step_types,
    "config": st.one_of(
        st.none(),
        st.fixed_dictionaries({
            "timeout": st.integers(min_value=1, max_value=600),
        }).map(json.dumps),
    ),
    "code": _optional_safe_text,
    "position": st.integers(min_value=0, max_value=50),
})

_run_strategy = st.fixed_dictionaries({
    "status": _run_statuses,
    "step_results": _step_results_strategy,
})

# Composite: a session with 1-5 steps and 0-3 runs
_migration_data_strategy = st.fixed_dictionaries({
    "session": _session_strategy,
    "steps": st.lists(_step_strategy, min_size=1, max_size=5),
    "runs": st.lists(_run_strategy, min_size=0, max_size=3),
})


# ---------------------------------------------------------------------------
# Status/type mapping (mirrors the service module constants)
# ---------------------------------------------------------------------------

_SESSION_STATUS_MAP = {
    "planned": "draft",
    "in_progress": "active",
}

_SESSION_TYPE_MAP = {
    "instruction": "instruction",
    "scheduled": "scheduled",
}

_RUN_STATUS_MAP = {
    "in_progress": "running",
}


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


class TestMigrationFieldPreservation:
    """Property 1: Migration preserves all field values."""

    @settings(max_examples=100, deadline=None)
    @given(data=_migration_data_strategy)
    def test_migration_preserves_all_fields(self, data):
        """**Validates: Requirements 1.3**

        For any valid StepRunnerSession with associated StepRunnerSteps and
        StepRunnerRuns, migrating to AutoWorkflow/AutoWorkflowStep/AutoWorkflowRun
        SHALL preserve all field values according to the migration field mapping.
        """
        session_data = data["session"]
        steps_data = data["steps"]
        runs_data = data["runs"]

        # Ensure step positions are unique to avoid re-sequencing complexity
        # (re-sequencing is tested separately in unit tests)
        positions = [s["position"] for s in steps_data]
        assume(len(positions) == len(set(positions)))

        engine = _make_engine()
        factory = sessionmaker(bind=engine)

        now = datetime.utcnow()

        # Insert legacy data
        with engine.connect() as conn:
            _insert_session(
                conn,
                id=1,
                instruction=session_data["instruction"],
                status=session_data["status"],
                session_type=session_data["session_type"],
                chat_id=session_data["chat_id"],
                context_rules=session_data["context_rules"],
                workflow_input=session_data["workflow_input"],
                created_date=now,
                modified_date=now,
            )

            for i, step in enumerate(steps_data):
                _insert_step(
                    conn,
                    id=i + 1,
                    session_id=1,
                    position=step["position"],
                    title=step["title"],
                    instruction=step["instruction"],
                    verification=step["verification"],
                    status=step["status"],
                    result=step["result"],
                    tool_used=step["tool_used"],
                    step_type=step["step_type"],
                    config=step["config"],
                    code=step["code"],
                    created_date=now,
                    modified_date=now,
                )

            for i, run in enumerate(runs_data):
                _insert_run(
                    conn,
                    id=i + 1,
                    session_id=1,
                    started_at=now,
                    completed_at=now,
                    status=run["status"],
                    step_results=run["step_results"],
                )

            conn.commit()

        # Run migration
        import distr.core.workflow.service as svc

        def patched_get_session():
            return _session_ctx(factory)

        with patch.object(svc, "get_session", patched_get_session):
            result = svc.migrate_step_runner_data()

        assert result is True, "Migration should succeed"

        # ── Verify session → workflow mapping ──
        with _session_ctx(factory) as db:
            wf = db.query(AutoWorkflow).first()
            assert wf is not None, "Workflow should exist after migration"

            # instruction → description
            assert wf.description == session_data["instruction"]
            # instruction → name (truncated to 200)
            assert wf.name == session_data["instruction"][:200]
            # status mapping
            expected_status = _SESSION_STATUS_MAP.get(
                session_data["status"], session_data["status"]
            )
            assert wf.status == expected_status
            # session_type → workflow_type
            expected_type = _SESSION_TYPE_MAP.get(
                session_data["session_type"], "manual"
            )
            assert wf.workflow_type == expected_type
            # chat_id preserved
            assert wf.chat_id == session_data["chat_id"]
            # context_rules preserved
            assert wf.context_rules == session_data["context_rules"]
            # workflow_input preserved
            assert wf.workflow_input == session_data["workflow_input"]

            # ── Verify steps mapping ──
            migrated_steps = (
                db.query(AutoWorkflowStep)
                .filter_by(workflow_id=wf.id)
                .order_by(AutoWorkflowStep.position)
                .all()
            )
            assert len(migrated_steps) == len(steps_data)

            # Sort original steps by position to match query order
            sorted_steps = sorted(steps_data, key=lambda s: s["position"])

            for migrated, original in zip(migrated_steps, sorted_steps):
                # title → name
                assert migrated.name == original["title"]
                # instruction preserved
                assert migrated.instruction == original["instruction"]
                # verification preserved
                assert migrated.verification == original["verification"]
                # status preserved
                assert migrated.status == original["status"]
                # result preserved
                assert migrated.result == original["result"]
                # tool_used preserved
                assert migrated.tool_used == original["tool_used"]
                # step_type preserved
                assert migrated.step_type == original["step_type"]
                # config preserved
                assert migrated.config == original["config"]
                # code preserved
                assert migrated.code == original["code"]
                # position preserved (no re-sequencing since we assumed unique)
                assert migrated.position == original["position"]

            # ── Verify runs mapping ──
            migrated_runs = (
                db.query(AutoWorkflowRun)
                .filter_by(workflow_id=wf.id)
                .all()
            )
            assert len(migrated_runs) == len(runs_data)

            for migrated_run, original_run in zip(migrated_runs, runs_data):
                # status mapping
                expected_run_status = _RUN_STATUS_MAP.get(
                    original_run["status"], original_run["status"]
                )
                assert migrated_run.status == expected_run_status
                # step_results preserved as-is
                assert migrated_run.step_results == original_run["step_results"]
                # workflow_id correctly mapped
                assert migrated_run.workflow_id == wf.id
