"""gap2 2026-10-29: enforced read-only mode for automations.

An automation with ``action_config.read_only`` set runs its tool inside
``read_only_scope``. While the scope is active (per thread, via contextvars) a
process audit hook refuses:

* opening any file for writing, except the ONE named state file and its
  timestamped backups (``<state>.bak-<stamp>``) in the same directory;
* deleting, renaming, chmod/chown, mkdir/rmdir, truncation;
* spawning processes (``subprocess``/``os.system``/``os.exec*``/``os.spawn*``);
* sqlite connections that are not ``mode=ro`` URIs;
* outgoing HTTP that is not GET/HEAD (urllib and http.client, which also covers
  ``requests``), except the Google OAuth token refresh endpoint.

Tools additionally refuse send/reply/label/delete style actions through
``assert_action_allowed``. Outside a scope the hook is a no-op.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


class ReadOnlyViolation(PermissionError):
    """Raised when a read-only automation attempts a write/send/delete."""


_BLOCKED_ACTION = re.compile(
    r"(send|reply|forward|label|relabel|delete|trash|archive|mark_?read|move|approve|post|publish|update|create|write)",
    re.I,
)
_WRITE_METHODS = (b"POST ", b"PUT ", b"PATCH ", b"DELETE ")
_ALLOWED_POST_HOSTS = {"oauth2.googleapis.com"}  # access-token refresh only, no user data written
_BACKUP_RE = re.compile(r"\.bak-[0-9A-Za-z_-]+$")


class ReadOnlyPolicy:
    def __init__(self, state_file: str | os.PathLike[str] | None = None, *, label: str = "automation") -> None:
        self.label = label
        self.state_file = Path(os.path.expanduser(str(state_file))).resolve() if state_file else None
        self.blocked: list[str] = []

    # -- checks -----------------------------------------------------------
    def path_writable(self, path: Any) -> bool:
        if self.state_file is None or path is None:
            return False
        try:
            target = Path(os.fsdecode(path)).expanduser().resolve()
        except Exception:
            return False
        if target == self.state_file:
            return True
        if target.parent == self.state_file.parent:
            name = target.name
            base = self.state_file.name
            if name.startswith(base) and _BACKUP_RE.search(name[len(base):] or "x"):
                return True
            if name.startswith(base + ".tmp-"):
                return True  # atomic-replace temp file for the state file
        return False

    def deny(self, what: str) -> None:
        self.blocked.append(what)
        raise ReadOnlyViolation(f"read-only mode ({self.label}): blocked {what}")

    # -- the only sanctioned write ---------------------------------------
    def write_state(self, data: dict[str, Any], *, backup: bool = True) -> Path:
        if self.state_file is None:
            self.deny("state write (no state file configured)")
        assert self.state_file is not None
        if backup and self.state_file.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = self.state_file.with_name(f"{self.state_file.name}.bak-{stamp}")
            bak.write_bytes(self.state_file.read_bytes())
        tmp = self.state_file.with_name(f"{self.state_file.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
        os.replace(tmp, self.state_file)
        return self.state_file


_current: contextvars.ContextVar[ReadOnlyPolicy | None] = contextvars.ContextVar("automation_read_only", default=None)
_hook_lock = threading.Lock()
_hook_installed = False


def current_policy() -> ReadOnlyPolicy | None:
    return _current.get()


def assert_action_allowed(action: str) -> None:
    policy = _current.get()
    if policy is not None and _BLOCKED_ACTION.search(str(action or "")):
        policy.deny(f"action {action!r}")


def _open_is_write(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str):
        return any(ch in mode for ch in "wax+")
    if isinstance(flags, int):
        return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    return False


def _audit(event: str, args: tuple) -> None:
    policy = _current.get()
    if policy is None:
        return
    # Allow our own state write (it reaches here as open/os.replace on allowed paths).
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        if _open_is_write(mode, flags) and not policy.path_writable(path):
            policy.deny(f"file write {os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else path!r}")
    elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate", "shutil.rmtree", "os.link", "os.symlink"}:
        target = args[0] if args else None
        # Deleting/chmod is never allowed, not even on the state file; only our atomic temp file.
        if not (policy.path_writable(target) and ".tmp-" in os.fsdecode(target or "")):
            policy.deny(f"{event} {target!r}")
    elif event in {"os.rename"}:
        src, dst = (list(args) + [None, None])[:2]
        if not (policy.path_writable(src) and policy.path_writable(dst)):
            policy.deny(f"{event} {src!r} -> {dst!r}")
    elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty"}:
        policy.deny(f"process spawn ({event})")
    elif event == "sqlite3.connect":
        database = str(args[0]) if args else ""
        if database not in {":memory:"} and "mode=ro" not in database:
            policy.deny(f"sqlite connect without mode=ro ({database.split('?')[0]})")
    elif event == "urllib.Request":
        method = str((list(args) + [None] * 4)[3] or "GET").upper()
        if method not in {"GET", "HEAD"}:
            policy.deny(f"HTTP {method} {args[0]!r}")
    elif event == "http.client.send":
        conn, data = (list(args) + [None, None])[:2]
        if isinstance(data, (bytes, bytearray)) and bytes(data[:8]).upper().startswith(_WRITE_METHODS):
            host = str(getattr(conn, "host", "") or "")
            first = bytes(data[:200]).split(b"\r\n", 1)[0]
            if not (host in _ALLOWED_POST_HOSTS and first.startswith(b"POST /token")):
                policy.deny(f"HTTP {first.split(b' ', 1)[0].decode(errors='replace')} to {host}")


def _install_hook() -> None:
    global _hook_installed
    with _hook_lock:
        if not _hook_installed:
            sys.addaudithook(_audit)
            _hook_installed = True


@contextmanager
def read_only_scope(policy: ReadOnlyPolicy) -> Iterator[ReadOnlyPolicy]:
    _install_hook()
    token = _current.set(policy)
    try:
        yield policy
    finally:
        _current.reset(token)


def policy_from_action_config(action_config: dict[str, Any] | None, *, label: str = "automation") -> ReadOnlyPolicy | None:
    cfg = action_config if isinstance(action_config, dict) else {}
    ro = cfg.get("read_only")
    if not ro:
        return None
    state_file = ro.get("state_file") if isinstance(ro, dict) else None
    return ReadOnlyPolicy(state_file, label=label)
