"""User-facing alert when a Tensology ticket arrives for execution.

A Tensology ticket can land while the user is looking at another part of
Decisions (or another window entirely), so arriving work needs a visible
message and an audible cue rather than only a new row in a list.

Everything here is best-effort: a missing sound file, an unsupported desktop
platform, or a denied notification permission must never block the ticket from
being claimed and executed.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SOUND_FILENAME = "blip.mp3"
TITLE = "DecisionsAI"


def _notifications_disabled() -> bool:
    """True when alerts should be suppressed (tests, or explicit opt-out)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    raw = os.environ.get("DECISIONS_TENSOLOGY_NOTIFY")
    if raw is not None and raw.strip().lower() in {"0", "false", "no", "off"}:
        return True
    return False


def _project_root() -> Path:
    # distr/core/tensology_notify.py -> repository root is three levels up.
    return Path(__file__).resolve().parents[2]


def _sound_path() -> Path:
    return _project_root() / "assets" / "sounds" / SOUND_FILENAME


def play_incoming_sound() -> None:
    """Play the short incoming chime in a background thread. Never raises."""
    if _notifications_disabled():
        return
    sound = _sound_path()
    if not sound.is_file():
        logger.debug("Incoming sound missing: %s", sound)
        return

    def _play() -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["afplay", str(sound)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                return
            if sys.platform.startswith("linux"):
                for player in (
                    ["paplay", str(sound)],
                    ["aplay", "-q", str(sound)],
                    ["mpg123", "-q", str(sound)],
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(sound)],
                ):
                    try:
                        subprocess.Popen(
                            player,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL,
                        )
                        return
                    except FileNotFoundError:
                        continue
        except Exception:
            logger.debug("Incoming sound could not be played", exc_info=True)

    threading.Thread(target=_play, daemon=True).start()


def _osascript_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def show_desktop_notification(title: str, message: str) -> None:
    """Show a native desktop notification. Never raises."""
    if sys.platform != "darwin" or _notifications_disabled():
        return
    safe_title = _osascript_escape(str(title or TITLE))
    safe_message = _osascript_escape(str(message or "Incoming work"))
    try:
        subprocess.Popen(
            ["osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("Desktop notification could not be shown", exc_info=True)


def notify_tensology_ticket_incoming(
    *,
    ticket: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    chat_id: int | None = None,
) -> None:
    """Alert the user that a Tensology ticket has arrived and is being worked.

    Plays the incoming chime and shows a desktop notification naming the
    project and ticket. Best-effort only.
    """
    ticket = ticket or {}
    project = project or {}
    reference = str(ticket.get("reference") or ticket.get("id") or "").strip()
    title = str(ticket.get("title") or "Incoming ticket").strip()
    project_name = str(project.get("name") or ticket.get("project_name") or "").strip()

    heading = f"Incoming ticket {reference}".strip() if reference else "Incoming ticket"
    body = f"{project_name}: {title}" if project_name else title
    if chat_id:
        body = f"{body} (thread {chat_id})"

    play_incoming_sound()
    show_desktop_notification(heading, body)
    try:
        from distr.core.orchestration_events import emit_user_notification

        emit_user_notification(
            channel="desktop",
            text=f"{heading} — {body}",
            ticket_id=ticket.get("id") if isinstance(ticket.get("id"), int) else None,
            project_id=project.get("id") if isinstance(project.get("id"), int) else None,
            payload={"source": "tensology", "reference": reference, "thread_id": str(chat_id or "")},
        )
    except Exception:
        logger.debug("Incoming ticket notification ledger entry failed", exc_info=True)
