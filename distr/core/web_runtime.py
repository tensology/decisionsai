"""Resolve the local unified web server base URL and internal API auth."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import urllib.request
from typing import Any, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ponytail: login-shell startup (source ~/.zshrc, workon, etc.) can exceed 5s per command
STARTUP_TERMINAL_HTTP_TIMEOUT_SEC = 90.0


def get_local_web_base_url(default_port: int = 8765) -> str:
    port = (os.environ.get("DECISIONS_WEB_PORT") or "").strip()
    if port.isdigit():
        return f"http://127.0.0.1:{int(port)}"
    try:
        from distr.gui.web.server import get_unified_server

        server = get_unified_server()
        if server and getattr(server, "is_running", False):
            return server.get_url()
    except Exception:
        pass
    hosts = ("127.0.0.1", "localhost")
    for host in hosts:
        for port_num in range(8765, 8781):
            base = f"http://{host}:{port_num}"
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=0.25) as resp:
                    if resp.status == 200:
                        return base
            except Exception:
                continue
    return f"http://127.0.0.1:{default_port}"


def get_internal_api_token_for_local_web() -> str:
    """Token for agent/subprocess calls to POST /api/* on the local web server."""
    token = (os.getenv("DECISIONSAI_INTERNAL_API_TOKEN") or "").strip()
    if token:
        return token
    try:
        from distr.gui.web.security import get_internal_api_token

        token = (get_internal_api_token() or "").strip()
        if token:
            return token
    except Exception:
        pass
    try:
        base = get_local_web_base_url()
        with urllib.request.urlopen(f"{base}/settings", timeout=2.0) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        match = re.search(
            r'<meta name="decisionsai-internal-api-token" content="([^"]*)"',
            html,
        )
        return (match.group(1).strip() if match else "")
    except Exception:
        return ""


def internal_api_headers(content_type: str = "application/json") -> Dict[str, str]:
    from distr.gui.web.security import INTERNAL_AUTH_HEADER

    headers: Dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type
    token = get_internal_api_token_for_local_web()
    if token:
        headers[INTERNAL_AUTH_HEADER] = token
    return headers


def resolve_local_web_base_url() -> Optional[str]:
    """Best-effort base URL; None when health probe fails."""
    try:
        base = get_local_web_base_url()
        with urllib.request.urlopen(f"{base}/health", timeout=0.5) as resp:
            if resp.status == 200:
                return base.rstrip("/")
    except Exception:
        pass
    return None


def run_on_unified_server_loop(coro, *, timeout: float = STARTUP_TERMINAL_HTTP_TIMEOUT_SEC) -> Any:
    """Run a coroutine on the unified web server loop from a sync caller."""
    try:
        from distr.gui.web.server import get_unified_server

        server = get_unified_server()
        loop = getattr(server, "asyncio_loop", None) if server else None
        if loop is None or not loop.is_running():
            logger.warning(
                "run_on_unified_server_loop: web server loop unavailable (server=%s loop=%s)",
                bool(server),
                loop is not None,
            )
            return None
        try:
            running = asyncio.get_running_loop()
            if running is loop:
                # ponytail: sync-bridge from the server loop deadlocks on future.result().
                logger.warning(
                    "run_on_unified_server_loop: called from server loop — use await spawn_startup_shell_sessions instead",
                )
                return None
        except RuntimeError:
            pass
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    except Exception as exc:
        logger.warning("run_on_unified_server_loop failed: %s", exc)
        return None
