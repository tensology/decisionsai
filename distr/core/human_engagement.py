"""Human-facing engagement policy for Decisions.

This module sits above transports such as Telegram, desktop TTS, and remote
control.  Callers describe what they want to say; the policy decides whether it
should be said, where, in which format, and whether attachments are allowed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal


EngagementPriority = Literal["low", "normal", "high", "urgent"]
DeliveryFormat = Literal["text", "voice", "desktop_tts", "remote_audio", "silent"]

_PLACEHOLDER_PROJECT_NAMES = {
    "",
    "quiet app",
    "cursor project",
    "codex project",
    "untitled",
    "untitled project",
}
_DEDUPED_STATUS_KINDS = {
    "status_update",
    "workflow_status",
    "workflow_report",
    "workflow_terminal",
    "workflow_waiting",
    "workflow_idle_nudge",
    "automation_status",
    "automation_run",
    "execution_terminal",
    "execution_waiting",
    "idle_nudge",
    "initiative_update",
    "tool_result_status",
    "telegram_status",
}
_LOW_VALUE_STATUS_RE = re.compile(
    r"(?i)\b("
    r"saved\s+successfully|"
    r"saved\s+it\s+in\s+decisions|"
    r"saved\s+in\s+decisions|"
    r"saved\s+the\s+details\s+in\s+decisions|"
    r"logged\s+the\s+details\s+in\s+decisions|"
    r"stored\s+in\s+decisions|"
    r"recorded\s+in\s+decisions|"
    r"configuration\s+file\s+saved|"
    r"settings\s+saved|"
    r"automation\s+saved|"
    r"workflow\s+saved|"
    r"screenshot\s+(?:captured|saved|stored)|"
    r"evidence\s+(?:captured|saved|stored)|"
    r"memory\s+(?:captured|saved|stored|recorded)"
    r")\b"
)


@dataclass(frozen=True)
class EngagementAttachment:
    path: str
    kind: str = "document"
    name: str = ""

    def usable(self) -> bool:
        if not self.path or not os.path.exists(self.path):
            return False
        try:
            return os.path.getsize(self.path) > 0
        except OSError:
            return False


@dataclass(frozen=True)
class EngagementIntent:
    source: str
    surface: str
    kind: str
    priority: EngagementPriority
    subject_type: str
    subject_id: str
    state_fingerprint: str
    body: str
    voice_body: str | None = None
    attachments: list[EngagementAttachment] = field(default_factory=list)
    requires_response: bool = False
    explicit_artifact_intent: bool = False
    explicit_notification_intent: bool = False
    allow_text: bool = True
    allow_voice: bool = True
    workflow_id: int | None = None
    run_id: int | None = None
    step_id: int | None = None
    project_id: int | None = None
    execution_session_id: int | None = None
    thread_id: str | None = None

    def with_state(self, state_fingerprint: str) -> "EngagementIntent":
        return replace(self, state_fingerprint=state_fingerprint)


@dataclass(frozen=True)
class DeliveryDecision:
    should_send: bool
    channel: str
    format: DeliveryFormat
    final_text: str | None = None
    final_voice_text: str | None = None
    attachments: list[EngagementAttachment] = field(default_factory=list)
    dedupe_key: str = ""
    suppress_reason: str = ""
    route_reason: str = ""


def _ensure_ledger_table() -> None:
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS human_engagement_ledger (
                    dedupe_key VARCHAR PRIMARY KEY,
                    source VARCHAR,
                    surface VARCHAR,
                    kind VARCHAR,
                    subject_type VARCHAR,
                    subject_id VARCHAR,
                    state_fingerprint VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'sent',
                    channel VARCHAR,
                    format VARCHAR,
                    message_hash VARCHAR,
                    sent_at FLOAT NOT NULL,
                    answered_at FLOAT,
                    next_allowed_at FLOAT,
                    send_count INTEGER NOT NULL DEFAULT 1,
                    metadata TEXT
                )
            """))
            conn.commit()
    except Exception:
        return


def reset_engagement_ledger() -> None:
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        _ensure_ledger_table()
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM human_engagement_ledger"))
            conn.commit()
    except Exception:
        return


