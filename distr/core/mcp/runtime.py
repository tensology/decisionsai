"""Process-wide MCP hub, config watcher, and tool adapter wiring."""

from __future__ import annotations

import logging
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


def init_mcp_stack(config_path: str | Path | None = None) -> None:
    """Load MCP config, connect servers, attach adapter + hot-reload watcher."""
    global _hub, _adapter, _watcher

    if _hub is not None:
        return

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
    global _hub, _adapter, _watcher
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
