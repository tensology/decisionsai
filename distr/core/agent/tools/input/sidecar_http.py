from __future__ import annotations

"""
Local sidecar HTTP client (R23).

Python tools talk to the Go sidecar on ``127.0.0.1:DECISIONSAI_SIDECAR_HTTP_PORT`` (default 11435).
``GET /health`` returns HTTP 200 with JSON ``ok: true`` plus ``wire_version``, ``os``, ``tools``, etc.
"""

# Must stay aligned with ``sidecarWireVersion`` in ``DecisionsAI/sidecar/main.go`` and the relay
# ``SIDECAR_WIRE_VERSION_MAX_SUPPORTED`` in ``www.decisionsai.net/backend/app/main.py``.
SIDECAR_WIRE_VERSION = 1

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def sidecar_http_port() -> int:
    return int(os.environ.get("DECISIONSAI_SIDECAR_HTTP_PORT", "11435"))


def sidecar_base_url() -> str:
    return f"http://127.0.0.1:{sidecar_http_port()}"


def sidecar_health(timeout: float = 2.0) -> dict[str, Any] | None:
    """
    Probe ``GET /health``. Returns the decoded JSON body when ``ok`` is truthy, else ``None``.
    """
    try:
        r = requests.get(f"{sidecar_base_url()}/health", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, dict) and data.get("ok"):
            return data
        return None
    except Exception as e:
        logger.debug("sidecar health check failed: %s", e)
        return None


def is_sidecar_reachable(timeout: float = 2.0) -> bool:
    return sidecar_health(timeout=timeout) is not None


def call_sidecar_tool(tool: str, params: dict, *, timeout: int = 120) -> dict:
    """
    ``POST /tool/{tool}`` with JSON body. Raises ``RuntimeError`` on connection errors or HTTP failures.
    """
    url = f"{sidecar_base_url()}/tool/{tool}"
    try:
        resp = requests.post(url, json=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Sidecar returned non-object JSON for {tool!r}")
        return data
    except requests.ConnectionError as e:
        raise RuntimeError(
            "Sidecar not running. Start the sidecar (bundled with the app) or check "
            f"{sidecar_base_url()}/health."
        ) from e
    except requests.HTTPError as e:
        body = ""
        try:
            body = (e.response.text or "")[:500]
        except Exception:
            pass
        raise RuntimeError(f"Sidecar HTTP error ({tool}): {e} {body}") from e
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Sidecar call failed ({tool}): {e}") from e
