"""
Security helpers for DecisionsAI web surfaces.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import socket
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from starlette.requests import Request
from starlette.websockets import WebSocket

INTERNAL_AUTH_HEADER = "X-DecisionsAI-Internal-Token"
INTERNAL_AUTH_QUERY_PARAM = "internal_token"
LEDGER_AUTH_HEADER = "X-DecisionsAI-Ledger-Token"
LEDGER_PROVISION_CONFIRM = "provision-time-ledger"
LEDGER_POST_PATHS = frozenset({
    "/api/workflows/studio/time-ledger/backups",
    "/api/workflows/studio/time-ledger/entries",
    "/api/workflows/studio/time-ledger/delete",
    "/api/workflows/studio/time-ledger/restore",
})
PROJECTION_AUTH_HEADER = "X-DecisionsAI-Projection-Token"
PROJECTION_PROVISION_CONFIRM = "provision-projection-store"
PROJECTION_POST_PATHS = frozenset({
    "/api/workflows/studio/projection-store/backups",
    "/api/workflows/studio/projection-store/apply",
    "/api/workflows/studio/projection-store/revoke",
    "/api/workflows/studio/projection-store/restore",
})
PROJECTION_GET_PATHS = frozenset({
    "/api/workflows/studio/projection-store/evidence",
})
EVIDENCE_AUTH_HEADER = "X-DecisionsAI-Evidence-Token"
EVIDENCE_PROVISION_CONFIRM = "provision-evidence-reader"
EVIDENCE_GET_PATH = "/api/workflows/studio/projection-store/work-evidence"

ALLOWED_LOCAL_ORIGINS = [
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://[::1]:8765",
]

_runtime_internal_token: Optional[str] = None
_runtime_internal_token_lock = threading.Lock()


def _internal_token_path() -> Path:
    configured = (os.getenv("DECISIONSAI_INTERNAL_TOKEN_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".decisionsai" / "internal_api_token"


def _load_or_create_persistent_token(path: Path) -> str:
    """Keep local browser credentials valid across backend restarts."""
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
    except FileNotFoundError:
        pass

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def get_internal_api_token() -> str:
    """Return an env override or a stable, private local-runtime token."""
    env_token = (os.getenv("DECISIONSAI_INTERNAL_API_TOKEN") or "").strip()
    if env_token:
        return env_token
    global _runtime_internal_token
    if _runtime_internal_token:
        return _runtime_internal_token
    with _runtime_internal_token_lock:
        if not _runtime_internal_token:
            try:
                _runtime_internal_token = _load_or_create_persistent_token(
                    _internal_token_path()
                )
            except OSError:
                _runtime_internal_token = secrets.token_urlsafe(32)
    return _runtime_internal_token


def _extract_host(origin: str) -> Optional[str]:
    try:
        parsed = urlparse(origin)
        return (parsed.hostname or "").strip().lower() or None
    except Exception:
        return None


def is_allowed_local_origin(origin: Optional[str]) -> bool:
    """Only allow localhost/loopback origins."""
    if not origin:
        return False
    host = _extract_host(origin)
    return host in {"localhost", "127.0.0.1", "::1"}


def _request_token(req: Request) -> str:
    return (req.headers.get(INTERNAL_AUTH_HEADER) or req.query_params.get(INTERNAL_AUTH_QUERY_PARAM) or "").strip()


def _websocket_token(ws: WebSocket) -> str:
    qs_token = (ws.query_params.get(INTERNAL_AUTH_QUERY_PARAM) if ws.query_params else "") or ""
    return (ws.headers.get(INTERNAL_AUTH_HEADER) or qs_token or "").strip()


def require_internal_token_request(req: Request) -> None:
    """Enforce internal token auth for sensitive HTTP routes."""
    expected = get_internal_api_token()
    received = _request_token(req)
    if not expected or not secrets.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_internal_token_dependency(request: Request) -> None:
    """FastAPI dependency form of require_internal_token_request (per-route guard)."""
    require_internal_token_request(request)


def websocket_has_valid_internal_token(ws: WebSocket) -> bool:
    expected = get_internal_api_token()
    received = _websocket_token(ws)
    return bool(expected and received and secrets.compare_digest(received, expected))


def mask_secret(value: Optional[str]) -> str:
    """Return a redacted string while preserving minimal context."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 6:
        return "*" * len(raw)
    return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


