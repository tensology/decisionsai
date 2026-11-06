"""Local database discovery for Plan DB links (N5 phase 1).

No credentials, no protocol login, no queries: only loopback TCP connect probes, ``stat`` of well-known
unix socket paths, and an optional read of ``brew services list``. ``assert_loopback_host`` is the single
loopback gate later phases (link test, introspection) must call before any connection.
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import stat
import subprocess
from typing import Any, Callable, Iterable

DEFAULT_PORTS: dict[str, tuple[int, ...]] = {"postgres": (5432, 5433), "mysql": (3306, 3307)}
DEFAULT_SOCKETS: dict[str, tuple[str, ...]] = {
    "postgres": ("/tmp/.s.PGSQL.5432", "/tmp/.s.PGSQL.5433"),
    "mysql": ("/tmp/mysql.sock", "/var/run/mysqld/mysqld.sock"),
}
PROBE_HOSTS = ("127.0.0.1", "::1")
BREW_PREFIXES = {"postgresql": "postgres", "postgres": "postgres", "mysql": "mysql", "mariadb": "mysql"}


class NonLoopbackHost(ValueError):
    """The host is not (or does not resolve only to) a loopback address."""


def _is_loopback(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def assert_loopback_host(host: str, *, resolver: Callable[..., list] = socket.getaddrinfo) -> list[str]:
    """Resolve ``host`` and return its addresses only if ALL of them are loopback.

    Rejects unspecified addresses (0.0.0.0, ::), empty hosts, and DNS names that resolve anywhere else.
    Callers must connect to the returned address, not re-resolve the name (defeats DNS rebinding).
    """
    value = str(host or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if not value:
        raise NonLoopbackHost("A database host is required.")
    try:
        literal = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None and literal.is_unspecified:
        raise NonLoopbackHost("Use 127.0.0.1, ::1 or localhost; unspecified addresses are not allowed.")
    try:
        infos = resolver(value, None, 0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise NonLoopbackHost(f"Could not resolve the database host {value!r}.") from exc
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise NonLoopbackHost(f"Could not resolve the database host {value!r}.")
    for address in addresses:
        try:
            ok = _is_loopback(address)
        except ValueError:
            ok = False
        if not ok:
            raise NonLoopbackHost("Only databases on this Mac (127.0.0.1, ::1, localhost or a unix socket) can be linked.")
    return addresses


def probe_port(host: str, port: int, *, timeout: float = 0.3) -> bool:
    """Bare TCP connect to a loopback address; nothing is sent or read."""
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("Invalid port.")
    for address in assert_loopback_host(host):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            try:
                sock.connect((address, port))
                return True
            except OSError:
                continue
    return False


def socket_present(path: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def parse_brew_services(output: str) -> list[dict[str, str]]:
    """Parse ``brew services list`` text; keep only database services."""
    rows = []
    for line in str(output or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        name, status = parts[0], parts[1]
        engine = next((engine for prefix, engine in BREW_PREFIXES.items() if name.lower().startswith(prefix)), None)
        if engine:
            rows.append({"name": name, "engine": engine, "status": status})
    return rows


def brew_services(*, runner: Callable[..., Any] = subprocess.run, timeout: float = 5.0) -> list[dict[str, str]]:
    brew = shutil.which("brew") or next((p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(p)), None)
    if not brew:
        return []
    try:
        result = runner([brew, "services", "list"], capture_output=True, text=True, timeout=timeout, check=False,
                        env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1"})
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_brew_services(result.stdout if getattr(result, "returncode", 1) == 0 else "")


def discover(*, ports: dict[str, Iterable[int]] | None = None, sockets: dict[str, Iterable[str]] | None = None,
             hosts: Iterable[str] = PROBE_HOSTS, include_brew: bool = True,
             probe: Callable[[str, int], bool] = probe_port,
             brew: Callable[[], list[dict[str, str]]] = brew_services) -> dict[str, Any]:
    """Return candidate local databases. Never logs in, never reads credentials."""
    candidates = []
    for engine, engine_ports in (ports if ports is not None else DEFAULT_PORTS).items():
        for port in engine_ports:
            for host in hosts:
                try:
                    reachable = bool(probe(host, int(port)))
                except (NonLoopbackHost, ValueError):
                    reachable = False
                candidates.append({"engine": engine, "host": host, "port": int(port), "socket": None,
                                   "reachable": reachable, "source": "tcp"})
    for engine, paths in (sockets if sockets is not None else DEFAULT_SOCKETS).items():
        for path in paths:
            candidates.append({"engine": engine, "host": "localhost", "port": None, "socket": str(path),
                               "reachable": socket_present(str(path)), "source": "socket"})
    return {"candidates": candidates, "brew_services": brew() if include_brew else [],
            "note": "Discovery only checks whether something is listening. No login is attempted and no credentials are read."}
