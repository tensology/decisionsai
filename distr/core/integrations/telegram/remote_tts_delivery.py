"""Deliver Kokoro (or configured) TTS to the remote web app via Ogg streaming."""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Callable

from distr.core.integrations.telegram.remote_audio_stream import (
    iter_remote_audio_stream_messages,
    remote_audio_stopped_message,
)

logger = logging.getLogger(__name__)

DEFAULT_REMOTE_ACTIVE_WINDOW_S = 1800.0


def _pending_remote_contexts(manager: Any) -> list[dict]:
    if manager is None:
        return []
    pending_queue = getattr(manager, "_pending_remote_agent_responses", None)
    if isinstance(pending_queue, list):
        return [item for item in pending_queue if isinstance(item, dict)]
    legacy = getattr(manager, "_pending_remote_agent_response", None)
    if isinstance(legacy, dict) and legacy.get("request_id"):
        return [legacy]
    return []


def _store_pending_remote_contexts(manager: Any, pending_queue: list[dict]) -> None:
    if manager is None:
        return
    clean_queue = [item for item in pending_queue if isinstance(item, dict)]
    setattr(manager, "_pending_remote_agent_responses", clean_queue)
    setattr(manager, "_pending_remote_agent_response", clean_queue[-1] if clean_queue else None)


def _manager_connected(manager: Any) -> bool:
    if manager is None:
        return False
    try:
        return bool(manager.is_connected())
    except Exception:
        return False


def is_remote_delivery_available(
    manager: Any,
    *,
    now: float | None = None,
    window_s: float = DEFAULT_REMOTE_ACTIVE_WINDOW_S,
) -> bool:
    """Return True when the desktop relay is up and a remote web session is likely open."""
    if not _manager_connected(manager):
        return False
    now = float(now if now is not None else time.time())
    try:
        from distr.core.notification_routing import last_surface_activity

        remote_ts = last_surface_activity("remote")
        if remote_ts is not None and now - float(remote_ts) <= window_s:
            return True
    except Exception:
        pass

    for ctx in _pending_remote_contexts(manager):
        created = float(ctx.get("created_at") or 0)
        if created and now - created <= max(window_s, 180.0):
            return True
    return False


def build_synthetic_remote_context(data: dict | None = None) -> dict:
    """Build a remote response context when no agent turn is pending."""
    payload = data if isinstance(data, dict) else {}
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        request_id = f"remote-{int(time.time())}-{secrets.token_hex(4)}"
    return {
        "request_id": request_id,
        "source_command": str(
            payload.get("source_command")
            or payload.get("engagement_kind")
            or payload.get("engagement_source")
            or "notification"
        ),
        "mode": str(payload.get("mode") or payload.get("engagement_source") or "proactive"),
        "created_at": time.time(),
        "synthetic": True,
    }


def resolve_remote_delivery_context(
    manager: Any,
    data: dict | None = None,
    *,
    consume_pending: bool = False,
) -> dict | None:
    """Resolve remote routing context without stealing an in-flight agent turn."""
    payload = data if isinstance(data, dict) else {}
    proactive = bool(
        payload.get("mode") == "proactive"
        or payload.get("engagement_source") in {"workflow", "initiative"}
        or payload.get("explicit_notification_intent")
    )

    pending_queue = _pending_remote_contexts(manager)
    pending = pending_queue[0] if pending_queue else None
    if isinstance(pending, dict) and pending.get("request_id"):
        if proactive:
            if is_remote_delivery_available(manager):
                return build_synthetic_remote_context(payload)
            return None
        if consume_pending:
            _store_pending_remote_contexts(manager, pending_queue[1:])
            logger.info(
                "[REMOTE TTS] Consumed remote context: request_id=%s source=%s mode=%s remaining=%s",
                pending.get("request_id"),
                pending.get("source_command"),
                pending.get("mode"),
                len(pending_queue[1:]),
            )
            return pending
        return pending

    if is_remote_delivery_available(manager):
        return build_synthetic_remote_context(payload)
    return None