def redact_thirdparty_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Build a safe response for /api/thirdparty without plaintext secrets."""
    secret_fields = [
        "assemblyai_key",
        "openai_key",
        "anthropic_key",
        "cursor_key",
        "elevenlabs_key",
        "openrouter_key",
        "groq_key",
        "kilo_key",
        "gemini_key",
        "nvidia_key",
        "pixazo_key",
        "fishaudio_key",
        "masko_key",
        "rube_token",
        "jev_key",
        "tensology_key",
    ]
    response: Dict[str, Any] = {
        "ollama_url": settings.get("ollama_url", "http://localhost:11434/"),
        "assemblyai_enabled": settings.get("assemblyai_enabled", False),
        "openai_enabled": settings.get("openai_enabled", False),
        "anthropic_enabled": settings.get("anthropic_enabled", False),
        "cursor_enabled": settings.get("cursor_enabled", False),
        "elevenlabs_enabled": settings.get("elevenlabs_enabled", False),
        "openrouter_enabled": settings.get("openrouter_enabled", False),
        "groq_enabled": settings.get("groq_enabled", False),
        "kilo_enabled": settings.get("kilo_enabled", False),
        "gemini_enabled": settings.get("gemini_enabled", False),
        "nvidia_enabled": settings.get("nvidia_enabled", False),
        "pixazo_enabled": settings.get("pixazo_enabled", False),
        "fishaudio_enabled": settings.get("fishaudio_enabled", False),
        "masko_enabled": settings.get("masko_enabled", False),
        "composio_enabled": settings.get("rube_enabled", False),
        "jev_enabled": settings.get("jev_enabled", False),
        "tensology_enabled": settings.get("tensology_enabled", False),
    }
    for field in secret_fields:
        raw = (settings.get(field) or "").strip()
        response_field = field
        if field == "rube_token":
            response_field = "composio_key"
        response[response_field] = ""
        response[f"{response_field}_set"] = bool(raw)
    return response


def redact_connected_account(account: Dict[str, Any]) -> Dict[str, Any]:
    """Strip credential fields from account rows."""
    provider = (account.get("provider") or "").strip().lower()
    base = {
        "provider": provider,
        "name": account.get("name"),
        "is_valid": bool(account.get("is_valid", False)),
        "created_at": account.get("created_at"),
    }
    if provider == "jira":
        base["server_url"] = account.get("server_url")
        base["email_masked"] = mask_secret(account.get("email"))
        base["has_api_token"] = bool((account.get("api_token") or "").strip())
    elif provider == "trello":
        base["api_key_masked"] = mask_secret(account.get("api_key"))
        base["has_api_token"] = bool((account.get("api_token") or "").strip())
    elif provider == "discord_bot":
        base["bot_token_masked"] = mask_secret(account.get("bot_token"))
        base["has_bot_token"] = bool((account.get("bot_token") or "").strip())
    elif provider == "slack_app":
        base["bot_token_masked"] = mask_secret(account.get("bot_token"))
        base["signing_secret_masked"] = mask_secret(account.get("signing_secret"))
        base["has_bot_token"] = bool((account.get("bot_token") or "").strip())
        base["has_signing_secret"] = bool((account.get("signing_secret") or "").strip())
    return base


def _is_blocked_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_safe_outbound_url(url: str) -> str:
    """
    Validate outbound URL against SSRF to local/private targets.
    Returns normalized URL.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError("URL host is required")
    if host.lower() in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Localhost targets are not allowed")
    try:
        addr_info = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve host: {host}") from exc
    ips = {entry[4][0] for entry in addr_info if entry and entry[4]}
    for ip_value in ips:
        try:
            if _is_blocked_ip(ip_value):
                raise ValueError("Private or local network targets are not allowed")
        except ValueError:
            raise
        except Exception:
            continue
    return parsed.geturl().rstrip("/")


