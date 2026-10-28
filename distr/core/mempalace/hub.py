"""MemPalace hub client for Decisions (stdlib only).

When a ``mempalace serve`` hub owns the Decisions palace, Decisions must talk to
it over HTTP MCP and never open the Chroma files itself (one writer per palace).

Discovery mirrors ``mempalace.server_registry`` (3.7+): the hub records
``~/.mempalace/server/<sha256(realpath(palace))[:24]>/serverinfo.json`` with its
pid/host/port, and an optional 0600 bearer token sits next to it as ``token``.
The token is read at call time and never logged.

Writes made while no hub is reachable are appended to a local 0600 JSONL
journal and replayed on the next successful hub call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AGENT_ID = os.environ.get("DECISIONS_MEMPALACE_AGENT_ID", "mac:decisions:decisionsai")
HUB_URL_ENV = "DECISIONS_MEMPALACE_HUB_URL"  # optional explicit override, e.g. http://127.0.0.1:8765
CALL_TIMEOUT_S = float(os.environ.get("DECISIONS_MEMPALACE_HUB_TIMEOUT", "20"))
JOURNAL_REPLAY_BATCH = 50

_lock = threading.Lock()
_replay_lock = threading.Lock()
_rpc_id = 0


def _canonical(palace_path: str) -> str:
    return os.path.abspath(os.path.realpath(os.path.expanduser(palace_path)))


def server_state_dir(palace_path: str) -> Path:
    key = hashlib.sha256(os.path.normcase(_canonical(palace_path)).encode("utf-8")).hexdigest()[:24]
    return Path.home() / ".mempalace" / "server" / key


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def discover(palace_path: str) -> dict[str, Any] | None:
    """Return ``{"base_url", "headers", "pid"}`` for a live hub, else None."""
    state = server_state_dir(palace_path)
    token = ""
    try:
        token = (state / "token").read_text(encoding="utf-8").strip()
    except OSError:
        token = os.environ.get("MEMPALACE_MCP_HTTP_TOKEN", "").strip()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    override = (os.environ.get(HUB_URL_ENV) or "").strip().rstrip("/")
    if override:
        return {"base_url": override, "headers": headers, "pid": None}
    try:
        info = json.loads((state / "serverinfo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not _pid_alive(info.get("pid")):
        return None
    if not isinstance(info.get("port"), int) or not info.get("host"):
        return None
    host = str(info["host"]).strip()
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return {
        "base_url": f"{info.get('scheme') or 'http'}://{host}:{info['port']}",
        "headers": headers,
        "pid": info.get("pid"),
        "read_only": bool(info.get("read_only")),
    }


def hub_is_up(palace_path: str) -> bool:
    return discover(palace_path) is not None


def hub_configured(palace_path: str) -> bool:
    """True once a hub has ever been set up for this palace (state dir exists).

    On such machines Decisions never writes Chroma directly: writes go to the
    hub, or to the journal while it is down. Machines with no hub keep the
    legacy in-process path.
    """
    if (os.environ.get(HUB_URL_ENV) or "").strip():
        return True
    return server_state_dir(palace_path).is_dir()


class HubError(RuntimeError):
    pass


class HubToolError(HubError):
    """The hub answered but rejected this call (retrying the same call won't help)."""


def call_tool(
    palace_path: str,
    name: str,
    arguments: dict[str, Any],
    *,
    hub: dict | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Call one MCP tool on the hub; return the decoded JSON payload.

    ``timeout`` overrides the 20s default. Callers on a latency-critical path
    (building the system prompt) must pass a short one: a hung hub otherwise
    stalled every single turn for the full timeout.
    """
    global _rpc_id
    hub = hub or discover(palace_path)
    if hub is None:
        raise HubError("no live MemPalace hub")
    with _lock:
        _rpc_id += 1
        rid = _rpc_id
    body = json.dumps(
        {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(f"{hub['base_url']}/mcp", data=body, headers=dict(hub["headers"]), method="POST")
    try:
        with urllib.request.urlopen(
            req, timeout=CALL_TIMEOUT_S if timeout is None else timeout
        ) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise HubError(f"hub HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise HubError(f"hub unreachable: {type(exc).__name__}") from None
    try:
        msg = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise HubError("hub returned non-JSON") from exc
    if msg.get("error"):
        err = msg["error"]
        detail = err.get("message", err) if isinstance(err, dict) else err
        raise HubToolError(f"hub error: {str(detail)[:200]}")
    result = msg.get("result") or {}
    if result.get("isError"):
        raise HubToolError("hub tool error: " + _first_text(result)[:200])
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text = _first_text(result)
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        payload = {"text": text}
    return payload if isinstance(payload, dict) else {"result": payload}


def _first_text(result: dict[str, Any]) -> str:
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "")
    return ""


# ── write journal (used only when no hub is reachable) ────────────────────────

def journal_path(palace_path: str) -> Path:
    return Path(_canonical(palace_path)).parent / "hub-journal.jsonl"


def journal_append(palace_path: str, tool: str, arguments: dict[str, Any]) -> Path:
    path = journal_path(palace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": time.time(), "tool": tool, "arguments": arguments}, ensure_ascii=False)
    with _lock:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return path


def journal_pending(palace_path: str) -> int:
    try:
        with journal_path(palace_path).open(encoding="utf-8") as fh:
            return sum(1 for ln in fh if ln.strip())
    except OSError:
        return 0


def journal_replay(palace_path: str, *, hub: dict | None = None, limit: int = JOURNAL_REPLAY_BATCH) -> int:
    """Replay up to ``limit`` journaled writes to the hub. Returns count replayed."""
    path = journal_path(palace_path)
    if not path.exists():
        return 0
    hub = hub or discover(palace_path)
    if hub is None:
        return 0
    if not _replay_lock.acquire(blocking=False):
        return 0
    try:
        return _journal_replay_locked(path, palace_path, hub, limit)
    finally:
        _replay_lock.release()


def _journal_replay_locked(path: Path, palace_path: str, hub: dict, limit: int) -> int:
    with _lock:
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return 0
    done = 0
    for ln in lines[:limit]:
        try:
            item = json.loads(ln)
            call_tool(palace_path, item["tool"], item["arguments"], hub=hub)
        except HubToolError as exc:
            # A line the hub rejects would otherwise block the journal forever.
            logger.warning("dropping journaled MemPalace write the hub rejected: %s", exc)
        except HubError:
            break
        except Exception:
            logger.debug("dropping unreadable journal line", exc_info=True)
        done += 1
    if done:
        with _lock:
            try:
                current = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            except OSError:
                current = lines
            rest = current[done:]
            tmp = path.with_suffix(".jsonl.tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(r + "\n" for r in rest))
            os.replace(str(tmp), str(path))
    return done
