"""Per-run environment note and Project Notes prompt block.

Every Development / proposal run records which database it worked against (settings module,
engine, DB name, host, local or remote), whether the project's refresh command (for example
``load_latest_db``) ran and when, and whether production was checked and how. The note comes
from the run's recorded tool commands and the project's config, never from the model's claims.
It is stored with the run in ``outcome["environment"]`` (thread execution state and the
execution session's ``output_packet.completion_review``) and shown as a compact note in the thread.

Only ENGINE / NAME / HOST / PORT are ever read from a settings file; credentials are not.
"""
from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any

DEFAULT_REFRESH_COMMANDS = ("load_latest_db",)
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build", ".pi"}
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}
_WRITE_RISK = re.compile(r"\b(?:sudo|systemctl|supervisorctl|restart|reboot|migrate|loaddata|flush|"
                         r"\.save\(|\.delete\(|\.update\(|\.create\(|bulk_create|DROP|DELETE|UPDATE|INSERT|TRUNCATE|"
                         r"git\s+(?:pull|reset|checkout|merge)|rsync|scp\s+\S+\s+\S+:)", re.I)

SUPPORT_STANDARD = (
    "Support-project standard: reproduce and test locally on a fresh copy of the data first "
    "(run the refresh command{refresh} before investigating data issues or running tests). "
    "Then confirm the outcome against the production server READ-ONLY (a read-only query or "
    "Django shell over the existing SSH access): no writes, deploys, restarts, sudo or customer "
    "emails unless the user explicitly asks. In the final reply, state the local database used, "
    "when it was refreshed, and whether production was checked, how, and the result."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def refresh_commands(notes: str) -> list[str]:
    """Refresh commands named in Project Notes ("Refresh command: X"), plus the defaults."""
    found: list[str] = []
    for line in str(notes or "").splitlines():
        match = re.match(r"\s*[-*]?\s*refresh(?:\s+command)?\s*:\s*`?([^`\n]+?)`?\s*$", line, re.I)
        if match:
            found.append(match.group(1).strip())
    for name in DEFAULT_REFRESH_COMMANDS:
        if name not in found:
            found.append(name)
    return found


def is_support_project(notes: str) -> bool:
    """A project is a support project when its notes say so ("Project type: support") or
    name a refresh command such as load_latest_db."""
    text = str(notes or "")
    if re.search(r"^\s*[-*]?\s*project\s+type\s*:\s*support\b", text, re.I | re.M):
        return True
    if re.search(r"^\s*[-*]?\s*refresh(?:\s+command)?\s*:", text, re.I | re.M):
        return True
    return bool(re.search(r"\bload_latest_db\b", text))


def project_notes_block(project: Any) -> str:
    """Instruction block for a project's Project Notes (projects.notes)."""
    notes = str(getattr(project, "notes", "") or "").strip()
    if not notes:
        return ""
    lines = [
        "[PROJECT NOTES]",
        "Standing notes the user keeps for this project (Edit board > Notes). Follow them for every "
        "proposal and run in this project unless the current instruction says otherwise.",
        notes[:6000],
    ]
    if is_support_project(notes):
        named = [c for c in refresh_commands(notes) if c not in DEFAULT_REFRESH_COMMANDS] or ["load_latest_db"]
        lines.append(SUPPORT_STANDARD.format(refresh=" (" + ", ".join(named) + ")"))
    return "\n".join(lines)


def _segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ";&|\n" for char in token):
            segments.append([])
        else:
            segments[-1].append(token)
    out = []
    for args in segments:
        while args and (re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", args[0]) or args[0] in {"env", "time", "command"}):
            args = args[1:]
        if args:
            out.append(args)
    return out


class EnvironmentReceipts:
    """Collect executed commands (with exit codes and times) from turn events."""

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}
        self.commands: list[dict[str, Any]] = []

    def observe(self, event: Any) -> None:
        details = getattr(event, "details", None) or {}
        call_id = str(details.get("call_id") or "")
        command = str(details.get("command") or "")
        if command and call_id:
            self._pending[call_id] = command
        kind = getattr(getattr(event, "kind", None), "value", "")
        if kind != "tool_completed":
            return
        command = command or self._pending.pop(call_id, "")
        if not command.strip():
            return
        code = details.get("exit_code")
        self.commands.append({
            "command": command[:2000],
            "exit_code": code if type(code) is int else None,
            "status": "passed" if code == 0 and type(code) is int else "failed" if type(code) is int or details.get("status") == "error" else "unknown",
            "at": _utc_now(),
        })


def _find_manage_py(folder: str) -> Path | None:
    root = Path(str(folder or "")).expanduser()
    if not root.is_dir():
        return None
    queue = [(root, 0)]
    while queue:
        path, depth = queue.pop(0)
        candidate = path / "manage.py"
        if candidate.is_file():
            return candidate
        if depth >= 3:
            continue
        try:
            children = sorted(p for p in path.iterdir() if p.is_dir() and p.name not in _SKIP_DIRS and not p.name.startswith("."))
        except OSError:
            continue
        queue.extend((child, depth + 1) for child in children)
    return None


def _database_default(text: str) -> dict[str, str]:
    """ENGINE / NAME / HOST / PORT of DATABASES['default'] (credentials are never read)."""
    start = re.search(r"DATABASES\s*=\s*\{.*?['\"]default['\"]\s*:\s*\{", text, re.S)
    if not start:
        return {}
    depth, end = 1, start.end()
    while end < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        end += 1
    block = text[start.end():end]
    out: dict[str, str] = {}
    for key in ("ENGINE", "NAME", "HOST", "PORT"):
        match = re.search(rf"['\"]{key}['\"]\s*:\s*(?:['\"]([^'\"]*)['\"]|(\d+))", block)
        if match:
            out[key.lower()] = match.group(1) if match.group(1) is not None else match.group(2)
    return out


