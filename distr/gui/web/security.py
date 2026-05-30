"""
Security helpers for DecisionsAI web surfaces.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import socket
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from starlette.requests import Request
from starlette.websockets import WebSocket

INTERNAL_AUTH_HEADER = "X-DecisionsAI-Internal-Token"
INTERNAL_AUTH_QUERY_PARAM = "internal_token"

ALLOWED_LOCAL_ORIGINS = [
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://[::1]:8765",
]

_runtime_internal_token = secrets.token_urlsafe(32)


def get_internal_api_token() -> str:
    """Return the internal API token from env or runtime-generated value."""
    env_token = (os.getenv("DECISIONSAI_INTERNAL_API_TOKEN") or "").strip()
    return env_token or _runtime_internal_token


def _extract_host(origin: str) -> Optional[str]:
    try:
        parsed = urlparse(origin)
        return (parsed.hostname or "").strip().lower() or None
    except Exception:
        return None


def is_allowed_local_origin(origin: Optional[str]) -> bool:
    """Only allow localhost/loopback origins."""
    if not origin:
        return False
    host = _extract_host(origin)
    return host in {"localhost", "127.0.0.1", "::1"}


def _request_token(req: Request) -> str:
    return (req.headers.get(INTERNAL_AUTH_HEADER) or req.query_params.get(INTERNAL_AUTH_QUERY_PARAM) or "").strip()


def _websocket_token(ws: WebSocket) -> str:
    qs_token = (ws.query_params.get(INTERNAL_AUTH_QUERY_PARAM) if ws.query_params else "") or ""
    return (ws.headers.get(INTERNAL_AUTH_HEADER) or qs_token or "").strip()


def require_internal_token_request(req: Request) -> None:
    """Enforce internal token auth for sensitive HTTP routes."""
    expected = get_internal_api_token()
    received = _request_token(req)
    if not expected or not secrets.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def websocket_has_valid_internal_token(ws: WebSocket) -> bool:
    expected = get_internal_api_token()
    received = _websocket_token(ws)
    return bool(expected and received and secrets.compare_digest(received, expected))


def mask_secret(value: Optional[str]) -> str:
    """Return a redacted string while preserving minimal context."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 6:
        return "*" * len(raw)
    return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


def redact_thirdparty_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Build a safe response for /api/thirdparty without plaintext secrets."""
    secret_fields = [
        "assemblyai_key",
        "openai_key",
        "anthropic_key",
        "cursor_key",
        "elevenlabs_key",
        "openrouter_key",
        "groq_key",
        "kilo_key",
        "gemini_key",
        "masko_key",
    ]
    response: Dict[str, Any] = {
        "ollama_url": settings.get("ollama_url", "http://localhost:11434/"),
        "assemblyai_enabled": settings.get("assemblyai_enabled", False),
        "openai_enabled": settings.get("openai_enabled", False),
        "anthropic_enabled": settings.get("anthropic_enabled", False),
        "cursor_enabled": settings.get("cursor_enabled", False),
        "elevenlabs_enabled": settings.get("elevenlabs_enabled", False),
        "openrouter_enabled": settings.get("openrouter_enabled", False),
        "groq_enabled": settings.get("groq_enabled", False),
        "kilo_enabled": settings.get("kilo_enabled", False),
        "gemini_enabled": settings.get("gemini_enabled", False),
        "masko_enabled": settings.get("masko_enabled", False),
    }
    for field in secret_fields:
        raw = (settings.get(field) or "").strip()
        response[field] = ""
        response[f"{field}_set"] = bool(raw)
    return response


def redact_connected_account(account: Dict[str, Any]) -> Dict[str, Any]:
    """Strip credential fields from account rows."""
    provider = (account.get("provider") or "").strip().lower()
    base = {
        "provider": provider,
        "name": account.get("name"),
        "is_valid": bool(account.get("is_valid", False)),
        "created_at": account.get("created_at"),
    }
    if provider == "jira":
        base["server_url"] = account.get("server_url")
        base["email_masked"] = mask_secret(account.get("email"))
        base["has_api_token"] = bool((account.get("api_token") or "").strip())
    elif provider == "trello":
        base["api_key_masked"] = mask_secret(account.get("api_key"))
        base["has_api_token"] = bool((account.get("api_token") or "").strip())
    elif provider == "discord_bot":
        base["bot_token_masked"] = mask_secret(account.get("bot_token"))
        base["has_bot_token"] = bool((account.get("bot_token") or "").strip())
    elif provider == "slack_app":
        base["bot_token_masked"] = mask_secret(account.get("bot_token"))
        base["signing_secret_masked"] = mask_secret(account.get("signing_secret"))
        base["has_bot_token"] = bool((account.get("bot_token") or "").strip())
        base["has_signing_secret"] = bool((account.get("signing_secret") or "").strip())
    return base


def _is_blocked_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_safe_outbound_url(url: str) -> str:
    """
    Validate outbound URL against SSRF to local/private targets.
    Returns normalized URL.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError("URL host is required")
    if host.lower() in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Localhost targets are not allowed")
    try:
        addr_info = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve host: {host}") from exc
    ips = {entry[4][0] for entry in addr_info if entry and entry[4]}
    for ip_value in ips:
        try:
            if _is_blocked_ip(ip_value):
                raise ValueError("Private or local network targets are not allowed")
        except ValueError:
            raise
        except Exception:
            continue
    return parsed.geturl().rstrip("/")


class SimpleRateLimiter:
    """In-memory sliding-window rate limiter keyed by string."""

    def __init__(self) -> None:
        self._events: Dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        threshold = now - float(window_seconds)
        with self._lock:
            bucket = self._events.setdefault(key, [])
            bucket[:] = [ts for ts in bucket if ts >= threshold]
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


rate_limiter = SimpleRateLimiter()
