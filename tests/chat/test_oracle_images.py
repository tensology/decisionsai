"""Oracle chat image attachments + conditional vision fallback (no network, no paid calls)."""
from __future__ import annotations

import asyncio
import base64
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core import chat_images as ci
from distr.core.db import Base, Chat
from distr.core.agent.services.llm import image_utils as iu
from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture()
def img_dir(tmp_path, monkeypatch):
    d = tmp_path / "chat_images"
    monkeypatch.setattr(ci, "chat_images_dir", lambda: d)
    return d


def test_store_accepts_png_and_rejects_others(img_dir):
    stored = ci.store_chat_image(PNG)
    assert stored["mime_type"] == "image/png" and Path(stored["path"]).is_file()
    assert stored["url"].startswith("/api/chat-images/")
    for bad in (b"\x00\x00\x00 ftypheic....", b"<svg/>", b"", b"\x89PNG\r\n\x1a\n" + b"0" * ci.MAX_CHAT_IMAGE_BYTES):
        with pytest.raises(ci.ChatImageError):
            ci.store_chat_image(bad)


def test_resolve_only_inside_image_dir(img_dir, tmp_path):
    stored = ci.store_chat_image(PNG)
    assert ci.resolve_chat_image(stored["path"]) == Path(stored["path"]).resolve()
    assert ci.resolve_chat_image(stored["name"]) is not None
    outside = tmp_path / stored["name"]
    outside.write_bytes(PNG)
    assert ci.resolve_chat_image(str(outside)) is None
    assert ci.resolve_chat_image("../../etc/passwd") is None
    assert ci.resolve_chat_image("/etc/passwd") is None


@pytest.fixture()
def client(img_dir):
    from distr.gui.web.routes import chat as chat_routes

    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Chat.__table__])
    Session = sessionmaker(bind=engine)

    @contextmanager
    def fake_session():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    with Session() as s:
        s.add(Chat(id=5, title="t", provider="OpenAI", model_name="gpt-5.6-luna"))
        s.commit()
    app = FastAPI()
    app.include_router(chat_routes.create_routes(Path("."), ""), prefix="/api")
    with patch.object(chat_routes, "get_session", fake_session), \
         patch.object(chat_routes, "is_development_thread", return_value=False):
        yield TestClient(app)


def test_upload_route_and_send_passes_uploaded_image_path(client):
    res = client.post("/api/chat-images", files={"file": ("shot.png", PNG, "image/png")})
    assert res.status_code == 200, res.text
    data = res.json()
    assert client.get(data["url"]).content == PNG
    bad = client.post("/api/chat-images", files={"file": ("x.heic", b"notanimage", "image/heic")})
    assert bad.status_code == 415 and "8 MB" in bad.json()["detail"]

    sm = MagicMock()
    with patch("distr.core.signals.signal_manager", sm):
        ok = client.post("/api/chats/5/send-to-agent", json={"message": "what is this", "image_path": data["path"], "speak": False})
        assert ok.status_code == 200, ok.text
        args = sm.web_send_to_agent_requested.emit.call_args[0]
        assert args[0] == 5 and args[5]["uploaded_image_path"] == str(Path(data["path"]).resolve())
        rejected = client.post("/api/chats/5/send-to-agent", json={"message": "x", "image_path": "/etc/passwd"})
        assert rejected.status_code == 400


def test_transcript_row_exposes_image_url(img_dir):
    from distr.gui.web.routes.chat import _append_row_messages

    stored = ci.store_chat_image(PNG)
    row = SimpleNamespace(id=9, input="look", response=None, image=stored["path"], params=None,
                          additional_context=None, created_date=None, modified_date=None, is_hidden=False)
    msgs = []
    with patch("distr.gui.web.routes.chat._row_chat_marker", return_value=None):
        _append_row_messages(msgs, row)
    assert msgs[0]["image_url"] == stored["url"]


def test_capability_check_matches_ui():
    assert iu.check_vision_model_support("gpt-5.6-luna", "OpenAI")
    assert iu.check_vision_model_support("claude-sonnet-4", "Anthropic")
    assert not iu.check_vision_model_support("deepseek-chat", "DeepSeek")
    assert not iu.check_vision_model_support("llama3", "Ollama")


class _Svc:
    _image_turn_content = LLMSharedMixin._image_turn_content
    _describe_image_for_text_model = LLMSharedMixin._describe_image_for_text_model
    _format_vision_message = LLMSharedMixin._format_vision_message

    def __init__(self, model):
        self._model_name = model


def _run(coro):
    return asyncio.run(coro)


def test_native_branch_never_calls_vision_model(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    with patch.object(iu, "analyze_image_with_vision_llm", AsyncMock()) as vis:
        out = _run(_Svc("gpt-5.6-luna")._image_turn_content(str(p), "hi", "hi", "OpenAI"))
    vis.assert_not_called()
    assert out[1]["type"] == "image_url" and out[1]["image_url"]["url"].startswith("data:image/")


def test_text_only_model_gets_vision_description(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    with patch.object(iu, "resolve_vision_model", return_value=("OpenAI", "gpt-4o-mini")), \
         patch.object(iu, "analyze_image_with_vision_llm", AsyncMock(return_value="A red square.")) as vis:
        out = _run(_Svc("deepseek-chat")._image_turn_content(str(p), "what is it", "what is it", "DeepSeek"))
    vis.assert_awaited_once()
    assert out == "Image description from gpt-4o-mini: A red square.\n\nwhat is it"


def test_text_only_model_without_vision_model_tells_user(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    with patch.object(iu, "resolve_vision_model", return_value=None), \
         patch.object(iu, "analyze_image_with_vision_llm", AsyncMock()) as vis:
        out = _run(_Svc("deepseek-chat")._image_turn_content(str(p), "q", "q", "DeepSeek"))
    vis.assert_not_called()
    assert "no vision model is configured" in out and out.endswith("q")


def test_resolve_vision_model_prefers_setting_and_skips_text_only():
    assert iu.resolve_vision_model({"vision_llm_provider": "OpenAI", "vision_llm_model": "gpt-4o"}) == ("OpenAI", "gpt-4o")
    assert iu.resolve_vision_model({"conversational_llm_provider": "DeepSeek", "conversational_llm_model": "deepseek-chat"}) is None
