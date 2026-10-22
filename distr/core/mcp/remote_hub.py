"""Hub proxy used by spawned processes (agent worker) so MCP servers run once.

The web-server process owns the real :class:`MCPClientHub` and serves it at
``/api/internal/mcp/hub/*`` (internal-token protected, loopback). This proxy has
the same read/call surface the adapter and laya_runtime use, and never spawns.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Any, Callable

from distr.core.mcp.client import MCPTransportError
from distr.core.mcp.config import MCPConfigDocument

logger = logging.getLogger(__name__)

STATE_PATH = "/api/internal/mcp/hub/state"
CALL_PATH = "/api/internal/mcp/hub/call"
POLL_INTERVAL_SEC = 20.0
STATE_CACHE_SEC = 5.0


def _default_transport(method: str, path: str, body: dict | None, timeout: float) -> dict:
    from distr.core.web_runtime import get_local_web_base_url, internal_api_headers

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(get_local_web_base_url() + path, data=data, method=method,
                                 headers=internal_api_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


class RemoteMCPHub:
    def __init__(self, transport: Callable[[str, str, dict | None, float], dict] | None = None) -> None:
        self._transport = transport or _default_transport
        self._lock = threading.Lock()
        self._state: dict[str, list[dict[str, Any]]] = {}
        self._state_at = 0.0

    # -- state -------------------------------------------------------------
    def refresh(self, *, force: bool = False) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            if not force and time.monotonic() - self._state_at < STATE_CACHE_SEC:
                return self._state
        try:
            raw = self._transport("GET", STATE_PATH, None, 15.0)
            servers = raw.get("servers") if isinstance(raw, dict) else None
            state = {
                str(s["name"]): list(s.get("tools") or [])
                for s in (servers or []) if isinstance(s, dict) and s.get("name")
            }
        except Exception as e:
            logger.debug("remote MCP hub unreachable: %s", e)
            state = {}
        with self._lock:
            self._state, self._state_at = state, time.monotonic()
            return state

    def signature(self) -> tuple:
        st = self.refresh(force=True)
        return tuple(sorted((k, tuple(sorted(str(t.get("name")) for t in v))) for k, v in st.items()))

    # -- MCPClientHub-compatible surface ----------------------------------
    @property
    def config_path(self) -> str:
        return "remote"

    def load_and_apply(self) -> MCPConfigDocument:
        self.refresh(force=True)
        return MCPConfigDocument()

    def apply_document(self, _doc: MCPConfigDocument) -> None:
        return None

    def tick_reconnect(self, now: float | None = None) -> None:
        return None

    def disconnect(self, _server_name: str) -> None:
        return None

    def disconnect_all(self) -> None:
        return None

    def get_session(self, _server_name: str):
        return None

    def enabled_connected_servers(self) -> list[str]:
        return sorted(self.refresh())

    def is_connected(self, server_name: str) -> bool:
        return server_name in self.refresh()

    def list_tools(self, server_name: str) -> list[dict[str, Any]]:
        st = self.refresh()
        if server_name not in st:
            raise MCPTransportError(f"unknown MCP server {server_name!r}")
        return st[server_name]

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        try:
            raw = self._transport("POST", CALL_PATH,
                                  {"server": server_name, "tool": tool_name, "arguments": arguments or {}},
                                  120.0)
        except Exception as e:
            raise MCPTransportError(f"remote MCP hub call failed: {e}") from e
        if not isinstance(raw, dict) or not raw.get("ok"):
            raise MCPTransportError(str((raw or {}).get("error") or "remote MCP call failed"))
        return raw.get("result") or {}


class RemoteHubPoller:
    """Re-registers adapter tools when the owner's server/tool set changes."""

    def __init__(self, hub: RemoteMCPHub, adapter, interval: float = POLL_INTERVAL_SEC) -> None:
        self._hub, self._adapter, self._interval = hub, adapter, interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: tuple | None = None

    def poll_once(self) -> bool:
        sig = self._hub.signature()
        if sig == self._last:
            return False
        self._last = sig
        try:
            self._adapter.reconcile()
        except Exception:
            logger.exception("remote MCP adapter reconcile failed")
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.debug("remote MCP poll failed", exc_info=True)
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="mcp-remote-hub-poll", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
