"""Sidecar HTTP helpers (R23)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import distr.core.agent.tools.input.sidecar_http as sh


def test_sidecar_health_success(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "wire_version": 1, "os": "darwin"}

    def fake_get(url, timeout):
        assert "/health" in url
        return mock_resp

    monkeypatch.setattr(sh.requests, "get", fake_get)
    assert sh.sidecar_health() == {"ok": True, "wire_version": 1, "os": "darwin"}
    assert sh.is_sidecar_reachable() is True


def test_sidecar_health_non_200(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 503

    monkeypatch.setattr(sh.requests, "get", lambda url, timeout: mock_resp)
    assert sh.sidecar_health() is None
    assert sh.is_sidecar_reachable() is False


def test_sidecar_health_missing_ok(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "bad"}

    monkeypatch.setattr(sh.requests, "get", lambda url, timeout: mock_resp)
    assert sh.sidecar_health() is None


def test_call_sidecar_tool_connection_error(monkeypatch):
    import requests

    def boom(url, json, timeout):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(sh.requests, "post", boom)

    with pytest.raises(RuntimeError, match="Sidecar not running"):
        sh.call_sidecar_tool("run_python", {}, timeout=5)
