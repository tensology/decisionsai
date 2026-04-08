"""
Tests for legacy /api/step-runner/* → /api/workflows/* redirect (HTTP 301).

Validates: Requirements 2.8
"""
import pytest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient

from distr.gui.web.routes.settings.workflows import register_routes


@pytest.fixture
def client():
    """Create a test client with only the workflow routes (including legacy redirects)."""
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestLegacyStepRunnerRedirect:
    """Test that /api/step-runner/* paths redirect to /api/workflows/* with HTTP 301."""

    def test_redirect_plan(self, client):
        resp = client.get("/api/step-runner/plan", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/plan"

    def test_redirect_version(self, client):
        resp = client.get("/api/step-runner/version", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/version"

    def test_redirect_sessions_path(self, client):
        resp = client.get("/api/step-runner/sessions/123", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/sessions/123"

    def test_redirect_nested_path(self, client):
        resp = client.get("/api/step-runner/sessions/5/runs", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/sessions/5/runs"

    def test_redirect_post_method(self, client):
        resp = client.post("/api/step-runner/plan", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/plan"

    def test_redirect_patch_method(self, client):
        resp = client.patch("/api/step-runner/sessions/1/schedule", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/sessions/1/schedule"

    def test_redirect_delete_method(self, client):
        resp = client.delete("/api/step-runner/sessions/1", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/sessions/1"

    def test_redirect_root(self, client):
        resp = client.get("/api/step-runner", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows"

    def test_redirect_preserves_path_segments(self, client):
        resp = client.get("/api/step-runner/steps/42/generate-code", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/api/workflows/steps/42/generate-code"
