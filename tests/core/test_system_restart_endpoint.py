"""POST /api/system/restart must go through the in-app path, never kill the process."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes import observability


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(observability.create_routes(), prefix="/api")
    return TestClient(app)


def test_restart_emits_the_signal_directly(client, monkeypatch):
    """The signal must be emitted synchronously.

    A QTimer.singleShot on the asyncio thread never fires (there is no Qt event
    loop there), which made the endpoint answer 200 and restart nothing.
    """
    import distr.core.signals as signals

    fired = []
    monkeypatch.setattr(
        signals.signal_manager, "restart_app",
        type("S", (), {"emit": lambda self: fired.append(1)})(),
    )

    body = client.post("/api/system/restart").json()
    assert body == {"success": True, "restarting": True}
    assert fired == [1]


def test_restart_never_raises_when_the_signal_is_missing(client, monkeypatch):
    import distr.core.signals as signals

    monkeypatch.delattr(signals.signal_manager, "restart_app", raising=False)

    body = client.post("/api/system/restart").json()
    assert body == {"success": True, "restarting": True}


def test_runtime_diagnostics_still_works(client):
    body = client.get("/api/diagnostics/runtime").json()
    assert body["status"] == "ok"
    assert "pid" in body