def _ledger_row(dedupe_key: str) -> dict[str, Any] | None:
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        _ensure_ledger_table()
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT dedupe_key, status, answered_at, next_allowed_at, send_count
                    FROM human_engagement_ledger
                    WHERE dedupe_key = :dedupe_key
                """),
                {"dedupe_key": dedupe_key},
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def _write_ledger(intent: EngagementIntent, decision: DeliveryDecision, now: float) -> None:
    try:
        from sqlalchemy import text
        from distr.core.db import engine

        _ensure_ledger_table()
        message_hash = hashlib.sha256(
            json.dumps(
                {
                    "text": decision.final_text,
                    "voice": decision.final_voice_text,
                    "attachments": [a.path for a in decision.attachments],
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        metadata = {
            "workflow_id": intent.workflow_id,
            "run_id": intent.run_id,
            "step_id": intent.step_id,
            "project_id": intent.project_id,
            "execution_session_id": intent.execution_session_id,
            "thread_id": intent.thread_id,
            "requires_response": intent.requires_response,
        }
        with engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO human_engagement_ledger(
                        dedupe_key, source, surface, kind, subject_type, subject_id,
                        state_fingerprint, status, channel, format, message_hash,
                        sent_at, answered_at, next_allowed_at, send_count, metadata
                    )
                    VALUES (
                        :dedupe_key, :source, :surface, :kind, :subject_type,
                        :subject_id, :state_fingerprint, 'sent', :channel, :format,
                        :message_hash, :sent_at, NULL, NULL, 1, :metadata
                    )
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        sent_at = excluded.sent_at,
                        channel = excluded.channel,
                        format = excluded.format,
                        message_hash = excluded.message_hash,
                        send_count = human_engagement_ledger.send_count + 1,
                        metadata = excluded.metadata
                """),
                {
                    "dedupe_key": decision.dedupe_key,
                    "source": intent.source,
                    "surface": intent.surface,
                    "kind": intent.kind,
                    "subject_type": intent.subject_type,
                    "subject_id": intent.subject_id,
                    "state_fingerprint": intent.state_fingerprint,
                    "channel": decision.channel,
                    "format": decision.format,
                    "message_hash": message_hash,
                    "sent_at": now,
                    "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
                },
            )
            conn.commit()
    except Exception:
        return


