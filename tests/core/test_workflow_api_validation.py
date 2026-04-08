# Feature: workflow-step-runner-unification, Task 9.3
# Tests for workflow_type validation (HTTP 422) and audit read-only enforcement (HTTP 403)
# Validates: Requirements 1.7, 7.4
"""
Unit tests verifying that the API layer returns HTTP 422 for invalid
workflow_type values and HTTP 403 when attempting to edit, run, or delete
audit workflows.
"""
import contextlib
from unittest.mock import patch

import pytest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
from distr.gui.web.routes.settings.workflows import register_routes


@pytest.fixture
def db_setup():
    """Create an in-memory SQLite DB that works across threads (for TestClient)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Enable WAL-like behavior for SQLite
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

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

    return engine, factory, session_ctx


@pytest.fixture
def client(db_setup):
    engine, factory, session_ctx = db_setup
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    with patch("distr.core.workflow.service.get_session", session_ctx):
        yield TestClient(app), factory


class TestWorkflowTypeValidation422:
    """API returns HTTP 422 for invalid workflow_type on create/update."""

    def test_create_invalid_workflow_type(self, client):
        tc, _ = client
        resp = tc.post("/api/workflows", json={
            "name": "bad-type",
            "workflow_type": "bogus",
        })
        assert resp.status_code == 422

    def test_create_valid_workflow_type(self, client):
        tc, _ = client
        resp = tc.post("/api/workflows", json={
            "name": "good-type",
            "workflow_type": "manual",
        })
        assert resp.status_code == 200
        assert resp.json()["workflow_type"] == "manual"

    def test_create_default_type(self, client):
        tc, _ = client
        resp = tc.post("/api/workflows", json={"name": "default"})
        assert resp.status_code == 200
        assert resp.json()["workflow_type"] == "manual"

    def test_update_invalid_workflow_type(self, client):
        tc, _ = client
        create_resp = tc.post("/api/workflows", json={"name": "to-update"})
        assert create_resp.status_code == 200
        wf_id = create_resp.json()["id"]
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"workflow_type": "invalid_value"})
        assert resp.status_code == 422

    def test_update_valid_workflow_type(self, client):
        tc, _ = client
        create_resp = tc.post("/api/workflows", json={"name": "to-update"})
        assert create_resp.status_code == 200
        wf_id = create_resp.json()["id"]
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"workflow_type": "scheduled"})
        assert resp.status_code == 200


class TestAuditWorkflowReadOnly403:
    """API returns HTTP 403 when attempting to edit, run, or delete audit workflows."""

    @staticmethod
    def _create_audit_workflow(factory) -> int:
        session = factory()
        wf = AutoWorkflow(name="Audit Trail", description="", status="active", workflow_type="audit")
        session.add(wf)
        session.commit()
        wf_id = wf.id
        session.close()
        return wf_id

    def test_update_audit_workflow_returns_403(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"name": "renamed"})
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"].lower()

    def test_delete_audit_workflow_returns_403(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.delete(f"/api/workflows/{wf_id}")
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"].lower()

    def test_run_audit_workflow_returns_403(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.post(f"/api/workflows/{wf_id}/run")
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"].lower()

    def test_get_audit_workflow_allowed(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.get(f"/api/workflows/{wf_id}")
        assert resp.status_code == 200
        assert resp.json()["workflow_type"] == "audit"

    def test_edit_non_audit_workflow_allowed(self, client):
        tc, _ = client
        create_resp = tc.post("/api/workflows", json={"name": "normal"})
        assert create_resp.status_code == 200
        wf_id = create_resp.json()["id"]
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"name": "renamed"})
        assert resp.status_code == 200
