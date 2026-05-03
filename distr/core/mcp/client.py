"""MCP transport lifecycle per server: stdio (built-in JSON-RPC) + optional SDK streamable HTTP."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from distr.core.mcp.config import (
    MCPConfigDocument,
    MCPServerConfig,
    default_config_path,
    load_mcp_config,
    server_names_to_reconnect,
)
from distr.core.mcp.jsonrpc_stdio import MCPStdioRpcError, StdioJsonRpcSession

if TYPE_CHECKING:
    from distr.core.events.bus import EventBus

logger = logging.getLogger(__name__)

RECONNECT_MAX_ATTEMPTS = 5
RECONNECT_INTERVAL_SEC = 30.0


class MCPTransportError(RuntimeError):
    """Transport-level MCP failure (disconnect, RPC error)."""


class MCPServerSession:
    """One logical MCP server: ``connect_stdio`` / ``connect_sse``, ``list_tools``, ``call_tool``."""

    def __init__(self, name: str, bus: EventBus | None = None) -> None:
        self.name = name
        self._bus = bus
        self._stdio: StdioJsonRpcSession | None = None
        self._streamable: Any = None
        self._lock = threading.Lock()
        self.reconnect_failures = 0
        self.next_retry_monotonic = 0.0

    def reset_backoff(self) -> None:
        self.reconnect_failures = 0
        self.next_retry_monotonic = 0.0

    def note_failure(self) -> None:
        self.reconnect_failures += 1
        self.next_retry_monotonic = time.monotonic() + RECONNECT_INTERVAL_SEC

    def can_retry(self, now: float) -> bool:
        if self.reconnect_failures >= RECONNECT_MAX_ATTEMPTS:
            return False
        return now >= self.next_retry_monotonic

    def connect_stdio(self, cfg: MCPServerConfig) -> None:
        self.disconnect()
        env_map = os.environ.copy()
        env_map.update(dict(cfg.env))
        self._stdio = StdioJsonRpcSession(list(cfg.command), env=env_map)
        self._stdio.start()
        self.reset_backoff()
        self._publish_connected()

    def connect_sse(self, cfg: MCPServerConfig) -> None:
        from distr.core.mcp.streamable_sdk import StreamableSdkSession, mcp_sdk_available

        self.disconnect()
        if not mcp_sdk_available():
            raise ImportError(
                "MCP streamable/SSE URL transport requires the `mcp` Python package "
                "(supported Python versions per upstream)."
            )
        self._streamable = StreamableSdkSession(cfg.url, dict(cfg.headers))
        self._streamable.start()
        self.reset_backoff()
        self._publish_connected()

    def disconnect(self) -> None:
        with self._lock:
            had = self.is_connected()
            if self._stdio:
                self._stdio.close()
                self._stdio = None
            if self._streamable:
                try:
                    self._streamable.stop()
                except Exception:
                    logger.debug("MCP streamable stop failed", exc_info=True)
                self._streamable = None
            if had:
                self._publish_disconnected()

    def is_connected(self) -> bool:
        if self._stdio is not None:
            return self._stdio.is_alive()
        if self._streamable is not None:
            try:
                return bool(self._streamable.is_alive())
            except Exception:
                return False
        return False

    def list_tools(self) -> list[dict[str, Any]]:
        try:
            if self._stdio:
                raw = self._stdio.list_tools()
                tools = raw.get("tools") if isinstance(raw, dict) else None
                return tools if isinstance(tools, list) else []
            if self._streamable:
                raw = self._streamable.list_tools(timeout=60.0)
                tools = raw.get("tools") if isinstance(raw, dict) else None
                return tools if isinstance(tools, list) else []
        except (BrokenPipeError, OSError, MCPStdioRpcError) as e:
            logger.warning("MCP list_tools failed (%s): %s", self.name, e)
            self._mark_dead()
            raise MCPTransportError(str(e)) from e
        raise MCPTransportError("not connected")

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        try:
            if self._stdio:
                return self._stdio.call_tool(tool_name, arguments)
            if self._streamable:
                return self._streamable.call_tool(tool_name, arguments, timeout=60.0)
        except (BrokenPipeError, OSError, MCPStdioRpcError) as e:
            logger.warning("MCP call_tool failed (%s/%s): %s", self.name, tool_name, e)
            self._mark_dead()
            raise MCPTransportError(str(e)) from e
        raise MCPTransportError("not connected")

    def _mark_dead(self) -> None:
        with self._lock:
            if self._stdio:
                try:
                    self._stdio.close()
                except Exception:
                    pass
                self._stdio = None
            if self._streamable:
                try:
                    self._streamable.stop()
                except Exception:
                    pass
                self._streamable = None
        self._publish_disconnected()
        self.note_failure()

    def _publish_connected(self) -> None:
        if self._bus:
            from distr.core.events.types import MCP_SERVER_CONNECTED

            self._bus.publish(MCP_SERVER_CONNECTED, {"server": self.name})

    def _publish_disconnected(self) -> None:
        if self._bus:
            from distr.core.events.types import MCP_SERVER_DISCONNECTED

            self._bus.publish(MCP_SERVER_DISCONNECTED, {"server": self.name})


class MCPClientHub:
    """Loads config, tracks sessions, applies hot-reload diffs, schedules reconnects (R5)."""

    def __init__(
        self, bus: EventBus | None = None, config_path: str | Path | None = None
    ):
        self._bus = bus
        self._path = Path(config_path) if config_path is not None else default_config_path()
        self._document = MCPConfigDocument()
        self._sessions: dict[str, MCPServerSession] = {}
        self._lock = threading.RLock()

    @property
    def config_path(self) -> str:
        return str(self._path)

    def load_and_apply(self) -> MCPConfigDocument:
        doc = load_mcp_config(self._path)
        self.apply_document(doc)
        return doc

    def apply_document(self, doc: MCPConfigDocument) -> None:
        with self._lock:
            removed, changed, _ = server_names_to_reconnect(self._document, doc)
            drop = removed | changed
            for name in drop:
                sess = self._sessions.pop(name, None)
                if sess:
                    sess.disconnect()
                    sess.reset_backoff()
            self._document = doc
            for cfg in doc.servers:
                if not cfg.enabled:
                    sess = self._sessions.pop(cfg.name, None)
                    if sess:
                        sess.disconnect()
                    continue
                if cfg.name in drop or cfg.name not in self._sessions:
                    self._connect_cfg(cfg, force_reset_backoff=True)

    def _connect_cfg(self, cfg: MCPServerConfig, *, force_reset_backoff: bool) -> None:
        prev = self._sessions.pop(cfg.name, None)
        if prev:
            prev.disconnect()
        sess = MCPServerSession(cfg.name, self._bus)
        try:
            if cfg.transport == "stdio":
                sess.connect_stdio(cfg)
            else:
                sess.connect_sse(cfg)
            self._sessions[cfg.name] = sess
        except Exception:
            logger.exception("MCP connect failed for server %r", cfg.name)
            sess.disconnect()
            if force_reset_backoff:
                sess.reset_backoff()
            sess.note_failure()
            self._sessions[cfg.name] = sess

    def tick_reconnect(self, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            for cfg in self._document.servers:
                if not cfg.enabled:
                    continue
                sess = self._sessions.get(cfg.name)
                if sess is None:
                    sess = MCPServerSession(cfg.name, self._bus)
                    self._sessions[cfg.name] = sess
                if sess.is_connected():
                    continue
                if not sess.can_retry(now):
                    continue
                try:
                    if cfg.transport == "stdio":
                        sess.connect_stdio(cfg)
                    else:
                        sess.connect_sse(cfg)
                except Exception:
                    logger.warning(
                        "MCP reconnect failed for %r (attempt %s/%s)",
                        cfg.name,
                        sess.reconnect_failures + 1,
                        RECONNECT_MAX_ATTEMPTS,
                        exc_info=True,
                    )
                    sess.note_failure()

    def disconnect(self, server_name: str) -> None:
        with self._lock:
            sess = self._sessions.pop(server_name, None)
            if sess:
                sess.disconnect()

    def disconnect_all(self) -> None:
        with self._lock:
            for sess in self._sessions.values():
                sess.disconnect()
            self._sessions.clear()

    def enabled_connected_servers(self) -> list[str]:
        """Names of enabled config entries that currently have a live session."""
        with self._lock:
            out: list[str] = []
            for cfg in self._document.servers:
                if not cfg.enabled:
                    continue
                sess = self._sessions.get(cfg.name)
                if sess is not None and sess.is_connected():
                    out.append(cfg.name)
            return out

    def get_session(self, server_name: str) -> MCPServerSession | None:
        return self._sessions.get(server_name)

    def is_connected(self, server_name: str) -> bool:
        sess = self._sessions.get(server_name)
        return bool(sess and sess.is_connected())

    def list_tools(self, server_name: str) -> list[dict[str, Any]]:
        sess = self._sessions.get(server_name)
        if not sess:
            raise MCPTransportError(f"unknown MCP server {server_name!r}")
        return sess.list_tools()

    def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        sess = self._sessions.get(server_name)
        if not sess:
            raise MCPTransportError(f"unknown MCP server {server_name!r}")
        return sess.call_tool(tool_name, arguments)
