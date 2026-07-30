#!/usr/bin/env python3
"""Root launchd helper for DecisionsAI Monk Mode.

This file intentionally uses only the Python standard library. It accepts a
strict JSON desired-state file owned by one user and can change only the marked
DecisionsAI section of /etc/hosts.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

BEGIN = "# BEGIN DecisionsAI Monk Mode"
END = "# END DecisionsAI Monk Mode"
RESOLVER_MARKER = "# DecisionsAI Monk Mode wildcard resolver"
BLOCK_RE = re.compile(rf"(?:^|\n){re.escape(BEGIN)}\n.*?\n{re.escape(END)}(?:\n|$)", re.DOTALL)
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def valid_hostname(hostname: object) -> bool:
    if not isinstance(hostname, str) or len(hostname) > 253:
        return False
    labels = hostname.split(".")
    return len(labels) >= 2 and all(LABEL_RE.fullmatch(label) for label in labels)


def load_state(path: Path, expected_uid: int) -> tuple[bool, list[str], list[str]]:
    stat = path.stat()
    if stat.st_uid != expected_uid or stat.st_mode & 0o077:
        raise ValueError("Unsafe Monk Mode state-file ownership or permissions")
    payload = json.loads(path.read_text(encoding="utf-8"))
    enabled = payload.get("enabled") is True
    hostnames = payload.get("hostnames") or []
    domains = payload.get("domains") or []
    if not isinstance(hostnames, list) or len(hostnames) > 2000:
        raise ValueError("Invalid Monk Mode hostname list")
    if not isinstance(domains, list) or len(domains) > 500:
        raise ValueError("Invalid Monk Mode domain list")
    normalized = sorted(set(hostnames))
    normalized_domains = sorted(set(domains))
    if any(not valid_hostname(hostname) for hostname in normalized + normalized_domains):
        raise ValueError("Invalid Monk Mode hostname")
    if enabled and (not normalized or not normalized_domains):
        raise ValueError("Enabled Monk Mode state has no hostnames or domains")
    return enabled, normalized, normalized_domains


def build_content(current: str, enabled: bool, hostnames: list[str]) -> str:
    base = BLOCK_RE.sub("\n", current).rstrip("\n")
    if not enabled:
        return base + "\n"
    lines = [BEGIN]
    for hostname in hostnames:
        lines.extend((f"127.0.0.1 {hostname}", f"::1 {hostname}"))
    lines.append(END)
    return (f"{base}\n\n" if base else "") + "\n".join(lines) + "\n"


def write_hosts(path: Path, content: str) -> None:
    stat = path.stat()
    fd, temp_name = tempfile.mkstemp(prefix=".decisions-monk-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, stat.st_mode & 0o7777)
        os.chown(temp_name, stat.st_uid, stat.st_gid)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def resolver_content(port: int) -> str:
    if port < 1024 or port > 65535:
        raise ValueError("Invalid Monk Mode DNS port")
    return (
        f"{RESOLVER_MARKER}\n"
        "nameserver 127.0.0.1\n"
        f"port {port}\n"
        "timeout 1\n"
        "options attempts:1\n"
    )


def sync_resolvers(directory: Path, enabled: bool, domains: list[str], port: int) -> None:
    """Create only marked resolver files and preserve every unrelated file."""
    desired = set(domains if enabled else [])
    content = resolver_content(port)
    for domain in desired:
        if not valid_hostname(domain):
            raise ValueError("Invalid Monk Mode resolver domain")
        target = directory / domain
        if target.exists() and not target.read_text(encoding="utf-8").startswith(RESOLVER_MARKER + "\n"):
            raise ValueError(f"Resolver configuration already exists for {domain}")
    if directory.exists():
        for path in directory.iterdir():
            if not path.is_file():
                continue
            try:
                managed = path.read_text(encoding="utf-8").startswith(RESOLVER_MARKER + "\n")
            except OSError:
                managed = False
            if managed and path.name not in desired:
                path.unlink()
    if not desired:
        return
    directory.mkdir(parents=True, exist_ok=True)
    for domain in sorted(desired):
        target = directory / domain
        fd, temp_name = tempfile.mkstemp(prefix=".decisions-monk-resolver-", dir=str(directory))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        return 2
    state_path = Path(argv[1])
    hosts_path = Path(argv[2])
    expected_uid = int(argv[3])
    resolver_dir = Path(argv[4])
    dns_port = int(argv[5])
    enabled, hostnames, domains = load_state(state_path, expected_uid)
    current = hosts_path.read_text(encoding="utf-8")
    desired = build_content(current, enabled, hostnames)
    if desired != current:
        write_hosts(hosts_path, desired)
    sync_resolvers(resolver_dir, enabled, domains, dns_port)
    if hosts_path == Path("/etc/hosts"):
        os.system("/usr/bin/dscacheutil -flushcache >/dev/null 2>&1")
        os.system("/usr/bin/killall -HUP mDNSResponder >/dev/null 2>&1")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as exc:
        print(f"DecisionsAI Monk Mode helper failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