def _engine_label(engine: str) -> str:
    value = str(engine or "").lower()
    for needle, label in (("postgres", "Postgres"), ("mysql", "MySQL"), ("sqlite", "SQLite"), ("oracle", "Oracle")):
        if needle in value:
            return label
    return engine or ""


def detect_database(folder: str, commands: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    manage = _find_manage_py(folder)
    if manage is None:
        return {}
    module = ""
    try:
        match = re.search(r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([\w.]+)['\"]", manage.read_text(errors="replace"))
        module = match.group(1) if match else ""
    except OSError:
        pass
    for row in commands or []:
        match = re.search(r"(?:DJANGO_SETTINGS_MODULE=|--settings[= ])([\w.]+)", str(row.get("command") or ""))
        if match:
            module = match.group(1)
    if not module:
        return {}
    base = manage.parent
    settings_path = base.joinpath(*module.split(".")).with_suffix(".py")
    info: dict[str, Any] = {"settings_module": module, "settings_file": str(settings_path.relative_to(Path(folder).expanduser())) if settings_path.is_file() else ""}
    try:
        text = settings_path.read_text(errors="replace")
    except OSError:
        return info
    db = _database_default(text)
    local_override = settings_path.with_name("settings_local.py")
    if "settings_local" in text and local_override.is_file():
        try:
            override = _database_default(local_override.read_text(errors="replace"))
        except OSError:
            override = {}
        if override:
            db = {**db, **override}
            info["settings_file"] = str(local_override.relative_to(Path(folder).expanduser()))
    if db:
        host = str(db.get("host") or "")
        sqlite = "sqlite" in str(db.get("engine") or "").lower()
        info.update(engine=_engine_label(db.get("engine", "")), name=str(db.get("name") or ""), host=host or ("file" if sqlite else "localhost"),
                    port=str(db.get("port") or ""), scope="local" if sqlite or host.lower() in _LOCAL_HOSTS else "remote")
    return info


def _matches_refresh(args: list[str], names: list[str]) -> str:
    joined = " ".join(args)
    for name in names:
        stem = PurePath(name.split()[0]).name.removesuffix(".sh") if name.strip() else ""
        if not stem:
            continue
        if any(PurePath(a).name.removesuffix(".sh") == stem for a in args) or name in joined:
            return name
    return ""


def build_environment_note(folder: str, notes: str, receipts: EnvironmentReceipts | None) -> dict[str, Any]:
    commands = list(getattr(receipts, "commands", None) or [])
    names = refresh_commands(notes)
    refresh: dict[str, Any] = {"command": "", "ran": False}
    prod: dict[str, Any] = {"checked": False}
    prod_rows: list[dict[str, Any]] = []
    for row in commands:
        for args in _segments(str(row.get("command") or "")):
            first = PurePath(args[0]).name
            matched = _matches_refresh(args, names) if first not in {"cat", "less", "head", "tail", "grep", "rg", "sed", "ls", "echo", "vim", "nano", "open"} else ""
            if matched:
                refresh = {"command": matched, "ran": True, "at": row.get("at"), "exit_code": row.get("exit_code"), "status": row.get("status")}
                break
            if first == "ssh":
                target = next((a for a in args[1:] if not a.startswith("-") and ("@" in a or "." in a)), "")
                prod_rows.append({"target": target, "at": row.get("at"), "status": row.get("status"),
                                  "write_risk": bool(_WRITE_RISK.search(" ".join(args[2:])))})
                break
    if not refresh["ran"]:
        refresh["command"] = next((n for n in names if n not in DEFAULT_REFRESH_COMMANDS), names[0] if names else "")
    if prod_rows:
        statuses = {r["status"] for r in prod_rows}
        prod = {
            "checked": True,
            "how": "ssh " + ", ".join(sorted({r["target"] for r in prod_rows if r["target"]})),
            "commands": len(prod_rows),
            "at": prod_rows[-1]["at"],
            "result": "failed" if "failed" in statuses else "unknown" if "unknown" in statuses else "passed",
            "read_only": not any(r["write_risk"] for r in prod_rows),
        }
    database = detect_database(folder, commands)
    note: dict[str, Any] = {
        "database": database,
        "refresh": refresh,
        "production_check": prod,
        "support_project": is_support_project(notes),
        "recorded_at": _utc_now(),
    }
    note["summary"] = environment_summary(note)
    return note


def environment_summary(note: dict[str, Any]) -> str:
    db = note.get("database") or {}
    if db.get("name") or db.get("engine"):
        where = f"{db.get('host')}:{db['port']}" if db.get("port") else str(db.get("host") or "")
        part_db = (f"DB: {db.get('engine') or 'database'} {db.get('name') or ''} @ {where} ({db.get('scope') or 'unknown'})"
                   f" via {db.get('settings_module')}" + (f" [{db.get('settings_file')}]" if db.get("settings_file") else ""))
    elif db.get("settings_module"):
        part_db = f"DB: settings {db['settings_module']} (database not detected)"
    else:
        part_db = "DB: not detected"
    refresh = note.get("refresh") or {}
    if refresh.get("ran"):
        part_refresh = f"refresh {refresh.get('command')} ran at {refresh.get('at')} ({refresh.get('status')})"
    else:
        part_refresh = f"refresh {refresh.get('command') or 'command'} not run"
    prod = note.get("production_check") or {}
    if prod.get("checked"):
        part_prod = (f"production checked via {prod.get('how')} ×{prod.get('commands')} ({prod.get('result')}"
                     + ("" if prod.get("read_only") else ", possible write") + ")")
    else:
        part_prod = "production not checked"
    return "; ".join((part_db, part_refresh, part_prod))
