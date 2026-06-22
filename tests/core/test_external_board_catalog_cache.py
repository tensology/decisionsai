from __future__ import annotations

import contextlib
import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base


def _make_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


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


def test_external_board_catalog_uses_cached_remote_snapshot():
    from distr.gui.web.routes.kanban import create_routes, _invalidate_external_board_list_cache

    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    call_count = {"value": 0}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return [{"id": "board-1", "name": "Product", "url": "https://trello.example/board-1", "closed": False}]

    def fake_get(url, **kwargs):
        call_count["value"] += 1
        return _FakeResponse()

    class _ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, **_extras):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            if self._target:
                self._target(*self._args, **self._kwargs)

    settings_payload = {
        "connected_accounts": json.dumps([
            {
                "provider": "trello",
                "api_key": "key",
                "api_token": "token",
                "is_valid": True,
            }
        ])
    }

    app = FastAPI()
    with patch("distr.gui.web.routes.kanban.get_session", get_session), \
         patch("distr.core.settings.load_settings_from_db", lambda: settings_payload), \
         patch("requests.get", fake_get), \
         patch("threading.Thread", _ImmediateThread):
        _invalidate_external_board_list_cache()
        app.include_router(create_routes(), prefix="/api")
        client = TestClient(app)

        first = client.get("/api/tickets/external-boards")
        second = client.get("/api/tickets/external-boards")

        _invalidate_external_board_list_cache()

    assert first.status_code == 200, first.text
    assert first.json()["cache_ready"] is False
    assert second.status_code == 200, second.text
    payload = second.json()
    assert payload["cache_ready"] is True
    assert payload["trello"][0]["id"] == "board-1"
    assert call_count["value"] == 1
