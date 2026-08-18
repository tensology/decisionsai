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


def call_sidecar_tool(tool: str, params: dict, *, timeout: float = 120) -> dict:
    """
    ``POST /tool/{tool}`` with JSON body.

    If the sidecar is down or refuses the call, window/screenshot tools run in the
    Decisions process so TCC grants attach to the app instead of decisionsai-sidecar.
    """
    url = f"{sidecar_base_url()}/tool/{tool}"
    try:
        resp = requests.post(url, json=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Sidecar returned non-object JSON for {tool!r}")
        return data
    except Exception as exc:
        local = _run_in_decisions_process(tool, params)
        if local is not None:
            logger.info("desktop tool %s ran in Decisions (sidecar: %s)", tool, exc)
            return local
        if isinstance(exc, requests.ConnectionError):
            raise RuntimeError(
                "Sidecar not running. Start the sidecar (bundled with the app) or check "
                f"{sidecar_base_url()}/health."
            ) from exc
        if isinstance(exc, requests.HTTPError):
            body = ""
            try:
                body = (exc.response.text or "")[:500]
            except Exception:
                pass
            raise RuntimeError(f"Sidecar HTTP error ({tool}): {exc} {body}") from exc
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"Sidecar call failed ({tool}): {exc}") from exc


def _run_in_decisions_process(tool: str, params: dict) -> dict[str, Any] | None:
    from distr.core.agent.tools.input.desktop_local import LOCAL_DESKTOP_TOOLS, run_local_desktop_tool

    if tool not in LOCAL_DESKTOP_TOOLS:
        return None
    return run_local_desktop_tool(tool, params)
