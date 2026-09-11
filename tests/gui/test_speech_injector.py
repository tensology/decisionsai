from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes import speech_injector


class _Signal:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def emit(self, *args: object) -> None:
        self.calls.append(args)


def _client(monkeypatch):
    signal = _Signal()
    monkeypatch.setattr(speech_injector, "require_internal_token_request", lambda request: None)

    import distr.core.signals as signals_module

    monkeypatch.setattr(signals_module, "signal_manager", type("Signals", (), {"speak_text_directly": signal})())
    app = FastAPI()
    app.include_router(speech_injector.router)
    return TestClient(app), signal


def test_inject_speech_emits_direct_tts(monkeypatch):
    client, signal = _client(monkeypatch)

    response = client.post("/api/speech/inject", json={"text": "A message from Grok."})

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "text_length": 21}
    assert signal.calls == [("A message from Grok.",)]


def test_inject_speech_rejects_blank_text(monkeypatch):
    client, signal = _client(monkeypatch)

    response = client.post("/api/speech/inject", json={"text": "   "})

    assert response.status_code == 422
    assert signal.calls == []


def test_inject_speech_requires_auth(monkeypatch):
    app = FastAPI()

    def reject(_request):
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Unauthorized")

    monkeypatch.setattr(speech_injector, "require_internal_token_request", reject)
    app.include_router(speech_injector.router)

    response = TestClient(app).post("/api/speech/inject", json={"text": "hello"})

    assert response.status_code == 401
