"""SSE /api/events/stream (R22) — auth + per-client connection slots."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from distr.gui.web.routes import events_stream as es
from distr.gui.web.server import create_app


@pytest.fixture
def app():
    return create_app()


def test_events_stream_requires_token(app):
    client = TestClient(app)
    assert client.get("/api/events/stream").status_code == 401


def test_sse_slot_limit_per_client_id():
    """Same counters used by GET /api/events/stream (max 5 per client_id)."""
    cid = "pytest-sse-slot-client"
    acquired = 0
    try:
        for _ in range(es._MAX_SSE_PER_CLIENT):
            assert es._acquire_sse_slot(cid) is True
            acquired += 1
        assert es._acquire_sse_slot(cid) is False
    finally:
        for _ in range(acquired):
            es._release_sse_slot(cid)
