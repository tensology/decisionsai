"""Tests for local web runtime helpers."""

import os

from distr.core.web_runtime import get_internal_api_token_for_local_web, internal_api_headers
from distr.gui.web.security import INTERNAL_AUTH_HEADER


def test_internal_api_headers_uses_env_token(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "test-token-abc")
    headers = internal_api_headers()
    assert headers[INTERNAL_AUTH_HEADER] == "test-token-abc"
    assert headers["Content-Type"] == "application/json"


def test_get_internal_api_token_prefers_env(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "env-token")
    assert get_internal_api_token_for_local_web() == "env-token"
