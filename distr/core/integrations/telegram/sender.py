"""Outbound message sending mixin — send_to_telegram, message queue, connection data."""

import base64
import hashlib
import json
import logging
import mimetypes
import os
import queue
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)


def _audit_outbound_telegram_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    clean = str(text).strip()
    try:
        from distr.core.human_engagement import sanitize_engagement_text

        return sanitize_engagement_text(clean)
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
    if len(clean) > 900:
        clean = clean[:890].rsplit(" ", 1)[0].rstrip() + "\nMore detail is in the app."
    return clean


def _usable_outbound_file(path: Optional[str]) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


class TelegramSenderMixin:
    """Handles sending messages to Telegram, message queue processing, and connection data persistence."""

    def send_to_telegram(
        self,
        text: Optional[str] = None,
        audio_file_path: Optional[str] = None,
        screenshot_path: Optional[str] = None,
        document_path: Optional[str] = None,
        video_path: Optional[str] = None,
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
        if text:
            text = _audit_outbound_telegram_text(text)
            if not text and not any((audio_file_path, screenshot_path, document_path, video_path)):
                return False
            try:
                from distr.core.human_engagement import is_low_value_status_text

                if is_low_value_status_text(text):
                    logger.debug("[Telegram] Suppressed low-value status message: %s", text[:100])
                    return False
            except Exception:
                pass
            text_lower = (text or "").lower()
            # Block disconnect/reconnect messages (except "has shut down" which is only on manual disconnect)
            if (
                "disconnect" in text_lower or "reconnect" in text_lower
            ) and "has shut down" not in text_lower:
                logger.warning(
                    f"[Telegram] 🚫 BLOCKED disconnect/reconnect message: '{text[:100]}...'"
                )
                return False

            # Log what we're sending (especially for connection messages)
            # Log connection-related messages more prominently
            if "online" in text_lower or "shut down" in text_lower:
                logger.info(
                    f"[Telegram] 📤 Queuing message to Telegram: '{text[:100]}...'"
                )
            else:
                logger.debug(f"[Telegram] Queuing message: '{text[:50]}...'")

        # Rate Limiting Check
        now = time.time()
        if now - self._last_send_time < self._min_send_interval:
            logger.warning("Rate limit hit, dropping message")
            return False

        # Outgoing Deduplication
        import hashlib
        import base64
        import mimetypes

        message_content = f"{text or ''}|{audio_file_path or ''}|{screenshot_path or ''}|{document_path or ''}|{video_path or ''}"
        message_hash = hashlib.md5(message_content.encode()).hexdigest()

        if message_hash in self._recent_messages:
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

        self._recent_messages[message_hash] = now

        # Build Message Payload
        msg = {"type": "send_message"}

        if text:
            msg["text"] = text

        # CRITICAL: ALWAYS send to private chat (telegram_user_id), NEVER to groups
        # Helper to get valid chat id - ALWAYS prioritize telegram_user_id (private chat)
        # Never use self.chat_id if it's a group/channel (negative ID)
        effective_chat_id = None

        # First priority: telegram_user_id (private chat, always positive)
        if self.telegram_user_id:
            effective_chat_id = self.telegram_user_id
            logger.info(
                f"[Telegram] 📤 Using telegram_user_id (private chat): {effective_chat_id}"
            )

        # Second priority: self.chat_id ONLY if it's a private chat (positive) AND telegram_user_id is not set
        elif (
            self.chat_id
            and isinstance(self.chat_id, (int, str))
            and not str(self.chat_id).startswith("-")
        ):
            effective_chat_id = (
                int(self.chat_id) if isinstance(self.chat_id, str) else self.chat_id
            )
            logger.info(
                f"[Telegram] 📤 Using chat_id (private chat, fallback): {effective_chat_id}"
            )

        # CRITICAL: Block sending to groups/channels
        elif (
            self.chat_id
            and isinstance(self.chat_id, (int, str))
            and str(self.chat_id).startswith("-")
        ):
            logger.error(
                f"[Telegram] 🚫 BLOCKED: Attempted to send to group/channel (chat_id: {self.chat_id}). Only private chats allowed!"
            )
            # Try to load telegram_user_id from settings as last resort
            if not self.telegram_user_id:
                logger.warning(
                    f"[Telegram] ⚠️ telegram_user_id is None, cannot send to group. Message blocked."
                )
                return False

        # Log what chat_id we're using for sending
        logger.info(
            f"[Telegram] 📤 Preparing to send - chat_id: {self.chat_id}, telegram_user_id: {self.telegram_user_id}, effective_chat_id: {effective_chat_id}"
        )

        if effective_chat_id:
            msg["chat_id"] = effective_chat_id
            logger.info(f"[Telegram] ✅ Using chat_id: {effective_chat_id} for sending")
        elif self.app_user_id:
            msg["app_user_id"] = self.app_user_id
            logger.info(
                f"[Telegram] ✅ Using app_user_id: {self.app_user_id} for sending"
            )
        else:
            logger.error("No destination (chat_id/user_id) for message")
            return False

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

                # Save to temporary WebP file (quality 70 = smaller payload, still good for screenshots)
                with tempfile.NamedTemporaryFile(
                    suffix=".webp", delete=False
                ) as tmp_file:
                    tmp_webp_path = tmp_file.name

                img.save(tmp_webp_path, "WEBP", quality=70, method=2)

                # Read the WebP file
                with open(tmp_webp_path, "rb") as webp_file:
                    image_data = webp_file.read()

                # Format file size for human readability
                webp_size = len(image_data)
                if webp_size < 1024:
                    size_str = f"{webp_size} B"
                elif webp_size < 1024 * 1024:
                    size_str = f"{webp_size / 1024:.1f} KB"
                else:
                    size_str = f"{webp_size / (1024 * 1024):.1f} MB"

                logger.info("[Telegram] Uploading WebP screenshot: %s", size_str)

                # Clean up temp file
                try:
                    os.unlink(tmp_webp_path)
                except OSError:
                    pass

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
                with open(screenshot_path, "rb") as f:
                    image_data = f.read()
                    b64 = base64.b64encode(image_data).decode("utf-8")
                    # Determine MIME type from file extension
                    mime_type, _ = mimetypes.guess_type(screenshot_path)
                    if not mime_type or not mime_type.startswith("image/"):
                        mime_type = "image/png"

                    # Format file size for human readability
                    orig_size = len(image_data)
                    if orig_size < 1024:
                        size_str = f"{orig_size} B"
                    elif orig_size < 1024 * 1024:
                        size_str = f"{orig_size / 1024:.1f} KB"
                    else:
                        size_str = f"{orig_size / (1024 * 1024):.1f} MB"

                    logger.info(
                        "[Telegram] Uploading %s screenshot: %s", mime_type, size_str
                    )
                    msg["screenshot"] = {
                        "data": b64,
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
        has_media = (
            "video" in msg or "audio" in msg or "document" in msg or "screenshot" in msg
        )
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
        self._message_queue.put(msg)
        # Update last message time when sending (connection is active)
        self._last_message_time = time.time()
        return True

    def _process_message_queue(self):
        """Pop queue run by QTimer."""
        if not self.is_connected():
            logger.debug("[Telegram] Queue processor: Not connected, skipping")
            return

        try:
            while not self._message_queue.empty():
                msg = self._message_queue.get_nowait()
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

                self._send_websocket_message(msg)
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
