"""Monk Mode website blocking and weekly scheduling.

DecisionsAI owns only the marked block it adds to the system hosts file. All
other hosts-file content is preserved byte-for-byte apart from normalizing the
single newline immediately around the managed block.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
import plistlib
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from distr.core.monk_dns import DNS_PORT, get_monk_dns_server

logger = logging.getLogger(__name__)

HOSTS_BLOCK_BEGIN = "# BEGIN DecisionsAI Monk Mode"
HOSTS_BLOCK_END = "# END DecisionsAI Monk Mode"
MACOS_HELPER_LABEL = "com.decisionsai.monk-mode"
MACOS_HELPER_PATH = Path("/Library/PrivilegedHelperTools/com.decisionsai.monk-mode.py")
MACOS_PLIST_PATH = Path("/Library/LaunchDaemons/com.decisionsai.monk-mode.plist")
MACOS_RESOLVER_DIR = Path("/etc/resolver")
RESOLVER_MARKER = "# DecisionsAI Monk Mode wildcard resolver"
_HOSTS_BLOCK_RE = re.compile(
    rf"(?:^|\n){re.escape(HOSTS_BLOCK_BEGIN)}\n.*?\n{re.escape(HOSTS_BLOCK_END)}(?:\n|$)",
    re.DOTALL,
)
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

# A hosts file cannot express wildcards. These are alternate entry points that
# commonly replace the address a person typed after a redirect or device check.
_COMMON_HOST_ALIASES: dict[str, tuple[str, ...]] = {
    "facebook.com": ("www", "m", "mbasic", "touch", "web"),
    "instagram.com": ("www", "m"),
}


class MonkModeError(RuntimeError):
    """A user-actionable Monk Mode failure."""


class MonkPermissionCancelled(MonkModeError):
    """The operating-system permission prompt was cancelled."""


def system_hosts_path() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/drivers/etc/hosts"
    return Path("/etc/hosts")


def normalize_hostname(value: str) -> str:
    """Return a safe ASCII hostname from a hostname or HTTP(S) URL."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Enter a web address.")
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Only HTTP and HTTPS web addresses can be blocked.")
        hostname = (parsed.hostname or "").strip().rstrip(".").lower()
    except ValueError as exc:
        raise ValueError("Enter a valid web address.") from exc
    if not hostname or "*" in hostname:
        raise ValueError("Enter a specific website hostname. Wildcards are not supported.")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Enter a valid website hostname.") from exc
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Localhost cannot be added to Monk Mode.")
    labels = hostname.split(".")
    if len(labels) < 2 or len(hostname) > 253 or any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError("Enter a valid website hostname, such as example.com.")
    return hostname


def normalize_site(site: dict[str, Any], *, site_id: str | None = None) -> dict[str, str]:
    url = str(site.get("url") or site.get("hostname") or "").strip()
    hostname = normalize_hostname(url)
    label = str(site.get("label") or "").strip()[:80]
    return {
        "id": str(site_id or site.get("id") or uuid.uuid4().hex),
        "url": url,
        "hostname": hostname,
        "label": label,
    }


