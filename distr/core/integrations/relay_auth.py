"""Shared auth for decisions.tensology.com relay REST calls."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_env_loaded = False


def canonical_relay_url(url: str) -> str:
    """Avoid legacy-host redirects that turn authenticated POSTs into GETs."""
    parts = urlsplit(str(url or "").strip())
    if (parts.hostname or "").lower() not in {"decisionsai.net", "www.decisionsai.net"}:
        return str(url or "").strip()
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"decisions.tensology.com{port}", parts.path, parts.query, parts.fragment))


def _project_env_path() -> Path:
    try:
        from distr.core.plugins.paths import project_root

        return project_root() / ".env"
    except Exception:
        return Path(__file__).resolve().parents[3] / ".env"


def ensure_relay_env_loaded() -> None:
    """Load project .env once so RELAY_INTERNAL_TOKEN is visible to relay clients."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    env_path = _project_env_path()
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, _, value = raw.partition("=")
            key = key.strip()
            if not key:
                continue
            os.environ.setdefault(key, value.strip().strip("'\""))
    except OSError as exc:
        logger.debug("Could not read relay .env: %s", exc)


def relay_auth_headers(*, force_refresh: bool = False) -> dict[str, str]:
    """Headers for authenticated relay REST (internal token, else device identity Bearer)."""
    ensure_relay_env_loaded()
    from distr.core.integrations.telegram.utils import relay_internal_token

    token = relay_internal_token()
    if token:
        return {"X-Relay-Internal-Token": token}

    from distr.core.integrations.whatsapp.relay_client import relay_request_headers

    headers = relay_request_headers(force_refresh=force_refresh)
    if headers:
        return headers

    return {}


def relay_public_base() -> str:
    explicit = (os.environ.get("DECISIONSAI_RELAY_API_BASE") or "").strip()
    if explicit:
        return canonical_relay_url(explicit).rstrip("/")
    return "https://decisions.tensology.com"

# Transient nginx/upstream failures when minting a short-lived WebSocket JWT.
# Five attempts: immediate, then 1s, 2s, 4s, 8s.
WS_TOKEN_RETRY_STATUSES = frozenset({404, 502, 503})
WS_TOKEN_RETRY_DELAYS_S = (1, 2, 4, 8)


def should_retry_ws_token_status(status: int) -> bool:
    try:
        return int(status) in WS_TOKEN_RETRY_STATUSES
    except (TypeError, ValueError):
        return False


def ws_token_retry_sleep(seconds: float) -> None:
    """Sleep between ws-token retries. Tests monkeypatch this."""
    if seconds and seconds > 0:
        time.sleep(seconds)



# Clients refresh the relay JWT this many seconds before the exp claim.
RELAY_TOKEN_REFRESH_LEAD_S = 60
RELAY_TOKEN_REFRESH_MIN_DELAY_MS = 5_000
RELAY_TOKEN_REFRESH_FALLBACK_MS = 240_000


def relay_jwt_exp_iat(token: str) -> tuple[float | None, float | None]:
    """Return only the exp and iat unix timestamps from a JWT. Never logs the token."""
    try:
        parts = (token or "").split(".")
        if len(parts) < 2 or not parts[1]:
            return None, None
        payload = parts[1]
        pad = "=" * ((4 - len(payload) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + pad))
        if not isinstance(data, dict):
            return None, None
        exp = data.get("exp")
        iat = data.get("iat")
        return (
            float(exp) if exp is not None else None,
            float(iat) if iat is not None else None,
        )
    except Exception:
        return None, None


def relay_token_refresh_delay_ms(
    exp: float | None,
    *,
    now: float | None = None,
    lead_s: float = RELAY_TOKEN_REFRESH_LEAD_S,
) -> int:
    """Milliseconds until a pre-expiry refresh. Falls back if exp is missing."""
    if exp is None:
        return RELAY_TOKEN_REFRESH_FALLBACK_MS
    now_ts = time.time() if now is None else now
    return max(
        RELAY_TOKEN_REFRESH_MIN_DELAY_MS,
        int((float(exp) - now_ts - lead_s) * 1000),
    )
