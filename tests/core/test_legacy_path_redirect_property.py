# Feature: workflow-step-runner-unification, Property 3: Legacy path redirect correctness
"""
Property-based test verifying that any legacy `/api/step-runner/*` path
returns HTTP 301 with a `Location` header pointing to the equivalent
`/api/workflows/*` path.

**Validates: Requirements 2.8**
"""
import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient

from distr.gui.web.routes.settings.workflows import register_routes


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A single path segment: alphanumeric with hyphens, 1-20 chars
_segment = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9\-]{0,19}", fullmatch=True)

# A path made of 1-4 segments joined by "/"
_path_strategy = st.lists(_segment, min_size=1, max_size=4).map("/".join)

# HTTP methods supported by the catch-all route
_method_strategy = st.sampled_from(["GET", "POST", "PUT", "PATCH", "DELETE"])


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Create a test client with only the workflow routes (including legacy redirects)."""
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(path=_path_strategy, method=_method_strategy)
def test_legacy_step_runner_redirect_correctness(client, path, method):
    """
    **Validates: Requirements 2.8**

    For any generated path suffix and HTTP method, a request to
    `/api/step-runner/{path}` must return HTTP 301 with a Location
    header equal to `/api/workflows/{path}`.
    """
    legacy_url = f"/api/step-runner/{path}"
    expected_location = f"/api/workflows/{path}"

    resp = client.request(method, legacy_url, follow_redirects=False)

    assert resp.status_code == 301, (
        f"Expected 301 for {method} {legacy_url}, got {resp.status_code}"
    )
    assert resp.headers["location"] == expected_location, (
        f"Expected Location: {expected_location}, got {resp.headers.get('location')}"
    )