def _dedupe_key(intent: EngagementIntent) -> str:
    raw = "|".join([
        intent.source or "",
        intent.kind or "",
        intent.subject_type or "",
        str(intent.subject_id or ""),
        str(intent.state_fingerprint or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_engagement_text(text: str, *, preserve_links: bool = False) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    lower = clean.lower()
    if "has shut down" in lower:
        return "Goodbye."
    if "welcome back" in lower or " is online" in lower:
        return "I'm back online."

    clean = clean.replace("⚠️", "").replace("✅", "").replace("❌", "").strip()

    workflow_failure = re.search(
        r"(?is)\bworkflow\s+(.+?)\s+(?:\[failed\]|failed\b|finished with status failed)",
        clean,
    )
    if workflow_failure and (
        "quota" in lower
        or "billing" in lower
        or "api-errors" in lower
        or "openai" in lower
        or "raw stack" in lower
    ):
        name = workflow_failure.group(1).strip(" :-[]")
        if name.lower().startswith("workflow "):
            name = name[9:].strip()
        return f"{name or 'Workflow'} failed. I've logged the details in Decisions."

    if not preserve_links:
        clean = re.sub(r"https?://\S+", "", clean)
    clean = re.sub(r"^\s*\[Initiative\]\s*", "", clean)
    clean = re.sub(r"\[APPROVE\]|\[ESCALATE\]|\[SUGGEST_ONLY\]", "", clean)
    clean = re.sub(r"\n{2,}Draft:\n.*?(?=\n{2,}Payload:|\n{2,}[A-Z][A-Za-z ]{2,}:|\Z)", "", clean, flags=re.S)
    clean = re.sub(r"\n{1,}Payload:\s*\{.*?\}(?=\n|$)", "", clean, flags=re.S)
    clean = re.sub(r"(?i)^quick update:\s*#{1,6}\s*quick check-?in\s*[-:]*\s*", "Quick check-in: ", clean)
    clean = re.sub(r"(?im)^\s*#{1,6}\s*", "", clean)
    clean = re.sub(r"(?m)^\s*[-*]\s+", "", clean)
    clean = re.sub(r"(?m)^\s*\d+\.\s+", "", clean)
    clean = clean.replace("**", "").replace("__", "").replace("`", "")
    clean = re.sub(r"(?i)^quick update:\s*quick check-?in\s*[-:]*\s*", "Quick check-in: ", clean)
    clean = re.sub(r"(?i)^quick update:\s*", "", clean)
    clean = re.sub(r"\s+([.,;:])", r"\1", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    if len(clean) > 900:
        clean = clean[:890].rsplit(" ", 1)[0].rstrip() + "\nMore detail is in the app."
    return clean


def is_low_value_status_text(text: str) -> bool:
    """Return whether text is a low-value status that should not become a voice nudge."""
    clean = sanitize_engagement_text(text)
    return bool(clean and _LOW_VALUE_STATUS_RE.search(clean))


def human_project_label(name: str | None, *, workspace_path: str | None = None, surface: str = "project") -> str:
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    is_project_id = bool(re.fullmatch(r"(?i)project\s+\d+", clean))
    if clean.lower() not in _PLACEHOLDER_PROJECT_NAMES and not is_project_id:
        return clean

    if workspace_path:
        base = Path(workspace_path).expanduser().name.strip()
        if base and base.lower() not in _PLACEHOLDER_PROJECT_NAMES:
            return base

    surface_label = (surface or "project").strip().title()
    if surface_label.lower() in {"cursor", "codex"}:
        return f"the {surface_label} session"
    return "the project session"


def _explicit_text_only_enabled() -> bool:
    try:
        from distr.core.settings import load_settings_from_db
        from distr.core.integrations.telegram.response_format import load_response_format_settings

        text_only, _auto = load_response_format_settings(load_settings_from_db())
        return bool(text_only)
    except Exception:
        return False


def _route_for_intent(intent: EngagementIntent, telegram_manager: Any, allow_telegram: bool, now: float) -> tuple[str, str]:
    surface = (intent.surface or "").strip().lower()
    if surface in {"telegram", "desktop", "remote"}:
        return surface, f"intent requested {surface}"
    try:
        from distr.core.notification_routing import choose_notification_route

        route = choose_notification_route(
            telegram_manager=telegram_manager,
            allow_telegram=allow_telegram,
            now=now,
        )
        if route:
            return route.surface, route.reason
    except Exception:
        pass
    if allow_telegram:
        return "telegram", "Telegram fallback"
    return "silent", "no active delivery surface"


class HumanEngagementService:
    def __init__(
        self,
        *,
        telegram_manager: Any = None,
        now: Callable[[], float] = time.time,
        allow_telegram: bool = True,
    ) -> None:
        self.telegram_manager = telegram_manager
        self.now = now
        self.allow_telegram = allow_telegram

    def decide(self, intent: EngagementIntent) -> DeliveryDecision:
        now = float(self.now())
        dedupe_key = _dedupe_key(intent)
        existing = _ledger_row(dedupe_key)
        if intent.requires_response and existing and not existing.get("answered_at"):
            return DeliveryDecision(
                should_send=False,
                channel="silent",
                format="silent",
                dedupe_key=dedupe_key,
                suppress_reason="awaiting_user_response",
            )
        if (
            intent.kind in _DEDUPED_STATUS_KINDS
            and intent.priority not in {"high", "urgent"}
            and not intent.explicit_notification_intent
            and is_low_value_status_text(intent.body)
        ):
            return DeliveryDecision(
                should_send=False,
                channel="silent",
                format="silent",
                dedupe_key=dedupe_key,
                suppress_reason="low_value_status",
            )
        if (
            existing
            and intent.kind in _DEDUPED_STATUS_KINDS
            and intent.priority not in {"high", "urgent"}
        ):
            return DeliveryDecision(
                should_send=False,
                channel="silent",
                format="silent",
                dedupe_key=dedupe_key,
                suppress_reason="duplicate_state",
            )

        preserve_links = (
            intent.kind == "remote_link"
            or "/api/remote/" in str(intent.body or "")
            or "/api/remote/" in str(intent.voice_body or "")
        )
        body = sanitize_engagement_text(intent.body, preserve_links=preserve_links)
        voice_body = sanitize_engagement_text(intent.voice_body or intent.body)
        had_attachment_request = bool(intent.attachments)
        usable_attachments = [a for a in intent.attachments if a.usable()]
        if not intent.explicit_artifact_intent:
            usable_attachments = []

        if not body and not voice_body and not usable_attachments:
            return DeliveryDecision(
                should_send=False,
                channel="silent",
                format="silent",
                dedupe_key=dedupe_key,
                suppress_reason="empty",
            )

        channel, route_reason = _route_for_intent(
            intent,
            self.telegram_manager,
            self.allow_telegram,
            now,
        )
        if channel == "silent":
            return DeliveryDecision(
                should_send=False,
                channel="silent",
                format="silent",
                dedupe_key=dedupe_key,
                suppress_reason="no_route",
                route_reason=route_reason,
            )

        if channel == "desktop":
            fmt: DeliveryFormat = "desktop_tts"
        elif channel == "remote":
            fmt = "remote_audio"
        elif (
            channel == "telegram"
            and intent.allow_voice
            and bool(intent.voice_body)
            and not preserve_links
            and not _explicit_text_only_enabled()
            and not usable_attachments
            and not had_attachment_request
        ):
            fmt = "voice"
        else:
            fmt = "text"

        final_text = body if fmt == "text" else None
        final_voice_text = voice_body if fmt in {"voice", "desktop_tts", "remote_audio"} else None

        decision = DeliveryDecision(
            should_send=True,
            channel=channel,
            format=fmt,
            final_text=final_text,
            final_voice_text=final_voice_text,
            attachments=usable_attachments,
            dedupe_key=dedupe_key,
            route_reason=route_reason,
        )
        _write_ledger(intent, decision, now)
        return decision