def deliver_remote_tts(
    manager: Any,
    text: str,
    remote_ctx: dict,
    *,
    generate_tts: Callable[[str], Any],
    convert_wav_to_ogg: Callable[[Any], Any],
    cleanup_files: Callable[..., None],
    send_text_first: bool = True,
) -> bool:
    """Send remote text immediately, then stream Ogg audio when generation finishes."""
    clean = (text or "").strip()
    if not manager or not clean or not isinstance(remote_ctx, dict):
        return False
    if not hasattr(manager, "_send_websocket_message"):
        return False

    request_id = str(remote_ctx.get("request_id") or "").strip()
    if not request_id:
        request_id = build_synthetic_remote_context(remote_ctx).get("request_id")

    try:
        from distr.core.agent.services.tts.outbound_voice import resolve_outbound_voice_settings

        voice_provider_id, voice_id, _voice_label = resolve_outbound_voice_settings()
    except Exception:
        voice_provider_id = ""
        voice_id = ""

    def response_data(*, audio_streamed: bool, audio_pending: bool, audio_mime_type: str | None = None) -> dict:
        payload = {
            "text": clean,
            "mode": remote_ctx.get("mode") or "command",
            "source_command": remote_ctx.get("source_command"),
            "audio": None,
            "audio_streamed": audio_streamed,
            "audio_pending": audio_pending,
            "voice_provider": voice_provider_id or None,
            "voice_id": voice_id or None,
        }
        if audio_mime_type:
            payload["audio_mime_type"] = audio_mime_type
        return payload

    def is_cancelled() -> bool:
        try:
            cancelled = getattr(manager, "_cancelled_remote_audio_requests", set())
            return bool(request_id and request_id in cancelled)
        except Exception:
            return False

    logger.info(
        "[REMOTE TTS] Delivering response: request_id=%s chars=%d mode=%s source=%s",
        request_id,
        len(clean),
        remote_ctx.get("mode"),
        remote_ctx.get("source_command"),
    )

    if send_text_first and not is_cancelled():
        manager._send_websocket_message(
            {
                "type": "remote_agent_response",
                "request_id": request_id,
                "data": response_data(
                    audio_streamed=True,
                    audio_pending=True,
                    audio_mime_type="audio/ogg; codecs=opus",
                ),
            }
        )

    generated_audio_file = None
    stream_audio_file = None
    audio_stream_ready = False

    if not is_cancelled():
        generated_audio_file = generate_tts(clean)
        stream_audio_file = generated_audio_file
        if stream_audio_file and stream_audio_file.exists() and str(stream_audio_file).endswith(".wav"):
            stream_audio_file = convert_wav_to_ogg(stream_audio_file)

        if stream_audio_file and stream_audio_file.exists() and str(stream_audio_file).endswith(".ogg"):
            audio_stream_ready = True
            logger.info(
                "[REMOTE TTS] Ogg stream ready: request_id=%s bytes=%d file=%s",
                request_id,
                stream_audio_file.stat().st_size,
                stream_audio_file.name,
            )
        elif stream_audio_file and stream_audio_file.exists():
            logger.warning(
                "[REMOTE TTS] Skipping non-Ogg remote audio stream: request_id=%s file=%s",
                request_id,
                stream_audio_file,
            )

    if not send_text_first:
        manager._send_websocket_message(
            {
                "type": "remote_agent_response",
                "request_id": request_id,
                "data": response_data(
                    audio_streamed=audio_stream_ready,
                    audio_pending=False,
                    audio_mime_type="audio/ogg; codecs=opus" if audio_stream_ready else None,
                ),
            }
        )
    elif audio_stream_ready and not is_cancelled():
        manager._send_websocket_message(
            {
                "type": "remote_agent_response",
                "request_id": request_id,
                "data": response_data(
                    audio_streamed=True,
                    audio_pending=False,
                    audio_mime_type="audio/ogg; codecs=opus",
                ),
            }
        )

    try:
        if audio_stream_ready and stream_audio_file and not is_cancelled():
            for message in iter_remote_audio_stream_messages(
                request_id=str(request_id),
                audio_path=stream_audio_file,
            ):
                if is_cancelled():
                    manager._send_websocket_message(
                        remote_audio_stopped_message(str(request_id), reason="user_stop")
                    )
                    logger.info("[REMOTE TTS] Remote audio stream stopped: request_id=%s", request_id)
                    break
                manager._send_websocket_message(message)
                if message.get("type") == "remote_agent_audio_chunk":
                    time.sleep(0.002)
        elif send_text_first and not audio_stream_ready and not is_cancelled():
            manager._send_websocket_message(
                {
                    "type": "remote_agent_response",
                    "request_id": request_id,
                    "data": response_data(audio_streamed=False, audio_pending=False),
                }
            )
    except Exception:
        logger.exception("[REMOTE TTS] Failed streaming audio: request_id=%s", request_id)
        return False
    finally:
        time.sleep(0.5)
        cleanup_files(stream_audio_file, None, None)
        if generated_audio_file and generated_audio_file != stream_audio_file:
            cleanup_files(generated_audio_file, None, None)

    return True


def enqueue_remote_tts_delivery(
    text: str,
    *,
    data: dict | None = None,
    block: bool = False,
) -> bool:
    """Queue a remote TTS delivery through the desktop event loop."""
    clean = (text or "").strip()
    if not clean:
        return False
    try:
        from distr.core.agent.services.tts.outbound_voice import voice_delivery_provider_for_event
        from distr.core.signals import get_agent_event_queue

        queue = get_agent_event_queue()
        if not queue:
            return False
        payload = {
            "text": clean,
            "provider": voice_delivery_provider_for_event(),
            "is_done": True,
        }
        if isinstance(data, dict):
            payload.update(data)
        queue.put(("send_to_remote", payload), block=block)
        return True
    except Exception:
        logger.debug("enqueue_remote_tts_delivery failed", exc_info=True)
        return False
