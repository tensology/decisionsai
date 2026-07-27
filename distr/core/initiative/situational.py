"""Compact situational spine for Initiative cycles (time, gaps, Decisions handoff)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Prefer resume-from-handoff over work_scan nags after this idle.
LONG_IDLE_SECONDS = 2 * 3600


def format_gap_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        rem = secs % 60
        return f"{mins}m" if rem == 0 else f"{mins}m {rem}s"
    hours = mins // 60
    rem_m = mins % 60
    if hours < 48:
        return f"{hours}h" if rem_m == 0 else f"{hours}h {rem_m}m"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d" if rem_h == 0 else f"{days}d {rem_h}h"


def peek_handoff(project_folder: str, *, max_lines: int = 15) -> str:
    """First lines of project handoff.md (.decisions preferred, then .cursor fallback)."""
    root = Path(str(project_folder or "").strip()).expanduser()
    if not root.is_dir():
        return ""
    for rel in (
        Path(".decisions") / "memory" / "handoff.md",
        Path(".cursor") / "memory" / "handoff.md",
    ):
        path = root / rel
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        peek = "\n".join(lines[:max_lines]).strip()
        if peek:
            return peek
    return ""


def _project_folder(active_project: dict, developer_context: dict) -> str:
    folder = str((active_project or {}).get("folder_location") or "").strip()
    if folder:
        return folder
    runtime = (developer_context or {}).get("runtime") or {}
    if isinstance(runtime, dict):
        return str(runtime.get("cwd") or "").strip()
    active = (developer_context or {}).get("active_project") or {}
    if isinstance(active, dict):
        return str(active.get("folder_location") or "").strip()
    return ""


def _handoff_body_line(handoff: str) -> str:
    for ln in (handoff or "").splitlines():
        text = ln.strip()
        if not text or text.startswith("#") or text.startswith("_"):
            continue
        return text
    return ""


def build_situational(
    *,
    active_project: dict | None = None,
    developer_context: dict | None = None,
    last_cycle_at: float | None = None,
    last_chat_stream_at: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Machine-readable situational block for prompts and suggest/draft text."""
    local = now or datetime.now().astimezone()
    utc = local.astimezone(timezone.utc)
    epoch = local.timestamp()
    active_project = active_project or {}
    developer_context = developer_context or {}
    folder = _project_folder(active_project, developer_context)

    idle_gap_seconds: float | None = None
    if last_chat_stream_at:
        idle_gap_seconds = max(0.0, epoch - float(last_chat_stream_at))
    idle_gap = format_gap_seconds(idle_gap_seconds)

    last_cycle_age_seconds: float | None = None
    if last_cycle_at:
        last_cycle_age_seconds = max(0.0, epoch - float(last_cycle_at))
    last_cycle_age = format_gap_seconds(last_cycle_age_seconds)

    handoff = peek_handoff(folder)

    desktop: dict[str, Any] = {}
    try:
        # Hot path: cache only — never refresh here.
        from distr.core.desktop_awareness import desktop_for_situational

        desktop = desktop_for_situational()
    except Exception:
        desktop = {}

    return {
        "now_local": local.replace(microsecond=0).isoformat(),
        "now_utc": utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "weekday": local.strftime("%A"),
        "timezone": local.tzname() or str(local.tzinfo or ""),
        "idle_gap": idle_gap,
        "idle_gap_seconds": idle_gap_seconds,
        "last_chat_age": idle_gap,
        "last_cycle_age": last_cycle_age,
        "last_cycle_age_seconds": last_cycle_age_seconds,
        "handoff_peek": handoff,
        "project_folder": folder,
        "desktop": desktop,
    }


def should_prefer_handoff_resume(situational: dict[str, Any] | None) -> bool:
    """True when idle is long and a real handoff body exists."""
    if not isinstance(situational, dict):
        return False
    idle_s = situational.get("idle_gap_seconds")
    try:
        idle_val = float(idle_s) if idle_s is not None else 0.0
    except (TypeError, ValueError):
        idle_val = 0.0
    if idle_val < LONG_IDLE_SECONDS:
        return False
    return bool(_handoff_body_line(str(situational.get("handoff_peek") or "")))


def handoff_resume_proposal(situational: dict[str, Any] | None) -> dict[str, Any] | None:
    """Raw proposal dict for resume-from-handoff (caller wraps ProposedAction)."""
    if not should_prefer_handoff_resume(situational):
        return None
    assert isinstance(situational, dict)
    body = _handoff_body_line(str(situational.get("handoff_peek") or ""))
    idle = situational.get("idle_gap") or "a while"
    peek = (body[:160] + ("…" if len(body) > 160 else "")) if body else "the last project notes"
    description = (
        f"I noticed we left off about {idle} ago. Last notes: {peek}. "
        "Want me to pick that up?"
    )
    return {
        "action_type": "suggestion",
        "description": description,
        "draft": description,
        "telegram_message": description,
        "payload": {
            "kind": "handoff_resume",
            "idle_gap": idle,
            "project_folder": situational.get("project_folder") or "",
        },
    }


def situational_one_liner(situational: dict[str, Any] | None) -> str:
    """Short human line for suggest/draft messages (no transcript dump)."""
    if not isinstance(situational, dict) or not situational:
        return ""
    bits: list[str] = []
    if situational.get("idle_gap"):
        bits.append(f"idle {situational['idle_gap']}")
    elif situational.get("last_cycle_age"):
        bits.append(f"last cycle {situational['last_cycle_age']} ago")
    body = _handoff_body_line(str(situational.get("handoff_peek") or ""))
    if body:
        bits.append(f"handoff: {body[:90]}")
    desktop = situational.get("desktop") if isinstance(situational.get("desktop"), dict) else {}
    app = str(desktop.get("app") or "").strip()
    title = str(desktop.get("title") or "").strip()
    if app or title:
        bits.append(f"desktop: {app}" + (f' — "{title[:60]}"' if title else ""))
    if not bits:
        now_local = situational.get("now_local") or ""
        if now_local:
            bits.append(f"now {now_local}")
    return "; ".join(bits)


def format_situational_prompt_block(situational: dict[str, Any] | None) -> str:
    if not isinstance(situational, dict) or not situational:
        return ""
    lines = [
        "Situational:",
        f"- now: {situational.get('now_local')} ({situational.get('weekday')}, {situational.get('timezone')})",
        f"- now_utc: {situational.get('now_utc')}",
    ]
    if situational.get("idle_gap"):
        lines.append(f"- idle_gap_since_chat: {situational['idle_gap']}")
    if situational.get("last_cycle_age"):
        lines.append(f"- last_initiative_cycle: {situational['last_cycle_age']} ago")
    if should_prefer_handoff_resume(situational):
        lines.append(
            "- preference: long idle + handoff present — prefer resume-from-handoff "
            "over new work_scan nags unless something is on fire."
        )
    desktop = situational.get("desktop") if isinstance(situational.get("desktop"), dict) else {}
    if desktop.get("line"):
        lines.append("- desktop_ambient (cached, may be seconds old):")
        for ln in str(desktop["line"]).splitlines()[:6]:
            lines.append(f"  {ln}")
        if desktop.get("stale"):
            lines.append("  (stale: true)")
    if situational.get("handoff_peek"):
        lines.append("- handoff_peek:")
        for ln in str(situational["handoff_peek"]).splitlines()[:12]:
            lines.append(f"  {ln}")
    return "\n".join(lines) + "\n"
