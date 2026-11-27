"""Self-serve before asking Paul.

When a worker reports ``needs_input``, Decisions first looks for answers it
already has on this machine: the linked project's folder, other repositories
named in the brief that exist on disk, SSH host aliases and repo scripts that
already reach the server it is asking about. If it finds something, the worker
is resumed once with those pointers instead of pinging the user. Only pointers
(paths, host aliases, script names) are shared; no key, token or password is
read or copied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

MAX_SELF_SERVE_ATTEMPTS = 1

_ACCESS_WORDS = re.compile(
    r"\b(ssh|server|logs?|journalctl|pgbouncer|postgres\w*|production|prod|host|not reachable|"
    r"cannot reach|can't reach|no access|access to|credentials?|settings_local|nginx|gunicorn)\b",
    re.I,
)
_PATH_WORDS = re.compile(
    r"\b(repo(?:sitory)?|checkout|folder|path|directory|codebase|not (?:here|in (?:this|the) (?:project|checkout|repo))|"
    r"cut off|outside (?:the|this) (?:project|workspace))\b",
    re.I,
)
_ABS_PATH = re.compile(r"(?<![\w.])(?:~|/Users|/home|/opt|/srv|/var|/private|/tmp|/Volumes)/[^\s'\"`),;]+")
_DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|co\.za|net|org|io|dev|app))\b", re.I)


@dataclass
class SelfServeAnswer:
    facts: list[str] = field(default_factory=list)

    @property
    def feedback(self) -> str:
        lines = "\n".join(f"- {fact}" for fact in self.facts)
        return (
            "Decisions looked this up on this machine before asking Paul:\n"
            f"{lines}\n"
            "Use these and continue. Stay within the original brief and its safety rules "
            "(read-only on production unless the brief says otherwise). Only report needs_input "
            "again if you are still blocked, and then say exactly what you tried and what you need."
        )

    @property
    def tried(self) -> str:
        return "; ".join(self.facts)[:400]


def _words(*values: str) -> set[str]:
    out: set[str] = set()
    for value in values:
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower()):
            if len(token) >= 4:
                out.add(token)
    return out


def ssh_host_aliases(config_path: Path | None = None) -> list[tuple[str, str]]:
    """(alias, hostname) pairs from ~/.ssh/config; never reads key files."""
    path = config_path or Path("~/.ssh/config").expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    pairs: list[tuple[str, str]] = []
    alias = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        key = key.lower()
        value = value.strip()
        if key == "host":
            alias = value.split()[0] if value and "*" not in value else ""
            if alias:
                pairs.append((alias, ""))
        elif key == "hostname" and alias and pairs and pairs[-1][0] == alias:
            pairs[-1] = (alias, value)
    return pairs


def _ssh_scripts(folder: Path, limit: int = 3) -> list[str]:
    """Repo scripts that already reach a server (e.g. load_latest_db)."""
    found: list[str] = []
    candidates: Iterable[Path] = []
    try:
        candidates = [
            *folder.glob("*.sh"),
            *folder.glob("scripts/*.sh"),
            *folder.glob("bin/*"),
            *folder.rglob("management/commands/*.py"),
        ]
    except OSError:
        return found
    for path in candidates:
        try:
            if not path.is_file() or path.stat().st_size > 200_000 or any(p in {".venv", "node_modules", ".git"} for p in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r"\b(ssh|scp|rsync)\b", text):
            found.append(str(path.relative_to(folder)))
            if len(found) >= limit:
                break
    return found


def find_self_serve_facts(
    *,
    question: str,
    blockers: str = "",
    brief: str = "",
    project_name: str = "",
    project_folder: str = "",
    ssh_config: Path | None = None,
) -> list[str]:
    text = " ".join(str(item or "") for item in (question, blockers))
    facts: list[str] = []
    folder = Path(str(project_folder or "")).expanduser() if project_folder else None

    if _PATH_WORDS.search(text) or _ABS_PATH.search(text):
        if folder and folder.is_dir():
            facts.append(f"This run's project folder is {folder}.")
        seen: set[str] = set()
        for raw in _ABS_PATH.findall(f"{brief} {text}"):
            candidate = Path(raw.rstrip(".:")).expanduser()
            key = str(candidate)
            if key in seen or (folder and candidate == folder):
                continue
            seen.add(key)
            if candidate.exists():
                facts.append(f"{candidate} exists on this machine (named in the brief).")
            if len(seen) >= 4:
                break

    if _ACCESS_WORDS.search(text):
        names = _words(project_name, *_DOMAIN.findall(f"{brief} {text}"))
        matches = []
        for alias, hostname in ssh_host_aliases(ssh_config):
            if names & _words(alias, hostname):
                matches.append(f"{alias}" + (f" ({hostname})" if hostname else ""))
        if matches:
            facts.append("SSH host aliases already configured for this project: " + ", ".join(matches[:3]) + ".")
        if folder and folder.is_dir():
            scripts = _ssh_scripts(folder)
            if scripts:
                facts.append("Repo scripts that already connect to the server: " + ", ".join(scripts) + ".")
    return facts


def self_serve_answer(
    run_data: dict[str, Any],
    *,
    question: str,
    blockers: str = "",
    project_folder: str = "",
    project_name: str = "",
) -> SelfServeAnswer | None:
    """Return pointers to retry with, or ``None`` when Paul really is needed."""
    try:
        attempts = int(run_data.get("self_serve_attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    if attempts >= MAX_SELF_SERVE_ATTEMPTS:
        return None
    brief = " ".join(
        str(run_data.get(key) or "") for key in ("ticket_title", "ticket_description", "user_request", "instruction")
    )
    facts = find_self_serve_facts(
        question=question,
        blockers=blockers,
        brief=brief,
        project_name=project_name or str(run_data.get("project_name") or ""),
        project_folder=project_folder or str(run_data.get("project_folder") or ""),
    )
    return SelfServeAnswer(facts) if facts else None
