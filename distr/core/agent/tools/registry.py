"""Central registry for tool instances with source tracking (native, MCP, skills)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """One bound tool plus provenance for unregister_by_source and MCP lifecycle."""

    tool: BaseTool
    source: str  # e.g. "native", "mcp:server_name", "skill:skill_name"
    available: bool
    registered_at: datetime


class ToolRegistry:
    """Thread-safe registry. Duplicate ``tool.name`` raises ``ValueError``."""

    __slots__ = ("_lock", "_by_name")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_name: dict[str, RegisteredTool] = {}

    def register(
        self,
        tool: BaseTool,
        source: str,
        *,
        available: bool = True,
    ) -> None:
        """Register ``tool`` under ``tool.name``."""
        name = tool.name
        with self._lock:
            if name in self._by_name:
                raise ValueError(
                    f"Duplicate tool name {name!r} (existing source="
                    f"{self._by_name[name].source!r}, new source={source!r})"
                )
            self._by_name[name] = RegisteredTool(
                tool=tool,
                source=source,
                available=available,
                registered_at=_utc_now(),
            )

    def unregister(self, name: str) -> bool:
        """Remove registration for ``name``. Returns True if something was removed."""
        with self._lock:
            return self._by_name.pop(name, None) is not None

    def unregister_by_source(self, source: str) -> int:
        """Drop every tool registered with ``source``. Returns count removed."""
        with self._lock:
            to_drop = [n for n, rt in self._by_name.items() if rt.source == source]
            for n in to_drop:
                del self._by_name[n]
            return len(to_drop)

    def get_by_name(self, name: str) -> BaseTool | None:
        """Return the tool if registered and available."""
        with self._lock:
            rt = self._by_name.get(name)
            if rt is None or not rt.available:
                return None
            return rt.tool

    def get_record(self, name: str) -> RegisteredTool | None:
        """Return the full record (including unavailable tools)."""
        with self._lock:
            return self._by_name.get(name)

    def get_all(self) -> list[BaseTool]:
        """All tools that are currently marked available."""
        with self._lock:
            return [rt.tool for rt in self._by_name.values() if rt.available]

    def get_by_source(self, source: str) -> list[BaseTool]:
        """Available tools registered under ``source``."""
        with self._lock:
            return [
                rt.tool
                for rt in self._by_name.values()
                if rt.source == source and rt.available
            ]

    def iter_records(self) -> list[RegisteredTool]:
        """Snapshot of all records (for diagnostics)."""
        with self._lock:
            return list(self._by_name.values())

    def search(self, query: str) -> list[BaseTool]:
        """Lowercase substring match on tool name or description (available only)."""
        q = (query or "").lower().strip()
        if not q:
            return self.get_all()
        out: list[BaseTool] = []
        with self._lock:
            for rt in self._by_name.values():
                if not rt.available:
                    continue
                t = rt.tool
                desc = getattr(t, "description", "") or ""
                if q in t.name.lower() or q in desc.lower():
                    out.append(t)
        return out

    def count(self) -> int:
        """Total registered names (available or not)."""
        with self._lock:
            return len(self._by_name)

    def set_available(self, name: str, available: bool) -> bool:
        """Toggle availability (e.g. MCP disconnect). Returns False if unknown name."""
        with self._lock:
            rt = self._by_name.get(name)
            if rt is None:
                return False
            if rt.available == available:
                return True
            self._by_name[name] = RegisteredTool(
                tool=rt.tool,
                source=rt.source,
                available=available,
                registered_at=rt.registered_at,
            )
            return True


_global_registry: ToolRegistry | None = None
_registry_lock = threading.Lock()


def get_tool_registry() -> ToolRegistry:
    """Process-wide default registry (native tools + future MCP/skills)."""
    global _global_registry
    with _registry_lock:
        if _global_registry is None:
            _global_registry = ToolRegistry()
        return _global_registry


def reset_tool_registry_for_tests() -> None:
    """Clear the default registry (tests only)."""
    global _global_registry
    with _registry_lock:
        _global_registry = ToolRegistry()
