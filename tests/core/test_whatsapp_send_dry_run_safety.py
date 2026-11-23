"""Composer dry-run and relay send deduplication."""

from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.integrations.whatsapp import relay_client
from distr.gui.web.routes import kanban_whatsapp


@pytest.fixture
def broken_settings(monkeypatch):
    settings = import_module("distr.core.settings")
    load = Mock(side_effect=RuntimeError("fixture settings failure detail"))
    monkeypatch.setattr(settings, "load_settings_from_db", load)
    return load


def test_dry_run_fails_closed(monkeypatch, broken_settings, caplog):
    monkeypatch.delenv("DECISIONSAI_WHATSAPP_DRY_RUN", raising=False)

    assert relay_client.whatsapp_send_dry_run_enabled() is True
    broken_settings.assert_called_once_with()
    assert any(record.levelname == "WARNING" and record.name == relay_client.__name__
               for record in caplog.records)
    assert "fixture settings failure detail" not in caplog.text


def test_explicit_dry_run_zero_takes_precedence(monkeypatch, broken_settings):
    monkeypatch.setenv("DECISIONSAI_WHATSAPP_DRY_RUN", "0")

    assert relay_client.whatsapp_send_dry_run_enabled() is False
    broken_settings.assert_not_called()


@pytest.fixture
def composer(monkeypatch):
    monkeypatch.setattr(kanban_whatsapp, "_wa_send_cache", {})
    monkeypatch.setenv("DECISIONSAI_WA_API_BASE", "http://fixture.invalid")
    router = APIRouter()
    kanban_whatsapp.register_whatsapp_routes(router, lambda _: {"Fixture": "relay"}, lambda: {})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_composer_dry_run_skips_relay(monkeypatch, composer, caplog):
    monkeypatch.setattr(kanban_whatsapp, "whatsapp_send_dry_run_enabled", lambda: True)
    transport = Mock(side_effect=AssertionError("Dry-run contacted the relay"))
    monkeypatch.setattr(httpx, "AsyncClient", transport)
    caplog.set_level("INFO", logger=kanban_whatsapp.__name__)

    response = composer.post("/tickets/whatsapp/send", json={
        "jid": "fixture@g.us", "text": "Fixture message", "caption": "Fixture caption",
    })

    assert response.status_code == 200
    assert response.json() == {"success": True, "dry_run": True, "jid": "fixture@g.us"}
    transport.assert_not_called()
    assert "jid=fixture@g.us text_len=15 caption_len=15" in caplog.text
    assert "Fixture message" not in caplog.text
    assert "Fixture caption" not in caplog.text


@pytest.mark.parametrize("field", ["client_key", "idempotency_key"])
def test_composer_reuses_successful_response(monkeypatch, composer, field):
    monkeypatch.setattr(kanban_whatsapp, "whatsapp_send_dry_run_enabled", lambda: False)
    body = {"success": True, "message_id": "fixture-ack"}
    post = AsyncMock(return_value=httpx.Response(201, json=body))
    transport = MagicMock()
    transport.__aenter__.return_value.post = post
    monkeypatch.setattr(httpx, "AsyncClient", Mock(return_value=transport))
    payload = {"jid": "fixture@g.us", "text": "Fixture message", field: "composer-1"}

    first = composer.post("/tickets/whatsapp/send", json=payload)
    duplicate = composer.post("/tickets/whatsapp/send", json=payload)

    assert first.status_code == duplicate.status_code == 201
    assert first.json() == body
    assert duplicate.json() == {**body, "duplicate": True}
    post.assert_awaited_once()
