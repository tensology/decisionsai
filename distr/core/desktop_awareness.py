"""Ambient desktop awareness — cache-only hot path, budgeted background refresh.

Hot paths (chat / situational / Initiative LLM) MUST only call get_cached_* /
get_desktop_inject_block / desktop_for_situational. Never refresh there.

Background (Initiative schedule tick) may call refresh_desktop_awareness_cache /
purge_dead_desktop_awareness. Fail-open always.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_LINE_CHARS = 400
TIER_A_BUDGET_S = 0.15
TOTAL_REFRESH_BUDGET_S = 0.50
STALE_AFTER_S = 300.0
DELETE_AFTER_S = 24 * 3600.0  # wipe memory + disk after this with no refresh
INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "textfield",
        "text field",
        "checkbox",
        "combobox",
        "combo box",
        "link",
        "menuitem",
        "menu item",
        "tab",
        "slider",
        "searchfield",
        "search field",
        "textarea",
        "text area",
    }
)

_lock = threading.Lock()
_last_injected_hash: str = ""
_persist_loaded = False


def _empty_cache() -> dict[str, Any]:
    return {
        "line": "",
        "content_hash": "",
        "captured_at": 0.0,
        "sidecar_ok": False,
        "app": "",
        "title": "",
        "focused": "",
        "ui": [],
        "stale": True,
    }


_cache: dict[str, Any] = _empty_cache()


def _db_dir() -> Path:
    raw = (os.environ.get("DECISIONS_DB_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".decisions"


def _persist_path() -> Path:
    return _db_dir() / "desktop_awareness.json"


def _hash_line(line: str) -> str:
    return hashlib.sha256((line or "").encode("utf-8")).hexdigest()[:16]


def _load_persist_once() -> None:
    global _persist_loaded
    if _persist_loaded:
        return
    _persist_loaded = True
    path = _persist_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("content_hash"):
            return
        with _lock:
            if _cache.get("content_hash"):
                return
            for key in ("line", "content_hash", "app", "title", "focused", "ui"):
                if key in data:
                    _cache[key] = data[key]
            _cache["captured_at"] = float(data.get("captured_at") or 0.0)
            _cache["sidecar_ok"] = bool(data.get("sidecar_ok"))
            _cache["stale"] = True
    except Exception:
        logger.debug("desktop_awareness: persist load failed", exc_info=True)


def _persist_best_effort(snapshot: dict[str, Any]) -> None:
    try:
        path = _persist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "line": snapshot.get("line") or "",
                    "content_hash": snapshot.get("content_hash") or "",
                    "captured_at": snapshot.get("captured_at") or 0.0,
                    "sidecar_ok": bool(snapshot.get("sidecar_ok")),
                    "app": snapshot.get("app") or "",
                    "title": snapshot.get("title") or "",
                    "focused": snapshot.get("focused") or "",
                    "ui": snapshot.get("ui") or [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("desktop_awareness: persist write failed", exc_info=True)


def _delete_persist_file() -> None:
    try:
        path = _persist_path()
        if path.is_file():
            path.unlink()
    except Exception:
        logger.debug("desktop_awareness: persist delete failed", exc_info=True)


def _tier_a_frontmost_and_title() -> tuple[str, str]:
    """Local-only: NSWorkspace + Quartz. No sidecar. Fail-open."""
    try:
        import platform

        if platform.system() != "Darwin":
            raise RuntimeError("not darwin")
        from AppKit import NSWorkspace

        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        app = ((front.localizedName() if front else "") or "").strip()
        if not app:
            return "", ""
        title = ""
        try:
            import Quartz

            wins = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID,
            )
            app_l = app.lower()
            for w in wins or []:
                owner = str(w.get(Quartz.kCGWindowOwnerName) or "")
                if owner.lower() != app_l:
                    continue
                name = str(w.get(Quartz.kCGWindowName) or "").strip()
                if name:
                    title = name
                    break
        except Exception:
            title = ""
        return app, title
    except Exception:
        pass
    try:
        from distr.core.actions.desktop import get_frontmost_app_name

        return (get_frontmost_app_name() or "").strip(), ""
    except Exception:
        return "", ""


def _tier_b_interactive_labels(*, timeout_s: float) -> tuple[str, list[str], bool]:
    """Optional shallow tree via sidecar. Returns focused, ui labels, sidecar_ok."""
    if timeout_s <= 0.05:
        return "", [], False
    try:
        from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool

        result = call_sidecar_tool(
            "get_window_tree",
            {"depth": 2},
            timeout=max(0.1, float(timeout_s)),
        )
    except Exception:
        return "", [], False
    if not isinstance(result, dict):
        return "", [], False
    elements = result.get("elements") or []
    focused = ""
    ui: list[str] = []
    if not isinstance(elements, list):
        return "", [], True
    for el in elements:
        if not isinstance(el, dict):
            continue
        role = str(el.get("control_type") or el.get("role") or "").strip().lower()
        name = str(el.get("name") or el.get("title") or "").strip()
        if not name:
            continue
        if el.get("focused") or el.get("is_focused"):
            focused = name[:80]
        if role in INTERACTIVE_ROLES or "button" in role or "field" in role:
            label = f"[{role.title()} {name[:40]}]" if role else f"[{name[:40]}]"
            if label not in ui:
                ui.append(label)
            if len(ui) >= 8:
                break
    return focused, ui, True


def _format_line(*, app: str, title: str, focused: str, ui: list[str]) -> str:
    if not app and not title:
        return ""
    head = f"desktop: {app}" if app else "desktop:"
    if title:
        head = f'{head} — "{title[:80]}"'
    parts = [head]
    if focused:
        parts.append(f"focused: {focused[:80]}")
    if ui:
        parts.append("ui: " + " ".join(ui[:8]))
    line = "\n".join(parts)
    if len(line) > MAX_LINE_CHARS:
        line = line[: MAX_LINE_CHARS - 1].rstrip() + "…"
    return line


def compact_desktop_snapshot(*, force_tier_b: bool = False) -> dict[str, Any]:
    """Budgeted snapshot for background refresh or explicit tool use. Fail-open."""
    started = time.monotonic()
    app, title = "", ""
    try:
        app, title = _tier_a_frontmost_and_title()
    except Exception:
        logger.debug("desktop_awareness: tier A failed", exc_info=True)
    elapsed = time.monotonic() - started
    focused, ui, sidecar_ok = "", [], False
    remain = TOTAL_REFRESH_BUDGET_S - elapsed
    if force_tier_b or (elapsed < TIER_A_BUDGET_S and remain > 0.08):
        try:
            focused, ui, sidecar_ok = _tier_b_interactive_labels(
                timeout_s=min(remain, 0.35)
            )
        except Exception:
            focused, ui, sidecar_ok = "", [], False
    line = _format_line(app=app, title=title, focused=focused, ui=ui)
    return {
        "line": line,
        "content_hash": _hash_line(line) if line else "",
        "captured_at": time.time(),
        "sidecar_ok": sidecar_ok,
        "app": app,
        "title": title,
        "focused": focused,
        "ui": ui,
        "stale": False,
    }


def purge_dead_desktop_awareness(*, now: float | None = None) -> bool:
    """If cache is older than DELETE_AFTER_S, clear it and delete the file."""
    global _last_injected_hash
    _load_persist_once()
    now_ts = time.time() if now is None else now
    with _lock:
        captured = float(_cache.get("captured_at") or 0.0)
        if not captured:
            return False
        age = now_ts - captured
        if age < DELETE_AFTER_S:
            return False
        _cache.clear()
        _cache.update(_empty_cache())
        _last_injected_hash = ""
    _delete_persist_file()
    logger.info("desktop_awareness: purged dead cache (age=%.0fs)", age)
    return True


def refresh_desktop_awareness_cache(*, force_tier_b: bool = False) -> dict[str, Any]:
    """Background-only refresh. On failure, keep last good cache (unless purged dead)."""
    purge_dead_desktop_awareness()
    _load_persist_once()
    try:
        snap = compact_desktop_snapshot(force_tier_b=force_tier_b)
    except Exception:
        logger.debug("desktop_awareness: refresh failed", exc_info=True)
        with _lock:
            return dict(_cache)
    if not snap.get("line") and not snap.get("app"):
        with _lock:
            _cache["sidecar_ok"] = bool(snap.get("sidecar_ok"))
            return dict(_cache)
    with _lock:
        _cache.update(snap)
        out = dict(_cache)
    _persist_best_effort(out)
    return out


def get_cached_snapshot() -> dict[str, Any]:
    """Hot-path safe: memory only (may purge dead entries; never fetches)."""
    purge_dead_desktop_awareness()
    _load_persist_once()
    with _lock:
        out = dict(_cache)
    captured = float(out.get("captured_at") or 0.0)
    age = (time.time() - captured) if captured else None
    out["stale"] = (age is None) or (age > STALE_AFTER_S) or (not out.get("line"))
    out["age_s"] = age
    return out


def get_cached_desktop_line() -> str:
    """Hot-path safe: return cached ambient line or empty."""
    return str(get_cached_snapshot().get("line") or "")


def get_desktop_inject_block(*, mark_injected: bool = True) -> str:
    """Hot-path inject with hash dedup. Empty if nothing cached."""
    global _last_injected_hash
    snap = get_cached_snapshot()
    line = str(snap.get("line") or "").strip()
    if not line:
        return ""
    chash = str(snap.get("content_hash") or "")
    stale = bool(snap.get("stale"))
    with _lock:
        if mark_injected and chash and chash == _last_injected_hash:
            return "desktop: (unchanged)"
        if mark_injected and chash:
            _last_injected_hash = chash
    if stale:
        return f"{line}\n(stale: true)"
    return line


def desktop_for_situational() -> dict[str, Any]:
    """Hot-path fragment for situational dict (cache only)."""
    snap = get_cached_snapshot()
    line = str(snap.get("line") or "").strip()
    if not line:
        return {}
    return {
        "line": line,
        "content_hash": snap.get("content_hash") or "",
        "app": snap.get("app") or "",
        "title": snap.get("title") or "",
        "stale": bool(snap.get("stale")),
        "age_s": snap.get("age_s"),
    }


def _reset_cache_for_tests() -> None:
    global _last_injected_hash, _persist_loaded
    with _lock:
        _cache.clear()
        _cache.update(_empty_cache())
    _last_injected_hash = ""
    _persist_loaded = True


def _set_cache_for_tests(**kwargs: Any) -> None:
    with _lock:
        _cache.update(kwargs)
        if "line" in kwargs and "content_hash" not in kwargs:
            _cache["content_hash"] = _hash_line(str(kwargs.get("line") or ""))
