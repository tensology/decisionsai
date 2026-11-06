"""Secrets for Plan DB links (N5 phase 2): macOS Keychain via the ``security`` CLI, else process memory.

Guarantees:
- A secret is never put on a command line: writes and reads that carry the secret go through
  ``security -i`` with the command on STDIN; argv is only ``[security, "-i"]``.
- The database only stores an opaque reference (``ref()``); nothing here logs or returns the secret
  except ``get()`` (for the later connection phase).
- Secrets that cannot be quoted safely inside the interactive command (quotes, backslashes, control
  characters, newlines) are refused with a clear error instead of being escaped.
- A configured keychain file must exist before any command runs: ``security`` silently falls back to
  the default (login) keychain for a missing path, so we never let it get that far.
- When ``security`` is missing, fails, or this is not macOS, a session-only in-memory store is used and
  ``backend()`` says ``"session"`` so the UI can warn that the secret is lost on restart.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Callable

logger = logging.getLogger(__name__)

SERVICE = "decisions-db-link"
KEYCHAIN_ENV = "DECISIONS_DB_SECRETS_KEYCHAIN"   # optional explicit keychain file (tests: a throwaway keychain)
MAX_SECRET = 1024
TIMEOUT = 5.0
_UNSAFE = re.compile(r'["\\\x00-\x1f\x7f]')
_ACCOUNT = re.compile(r"^[0-9]{1,12}$")
_SESSION: dict[tuple[str, str], str] = {}
_LOCK = threading.Lock()


class SecretError(ValueError):
    """A secret could not be accepted or stored (message never contains the secret)."""


def validate_secret(secret) -> str:
    if not isinstance(secret, str) or not secret:
        raise SecretError("Enter the database password.")
    if len(secret) > MAX_SECRET:
        raise SecretError(f"The password is longer than {MAX_SECRET} characters.")
    if _UNSAFE.search(secret):
        raise SecretError('The password contains a quote ("), backslash or control character, which cannot be stored safely. Change the database password or use a different one.')
    return secret


def _account(account) -> str:
    value = str(account)
    if not _ACCOUNT.match(value):
        raise SecretError("Invalid secret reference.")
    return value


class SecretStore:
    def __init__(self, *, keychain: str | None = None, security: str | None = None, runner: Callable = subprocess.run,
                 service: str = SERVICE, platform: str = sys.platform):
        self.keychain = keychain if keychain is not None else (os.environ.get(KEYCHAIN_ENV) or None)
        self.security = security or shutil.which("security") or ("/usr/bin/security" if os.path.exists("/usr/bin/security") else None)
        self.runner = runner
        self.service = service
        self.platform = platform
        self._failed = False

    # -- backend selection -------------------------------------------------------------------------
    def backend(self) -> str:
        if self._failed or self.platform != "darwin" or not self.security:
            return "session"
        if self.keychain is not None and (_UNSAFE.search(self.keychain) or not os.path.isfile(self.keychain)):
            return "session"
        return "keychain"

    def ref(self, account) -> str:
        return f"{self.backend()}:{self.service}/{_account(account)}"

    # -- keychain plumbing -------------------------------------------------------------------------
    def _target(self) -> list[str]:
        return [self.keychain] if self.keychain else []

    def _interactive(self, command: str) -> subprocess.CompletedProcess:
        if self.keychain is not None and not os.path.isfile(self.keychain):
            raise SecretError("The configured keychain is missing.")
        return self.runner([self.security, "-i"], input=command + "\n", capture_output=True, text=True,
                           timeout=TIMEOUT, check=False)

    def _plain(self, args: list[str]) -> subprocess.CompletedProcess:
        if self.keychain is not None and not os.path.isfile(self.keychain):
            raise SecretError("The configured keychain is missing.")
        return self.runner([self.security, *args, *self._target()], capture_output=True, text=True,
                           timeout=TIMEOUT, check=False)

    def _quoted_target(self) -> str:
        return f' "{self.keychain}"' if self.keychain else ""

    def _fail(self, what: str, account: str) -> None:
        logger.warning("db secret %s failed for link %s; using the session-only store", what, account)
        self._failed = True

    # -- public API --------------------------------------------------------------------------------
    def set(self, account, secret) -> str:
        account = _account(account)
        secret = validate_secret(secret)
        if self.backend() == "keychain":
            command = f'add-generic-password -a "{account}" -s "{self.service}" -w "{secret}" -U{self._quoted_target()}'
            try:
                result = self._interactive(command)
                ok = result.returncode == 0 and self._has_keychain(account)
            except (OSError, subprocess.SubprocessError, SecretError):
                ok = False
            del command
            if ok:
                with _LOCK:
                    _SESSION.pop((self.service, account), None)
                logger.info("db secret stored for link %s (keychain)", account)
                return "keychain"
            self._fail("store", account)
        with _LOCK:
            _SESSION[(self.service, account)] = secret
        logger.info("db secret stored for link %s (session)", account)
        return "session"

    def _has_keychain(self, account: str) -> bool:
        result = self._plain(["find-generic-password", "-a", account, "-s", self.service])   # attributes only, no secret
        if result.returncode != 0:
            return False
        if self.keychain:   # the item must be in the configured keychain, never a fallback one
            return os.path.realpath(self.keychain) in (result.stdout or "") or self.keychain in (result.stdout or "")
        return True

    def has(self, account) -> bool:
        account = _account(account)
        with _LOCK:
            if (self.service, account) in _SESSION:
                return True
        if self.backend() != "keychain":
            return False
        try:
            return self._has_keychain(account)
        except (OSError, subprocess.SubprocessError, SecretError):
            return False

    def get(self, account) -> str | None:
        account = _account(account)
        with _LOCK:
            if (self.service, account) in _SESSION:
                return _SESSION[(self.service, account)]
        if self.backend() != "keychain" or not self.has(account):
            return None
        try:
            result = self._interactive(f'find-generic-password -a "{account}" -s "{self.service}" -w{self._quoted_target()}')
        except (OSError, subprocess.SubprocessError, SecretError):
            return None
        if result.returncode != 0:
            return None
        value = (result.stdout or "").rstrip("\n")
        return value or None

    def delete(self, account) -> bool:
        account = _account(account)
        removed = False
        with _LOCK:
            removed = _SESSION.pop((self.service, account), None) is not None
        if self.backend() == "keychain":
            try:
                while self._has_keychain(account):
                    result = self._plain(["delete-generic-password", "-a", account, "-s", self.service])
                    if result.returncode != 0:
                        break
                    removed = True
            except (OSError, subprocess.SubprocessError, SecretError):
                pass
        if removed:
            logger.info("db secret deleted for link %s", account)
        return removed


_default: SecretStore | None = None


def get_store() -> SecretStore:
    global _default
    if _default is None:
        _default = SecretStore()
    return _default


def set_store(store: SecretStore | None) -> None:
    """Tests / later phases: swap the process-wide store."""
    global _default
    _default = store


def clear_session_secrets() -> None:
    with _LOCK:
        _SESSION.clear()
