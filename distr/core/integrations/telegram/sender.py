"""Outbound message sending mixin — send_to_telegram, message queue, connection data."""

import base64
import hashlib
import io
import json
import logging
import mimetypes
import os
import queue
import re
import time
from functools import partial
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.exc import OperationalError

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)


_INTERNAL_PROMPT_LEAK_RE = re.compile(
    r"(?:\s*It was working on:\s*)?\[?DECISIONS [A-Z ]*THREAD\]?[^\n]*(?:\n(?!\n)[^\n]*)*", re.I)


def _strip_internal_prompt(text: str) -> str:
    """Last line of defence: never send the internal Development-thread prompt to Telegram (audit 12 Nov)."""
    cleaned = _INTERNAL_PROMPT_LEAK_RE.sub("", text)
    cleaned = re.sub(r"\s*Work as the active IDE coding agent[^\n]*", "", cleaned)
    return re.sub(r"[ \t]+\n", "\n", cleaned).strip()


def _audit_outbound_telegram_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    clean = _strip_internal_prompt(str(text).strip())
    try:
        from distr.core.human_engagement import sanitize_engagement_text

        return sanitize_engagement_text(
            clean,
            preserve_links="/api/remote/" in clean,
        )
    except Exception:
        pass

    clean_lower = clean.lower()
    if "has shut down" in clean_lower:
        return "Goodbye."
    if "is online" in clean_lower or "welcome back" in clean_lower:
        return "I'm back online."

    clean = re.sub(r"^\s*\[Initiative\]\s*", "", clean)
    clean = re.sub(r"\[APPROVE\]|\[ESCALATE\]|\[SUGGEST_ONLY\]", "", clean)
    clean = re.sub(r"\n{2,}Draft:\n.*?(?=\n{2,}Payload:|\n{2,}[A-Z][A-Za-z ]{2,}:|\Z)", "", clean, flags=re.S)
    clean = re.sub(r"\n{2,}Payload:\s*\{.*?\}(?=\n|$)", "", clean, flags=re.S)
    clean = re.sub(r"\nPayload:\s*\{.*?\}(?=\n|$)", "", clean, flags=re.S)
    clean = re.sub(r"(?i)^quick update:\s*#{1,6}\s*quick check-?in\s*[-:]*\s*", "Quick check-in: ", clean)
    clean = re.sub(r"(?im)^\s*#{1,6}\s*", "", clean)
    clean = re.sub(r"(?m)^\s*[-*]\s+", "", clean)
    clean = re.sub(r"(?m)^\s*\d+\.\s+", "", clean)
    clean = clean.replace("**", "").replace("__", "").replace("`", "")
    clean = re.sub(r"(?i)^quick update:\s*quick check-?in\s*[-:]*\s*", "Quick check-in: ", clean)
    clean = re.sub(r"(?i)^quick update:\s*", "", clean)
    clean = re.sub(r"\s+([.,;:])", r"\1", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean


def _usable_outbound_file(path: Optional[str]) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _outbound_destination(sender):
    from distr.core.incoming.telegram_updates import _recipient_id
    owner = getattr(sender, 'telegram_user_id', None)
    chat = getattr(sender, 'chat_id', None)
    if owner is not None:
        identity = _recipient_id(owner)
        return {'chat_id': identity} if identity else None
    if chat is not None:
        identity = _recipient_id(chat)
        return {'chat_id': identity} if identity else None
    app_user = getattr(sender, 'app_user_id', None)
    return {'app_user_id': app_user} if isinstance(app_user, str) and app_user.strip() else None


class TelegramSenderMixin:
    """Handles sending messages to Telegram, message queue processing, and connection data persistence."""

    def queue_closing_reply(self, frame):
        try:
            self._message_queue.put_nowait(frame)
        except queue.Full:
            return False
        return True

    def send_to_telegram(
        self,
        text: Optional[str] = None,
        audio_file_path: Optional[str] = None,
        screenshot_path: Optional[str] = None,
        document_path: Optional[str] = None,
        video_path: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        interaction_token: Optional[str] = None,
        *,
        expected_chat_id: Optional[str] = None,
    ):
        """Enqueue message for sending."""
        # Stop the persistent typing loop — we're about to send the actual response
        self._stop_typing_loop()

        # CRITICAL: Load telegram_user_id from settings if not set (fallback)
        if not self.telegram_user_id:
            logger.info(
                f"[Telegram] 🔍 telegram_user_id is None, attempting to load from settings..."
            )
            try:
                from distr.core.settings import load_settings_from_db

                settings = load_settings_from_db()
                connected_accounts = settings.get("connected_accounts", [])
                logger.info(
                    f"[Telegram] 🔍 Loaded settings, found {len(connected_accounts) if isinstance(connected_accounts, list) else 'non-list'} connected_accounts"
                )

                if isinstance(connected_accounts, str):
                    connected_accounts = json.loads(connected_accounts)

                telegram_account_found = False
                for account in connected_accounts:
                    if (
                        isinstance(account, dict)
                        and account.get("provider") == "telegram"
                    ):
                        telegram_account_found = True
                        user_id = account.get("user_id")
                        logger.info(
                            f"[Telegram] 🔍 Found Telegram account, user_id: {user_id} (type: {type(user_id)})"
                        )
                        if user_id:
                            try:
                                user_id_int = (
                                    int(user_id)
                                    if isinstance(user_id, str)
                                    else user_id
                                )
                                logger.info(
                                    f"[Telegram] 🔍 Parsed user_id_int: {user_id_int} (positive: {user_id_int > 0})"
                                )
                                if user_id_int > 0:
                                    self.telegram_user_id = user_id_int
                                    logger.info(
                                        f"[Telegram] ✅ Loaded telegram_user_id from settings in send_to_telegram(): {self.telegram_user_id}"
                                    )
                                    break
                                else:
                                    logger.warning(
                                        f"[Telegram] ⚠️ user_id is negative (group): {user_id_int}, skipping"
                                    )
                            except (ValueError, TypeError) as e:
                                logger.warning(
                                    f"[Telegram] ⚠️ Could not parse user_id: {user_id} ({e})"
                                )
                        else:
                            logger.warning(
                                f"[Telegram] ⚠️ Telegram account found but user_id is None/empty"
                            )

                if not telegram_account_found:
                    logger.warning(
                        f"[Telegram] ⚠️ No Telegram account found in connected_accounts"
                    )

            except Exception as e:
                logger.error(
                    f"[Telegram] ❌ Could not load telegram_user_id from settings in send_to_telegram(): {e}",
                    exc_info=True,
                )

        # CRITICAL: Block any disconnect/reconnect messages from being sent
        durable_proposal = str(interaction_token or '').startswith('proposal-update:')
        if text and not durable_proposal:
            text = _audit_outbound_telegram_text(text)
            try:
                from distr.core.human_engagement import is_low_value_status_text

                if text and is_low_value_status_text(text):
                    logger.debug("[Telegram] Suppressed low-value status message: %s", text[:100])
                    text = None
            except Exception:
                pass
            if not text and not any((audio_file_path, screenshot_path, document_path, video_path)):
                return False
            text_lower = (text or "").lower()
            # Low-value connection notices are filtered above. A work result may
            # legitimately describe a disconnect/reconnect defect.

            # Log what we're sending (especially for connection messages)
            # Log connection-related messages more prominently
            if "online" in text_lower or "shut down" in text_lower:
                logger.info(
                    f"[Telegram] 📤 Queuing message to Telegram: '{text[:100]}...'"
                )
            elif text:
                logger.debug(f"[Telegram] Queuing message: '{text[:50]}...'")

        # Rate limiting should delay real user-facing messages, not drop them.
        now = time.time()
        not_before = 0.0
        if now - self._last_send_time < self._min_send_interval:
            not_before = self._last_send_time + self._min_send_interval
            logger.debug(
                "[Telegram] Rate limit active; deferring message for %.2fs",
                max(0.0, not_before - now),
            )

        # Outgoing Deduplication

        destination = _outbound_destination(self)
        if destination is None:
            logger.error('No valid private Telegram destination; message retained by its durable caller.')
            return False
        if expected_chat_id is not None and str(destination.get('chat_id')) != expected_chat_id:
            logger.warning('Telegram recipient changed before queueing the reviewed reply.')
            return False
        message_content = json.dumps([
            text or '', audio_file_path or '', screenshot_path or '',
            document_path or '', video_path or '', interaction_token or '',
            reply_markup or {}, destination,
        ], sort_keys=True, default=str)
        message_hash = hashlib.md5(message_content.encode()).hexdigest()

        # Proposal receipts claim transport durably. The in-memory window
        # would suppress a safe requeue after a pre-transport owner change.
        if not durable_proposal and message_hash in self._recent_messages:
            last_sent = self._recent_messages[message_hash]
            if now - last_sent < self._dedup_window:
                logger.debug(
                    f"Duplicate message dropped (sent {now - last_sent:.2f}s ago)"
                )
                return False

        # Clean up old dedup entries
        expired = [
            h
            for h, ts in self._recent_messages.items()
            if now - ts > self._dedup_window
        ]
        for h in expired:
            del self._recent_messages[h]

        # Build Message Payload
        msg = {"type": "send_message"}
        if interaction_token:
            # Opaque correlation value echoed by the relay.  This lets a
            # durable workflow interaction distinguish "queued locally" from
            # a Telegram API acknowledgment without exposing run internals.
            msg["client_message_id"] = str(interaction_token)
            if not durable_proposal:
                msg["_checkpoint_dedup_key"] = message_hash
        if not_before:
            msg["_not_before"] = not_before

        if text:
            msg["text"] = text
        if reply_markup:
            msg["reply_markup"] = reply_markup

        # Destination is captured once and checked again immediately before transport.
        msg.update(destination)

        # Add Audio/Screenshot encoding (simplified adapt from original)
        if audio_file_path and _usable_outbound_file(audio_file_path):
            try:
                with open(audio_file_path, "rb") as f:
                    audio_data = f.read()
                    b64 = base64.b64encode(audio_data).decode("utf-8")

                    # Determine MIME type
                    mime_type, _ = mimetypes.guess_type(audio_file_path)
                    if not mime_type or not mime_type.startswith("audio/"):
                        # Fallback based on extension
                        ext = os.path.splitext(audio_file_path)[1].lower()
                        mime_map = {
                            ".mp3": "audio/mpeg",
                            ".wav": "audio/wav",
                            ".ogg": "audio/ogg",
                            ".m4a": "audio/mp4",
                            ".aac": "audio/aac",
                            ".flac": "audio/flac",
                            ".wma": "audio/x-ms-wma",
                            ".opus": "audio/opus",
                            ".mp4a": "audio/mp4",
                        }
                        mime_type = mime_map.get(ext, "audio/ogg")

                    # Format file size
                    audio_size = len(audio_data)
                    if audio_size < 1024:
                        size_str = f"{audio_size} B"
                    elif audio_size < 1024 * 1024:
                        size_str = f"{audio_size / 1024:.1f} KB"
                    else:
                        size_str = f"{audio_size / (1024 * 1024):.1f} MB"

                    logger.info(
                        "[Telegram] Uploading audio: %s (%s)",
                        os.path.basename(audio_file_path),
                        size_str,
                    )
                    msg["audio"] = {
                        "data": b64,
                        "filename": os.path.basename(audio_file_path),
                        "mime_type": mime_type,
                    }
                    logger.info(
                        f"[Telegram] ✅ Audio prepared: {os.path.basename(audio_file_path)} ({size_str}), MIME: {mime_type}"
                    )
            except Exception as e:
                logger.error(f"Failed to encode audio: {e}", exc_info=True)

        if screenshot_path and _usable_outbound_file(screenshot_path):
            # Convert screenshot to WebP for better compression before sending
            # Resize large images to reduce payload size and avoid connection drops
            try:
                from PIL import Image

                img = Image.open(screenshot_path)
                # Resize if too large (reduces connection drops on slow/unstable links)
                max_width = 1920
                max_height = 1080
                if img.width > max_width or img.height > max_height:
                    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                # Convert RGBA to RGB if needed
                if img.mode in ("RGBA", "LA", "P"):
                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    rgb_img.paste(
                        img, mask=img.split()[-1] if img.mode == "RGBA" else None
                    )
                    img = rgb_img
                else:
                    img = img.convert("RGB")

                with io.BytesIO() as webp_file:
                    img.save(webp_file, "WEBP", quality=70, method=2)
                    image_data = webp_file.getvalue()

                # Format file size for human readability
                webp_size = len(image_data)
                if webp_size < 1024:
                    size_str = f"{webp_size} B"
                elif webp_size < 1024 * 1024:
                    size_str = f"{webp_size / 1024:.1f} KB"
                else:
                    size_str = f"{webp_size / (1024 * 1024):.1f} MB"

                logger.info("[Telegram] Uploading WebP screenshot: %s", size_str)

                b64 = base64.b64encode(image_data).decode("utf-8")
                # Generate WebP filename
                base_filename = os.path.basename(screenshot_path)
                webp_filename = os.path.splitext(base_filename)[0] + ".webp"
                msg["screenshot"] = {
                    "data": b64,
                    "filename": webp_filename,
                    "mime_type": "image/webp",
                }
                logger.info(
                    f"[Telegram] ✅ Screenshot converted to WebP: {os.path.basename(screenshot_path)} → {webp_filename} ({size_str}), MIME: image/webp"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to convert screenshot to WebP, using original: {e}"
                )
                # Fallback to original image
                try:
                    with open(screenshot_path, "rb") as f:
                        image_data = f.read()
                except OSError:
                    logger.warning("Could not read the original Telegram screenshot", exc_info=True)
                    image_data = b""
                if image_data:
                    mime_type, _ = mimetypes.guess_type(screenshot_path)
                    if not mime_type or not mime_type.startswith("image/"):
                        mime_type = "image/png"
                    msg["screenshot"] = {
                        "data": base64.b64encode(image_data).decode("utf-8"),
                        "filename": os.path.basename(screenshot_path),
                        "mime_type": mime_type,
                    }

        # Add Document encoding (PDF, DOC, etc.)
        if document_path and _usable_outbound_file(document_path):
            try:
                with open(document_path, "rb") as f:
                    doc_data = f.read()
                    b64 = base64.b64encode(doc_data).decode("utf-8")

                    # Determine MIME type
                    mime_type, _ = mimetypes.guess_type(document_path)
                    if not mime_type:
                        # Fallback based on extension
                        ext = os.path.splitext(document_path)[1].lower()
                        mime_map = {
                            ".pdf": "application/pdf",
                            ".doc": "application/msword",
                            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            ".txt": "text/plain",
                            ".rtf": "application/rtf",
                            ".xls": "application/vnd.ms-excel",
                            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            ".csv": "text/csv",
                            ".ppt": "application/vnd.ms-powerpoint",
                            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            ".odt": "application/vnd.oasis.opendocument.text",
                            ".ods": "application/vnd.oasis.opendocument.spreadsheet",
                            ".odp": "application/vnd.oasis.opendocument.presentation",
                            ".zip": "application/zip",
                            ".rar": "application/x-rar-compressed",
                            ".7z": "application/x-7z-compressed",
                            ".tar": "application/x-tar",
                            ".gz": "application/gzip",
                        }
                        mime_type = mime_map.get(ext, "application/octet-stream")

                    # Format file size
                    doc_size = len(doc_data)
                    if doc_size < 1024:
                        size_str = f"{doc_size} B"
                    elif doc_size < 1024 * 1024:
                        size_str = f"{doc_size / 1024:.1f} KB"
                    else:
                        size_str = f"{doc_size / (1024 * 1024):.1f} MB"

                    logger.info(
                        "[Telegram] Uploading document: %s (%s)",
                        os.path.basename(document_path),
                        size_str,
                    )
                    msg["document"] = {
                        "data": b64,
                        "filename": os.path.basename(document_path),
                        "mime_type": mime_type,
                    }
                    logger.info(
                        f"[Telegram] ✅ Document prepared: {os.path.basename(document_path)} ({size_str}), MIME: {mime_type}, base64 length: {len(b64)}"
                    )
            except Exception as e:
                logger.error(f"Failed to encode document: {e}", exc_info=True)

        # Add Video encoding
        if video_path and _usable_outbound_file(video_path):
            try:
                with open(video_path, "rb") as f:
                    video_data = f.read()
                    b64 = base64.b64encode(video_data).decode("utf-8")

                    # Determine MIME type
                    mime_type, _ = mimetypes.guess_type(video_path)
                    if not mime_type or not mime_type.startswith("video/"):
                        # Fallback based on extension
                        ext = os.path.splitext(video_path)[1].lower()
                        mime_map = {
                            ".mp4": "video/mp4",
                            ".avi": "video/x-msvideo",
                            ".mov": "video/quicktime",
                            ".mkv": "video/x-matroska",
                            ".wmv": "video/x-ms-wmv",
                            ".flv": "video/x-flv",
                            ".webm": "video/webm",
                            ".m4v": "video/x-m4v",
                            ".3gp": "video/3gpp",
                            ".mpg": "video/mpeg",
                            ".mpeg": "video/mpeg",
                        }
                        mime_type = mime_map.get(ext, "video/mp4")

                    # Format file size
                    video_size = len(video_data)
                    if video_size < 1024:
                        size_str = f"{video_size} B"
                    elif video_size < 1024 * 1024:
                        size_str = f"{video_size / 1024:.1f} KB"
                    else:
                        size_str = f"{video_size / (1024 * 1024):.1f} MB"

                    logger.info(
                        "[Telegram] Uploading video: %s (%s)",
                        os.path.basename(video_path),
                        size_str,
                    )
                    msg["video"] = {
                        "data": b64,
                        "filename": os.path.basename(video_path),
                        "mime_type": mime_type,
                    }
                    logger.info(
                        f"[Telegram] ✅ Video prepared: {os.path.basename(video_path)} ({size_str}), MIME: {mime_type}"
                    )
            except Exception as e:
                logger.error(f"Failed to encode video: {e}", exc_info=True)

        # CRITICAL: For media messages, map 'text' to 'caption' if 'caption' is not explicitly provided
        # This ensures the text appears attached to the media, and helps with server-side handling
        # (Telegram requires 'caption' for media, 'text' is for plain messages)
        media_fields = ("video", "audio", "document", "screenshot")
        for field in media_fields:
            if field in msg and not msg[field]["data"]:
                del msg[field]
        has_media = any(field in msg for field in media_fields)
        if not text and not has_media:
            return False
        if has_media and text and "caption" not in msg:
            msg["caption"] = text
            # We can request to remove 'text' to avoid duplicate messages or confusion,
            # but keeping it might be safer if server uses it for fallback.
            # However, standard practice is to use caption.
            # Let's keep 'text' for now but ensure 'caption' is populated.
            logger.info(
                f"[Telegram] 📎 Mapped text to caption for media message: '{text[:50]}...'"
            )

        # Enqueue
        try:
            self._message_queue.put_nowait(msg)
        except queue.Full:
            self._recent_messages.pop(message_hash, None)
            logger.error(
                "[Telegram] Outbound queue is full; rejected message instead of exhausting memory"
            )
            return False
        if not durable_proposal:
            self._recent_messages[message_hash] = now
        # Update last message time when sending (connection is active)
        self._last_message_time = time.time()
        return True

    def _process_message_queue(self):
        """Pop queue run by QTimer."""
        if not self.is_connected():
            logger.debug("[Telegram] Queue processor: Not connected, skipping")
            return

        try:
            while getattr(self, "_outbound_retry_message", None) is not None or not self._message_queue.empty():
                msg = getattr(self, "_outbound_retry_message", None)
                if msg is not None:
                    self._outbound_retry_message = None
                else:
                    msg = self._message_queue.get_nowait()
                not_before = max(float(msg.get("_not_before") or 0),
                                 self._last_send_time + self._min_send_interval)
                now = time.time()
                if not_before and now < not_before:
                    self._outbound_retry_message = msg
                    break
                msg.pop("_not_before", None)
                release_pending = msg.pop("_proposal_release_pending", False)
                if release_pending:
                    from distr.core.integrations.telegram.receipt_writes import flush_pending_receipt_writes

                    # Apply earlier relay evidence before the local refusal.
                    if not flush_pending_receipt_writes():
                        msg["_proposal_release_pending"] = True
                        self._outbound_retry_message = msg
                        break
                checkpoint_dedup_key = msg.pop("_checkpoint_dedup_key", None)
                json_str = json.dumps(msg)

                # Log what we're sending and to which chat_id
                chat_id_used = msg.get("chat_id", "N/A")
                app_user_id_used = msg.get("app_user_id", "N/A")
                txt = msg.get("text", "no-text")

                # Check for attachments
                has_document = "document" in msg
                has_screenshot = "screenshot" in msg
                has_audio = "audio" in msg
                has_video = "video" in msg

                attachment_info = []
                if has_document:
                    doc_info = msg.get("document", {})
                    filename = doc_info.get("filename", "unknown")
                    mime_type = doc_info.get("mime_type", "unknown")
                    data_len = len(doc_info.get("data", ""))
                    attachment_info.append(
                        f"document: {filename} ({mime_type}, {data_len} chars base64)"
                    )
                if has_screenshot:
                    attachment_info.append("screenshot")
                if has_audio:
                    attachment_info.append("audio")
                if has_video:
                    attachment_info.append("video")

                attachment_str = (
                    f" [Attachments: {', '.join(attachment_info)}]"
                    if attachment_info
                    else ""
                )

                logger.info(
                    f"[Telegram] 📤 Sending message via WebSocket - chat_id: {chat_id_used}, app_user_id: {app_user_id_used}, text: '{txt[:50]}...'{attachment_str}"
                )

                # Log JSON size for debugging
                json_size = len(json_str)
                if json_size > 1000000:  # > 1MB
                    logger.warning(
                        f"[Telegram] ⚠️ Large message payload: {json_size / 1024 / 1024:.2f} MB"
                    )

                # CRITICAL: Verify document is actually in the payload before sending
                if "document" in msg:
                    doc_info = msg.get("document", {})
                    doc_data_len = len(doc_info.get("data", ""))
                    doc_filename = doc_info.get("filename", "unknown")
                    doc_mime = doc_info.get("mime_type", "unknown")
                    logger.info(
                        f"[Telegram] 🔍 VERIFYING: Document in payload - filename: {doc_filename}, MIME: {doc_mime}, base64 data length: {doc_data_len} chars"
                    )
                    if doc_data_len == 0:
                        logger.error(
                            f"[Telegram] ❌ ERROR: Document payload has EMPTY data! This will fail on server!"
                        )
                    else:
                        # Verify base64 is valid
                        try:
                            import base64

                            test_decode = base64.b64decode(
                                doc_info.get("data", "")[:100]
                            )  # Test decode first 100 chars
                            logger.info(
                                f"[Telegram] ✅ Document base64 is valid (test decode successful)"
                            )
                        except Exception as e:
                            logger.error(
                                f"[Telegram] ❌ ERROR: Document base64 is INVALID: {e}"
                            )

                token = str(msg.get('client_message_id') or '')
                from distr.core.workflow import telegram_reply_receipts as closing

                if token.startswith(closing.PREFIX):
                    from distr.core.incoming.telegram_updates import _recipient_id

                    relay_verified = getattr(self, '_closing_reply_receipts', None)
                    if relay_verified is None:
                        self._outbound_retry_message = msg
                        break
                    try:
                        authorized = closing.authorize(msg, owner=_recipient_id(self.telegram_user_id),
                                                       relay_verified=relay_verified,
                                                       relay_topics=getattr(self, '_closing_reply_topics', False))
                    except OperationalError:
                        self._outbound_retry_message = msg
                        break
                    if authorized:
                        try:
                            accepted = self._send_websocket_message(msg)
                        except Exception:
                            accepted = False
                        if accepted is not True:
                            closing.accept_receipt(token, uncertain=True)
                        else:
                            self._last_send_time = self._last_message_time = time.time()
                    continue
                queue_id = getattr(self, '_proposal_delivery_queue_id', None)
                durable_proposal = token.startswith('proposal-update:')
                workflow_checkpoint = bool(token) and not durable_proposal
                outbound = msg
                queued_destination = {key: msg[key] for key in ('chat_id', 'app_user_id') if key in msg}
                try:
                    if queued_destination != _outbound_destination(self):
                        if durable_proposal:
                            from distr.core.incoming.telegram_updates import mark_delivery_recipient_changed
                            mark_delivery_recipient_changed(token, queue_id=queue_id)
                        if checkpoint_dedup_key:
                            self._recent_messages.pop(checkpoint_dedup_key, None)
                        logger.warning('Telegram recipient changed before transport; blocked queued payload for the previous owner.')
                        continue
                    if durable_proposal:
                        from distr.core.incoming.telegram_updates import mark_delivery_attempt
                        socket_attempt_id = uuid4().hex
                        if not mark_delivery_attempt(token, queue_id=queue_id, attempt_id=socket_attempt_id,
                                                     recipient_id=queued_destination.get('chat_id')):
                            continue  # Never send without the matching durable claim.
                    elif workflow_checkpoint:
                        from distr.core.incoming.telegram_updates import _recipient_id
                        from distr.core.workflow.interactions import (
                            TELEGRAM_CHECKPOINT_RECEIPT_PREFIX,
                            authorize_telegram_checkpoint_delivery,
                        )

                        owner = _recipient_id(self.telegram_user_id)
                        attempt = (
                            authorize_telegram_checkpoint_delivery(token=token, telegram_chat_id=owner)
                            if owner is not None and owner == queued_destination.get('chat_id') else None
                        )
                        if attempt is None or queued_destination != _outbound_destination(self):
                            if checkpoint_dedup_key:
                                self._recent_messages.pop(checkpoint_dedup_key, None)
                            logger.warning('Telegram workflow checkpoint was blocked before transport because its recipient or scope changed.')
                            continue
                        outbound = {**msg, 'client_message_id': f'{TELEGRAM_CHECKPOINT_RECEIPT_PREFIX}{token}:{attempt}'}
                except OperationalError:
                    if checkpoint_dedup_key:
                        msg['_checkpoint_dedup_key'] = checkpoint_dedup_key
                    self._outbound_retry_message = msg
                    logger.warning('Could not update the Telegram delivery receipt before transport; retained the queued message.')
                    break
                try:
                    sent = self._send_websocket_message(outbound)
                    if (durable_proposal or workflow_checkpoint) and type(sent) is not bool:
                        raise OSError('Telegram WebSocket did not return a definite write result.')
                except Exception as exc:
                    # Transport may have accepted the payload before raising.
                    # Keep the durable receipt unconfirmed, never blind-retry.
                    if durable_proposal:
                        from distr.core.incoming.telegram_updates import accept_relay_receipt
                        accept_relay_receipt(token, error=str(exc) or 'Telegram transport outcome was not confirmed.')
                    elif workflow_checkpoint:
                        from distr.core.integrations.telegram.receipt_writes import (
                            checkpoint_delivery_error_message,
                            persist_or_defer_receipt_write,
                        )
                        from distr.core.workflow.interactions import record_telegram_delivery_error

                        persist = partial(
                            record_telegram_delivery_error, token=token, attempt=attempt,
                            error=checkpoint_delivery_error_message(rate_limited=False),
                        )
                        persist_or_defer_receipt_write(
                            persist, journal_entry=("checkpoint-error", token, attempt, False),
                        )
                    logger.exception('Telegram transport outcome was not confirmed; automatic resend suppressed.')
                    continue
                if not sent:
                    if durable_proposal:
                        from distr.core.incoming.telegram_updates import accept_socket_refusal

                        released = accept_socket_refusal(token, queue_id=queue_id, attempt_id=socket_attempt_id)
                        if released is False:
                            break
                        if released is None:
                            msg['_proposal_release_pending'] = True
                    if checkpoint_dedup_key:
                        msg['_checkpoint_dedup_key'] = checkpoint_dedup_key
                    # The socket can disconnect after the loop-level connection
                    # check. Retain the exact payload at the front of the logical
                    # queue so reconnect processing cannot silently lose or reorder it.
                    self._outbound_retry_message = msg
                    logger.warning(
                        "[Telegram] WebSocket became unavailable while sending; retained message for retry"
                    )
                    break
                self._last_send_time = time.time()
                # Update last message time when sending (connection is active)
                self._last_message_time = time.time()

                # Log success
                if txt and (
                    "online" in txt.lower()
                    or "shut down" in txt.lower()
                    or "disconnect" in txt.lower()
                    or "reconnect" in txt.lower()
                    or "welcome" in txt.lower()
                ):
                    logger.info(
                        f"[Telegram] ✅ SENT connection/welcome message to Telegram (chat_id: {chat_id_used}): '{txt[:100]}...'"
                    )
                elif has_video or has_audio or has_document or has_screenshot:
                    logger.info(
                        f"[Telegram] ✅ SENT media message to Telegram (chat_id: {chat_id_used})"
                    )
                else:
                    logger.info(
                        f"[Telegram] ✅ SENT message to Telegram (chat_id: {chat_id_used}): '{txt[:50]}...'"
                    )

        except queue.Empty:
            pass

    # =========================================================================
    # Helpers
    # =========================================================================

    def _update_stored_connection_data(
        self,
        chat_id: Optional[int] = None,
        telegram_user_id: Optional[int] = None,
        app_user_id: Optional[str] = None,
    ):
        """
        Update stored Telegram connection data in the database.
        Called whenever we receive updated IDs from the server.

        CRITICAL: Only store private chat IDs (positive) as user_id. Never overwrite with group/channel IDs (negative).
        """
        try:
            from distr.core.settings import (
                load_settings_from_db,
                save_settings_to_db,
            )

            settings = load_settings_from_db()
            connected_accounts = settings.get("connected_accounts", [])

            if not isinstance(connected_accounts, list):
                connected_accounts = []

            # Find or create Telegram account entry
            telegram_account = None
            for account in connected_accounts:
                if isinstance(account, dict) and account.get("provider") == "telegram":
                    telegram_account = account
                    break

            if not telegram_account:
                telegram_account = {"provider": "telegram"}
                connected_accounts.append(telegram_account)

            # Update fields if provided
            updated = False

            # CRITICAL: Only update user_id if telegram_user_id is provided (private chat, positive ID)
            # OR if chat_id is provided AND it's a private chat (positive ID)
            # NEVER overwrite with group/channel IDs (negative)
            if telegram_user_id is not None and telegram_user_id > 0:
                # Store telegram_user_id as user_id (for private chats, user_id = telegram_user_id)
                old_user_id = telegram_account.get("user_id")
                if old_user_id != telegram_user_id:
                    telegram_account["user_id"] = telegram_user_id
                    logger.info(
                        f"✅ Updated stored telegram_user_id (user_id) in database: {old_user_id} -> {telegram_user_id}"
                    )
                    updated = True
            elif chat_id is not None and chat_id > 0:
                # Only update if it's a private chat (positive ID) and we don't already have a user_id
                # This prevents overwriting a valid private chat ID with a different one
                old_user_id = telegram_account.get("user_id")
                if old_user_id is None or old_user_id <= 0:
                    telegram_account["user_id"] = chat_id
                    logger.info(
                        f"✅ Updated stored chat_id (user_id) in database: {old_user_id} -> {chat_id}"
                    )
                    updated = True
                elif old_user_id != chat_id:
                    logger.warning(
                        f"[Telegram] ⚠️ Ignoring chat_id update: existing user_id ({old_user_id}) differs from new chat_id ({chat_id}). Keeping existing."
                    )
            elif chat_id is not None and chat_id <= 0:
                # This is a group/channel - don't store as user_id, but log it
                logger.debug(
                    f"[Telegram] Received group/channel chat_id ({chat_id}) - not storing as user_id"
                )

            if app_user_id is not None:
                old_app_user_id = telegram_account.get("app_user_id")
                if old_app_user_id != app_user_id:
                    telegram_account["app_user_id"] = app_user_id
                    updated = True

            if updated:
                settings["connected_accounts"] = connected_accounts
                save_settings_to_db(settings)
                logger.info(f"✅ Successfully updated stored Telegram connection data")
        except Exception as e:
            logger.warning(
                f"Could not update stored Telegram connection data: {e}", exc_info=True
            )

    def _get_chat_id(self):
        return self.chat_id

    def get_stored_group_messages(
        self, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve stored group/channel messages for later processing.

        Args:
            limit: Maximum number of messages to return (None = all)

        Returns:
            List of stored group messages, each containing:
            - message_id: Telegram message ID
            - chat_id: Group/channel chat ID
            - chat_type: Type of chat (group, supergroup, channel)
            - chat_title: Title of the group/channel
            - text: Message text (if any)
            - media: Media information (if any)
            - from: Sender information
            - date: Message date from Telegram
            - timestamp: When the message was stored
            - raw_data: Full raw message data
        """
        if limit is None:
            return self._group_messages_storage.copy()
        else:
            return self._group_messages_storage[-limit:] if limit > 0 else []

    def get_group_message_queue_size(self) -> int:
        """Get the number of group messages waiting in the queue."""
        return self._group_message_queue.qsize()

    def clear_stored_group_messages(self):
        """Clear all stored group messages."""
        self._group_messages_storage.clear()
        # Clear queue as well
        while not self._group_message_queue.empty():
            try:
                self._group_message_queue.get_nowait()
            except queue.Empty:
                break
        logger.info("[Telegram] Cleared all stored group messages")

    # ── Persistent typing indicator timer ────────────────────────────────────
