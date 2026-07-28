"""Unified integration message bus (R15) — thread mapping + routed agent text input."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

AgentTextSink = Callable[..., None]
ChatIdProvider = Callable[[], int | None]
ChatIdValidator = Callable[[int], bool]


@dataclass
class IncomingMessage:
    """Normalized inbound payload from any connector."""

    platform: str
    thread_id: str
    sender_id: str | None = None
    text: str = ""
    attachments: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    received_at_utc: str = ""
    speak: bool | None = None  # forwarded to agent when set (same flag as Telegram web)

    def __post_init__(self) -> None:
        if not self.received_at_utc:
            self.received_at_utc = datetime.now(timezone.utc).isoformat()


@dataclass
class OutgoingMessage:
    """Normalized outbound payload to a connector."""

    platform: str
    thread_id: str
    text: str = ""
    attachments: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def default_message_bus_mapping_path() -> Path:
    from distr.core.paths import MESSAGE_BUS_MAPPING_PATH

    return Path(MESSAGE_BUS_MAPPING_PATH)


_bus_singleton: IntegrationMessageBus | None = None
_bus_factory_lock = threading.Lock()


class IntegrationMessageBus:
    """Persist (platform, thread_id) → chat_id and route Telegram input behind one lock."""

    VERSION = 1
    MAX_PENDING_SINK_CALLS = 100

    def __init__(self, mapping_path: Path | None = None) -> None:
        self._mapping_path = mapping_path or default_message_bus_mapping_path()
        self._route_lock = threading.RLock()
        self._thread_to_chat: dict[str, int] = {}
        self._text_sink: AgentTextSink | None = None
        self._chat_id_provider: ChatIdProvider | None = None
        self._chat_id_validator: ChatIdValidator | None = None
        self._pending_sink_calls: list[tuple[str, bool, str | None, Any, str]] = []
        self._load_mapping()

    def set_text_sink(self, sink: AgentTextSink | None) -> None:
        """Register the ``send_text_input``-style sink used by integrations."""
        pending: list[tuple[str, bool, str | None, Any, str]] = []
        with self._route_lock:
            self._text_sink = sink
            if sink is not None and self._pending_sink_calls:
                pending = list(self._pending_sink_calls)
                self._pending_sink_calls.clear()
        if sink is not None:
            for text, is_telegram, image_path, speak, platform in pending:
                self._deliver_to_sink(sink, text, is_telegram, image_path, speak, platform)

    def set_chat_id_provider(self, provider: ChatIdProvider | None) -> None:
        """Return current internal chat id for mapping persistence."""
        self._chat_id_provider = provider

    def set_chat_id_validator(self, validator: ChatIdValidator | None) -> None:
        """Validate persisted chat mappings before routing new connector input."""
        self._chat_id_validator = validator

    def _mapped_chat_is_valid(self, chat_id: int | None) -> bool:
        if chat_id is None:
            return False
        validator = self._chat_id_validator
        if validator is None:
            return True
        try:
            return bool(validator(int(chat_id)))
        except Exception:
            logger.debug("message bus chat_id_validator failed", exc_info=True)
            return False

    def _load_mapping(self) -> None:
        p = self._mapping_path
        if not p.is_file():
            self._thread_to_chat = {}
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("message bus mapping unreadable — starting empty (%s)", p)
            self._thread_to_chat = {}
            return
        block = raw.get("thread_to_chat") if isinstance(raw, dict) else None
        if not isinstance(block, dict):
            self._thread_to_chat = {}
            return
        out: dict[str, int] = {}
        for k, v in block.items():
            if isinstance(k, str):
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    continue
        self._thread_to_chat = out

    def _persist_mapping_unlocked(self) -> None:
        p = self._mapping_path
        p.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(
            {
                "version": self.VERSION,
                "thread_to_chat": {k: self._thread_to_chat[k] for k in sorted(self._thread_to_chat)},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        data = blob.encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".msg_bus_", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as wf:
                wf.write(data)
                wf.flush()
                os.fsync(wf.fileno())
            os.replace(tmp, p)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass

    def remember_thread_chat(self, platform: str, thread_id: str, chat_id: int) -> None:
        key = f"{platform}:{thread_id}"
        with self._route_lock:
            self._thread_to_chat[key] = int(chat_id)
            self._persist_mapping_unlocked()

    def resolve_mapped_chat_id(self, platform: str, thread_id: str) -> int | None:
        key = f"{platform}:{thread_id}"
        with self._route_lock:
            return self._thread_to_chat.get(key)

    def resolve_thread_id_for_chat(self, platform: str, chat_id: int) -> str | None:
        """Inverse of ``remember_thread_chat``: connector ``thread_id`` for an internal chat id."""
        prefix = f"{platform}:"
        target = int(chat_id)
        with self._route_lock:
            matches = [
                k[len(prefix) :]
                for k, cid in self._thread_to_chat.items()
                if cid == target and isinstance(k, str) and k.startswith(prefix)
            ]
        if not matches:
            return None
        matches.sort()
        return matches[0]

    def _queue_pending_sink_call_unlocked(
        self,
        *,
        text: str,
        is_telegram: bool,
        image_path: str | None,
        speak: Any,
        platform: str,
    ) -> None:
        self._pending_sink_calls.append((text, is_telegram, image_path, speak, platform))
        overflow = len(self._pending_sink_calls) - self.MAX_PENDING_SINK_CALLS
        if overflow > 0:
            del self._pending_sink_calls[:overflow]

    @staticmethod
    def _telegram_metadata(speak: bool | None, input_type: str | None = None) -> dict[str, Any]:
        metadata: dict[str, Any] = {"speak": speak, "surface": "telegram"}
        if input_type:
            metadata["input_type"] = input_type
        return metadata

    @staticmethod
    def _connector_metadata(platform: str, speak: bool | None) -> dict[str, Any]:
        return {"speak": speak, "surface": platform}

    @staticmethod
    def _set_target_chat_metadata(metadata: dict[str, Any], chat_id: int | None) -> dict[str, Any]:
        if chat_id is not None:
            metadata["chat_id"] = int(chat_id)
        return metadata

    @staticmethod
    def _deliver_to_sink(
        sink: AgentTextSink,
        text: str,
        is_telegram: bool,
        image_path: str | None,
        speak: Any,
        platform: str,
    ) -> None:
        try:
            sink(text, is_telegram, image_path, speak)
        except Exception:
            logger.exception("IntegrationMessageBus: text sink raised (%s)", platform)

    @staticmethod
    def _with_recent_reply_context(
        text: str,
        platform: str,
        thread_id: str | None = None,
    ) -> str:
        try:
            from distr.core.human_engagement import build_remote_reply_context_preamble

            return build_remote_reply_context_preamble(
                text,
                platform=platform,
                thread_id=thread_id,
            )
        except Exception:
            logger.debug("IntegrationMessageBus: failed to attach remote reply context", exc_info=True)
            return text

    def deliver_telegram_user_input(
        self,
        *,
        text: str,
        image_path: str | None = None,
        telegram_chat_id: int | None = None,
        speak: bool | None = None,
        input_type: str | None = None,
    ) -> None:
        """Locked mapping update + delegate to Qt/agent sink (outside lock)."""
        sink: AgentTextSink | None
        thread_id = str(telegram_chat_id).strip() if telegram_chat_id is not None else None
        routed_text = self._with_recent_reply_context(
            text,
            "telegram",
            thread_id=thread_id,
        )
        metadata = self._telegram_metadata(speak, input_type)
        queued = False
        with self._route_lock:
            cid: int | None = None
            mapping_key = None
            if telegram_chat_id is not None:
                mapping_key = f"telegram:{int(telegram_chat_id)}"
                cid = self._thread_to_chat.get(mapping_key)
                if cid is not None and not self._mapped_chat_is_valid(cid):
                    logger.warning(
                        "IntegrationMessageBus: removing stale Telegram chat mapping %s -> %s",
                        telegram_chat_id,
                        cid,
                    )
                    self._thread_to_chat.pop(mapping_key, None)
                    self._persist_mapping_unlocked()
                    cid = None
            if cid is None:
                prov = self._chat_id_provider
                if prov:
                    try:
                        cid = prov()
                    except Exception:
                        logger.debug("message bus chat_id_provider failed", exc_info=True)
                if mapping_key is not None and cid is not None:
                    self._thread_to_chat[mapping_key] = int(cid)
                    self._persist_mapping_unlocked()
            self._set_target_chat_metadata(metadata, cid)
            sink = self._text_sink
            if sink is None:
                self._queue_pending_sink_call_unlocked(
                    text=routed_text,
                    is_telegram=True,
                    image_path=image_path,
                    speak=metadata,
                    platform="telegram",
                )
                queued = True
        if queued:
            logger.warning(
                "IntegrationMessageBus: text sink not configured — Telegram input queued"
            )
            return
        self._deliver_to_sink(sink, routed_text, True, image_path, metadata, "telegram")

    def ingest_incoming(self, msg: IncomingMessage) -> None:
        """Route normalized inbound text to the agent (Discord / Slack / WhatsApp / etc.).

        Uses the same Qt/agent sink as Telegram: ``is_telegram=True`` means
        "external messaging integration" (suppresses local speaker behaviour in LLM).
        """
        sink: AgentTextSink | None
        img = msg.attachments[0] if msg.attachments else None
        routed_text = self._with_recent_reply_context(
            msg.text,
            msg.platform,
            thread_id=msg.thread_id,
        )
        metadata = self._connector_metadata(msg.platform, msg.speak)
        queued = False
        with self._route_lock:
            cid: int | None = None
            if msg.thread_id:
                cid = self._thread_to_chat.get(f"{msg.platform}:{msg.thread_id}")
            if cid is None:
                prov = self._chat_id_provider
                if prov:
                    try:
                        cid = prov()
                    except Exception:
                        logger.debug("message bus chat_id_provider failed", exc_info=True)
                if cid is not None and msg.thread_id:
                    self._thread_to_chat[f"{msg.platform}:{msg.thread_id}"] = int(cid)
                    self._persist_mapping_unlocked()
            self._set_target_chat_metadata(metadata, cid)
            sink = self._text_sink
            if sink is None:
                self._queue_pending_sink_call_unlocked(
                    text=routed_text,
                    is_telegram=True,
                    image_path=img,
                    speak=metadata,
                    platform=msg.platform,
                )
                queued = True
        if queued:
            logger.warning("IntegrationMessageBus: text sink not configured — %s input queued", msg.platform)
            return
        self._deliver_to_sink(sink, routed_text, True, img, metadata, msg.platform)


def get_integration_message_bus() -> IntegrationMessageBus:
    global _bus_singleton
    with _bus_factory_lock:
        if _bus_singleton is None:
            _bus_singleton = IntegrationMessageBus()
        return _bus_singleton


def reset_integration_message_bus_for_tests() -> None:
    """Clear singleton (tests only)."""
    global _bus_singleton
    with _bus_factory_lock:
        _bus_singleton = None
