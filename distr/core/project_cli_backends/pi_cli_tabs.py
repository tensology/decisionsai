"""Durable Development thread CLI session metadata.

PTY processes live only in-process (_startup_sessions) and are killed on Decisions
exit (atexit). This store keeps one session record per thread (tab_id=thread-<chatId>)
so recovery can respawn the same CLIs in the project folder after a restart.
There is no multi-tab UI — the "tabs" list is one row per chat/thread.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Optional

_LOCK = threading.Lock()


def _path() -> str:
    from distr.core.paths import DB_DIR

    os.makedirs(DB_DIR, exist_ok=True)
    return os.path.join(DB_DIR, "pi_cli_tabs.json")


def _empty() -> dict[str, Any]:
    return {"version": 1, "projects": {}}


def _read() -> dict[str, Any]:
    path = _path()
    if not os.path.isfile(path):
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        if not isinstance(data, dict):
            return _empty()
        data.setdefault("version", 1)
        data.setdefault("projects", {})
        if not isinstance(data["projects"], dict):
            data["projects"] = {}
        return data
    except Exception:
        return _empty()


def _write(data: dict[str, Any]) -> None:
    path = _path()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def load_project_tabs(project_id: int) -> dict[str, Any]:
    """Return {tabs: [...], active_tab_id: str|None} for a project."""
    with _LOCK:
        data = _read()
        row = data["projects"].get(str(int(project_id))) or {}
        tabs = row.get("tabs") if isinstance(row.get("tabs"), list) else []
        cleaned = []
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            tab_id = str(tab.get("tab_id") or "").strip() or str(uuid.uuid4())
            cleaned.append({
                "tab_id": tab_id,
                "process_id": str(tab.get("process_id") or "").strip() or None,
                "command": str(tab.get("command") or "pi").strip() or "pi",
                "label": str(tab.get("label") or tab.get("command") or "pi").strip() or "pi",
                "cwd": str(tab.get("cwd") or "").strip() or None,
                "visible": bool(tab.get("visible", True)),
                "created_at": float(tab.get("created_at") or 0) or time.time(),
            })
        active = str(row.get("active_tab_id") or "").strip() or None
        if active and not any(t["tab_id"] == active for t in cleaned):
            active = next((t["tab_id"] for t in cleaned if t.get("visible")), None)
            if not active and cleaned:
                active = cleaned[0]["tab_id"]
        return {"project_id": int(project_id), "tabs": cleaned, "active_tab_id": active}


def save_project_tabs(
    project_id: int,
    *,
    tabs: list[dict[str, Any]],
    active_tab_id: Optional[str] = None,
) -> dict[str, Any]:
    """Replace the durable tab list for a project. Does not spawn or kill PTYs."""
    cleaned: list[dict[str, Any]] = []
    for tab in tabs or []:
        if not isinstance(tab, dict):
            continue
        tab_id = str(tab.get("tab_id") or "").strip() or str(uuid.uuid4())
        cleaned.append({
            "tab_id": tab_id,
            "process_id": str(tab.get("process_id") or "").strip() or None,
            "command": str(tab.get("command") or "pi").strip() or "pi",
            "label": str(tab.get("label") or tab.get("command") or "pi").strip() or "pi",
            "cwd": str(tab.get("cwd") or "").strip() or None,
            "visible": bool(tab.get("visible", True)),
            "created_at": float(tab.get("created_at") or 0) or time.time(),
        })
    active = str(active_tab_id or "").strip() or None
    if active and not any(t["tab_id"] == active for t in cleaned):
        active = next((t["tab_id"] for t in cleaned if t.get("visible")), None)
        if not active and cleaned:
            active = cleaned[0]["tab_id"]
    with _LOCK:
        data = _read()
        data["projects"][str(int(project_id))] = {
            "tabs": cleaned,
            "active_tab_id": active,
            "updated_at": time.time(),
        }
        _write(data)
    return {"project_id": int(project_id), "tabs": cleaned, "active_tab_id": active}


def clear_project_tabs(project_id: int) -> None:
    with _LOCK:
        data = _read()
        data["projects"].pop(str(int(project_id)), None)
        _write(data)