def normalize_sites(sites: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for site in sites:
        item = normalize_site(site)
        if item["hostname"] in seen:
            raise ValueError(f'{item["hostname"]} is already in Monk Mode.')
        seen.add(item["hostname"])
        normalized.append(item)
    return normalized


def expanded_hostnames(sites: Iterable[dict[str, Any]]) -> list[str]:
    """Expand saved addresses to the practical hostnames users can reach."""
    hosts: set[str] = set()
    for site in sites:
        hostname = normalize_hostname(str(site.get("hostname") or site.get("url") or ""))
        hosts.add(hostname)
        if hostname.startswith("www."):
            hosts.add(hostname[4:])
        else:
            hosts.add(f"www.{hostname}")
        for base, aliases in _COMMON_HOST_ALIASES.items():
            if hostname == base or hostname.endswith(f".{base}"):
                hosts.add(base)
                hosts.update(f"{alias}.{base}" for alias in aliases)
    return sorted(hosts)


def blocked_domains(sites: Iterable[dict[str, Any]]) -> list[str]:
    """Return domain suffixes whose complete subdomain trees must be blocked."""
    domains: set[str] = set()
    for site in sites:
        hostname = normalize_hostname(str(site.get("hostname") or site.get("url") or ""))
        domains.add(hostname[4:] if hostname.startswith("www.") else hostname)
    return sorted(domains)


def strip_managed_hosts_block(content: str) -> str:
    return _HOSTS_BLOCK_RE.sub("\n", content).rstrip("\n")


def build_hosts_content(content: str, *, enabled: bool, sites: Iterable[dict[str, Any]]) -> str:
    base = strip_managed_hosts_block(content)
    if not enabled:
        return base + "\n"
    hostnames = expanded_hostnames(sites)
    if not hostnames:
        raise ValueError("Add at least one web address before enabling Monk Mode.")
    lines = [HOSTS_BLOCK_BEGIN]
    for hostname in hostnames:
        lines.append(f"127.0.0.1 {hostname}")
        lines.append(f"::1 {hostname}")
    lines.append(HOSTS_BLOCK_END)
    prefix = f"{base}\n\n" if base else ""
    return prefix + "\n".join(lines) + "\n"


def normalize_schedule_window(window: dict[str, Any], *, window_id: str | None = None) -> dict[str, Any]:
    try:
        days = sorted({int(day) for day in window.get("days", [])})
    except (TypeError, ValueError) as exc:
        raise ValueError("Schedule days must be valid weekdays.") from exc
    if not days or any(day < 0 or day > 6 for day in days):
        raise ValueError("Choose at least one weekday for every schedule.")
    start = str(window.get("start") or "").strip()
    end = str(window.get("end") or "").strip()
    if not _TIME_RE.fullmatch(start) or not _TIME_RE.fullmatch(end):
        raise ValueError("Schedule times must use 24-hour HH:MM format.")
    if start == end:
        raise ValueError("A schedule start and end time cannot be the same.")
    return {
        "id": str(window_id or window.get("id") or uuid.uuid4().hex),
        "days": days,
        "start": start,
        "end": end,
        "enabled": bool(window.get("enabled", True)),
    }


def normalize_schedule(windows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_schedule_window(window) for window in windows]


def schedule_is_active(windows: Iterable[dict[str, Any]], now: datetime | None = None) -> bool:
    """Evaluate weekly windows, including windows that cross midnight."""
    current = now or datetime.now().astimezone()
    weekday = current.weekday()
    minute = current.hour * 60 + current.minute
    for raw in windows:
        window = normalize_schedule_window(raw)
        if not window["enabled"]:
            continue
        start_h, start_m = map(int, window["start"].split(":"))
        end_h, end_m = map(int, window["end"].split(":"))
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        days = set(window["days"])
        if start < end:
            if weekday in days and start <= minute < end:
                return True
        else:
            if weekday in days and minute >= start:
                return True
            previous_day = (weekday - 1) % 7
            if previous_day in days and minute < end:
                return True
    return False


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class HostsFileController:
    def __init__(
        self,
        hosts_path: str | os.PathLike[str] | None = None,
        privileged_runner: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self.hosts_path = Path(hosts_path) if hosts_path else system_hosts_path()
        self.privileged_runner = privileged_runner

    def read(self) -> str:
        try:
            return self.hosts_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def is_applied(self, sites: Iterable[dict[str, Any]]) -> bool:
        try:
            content = self.read()
            hosts_match = content == build_hosts_content(content, enabled=True, sites=sites)
            if not hosts_match:
                return False
            if platform.system() == "Darwin" and self.hosts_path == system_hosts_path():
                return self._resolvers_applied(blocked_domains(sites)) and get_monk_dns_server().running
            return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def _resolvers_applied(domains: Iterable[str]) -> bool:
        expected = set(domains)
        if not expected:
            return False
        for domain in expected:
            path = MACOS_RESOLVER_DIR / domain
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                return False
            if not content.startswith(RESOLVER_MARKER + "\n") or f"port {DNS_PORT}\n" not in content:
                return False
        return True

    @staticmethod
    def _managed_resolvers_present() -> bool:
        if not MACOS_RESOLVER_DIR.exists():
            return False
        try:
            paths = list(MACOS_RESOLVER_DIR.iterdir())
        except OSError:
            return True
        for path in paths:
            try:
                if path.is_file() and path.read_text(encoding="utf-8").startswith(RESOLVER_MARKER + "\n"):
                    return True
            except OSError:
                continue
        return False

    def is_disabled_applied(self) -> bool:
        hosts_clear = HOSTS_BLOCK_BEGIN not in self.read()
        if platform.system() == "Darwin" and self.hosts_path == system_hosts_path():
            return hosts_clear and not self._managed_resolvers_present()
        return hosts_clear

    def ensure_runtime(self, enabled: bool) -> None:
        if enabled and platform.system() == "Darwin" and self.hosts_path == system_hosts_path():
            try:
                get_monk_dns_server().start()
            except OSError as exc:
                raise MonkModeError(f"Monk Mode could not start its local DNS blocker: {exc}") from exc

    def ensure_schedule_support(self, enabled: bool, sites: Iterable[dict[str, Any]]) -> None:
        """Install the macOS launchd helper before relying on unattended changes."""
        if platform.system() == "Darwin" and self.hosts_path == system_hosts_path() and not os.access(self.hosts_path, os.W_OK):
            self.ensure_runtime(enabled)
            self._apply_with_macos_helper(
                enabled,
                expanded_hostnames(sites) if enabled else [],
                blocked_domains(sites) if enabled else [],
            )

    def apply(self, enabled: bool, sites: Iterable[dict[str, Any]]) -> bool:
        sites = list(sites)
        self.ensure_runtime(enabled)
        current = self.read()
        desired = build_hosts_content(current, enabled=enabled, sites=sites)
        if desired == current:
            return False
        if platform.system() == "Darwin" and self.hosts_path == system_hosts_path() and not os.access(self.hosts_path, os.W_OK):
            domains = blocked_domains(sites) if enabled else []
            self._apply_with_macos_helper(enabled, expanded_hostnames(sites) if enabled else [], domains)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                resolver_ready = self._resolvers_applied(domains) if enabled else not self._managed_resolvers_present()
                if self.read() == desired and resolver_ready:
                    return True
                time.sleep(0.2)
            raise MonkModeError("The Monk Mode system helper did not apply the hosts-file update.")
        self._write(desired)
        return True

    @staticmethod
    def _macos_state_path() -> Path:
        return Path.home() / ".decisions" / "monk-mode-state.json"

    def _macos_plist_bytes(self, state_path: Path) -> bytes:
        helper_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
        payload = {
            "Label": MACOS_HELPER_LABEL,
            "ProgramArguments": [
                helper_python,
                str(MACOS_HELPER_PATH),
                str(state_path),
                str(self.hosts_path),
                str(os.getuid()),
                str(MACOS_RESOLVER_DIR),
                str(DNS_PORT),
            ],
            "RunAtLoad": True,
            "StartInterval": 30,
            "WatchPaths": [str(state_path)],
            "ProcessType": "Background",
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
        }
        return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)

    def _apply_with_macos_helper(self, enabled: bool, hostnames: list[str], domains: list[str]) -> None:
        state_path = self._macos_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".monk-state-", dir=str(state_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "version": 2,
                        "enabled": bool(enabled),
                        "hostnames": hostnames,
                        "domains": domains,
                        "dns_port": DNS_PORT,
                    },
                    handle,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, state_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

        helper_source = Path(__file__).with_name("monk_privileged_helper.py")
        helper_bytes = helper_source.read_bytes()
        plist_bytes = self._macos_plist_bytes(state_path)
        installed = False
        try:
            installed = MACOS_HELPER_PATH.read_bytes() == helper_bytes and MACOS_PLIST_PATH.read_bytes() == plist_bytes
        except OSError:
            installed = False
        if not installed:
            self._install_macos_helper(helper_source, plist_bytes)
        else:
            # WatchPaths normally fires immediately. kickstart is best-effort and
            # makes the transition instant on macOS versions that allow it.
            subprocess.run(
                ["/bin/launchctl", "kickstart", "-k", f"system/{MACOS_HELPER_LABEL}"],
                capture_output=True,
                timeout=10,
            )

    def _install_macos_helper(self, helper_source: Path, plist_bytes: bytes) -> None:
        fd, plist_temp_name = tempfile.mkstemp(prefix="decisions-monk-", suffix=".plist")
        plist_temp = Path(plist_temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(plist_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(plist_temp, 0o644)
            command = (
                f"/usr/bin/install -m 755 {shlex.quote(str(helper_source))} {shlex.quote(str(MACOS_HELPER_PATH))}; "
                f"/usr/bin/install -m 644 {shlex.quote(str(plist_temp))} {shlex.quote(str(MACOS_PLIST_PATH))}; "
                f"/bin/launchctl bootout system/{MACOS_HELPER_LABEL} >/dev/null 2>&1 || true; "
                f"/bin/launchctl bootstrap system {shlex.quote(str(MACOS_PLIST_PATH))}; "
                f"/bin/launchctl kickstart -k system/{MACOS_HELPER_LABEL}"
            )
            script = f"do shell script {_applescript_string(command)} with administrator privileges"
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                message = (result.stderr or result.stdout or "Permission was not granted.").strip()
                if "User canceled" in message or "(-128)" in message:
                    raise MonkPermissionCancelled("Monk Mode was not changed because permission was cancelled.")
                raise MonkModeError(f"Could not install the Monk Mode system helper: {message}")
        finally:
            plist_temp.unlink(missing_ok=True)

    def _write(self, content: str) -> None:
        target = self.hosts_path
        # Test/custom targets and root-run installations can be updated directly.
        if target != system_hosts_path() or os.access(target, os.W_OK):
            target.parent.mkdir(parents=True, exist_ok=True)
            existing_stat = target.stat() if target.exists() else None
            fd, temp_name = tempfile.mkstemp(prefix=".decisions-monk-", dir=str(target.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if existing_stat is not None:
                    os.chmod(temp_name, existing_stat.st_mode & 0o7777)
                    try:
                        os.chown(temp_name, existing_stat.st_uid, existing_stat.st_gid)
                    except PermissionError:
                        pass
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            self._flush_dns()
            return

        fd, temp_name = tempfile.mkstemp(prefix="decisions-monk-", suffix=".hosts")
        staged = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staged, 0o644)
            if self.privileged_runner:
                self.privileged_runner(staged, target)
            else:
                self._write_privileged(staged, target)
        finally:
            staged.unlink(missing_ok=True)

    def _write_privileged(self, staged: Path, target: Path) -> None:
        system = platform.system()
        if system == "Darwin":
            command = (
                f"/usr/bin/install -m 644 {shlex.quote(str(staged))} {shlex.quote(str(target))}; "
                "/usr/bin/dscacheutil -flushcache; "
                "/usr/bin/killall -HUP mDNSResponder >/dev/null 2>&1 || true"
            )
            script = f"do shell script {_applescript_string(command)} with administrator privileges"
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                message = (result.stderr or result.stdout or "Permission was not granted.").strip()
                if "User canceled" in message or "(-128)" in message:
                    raise MonkPermissionCancelled("Monk Mode was not changed because permission was cancelled.")
                raise MonkModeError(f"Could not update the system hosts file: {message}")
            return
        if system == "Linux":
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise MonkModeError("Monk Mode needs pkexec to request permission on this Linux system.")
            result = subprocess.run(
                [pkexec, "/usr/bin/install", "-m", "644", str(staged), str(target)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode != 0:
                raise MonkModeError((result.stderr or "Permission was not granted.").strip())
            self._flush_dns()
            return
        if system == "Windows":
            ps = shutil.which("powershell") or shutil.which("pwsh")
            if not ps:
                raise MonkModeError("PowerShell is required to update the Windows hosts file.")
            args = f"-NoProfile -Command Copy-Item -LiteralPath '{str(staged).replace(chr(39), chr(39) * 2)}' -Destination '{str(target).replace(chr(39), chr(39) * 2)}' -Force"
            command = [
                ps,
                "-NoProfile",
                "-Command",
                f"$p=Start-Process -FilePath '{ps}' -ArgumentList {json.dumps(args)} -Verb RunAs -Wait -PassThru; exit $p.ExitCode",
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                raise MonkModeError("Administrator permission is required to update the hosts file.")
            self._flush_dns()
            return
        raise MonkModeError(f"Monk Mode does not yet support {system}.")

    @staticmethod
    def _flush_dns() -> None:
        system = platform.system()
        commands: list[list[str]] = []
        if system == "Darwin":
            commands = [["/usr/bin/dscacheutil", "-flushcache"], ["/usr/bin/killall", "-HUP", "mDNSResponder"]]
        elif system == "Linux" and shutil.which("resolvectl"):
            commands = [["resolvectl", "flush-caches"]]
        elif system == "Windows":
            commands = [["ipconfig", "/flushdns"]]
        for command in commands:
            try:
                subprocess.run(command, capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                logger.debug("DNS cache flush failed for %s", command, exc_info=True)


class MonkModeService:
    """Persist Monk settings and keep them synchronized with the hosts file."""

    def __init__(self, controller: HostsFileController | None = None) -> None:
        self.controller = controller or HostsFileController()
        self._lock = threading.RLock()

    @staticmethod
    def _load() -> dict[str, Any]:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        return {
            "enabled": bool(settings.get("monk_mode_enabled", False)),
            "sites": normalize_sites(settings.get("monk_sites") or []),
            "schedule_enabled": bool(settings.get("monk_schedule_enabled", False)),
            "schedule": normalize_schedule(settings.get("monk_schedule") or []),
            "schedule_state": settings.get("monk_schedule_state"),
        }

    @staticmethod
    def _save(config: dict[str, Any]) -> None:
        from distr.core.settings import save_settings_to_db

        save_settings_to_db(
            {
                "monk_mode_enabled": bool(config["enabled"]),
                "monk_sites": config["sites"],
                "monk_schedule_enabled": bool(config["schedule_enabled"]),
                "monk_schedule": config["schedule"],
                "monk_schedule_state": config.get("schedule_state"),
            }
        )

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            self.controller.ensure_runtime(config["enabled"])
            config["hosts_applied"] = self.controller.is_applied(config["sites"]) if config["enabled"] else self.controller.is_disabled_applied()
            config["whole_domain_blocking"] = platform.system() == "Darwin"
            config["scheduled_active_now"] = schedule_is_active(config["schedule"]) if config["schedule_enabled"] else None
            return config

    def replace_sites(self, sites: Iterable[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            normalized = normalize_sites(sites)
            if config["enabled"]:
                self.controller.apply(True, normalized)
            config["sites"] = normalized
            self._save(config)
            return self.get_state()

    def add_site(self, site: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            config["sites"].append(normalize_site(site))
            return self.replace_sites(config["sites"])

    def update_site(self, site_id: str, site: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            for index, current in enumerate(config["sites"]):
                if current["id"] == site_id:
                    config["sites"][index] = normalize_site(site, site_id=site_id)
                    return self.replace_sites(config["sites"])
            raise KeyError("Monk Mode website not found.")

    def remove_site(self, site_id: str) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            sites = [site for site in config["sites"] if site["id"] != site_id]
            if len(sites) == len(config["sites"]):
                raise KeyError("Monk Mode website not found.")
            if config["enabled"] and not sites:
                self.controller.apply(False, [])
                config["enabled"] = False
            if not sites:
                config["schedule_enabled"] = False
                config["schedule_state"] = None
            config["sites"] = sites
            self._save(config)
            return self.get_state()

    def set_enabled(self, enabled: bool, *, schedule_state: bool | None = None) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            enabled = bool(enabled)
            if enabled and not config["sites"]:
                raise ValueError("Add at least one web address before enabling Monk Mode.")
            self.controller.apply(enabled, config["sites"])
            config["enabled"] = enabled
            if schedule_state is not None:
                config["schedule_state"] = bool(schedule_state)
            self._save(config)
            return self.get_state()

    def set_schedule(self, enabled: bool, windows: Iterable[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            normalized = normalize_schedule(windows)
            if enabled and not normalized:
                raise ValueError("Add at least one schedule before enabling scheduled Monk Mode.")
            if enabled and not config["sites"]:
                raise ValueError("Add at least one web address before enabling scheduled Monk Mode.")
            if enabled:
                self.controller.ensure_schedule_support(config["enabled"], config["sites"])
            config["schedule_enabled"] = bool(enabled)
            config["schedule"] = normalized
            # Force a one-time reconciliation when scheduling is turned on or changed.
            config["schedule_state"] = None if enabled else config.get("schedule_state")
            self._save(config)
            return self.reconcile_schedule()

    def reconcile_schedule(self, now: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            config = self._load()
            if not config["schedule_enabled"]:
                return self.get_state()
            desired = schedule_is_active(config["schedule"], now=now)
            if config.get("schedule_state") is None or bool(config["schedule_state"]) != desired:
                return self.set_enabled(desired, schedule_state=desired)
            return self.get_state()


_service: MonkModeService | None = None
_service_lock = threading.Lock()


def get_monk_mode_service() -> MonkModeService:
    global _service
    with _service_lock:
        if _service is None:
            _service = MonkModeService()
        return _service
