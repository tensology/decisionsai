"""Process-wide MCP hub, config watcher, and tool adapter wiring."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from distr.core.events.bus import get_event_bus
from distr.core.mcp.client import MCPClientHub
from distr.core.mcp.config import MCPConfigWatcher, default_config_path

if TYPE_CHECKING:
    from distr.core.mcp.adapter import MCPToolAdapter

logger = logging.getLogger(__name__)

_hub: MCPClientHub | None = None
_adapter: MCPToolAdapter | None = None
_watcher: MCPConfigWatcher | None = None
_poller = None
# Serialises init: concurrent first callers (tool-cache warmup, laya_runtime,
# workflow agent threads) each used to build a hub and spawn every server; the
# loser's hub was dropped without disconnect, leaking its child processes.
_init_lock = threading.RLock()

HUB_MODE_ENV = "DECISIONS_MCP_HUB_MODE"  # auto (default) | local | remote


def _hub_mode() -> str:
    mode = (os.environ.get(HUB_MODE_ENV) or "auto").strip().lower()
    if mode in ("local", "remote"):
        return mode
    try:
        import multiprocessing

        # Spawned agent worker -> the parent (web server) process owns the hub.
        return "remote" if multiprocessing.parent_process() is not None else "local"
    except Exception:
        return "local"


def init_mcp_stack(config_path: str | Path | None = None) -> None:
    """Load MCP config, connect servers, attach adapter + hot-reload watcher.

    Idempotent and thread-safe. In a spawned worker (or ``DECISIONS_MCP_HUB_MODE=remote``)
    no servers are spawned: a :class:`RemoteMCPHub` relays to the owner process.
    """
    with _init_lock:
        if _hub is not None:
            return
        if config_path is None and _hub_mode() == "remote":
            _init_remote_stack()
            return
        _init_local_stack(config_path)


def _init_remote_stack() -> None:
    global _hub, _adapter, _poller

    from distr.core.mcp.adapter import MCPToolAdapter
    from distr.core.mcp.remote_hub import RemoteHubPoller, RemoteMCPHub

    bus = get_event_bus()
    hub = RemoteMCPHub()
    adapter = MCPToolAdapter(hub=hub, bus=bus)  # type: ignore[arg-type]
    adapter.attach()
    _hub, _adapter = hub, adapter  # type: ignore[assignment]
    poller = RemoteHubPoller(hub, adapter)
    poller.start()
    _poller = poller


def _init_local_stack(config_path: str | Path | None) -> None:
    global _hub, _adapter, _watcher

    from distr.core.mcp.adapter import MCPToolAdapter

    bus = get_event_bus()
    hub = MCPClientHub(bus=bus, config_path=config_path)
    hub.load_and_apply()
    adapter = MCPToolAdapter(hub=hub, bus=bus)
    adapter.attach()

    def _reload() -> None:
        try:
            hub.load_and_apply()
        except Exception:
            logger.exception("MCP config reload failed")
        try:
            adapter.reconcile()
        except Exception:
            logger.exception("MCP adapter reconcile after config reload failed")

    watch_path = Path(config_path) if config_path is not None else default_config_path()
    watcher = MCPConfigWatcher(path=watch_path, on_change=_reload)
    watcher.start()

    _hub = hub
    _adapter = adapter
    _watcher = watcher

    try:
        adapter.reconcile()
    except Exception:
        logger.exception("initial MCP adapter reconcile failed")


def tick_mcp_reconnect() -> None:
    if _hub is None:
        return
    try:
        _hub.tick_reconnect()
    except Exception:
        logger.debug("tick_mcp_reconnect failed", exc_info=True)


def get_mcp_hub() -> MCPClientHub | None:
    return _hub


def reset_mcp_runtime_for_tests() -> None:
    """Tear down MCP background threads (tests only)."""
    global _hub, _adapter, _watcher, _poller
    if _poller is not None:
        try:
            _poller.stop()
        except Exception:
            pass
        _poller = None
    if _watcher is not None:
        try:
            _watcher.stop()
        except Exception:
            pass
        _watcher = None
    if _adapter is not None:
        try:
            _adapter.detach()
        except Exception:
            pass
        _adapter = None
    if _hub is not None:
        try:
            _hub.disconnect_all()
        except Exception:
            pass
        _hub = None
