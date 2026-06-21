"""Tests for friendly Telegram offline logging helpers."""

from __future__ import annotations

import socket

import pytest

from distr.core.integrations.telegram.manager import (
    friendly_telegram_connect_error,
    friendly_telegram_immediate_close_reason,
    friendly_telegram_socket_error,
    relay_endpoint_label,
)


def test_relay_endpoint_label_strips_scheme():
    assert relay_endpoint_label("wss://www.decisionsai.net/ws/telegram") == "www.decisionsai.net"


@pytest.mark.parametrize(
    "message,expected_fragment",
    [
        ("Failed to resolve 'decisionsai.net'", "check internet or DNS"),
        ("[Errno 8] nodename nor servname provided, or not known", "check internet or DNS"),
        ("Can't assign requested address", "network unavailable"),
        ("Connection reset by peer", "closed the connection"),
        ("HTTPSConnectionPool(host='x'): Read timed out.", "timed out"),
    ],
)
def test_friendly_telegram_connect_error(message, expected_fragment):
    reason = friendly_telegram_connect_error(
        ConnectionError(message),
        endpoint="decisionsai.net",
    )
    assert expected_fragment in reason


@pytest.mark.parametrize(
    "err_str,expected_fragment",
    [
        ("Host not found", "check internet or DNS"),
        ("Can't assign requested address", "check internet or DNS"),
        ("The remote host closed the connection", "dropped the connection"),
        ("The TLS/SSL connection has been closed", "secure connection"),
    ],
)
def test_friendly_telegram_socket_error(err_str, expected_fragment):
    reason = friendly_telegram_socket_error(
        err_str,
        endpoint="www.decisionsai.net",
    )
    assert expected_fragment in reason


def test_friendly_telegram_immediate_close_reason():
    reason = friendly_telegram_immediate_close_reason(endpoint="www.decisionsai.net")
    assert "accepted the session token" in reason
    assert "stale" in reason or "unhealthy" in reason
