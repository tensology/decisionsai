"""Persist and validate ``mcp_config.json`` (R5) — atomic writes, optional lock, hot-reload-friendly."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    import fcntl  # Unix advisory locks for multi-process writers (R5).
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]


@dataclass(frozen=True)
class MCPServerConfig:
    """One MCP server entry."""

    name: str
    enabled: bool = True
    transport: str = "stdio"  # "stdio" | "sse"
    command: tuple[str, ...] = ()
    env: frozenset[tuple[str, str]] = frozenset()
    url: str = ""
    headers: frozenset[tuple[str, str]] = frozenset()

    def fingerprint(self) -> tuple[Any, ...]:
        """Stable tuple for hot-reload: reconnect when this changes for a given name."""
        return (
            self.enabled,
            self.transport,
            self.command,
            self.env,
            self.url.strip(),
            self.headers,
        )


@dataclass
class MCPConfigDocument:
    servers: tuple[MCPServerConfig, ...] = ()

    def by_name(self) -> dict[str, MCPServerConfig]:
        return {s.name: s for s in self.servers}


def default_config_path() -> Path:
    from distr.core.paths import MCP_CONFIG_PATH

    return Path(MCP_CONFIG_PATH)


def _normalize_env(raw: Any) -> frozenset[tuple[str, str]]:
    if not isinstance(raw, dict):
        return frozenset()
    out: list[tuple[str, str]] = []
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str):
            out.append((k, v))
    return frozenset(sorted(out))


def _normalize_headers(raw: Any) -> frozenset[tuple[str, str]]:
    return _normalize_env(raw)


def _parse_server(obj: Any) -> MCPServerConfig | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.warning("MCP config entry skipped: missing name")
        return None
    transport = obj.get("transport", "stdio")
    if transport not in ("stdio", "sse"):
        logger.warning("MCP server %r: invalid transport %r", name, transport)
        return None
    enabled = bool(obj.get("enabled", True))
    cmd_raw = obj.get("command", [])
    command: tuple[str, ...] = ()
    if isinstance(cmd_raw, list):
        command = tuple(str(x) for x in cmd_raw if isinstance(x, (str, int, float)))
    url = ""
    if isinstance(obj.get("url"), str):
        url = obj["url"]
    if transport == "stdio" and len(command) == 0:
        logger.warning("MCP server %r: stdio requires non-empty command", name)
        return None
    if transport == "sse" and not url.strip():
        logger.warning("MCP server %r: sse requires url", name)
        return None
    return MCPServerConfig(
        name=name.strip(),
        enabled=enabled,
        transport=transport,
        command=command,
        env=_normalize_env(obj.get("env")),
        url=url.strip(),
        headers=_normalize_headers(obj.get("headers")),
    )


def parse_config_dict(data: dict[str, Any]) -> MCPConfigDocument:
    raw_servers = data.get("servers", [])
    if not isinstance(raw_servers, list):
        logger.warning("MCP config servers not a list — ignoring")
        return MCPConfigDocument()
    servers: list[MCPServerConfig] = []
    seen: set[str] = set()
    for item in raw_servers:
        cfg = _parse_server(item)
        if cfg is None:
            continue
        if cfg.name in seen:
            logger.warning("duplicate MCP server name %r — skipping", cfg.name)
            continue
        seen.add(cfg.name)
        servers.append(cfg)
    return MCPConfigDocument(servers=tuple(servers))


def load_mcp_config(path: Path | None = None) -> MCPConfigDocument:
    """Load MCP config; malformed file → empty document (R5)."""
    p = path or default_config_path()
    if not p.is_file():
        return MCPConfigDocument()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        logger.exception("MCP config read failed: %s", p)
        return MCPConfigDocument()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.exception("Malformed mcp_config.json at %s — using empty config", p)
        return MCPConfigDocument()
    if not isinstance(data, dict):
        logger.error("MCP config root must be object — using empty config")
        return MCPConfigDocument()
    return parse_config_dict(data)


def document_to_dict(doc: MCPConfigDocument) -> dict[str, Any]:
    servers: list[dict[str, Any]] = []
    for s in doc.servers:
        entry: dict[str, Any] = {
            "name": s.name,
            "enabled": s.enabled,
            "transport": s.transport,
        }
        if s.transport == "stdio":
            entry["command"] = list(s.command)
            if s.env:
                entry["env"] = dict(sorted(s.env))
        else:
            entry["url"] = s.url
            if s.headers:
                entry["headers"] = dict(sorted(s.headers))
        servers.append(entry)
    return {"servers": servers}


class _FileLock:
    """Cross-process lock using flock when available; otherwise no-op (atomic write still applies)."""

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o644)
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_EX)

    def release(self) -> None:
        if self._fd is None:
            return
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def save_mcp_config(
    doc: MCPConfigDocument,
    path: Path | None = None,
    *,
    fsync_dir: bool = True,
) -> None:
    """Atomically write MCP JSON (temp + os.replace) with optional flock (R5)."""
    seen: set[str] = set()
    for s in doc.servers:
        if s.name in seen:
            raise ValueError(f"duplicate MCP server name: {s.name!r}")
        seen.add(s.name)
    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = _FileLock(p.with_suffix(p.suffix + ".lock"))
    lock.acquire()
    try:
        payload = json.dumps(document_to_dict(doc), indent=2, sort_keys=True)
        data = payload.encode("utf-8")
        fd, tmp = tempfile.mkstemp(
            dir=str(p.parent), prefix=".mcp_config_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            if fsync_dir:
                try:
                    dir_fd = os.open(str(p.parent), os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    finally:
        lock.release()


def server_names_to_reconnect(
    previous: MCPConfigDocument, current: MCPConfigDocument
) -> tuple[set[str], set[str], set[str]]:
    """Return (removed, added_or_changed, unchanged_connected_ok).

    Reconnect targets = removed ∪ added_or_changed (by fingerprint).
    """
    prev = previous.by_name()
    cur = current.by_name()
    removed = set(prev) - set(cur)
    changed: set[str] = set()
    for name, cfg in cur.items():
        old = prev.get(name)
        if old is None or old.fingerprint() != cfg.fingerprint():
            changed.add(name)
    return removed, changed, set(prev) & set(cur) - changed


class MCPConfigWatcher:
    """Poll ``st_mtime`` and invoke callback when the config file changes."""

    def __init__(
        self,
        path: Path | None = None,
        on_change: Callable[[], None] | None = None,
        poll_interval: float = 2.0,
    ) -> None:
        self.path = path or default_config_path()
        self.on_change = on_change
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mtime: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mcp-config-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        try:
            self._mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self._mtime = None
        except OSError:
            self._mtime = None
        while not self._stop.wait(self.poll_interval):
            try:
                st = self.path.stat()
            except FileNotFoundError:
                mtime = None
            except OSError:
                logger.debug("MCP config watcher stat failed", exc_info=True)
                continue
            else:
                mtime = st.st_mtime_ns
            if mtime != self._mtime:
                self._mtime = mtime
                if self.on_change:
                    try:
                        self.on_change()
                    except Exception:
                        logger.exception("MCP config on_change failed")
