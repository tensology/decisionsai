"""Jira/Trello external ticket update + create isolation."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanTicket


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


def _jira_settings():
    return {
        "connected_accounts": json.dumps(
            [
                {
                    "provider": "jira",
                    "domain": "example.atlassian.net",
                    "email": "dev@example.com",
                    "api_token": "token",
                    "is_valid": True,
                }
            ]
        )
    }


class _FakeResponse:
    def __init__(self, status_code=204, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


def test_update_external_jira_ticket_maps_summary_description_priority():
    from distr.gui.web.routes.kanban import create_routes, _invalidate_external_board_detail_cache

    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    put_calls = []

    def fake_put(url, **kwargs):
        put_calls.append({"url": url, **kwargs})
        return _FakeResponse(204)

    app = FastAPI()
    with patch("distr.gui.web.routes.kanban.get_session", get_session), patch(
        "distr.core.settings.load_settings_from_db", _jira_settings
    ), patch("requests.put", fake_put):
        _invalidate_external_board_detail_cache("jira", "35")
        app.include_router(create_routes(), prefix="/api")
        client = TestClient(app)
        response = client.put(
            "/api/tickets/external-boards/jira/35/update-ticket",
            json={
                "ticket_id": "DAI-42",
                "title": "Renamed issue",
                "description": "Updated body",
                "priority": "high",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["ticket"]["id"] == "DAI-42"
    assert body["ticket"]["title"] == "Renamed issue"
    assert len(put_calls) == 1
    call = put_calls[0]
    assert call["url"].endswith("/rest/api/2/issue/DAI-42")
    fields = call["json"]["fields"]
    assert fields["summary"] == "Renamed issue"
    assert fields["description"] == "Updated body"
    assert fields["priority"] == {"name": "High"}


def test_update_external_jira_ticket_surfaces_api_failure():
    from distr.gui.web.routes.kanban import create_routes

    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    def fake_put(url, **kwargs):
        return _FakeResponse(400, payload={"errorMessages": ["Field priority is not valid"]}, text='{"errorMessages":["Field priority is not valid"]}')

    app = FastAPI()
    with patch("distr.gui.web.routes.kanban.get_session", get_session), patch(
        "distr.core.settings.load_settings_from_db", _jira_settings
    ), patch("requests.put", fake_put):
        app.include_router(create_routes(), prefix="/api")
        client = TestClient(app)
        response = client.put(
            "/api/tickets/external-boards/jira/35/update-ticket",
            json={
                "ticket_id": "DAI-42",
                "title": "Renamed issue",
                "description": "Updated body",
                "priority": "high",
            },
        )

    assert response.status_code == 502
    assert "Jira update failed" in response.text


def test_jira_create_ticket_does_not_dual_write_local_kanban():
    from distr.gui.web.routes.kanban import create_routes, _invalidate_external_board_detail_cache

    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    def fake_get(url, **kwargs):
        if "configuration" in url:
            return _FakeResponse(200, {"location": {"projectKey": "DAI"}, "name": "Decisions"})
        if "transitions" in url:
            return _FakeResponse(200, {"transitions": []})
        return _FakeResponse(404, text="not found")

    def fake_post(url, **kwargs):
        if url.endswith("/rest/api/2/issue"):
            return _FakeResponse(201, {"id": "10001", "key": "DAI-99"})
        return _FakeResponse(404, text="not found")

    app = FastAPI()
    with patch("distr.gui.web.routes.kanban.get_session", get_session), patch(
        "distr.core.settings.load_settings_from_db", _jira_settings
    ), patch("requests.get", fake_get), patch("requests.post", fake_post):
        _invalidate_external_board_detail_cache("jira", "35")
        app.include_router(create_routes(), prefix="/api")
        client = TestClient(app)
        response = client.post(
            "/api/tickets/external-boards/jira/35/create-ticket",
            json={
                "title": "Remote only",
                "description": "Should not appear on local boards",
                "priority": "medium",
                "lane_id": "To Do",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["ticket"]["id"] == "DAI-99"

    with factory() as session:
        local_count = session.query(KanbanTicket).count()
    assert local_count == 0


def test_boards_ui_wires_external_update_and_editable_fields():
    root = Path(__file__).resolve().parents[2]
    js = (root / "distr/gui/web/static/development/boards/index.js").read_text()
    assert "externalEditable" in js
    assert "/update-ticket" in js
    assert "ticket_id: String(el('kanban-ticket-id').value || '')" in js
    assert "el('kanban-ticket-save').classList.toggle('hidden', externalExisting && !externalEditable);" in js
    # Existing external tickets stay create-isolated from local Save path
    assert "board.provider === 'decisions'" in js
    assert "/create-ticket" in js
