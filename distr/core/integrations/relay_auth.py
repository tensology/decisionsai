"""Shared auth for decisions.tensology.com relay REST calls."""

from __future__ import annotations

import logging
import os
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