class SimpleRateLimiter:
    """In-memory sliding-window rate limiter keyed by string."""

    def __init__(self) -> None:
        self._events: Dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        threshold = now - float(window_seconds)
        with self._lock:
            bucket = self._events.setdefault(key, [])
            bucket[:] = [ts for ts in bucket if ts >= threshold]
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


rate_limiter = SimpleRateLimiter()


class LedgerProvisionError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = int(code)
        self.message = message


def _ledger_token_path() -> Path:
    configured = (os.getenv("DECISIONSAI_LEDGER_TOKEN_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".decisionsai" / "time_ledger_operator_token"


def _private_owner_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    if info.st_uid != os.geteuid():
        return False
    if stat.S_IMODE(info.st_mode) & 0o077:
        return False
    return info.st_size >= 32


def owner_holds_internal_token_file() -> tuple[bool, str]:
    """OS owner boundary. The internal token bytes are not read or returned."""
    path = _internal_token_path()
    if path.is_symlink():
        return False, "internal token path is a symlink"
    if not path.is_file():
        return False, "internal token file is not present"
    try:
        info = path.stat()
    except OSError:
        return False, "internal token file is not readable by its owner"
    if info.st_uid != os.geteuid():
        return False, "current user does not own the internal token file"
    if stat.S_IMODE(info.st_mode) & 0o077:
        return False, "internal token file is readable by group or others"
    if info.st_size < 32:
        return False, "internal token file is not a provisioned credential"
    return True, ""


def get_ledger_operator_token() -> str:
    """Read the ledger credential from its private file. Never creates or prints it."""
    path = _ledger_token_path()
    if not _private_owner_file(path):
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ledger_token_matches(received: str) -> bool:
    expected = get_ledger_operator_token()
    presented = (received or "").strip()
    return bool(expected and presented and secrets.compare_digest(presented, expected))


def ledger_header_authorizes_write(path: str, method: str, ledger_header: str) -> bool:
    """True only for the four time-ledger POST routes with the operator credential."""
    if method != "POST":
        return False
    if str(path or "") not in LEDGER_POST_PATHS:
        return False
    return ledger_token_matches(ledger_header)


def _aliases_internal_token_file(path: Path) -> bool:
    """True when the operator path is the internal token file, a link to it, or the same inode."""
    internal = _internal_token_path()
    if path.expanduser().absolute() == internal.expanduser().absolute():
        return True
    try:
        if path.resolve(strict=False) == internal.resolve(strict=False):
            return True
    except OSError:
        return True
    try:
        if (path.exists() or path.is_symlink()) and (internal.exists() or internal.is_symlink()):
            left, right = path.lstat(), internal.lstat()
            if (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino):
                return True
            if not path.is_symlink() and not internal.is_symlink():
                followed_left, followed_right = path.stat(), internal.stat()
                if (followed_left.st_dev, followed_left.st_ino) == (followed_right.st_dev, followed_right.st_ino):
                    return True
    except OSError:
        return True
    return False


def _owner_private_incomplete(path: Path) -> bool:
    """An owner-private file that another creator has opened but not finished writing."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    if info.st_uid != os.geteuid():
        return False
    if stat.S_IMODE(info.st_mode) & 0o077:
        return False
    return info.st_size < 32


def _already_present(path: Path) -> dict:
    return {
        "created": False,
        "already_present": True,
        "path": str(path),
        "mode": "0600",
        "secret_printed": False,
    }


def provision_ledger_operator_token(*, confirm: str) -> dict:
    """Create the ledger credential once. The secret stays in the file and is not returned."""
    if confirm != LEDGER_PROVISION_CONFIRM:
        raise LedgerProvisionError(2, "confirm must be provision-time-ledger")
    allowed, reason = owner_holds_internal_token_file()
    if not allowed:
        raise LedgerProvisionError(3, reason)
    path = _ledger_token_path()
    if _aliases_internal_token_file(path):
        raise LedgerProvisionError(3, "ledger credential must be a distinct file from the internal token")
    if path.is_symlink():
        raise LedgerProvisionError(3, "ledger credential path is a symlink")
    if path.exists():
        if _private_owner_file(path) or _owner_private_incomplete(path):
            return _already_present(path)
        raise LedgerProvisionError(3, "existing ledger credential is not a private owner file")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if _aliases_internal_token_file(path):
            raise LedgerProvisionError(3, "ledger credential must be a distinct file from the internal token")
        if _private_owner_file(path) or _owner_private_incomplete(path):
            return _already_present(path)
        for _ in range(50):
            if _private_owner_file(path) or _owner_private_incomplete(path):
                return _already_present(path)
            time.sleep(0.01)
        raise LedgerProvisionError(3, "ledger credential appeared but is not a private owner file")
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
        token = ""
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {
        "created": True,
        "already_present": False,
        "path": str(path),
        "mode": "0600",
        "secret_printed": False,
    }


def _projection_token_path() -> Path:
    configured = (os.getenv("DECISIONSAI_PROJECTION_TOKEN_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".decisionsai" / "projection_operator_token"


def _aliases_protected_credential(path: Path, extra: list[Path] | None = None) -> bool:
    protected = [_internal_token_path(), _ledger_token_path(), *(extra or [])]
    for other in protected:
        if path.expanduser().absolute() == other.expanduser().absolute():
            return True
        try:
            if path.resolve(strict=False) == other.resolve(strict=False):
                return True
            if (path.exists() or path.is_symlink()) and (other.exists() or other.is_symlink()):
                if (path.lstat().st_dev, path.lstat().st_ino) == (other.lstat().st_dev, other.lstat().st_ino):
                    return True
                if not path.is_symlink() and not other.is_symlink():
                    if (path.stat().st_dev, path.stat().st_ino) == (other.stat().st_dev, other.stat().st_ino):
                        return True
        except OSError:
            return True
    return False


def get_projection_operator_token() -> str:
    path = _projection_token_path()
    if not _private_owner_file(path):
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def projection_token_matches(received: str) -> bool:
    expected = get_projection_operator_token()
    presented = (received or "").strip()
    return bool(expected and presented and secrets.compare_digest(presented, expected))


def projection_header_authorizes_write(path: str, method: str, projection_header: str) -> bool:
    if method != "POST":
        return False
    if str(path or "") not in PROJECTION_POST_PATHS:
        return False
    return projection_token_matches(projection_header)


def projection_header_authorizes_read(path: str, method: str, projection_header: str) -> bool:
    if method != "GET":
        return False
    if str(path or "") not in PROJECTION_GET_PATHS:
        return False
    return projection_token_matches(projection_header)


def provision_projection_operator_token(*, confirm: str) -> dict:
    if confirm != PROJECTION_PROVISION_CONFIRM:
        raise LedgerProvisionError(2, "confirm must be provision-projection-store")
    allowed, reason = owner_holds_internal_token_file()
    if not allowed:
        raise LedgerProvisionError(3, reason)
    path = _projection_token_path()
    if _aliases_protected_credential(path):
        raise LedgerProvisionError(3, "projection credential must be a distinct file from the internal and ledger tokens")
    if path.is_symlink():
        raise LedgerProvisionError(3, "projection credential path is a symlink")
    if path.exists():
        if _private_owner_file(path) or _owner_private_incomplete(path):
            return _already_present(path)
        raise LedgerProvisionError(3, "existing projection credential is not a private owner file")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if _private_owner_file(path) or _owner_private_incomplete(path):
            return _already_present(path)
        raise LedgerProvisionError(3, "projection credential appeared but is not a private owner file")
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {"created": True, "already_present": False, "path": str(path), "mode": "0600", "secret_printed": False}


def _evidence_token_path() -> Path:
    configured = (os.getenv("DECISIONSAI_EVIDENCE_TOKEN_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".decisionsai" / "evidence_operator_token"


def get_evidence_operator_token() -> str:
    path = _evidence_token_path()
    if not _private_owner_file(path):
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def evidence_token_matches(received: str) -> bool:
    expected = get_evidence_operator_token()
    presented = (received or "").strip()
    return bool(expected and presented and secrets.compare_digest(presented, expected))


def evidence_header_authorizes_read(path: str, method: str, evidence_header: str) -> bool:
    if method != "GET" or str(path or "") != EVIDENCE_GET_PATH:
        return False
    return evidence_token_matches(evidence_header)


def provision_evidence_operator_token(*, confirm: str) -> dict:
    if confirm != EVIDENCE_PROVISION_CONFIRM:
        raise LedgerProvisionError(2, "confirm must be provision-evidence-reader")
    allowed, reason = owner_holds_internal_token_file()
    if not allowed:
        raise LedgerProvisionError(3, reason)
    path = _evidence_token_path()
    if _aliases_protected_credential(path, extra=[_projection_token_path()]):
        raise LedgerProvisionError(3, "evidence credential must be a distinct file from the internal, ledger, and projection tokens")
    if path.is_symlink():
        raise LedgerProvisionError(3, "evidence credential path is a symlink")
    if path.exists():
        if _private_owner_file(path) or _owner_private_incomplete(path):
            return _already_present(path)
        raise LedgerProvisionError(3, "existing evidence credential is not a private owner file")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if _private_owner_file(path) or _owner_private_incomplete(path):
            return _already_present(path)
        raise LedgerProvisionError(3, "evidence credential appeared but is not a private owner file")
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {"created": True, "already_present": False, "path": str(path), "mode": "0600", "secret_printed": False}


def main(argv: list[str] | None = None) -> int:
    """Owner-only local setup. Prints the path and status, never the secret."""
    import argparse

    parser = argparse.ArgumentParser(prog="provision-time-ledger", add_help=True)
    parser.add_argument("command", nargs="?", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    commands = {
        LEDGER_PROVISION_CONFIRM: (
            provision_ledger_operator_token,
            LEDGER_AUTH_HEADER,
            "scope: POST /api/workflows/studio/time-ledger/backups|entries|delete|restore",
        ),
        PROJECTION_PROVISION_CONFIRM: (
            provision_projection_operator_token,
            PROJECTION_AUTH_HEADER,
            "scope: POST /api/workflows/studio/projection-store/backups|apply|revoke|restore",
        ),
        EVIDENCE_PROVISION_CONFIRM: (
            provision_evidence_operator_token,
            EVIDENCE_AUTH_HEADER,
            "scope: GET /api/workflows/studio/projection-store/work-evidence",
        ),
    }
    if args.command not in commands:
        print(
            "usage: python distr/gui/web/security.py "
            "provision-time-ledger --confirm provision-time-ledger | "
            "provision-projection-store --confirm provision-projection-store | "
            "provision-evidence-reader --confirm provision-evidence-reader",
            file=sys.stderr,
        )
        return 2
    provision, header, scope = commands[args.command]
    try:
        result = provision(confirm=args.confirm)
    except LedgerProvisionError as exc:
        print(exc.message, file=sys.stderr)
        return exc.code
    state = "already_present" if result["already_present"] else "created"
    print(f"status: {state}")
    print(f"path: {result['path']}")
    print("mode: 0600")
    print("secret_printed: false")
    print(f"header: {header}")
    print(scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
