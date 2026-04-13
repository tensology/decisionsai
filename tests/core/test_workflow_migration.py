"""
Unit tests for the StepRunner → AutoWorkflow migration logic.

Tests cover:
- Full migration of sessions, steps, and runs
- Status mapping (planned→draft, in_progress→active, in_progress→running)
- Session type mapping (instruction→instruction, scheduled→scheduled, unknown→manual)
- Step position re-sequencing when duplicates exist
- Migration marker idempotency (run twice, verify single execution)
- Degraded mode on failure
- Skipping when no legacy tables exist
"""
import contextlib
import json
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
)


@pytest.fixture
def db_setup():
    """Create an in-memory SQLite DB with both legacy StepRunner and AutoWorkflow tables."""
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

    # Create the unified tables via SQLAlchemy metadata
    Base.metadata.create_all(engine)

    # Create legacy StepRunner tables manually
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

    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def session_ctx():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return engine, factory, session_ctx


def _insert_session(conn, **kwargs):
    """Insert a legacy StepRunnerSession row."""
    defaults = {
        "instruction": "Test instruction",
        "status": "planned",
        "session_type": "instruction",
        "created_date": datetime.utcnow(),
        "modified_date": datetime.utcnow(),
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(text(f"INSERT INTO step_runner_sessions ({cols}) VALUES ({placeholders})"), defaults)


def _insert_step(conn, **kwargs):
    """Insert a legacy StepRunnerStep row."""
    defaults = {
        "session_id": 1,
        "position": 0,
        "title": "Step Title",
        "instruction": "Do something",
        "status": "pending",
        "step_type": "run_command",
        "created_date": datetime.utcnow(),
        "modified_date": datetime.utcnow(),
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(text(f"INSERT INTO step_runner_steps ({cols}) VALUES ({placeholders})"), defaults)


def _insert_run(conn, **kwargs):
    """Insert a legacy StepRunnerRun row."""
    defaults = {
        "session_id": 1,
        "started_at": datetime.utcnow(),
        "status": "running",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(text(f"INSERT INTO step_runner_runs ({cols}) VALUES ({placeholders})"), defaults)


class TestMigrationBasic:
    """Basic migration: sessions, steps, runs are copied with correct field mapping."""

    def test_full_migration(self, db_setup):
        engine, factory, session_ctx = db_setup

        # Seed legacy data
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="Build a website", status="planned",
                            session_type="instruction", chat_id=42,
                            context_rules="Use React", workflow_input='{"source":"kanban"}')
            _insert_step(conn, id=1, session_id=1, position=0, title="Setup project",
                         instruction="npm init", verification="package.json exists",
                         step_type="run_command", tool_used="terminal", code="npm init -y",
                         config='{"timeout":30}')
            _insert_step(conn, id=2, session_id=1, position=1, title="Install deps",
                         instruction="npm install react", step_type="run_command")
            _insert_run(conn, id=1, session_id=1, status="running",
                        step_results='[{"step_id":1,"status":"completed"}]')
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            result = svc.migrate_step_runner_data()

        assert result is True
        assert svc.is_migration_degraded() is False

        # Verify workflow
        with session_ctx() as session:
            wfs = session.query(AutoWorkflow).all()
            assert len(wfs) == 1
            wf = wfs[0]
            assert wf.description == "Build a website"
            assert wf.status == "draft"  # planned → draft
            assert wf.workflow_type == "instruction"
            assert wf.chat_id == 42
            assert wf.context_rules == "Use React"
            assert wf.workflow_input == '{"source":"kanban"}'

            # Verify steps
            steps = session.query(AutoWorkflowStep).filter_by(workflow_id=wf.id).order_by(AutoWorkflowStep.position).all()
            assert len(steps) == 2
            assert steps[0].name == "Setup project"
            assert steps[0].instruction == "npm init"
            assert steps[0].verification == "package.json exists"
            assert steps[0].step_type == "run_command"
            assert steps[0].tool_used == "terminal"
            assert steps[0].code == "npm init -y"
            assert steps[0].config == '{"timeout":30}'
            assert steps[0].position == 0
            assert steps[1].name == "Install deps"
            assert steps[1].position == 1

            # Verify run
            runs = session.query(AutoWorkflowRun).filter_by(workflow_id=wf.id).all()
            assert len(runs) == 1
            assert runs[0].status == "running"
            assert runs[0].step_results == '[{"step_id":1,"status":"completed"}]'


class TestStatusMapping:
    """Status values are mapped per the design spec."""

    def test_session_planned_maps_to_draft(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test", status="planned")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            assert wf.status == "draft"

    def test_session_in_progress_maps_to_active(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test", status="in_progress")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            assert wf.status == "active"

    def test_run_in_progress_maps_to_running(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test", status="planned")
            _insert_run(conn, id=1, session_id=1, status="in_progress")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            run = session.query(AutoWorkflowRun).first()
            assert run.status == "running"

    def test_unknown_status_passes_through(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test", status="completed")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            assert wf.status == "completed"


class TestSessionTypeMapping:
    """Session types are mapped per the design spec."""

    def test_instruction_type(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test", session_type="instruction")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            assert wf.workflow_type == "instruction"

    def test_scheduled_type(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test", session_type="scheduled",
                            schedule="daily", schedule_time="08:00")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            assert wf.workflow_type == "scheduled"
            assert wf.schedule_preset == "daily"
            assert wf.schedule_cron is not None

    def test_unknown_type_maps_to_manual(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test", session_type="unknown_type")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            assert wf.workflow_type == "manual"


class TestPositionResequencing:
    """Step positions are re-sequenced when duplicates exist within a workflow."""

    def test_duplicate_positions_resequenced(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test")
            # Two steps with the same position
            _insert_step(conn, id=1, session_id=1, position=0, title="Step A")
            _insert_step(conn, id=2, session_id=1, position=0, title="Step B")
            _insert_step(conn, id=3, session_id=1, position=1, title="Step C")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            steps = (
                session.query(AutoWorkflowStep)
                .filter_by(workflow_id=wf.id)
                .order_by(AutoWorkflowStep.position)
                .all()
            )
            positions = [s.position for s in steps]
            assert positions == [0, 1, 2]
            assert len(set(positions)) == 3  # all unique

    def test_no_duplicates_positions_preserved(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test")
            _insert_step(conn, id=1, session_id=1, position=0, title="Step A")
            _insert_step(conn, id=2, session_id=1, position=1, title="Step B")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            svc.migrate_step_runner_data()

        with session_ctx() as session:
            wf = session.query(AutoWorkflow).first()
            steps = (
                session.query(AutoWorkflowStep)
                .filter_by(workflow_id=wf.id)
                .order_by(AutoWorkflowStep.position)
                .all()
            )
            assert steps[0].position == 0
            assert steps[1].position == 1


class TestIdempotency:
    """Migration marker prevents re-running."""

    def test_run_twice_only_migrates_once(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test")
            conn.commit()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            result1 = svc.migrate_step_runner_data()
            assert result1 is True

            # Count workflows after first migration
            with session_ctx() as session:
                count1 = session.query(AutoWorkflow).count()

            # Run again
            result2 = svc.migrate_step_runner_data()
            assert result2 is True

            # Count should be the same
            with session_ctx() as session:
                count2 = session.query(AutoWorkflow).count()

            assert count1 == count2 == 1


class TestDegradedMode:
    """On failure, migration sets degraded mode flag."""

    def test_failure_sets_degraded_mode(self, db_setup):
        engine, factory, session_ctx = db_setup
        with engine.connect() as conn:
            _insert_session(conn, id=1, instruction="test")
            conn.commit()

        import distr.core.workflow.service as svc

        # Patch session.add to raise after the marker check passes
        @contextlib.contextmanager
        def error_session():
            session = factory()
            original_add = session.add

            def patched_add(obj):
                # Fail when trying to add an AutoWorkflow (the migration insert)
                if isinstance(obj, AutoWorkflow):
                    raise RuntimeError("Simulated DB failure during migration")
                return original_add(obj)

            session.add = patched_add
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        with patch("distr.core.workflow.migration.get_session", error_session):
            result = svc.migrate_step_runner_data()

        assert result is False
        assert svc.is_migration_degraded() is True

        # Reset for other tests
        import distr.core.workflow.migration as migration_mod
        migration_mod._migration_degraded_mode = False


class TestNoLegacyTables:
    """When no legacy tables exist, migration writes marker and succeeds."""

    def test_no_legacy_tables(self):
        """Migration succeeds when step_runner_sessions table doesn't exist."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        @contextlib.contextmanager
        def session_ctx():
            session = factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        import distr.core.workflow.service as svc
        with patch("distr.core.workflow.migration.get_session", session_ctx):
            result = svc.migrate_step_runner_data()

        assert result is True
        assert svc.is_migration_degraded() is False

        # Verify marker was written
        with session_ctx() as session:
            from distr.core.workflow.service import _check_migration_marker
            assert _check_migration_marker(session) is True
