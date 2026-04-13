"""
Unit tests for the workflow export functions.

Tests cover:
- export_workflow includes format_version "2.0" and unified fields
- export_workflow includes workflow_type, context_rules, step_type, verification, config
- export_workflow_bundle produces a valid ZIP with workflow.json
- export_workflow returns None for non-existent workflow
"""
import io
import json
import zipfile
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.workflow import import_export as import_export_mod
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowVariable,
)


@pytest.fixture
def db_setup():
    """Create an in-memory SQLite DB with AutoWorkflow tables."""
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

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session


@pytest.fixture
def populated_db(db_setup):
    """Create a workflow with steps and variables for export testing."""
    engine, Session = db_setup
    session = Session()
    try:
        wf = AutoWorkflow(
            name="Test Export Workflow",
            description="A workflow for testing export",
            workflow_type="instruction",
            context_rules="Always be polite",
            status="active",
            start_step_position=0,
        )
        session.add(wf)
        session.flush()

        step1 = AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Step One",
            action_type="agent_instruction",
            step_type="run_command",
            instruction="echo hello",
            verification="Check output contains hello",
            config=json.dumps({"timeout": 30}),
            validation_type="text_match",
            validation_prompt="hello",
            routing_mode="static",
        )
        step2 = AutoWorkflowStep(
            workflow_id=wf.id,
            position=1,
            name="Step Two",
            action_type="agent_instruction",
            step_type="agent_instruction",
            instruction="Summarize results",
            verification="",
            config=None,
        )
        session.add_all([step1, step2])
        session.flush()

        var = AutoWorkflowVariable(
            workflow_id=wf.id,
            name="greeting",
            default_value="hello",
            description="The greeting to use",
        )
        session.add(var)
        session.commit()

        yield engine, Session, wf.id
    finally:
        session.close()


class TestExportWorkflow:
    def test_export_includes_format_version(self, populated_db):
        """Export must include format_version '2.0'."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(wf_id)

        assert result is not None
        assert result["format_version"] == "2.0"

    def test_export_includes_workflow_type(self, populated_db):
        """Export must include workflow_type field."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(wf_id)

        assert result["workflow_type"] == "instruction"

    def test_export_includes_context_rules(self, populated_db):
        """Export must include context_rules field."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(wf_id)

        assert result["context_rules"] == "Always be polite"

    def test_export_step_includes_unified_fields(self, populated_db):
        """Each exported step must include step_type, verification, and config."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(wf_id)

        steps = result["steps"]
        assert len(steps) == 2

        s0 = steps[0]
        assert s0["step_type"] == "run_command"
        assert s0["verification"] == "Check output contains hello"
        assert s0["config"] == {"timeout": 30}

        s1 = steps[1]
        assert s1["step_type"] == "agent_instruction"
        assert s1["verification"] == ""
        assert s1["config"] == {}

    def test_export_includes_variables(self, populated_db):
        """Export must include variables."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(wf_id)

        assert len(result["variables"]) == 1
        assert result["variables"][0]["name"] == "greeting"

    def test_export_nonexistent_returns_none(self, db_setup):
        """Export of a non-existent workflow returns None."""
        engine, Session = db_setup
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(99999)

        assert result is None

    def test_export_preserves_backward_compat_format_field(self, populated_db):
        """Export must still include the legacy 'format' field for backward compat."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(wf_id)

        assert result["format"] == "decisionsai_workflow_v1"

    def test_export_json_serializable(self, populated_db):
        """The entire export dict must be JSON-serializable."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow(wf_id)

        # Should not raise
        serialized = json.dumps(result)
        assert isinstance(serialized, str)


class TestExportWorkflowBundle:
    def test_bundle_is_valid_zip(self, populated_db):
        """Bundle export must produce a valid ZIP archive."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            bundle = service.export_workflow_bundle(wf_id)

        assert bundle is not None
        buf = io.BytesIO(bundle)
        assert zipfile.is_zipfile(buf)

    def test_bundle_contains_workflow_json(self, populated_db):
        """Bundle must contain workflow.json with format_version 2.0."""
        engine, Session, wf_id = populated_db
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            bundle = service.export_workflow_bundle(wf_id)

        buf = io.BytesIO(bundle)
        with zipfile.ZipFile(buf, "r") as zf:
            data = json.loads(zf.read("workflow.json"))
            assert data["format_version"] == "2.0"
            assert data["workflow_type"] == "instruction"

    def test_bundle_nonexistent_returns_none(self, db_setup):
        """Bundle export of a non-existent workflow returns None."""
        engine, Session = db_setup
        from distr.core.workflow import service

        with patch.object(import_export_mod, "get_session") as mock_gs:
            mock_gs.return_value = _ctx_session(Session)
            result = service.export_workflow_bundle(99999)

        assert result is None


# ── Helper ──

import contextlib

def _ctx_session(SessionFactory):
    """Create a context manager that yields a session, mimicking get_session()."""
    @contextlib.contextmanager
    def _inner():
        session = SessionFactory()
        try:
            yield session
        finally:
            session.close()
    return _inner()
