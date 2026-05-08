"""Inbound message handling mixin — dedup, voice, file, group tracking, batching."""

import hashlib
import json
import logging
import os
import queue
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

try:
    import requests
except ImportError:
    requests = None

from PyQt6.QtCore import pyqtSlot

from distr.core.integrations.telegram.utils import hash_channel_id

logger = logging.getLogger(__name__)


class TelegramMessagesMixin:
    """Handles incoming Telegram messages, voice notes, files, and group tracking."""

    def _handle_telegram_message(self, wrapper_data: Dict[str, Any]):
        """Logic extracted from old implementation to process inbound messages."""
        inner_data = wrapper_data.get("data", {})
        msg_id = inner_data.get("message_id")

        # Log raw message data for debugging (especially chat_id)
        chat_id_raw = inner_data.get("chat_id")
        logger.info(
            f"📥 Telegram message received - msg_id: {msg_id}, chat_id: {chat_id_raw}, has_text: {bool(inner_data.get('text'))}, has_media: {bool(inner_data.get('media'))}"
        )

        # 1. ID Deduplication
        if msg_id and msg_id in self._processed_message_ids:
            logger.debug(f"Skipping duplicate message ID: {msg_id}")
            return
        if msg_id:
            self._processed_message_ids.add(msg_id)
            if len(self._processed_message_ids) > self._max_processed_cache_size:
                self._processed_message_ids.pop()

        # 2. Content Hash Deduplication
        import hashlib

        # Create a hash of the critical content — include message_id to avoid
        # false deduplication of voice notes (which have no text and similar dates)
        content_str = f"{msg_id}{inner_data.get('text')}{inner_data.get('date')}{inner_data.get('from', {}).get('id')}"
        msg_hash = hashlib.md5(content_str.encode()).hexdigest()

        if msg_hash in self._processed_message_hashes:
            logger.debug(f"Skipping duplicate message hash: {msg_hash}")
            return
        self._processed_message_hashes.add(msg_hash)
        if len(self._processed_message_hashes) > self._max_processed_cache_size:
            self._processed_message_hashes.pop()

        # 3. Update Chat ID knowledge and detect chat type
        chat_id = inner_data.get("chat_id")
        chat_type = None
        chat_title = None
        chat_type_display = None

        # Check for chat type in various possible locations
        chat_info = inner_data.get("chat", {})
        chat_description = None
        if isinstance(chat_info, dict):
            chat_type = chat_info.get("type")
            # Try multiple fields for title - title is most common for groups
            chat_title = (
                chat_info.get("title")
                or chat_info.get("first_name")  # For private chats
                or chat_info.get("username")
                or chat_info.get("name")
            )  # Alternative field name
            chat_description = (
                chat_info.get("description")
                or chat_info.get("about")
                or chat_info.get("bio")
            )
        else:
            # Try direct fields in inner_data
            chat_type = inner_data.get("chat_type")
            chat_title = (
                inner_data.get("chat_title")
                or inner_data.get("chat_first_name")
                or inner_data.get("chat_username")
                or inner_data.get("chat_name")
            )
            chat_description = (
                inner_data.get("chat_description")
                or inner_data.get("chat_about")
                or inner_data.get("chat_bio")
            )

        # If still no title and this is a group, try to get it from tracked groups
        if (
            not chat_title
            and chat_id
            and isinstance(chat_id, (int, str))
            and str(chat_id).startswith("-")
        ):
            try:
                from distr.core.settings import load_settings_from_db

                settings = load_settings_from_db()
                connected_accounts = settings.get("connected_accounts", [])
                if isinstance(connected_accounts, str):
                    connected_accounts = json.loads(connected_accounts)

                telegram_account = next(
                    (
                        acc
                        for acc in connected_accounts
                        if isinstance(acc, dict) and acc.get("provider") == "telegram"
                    ),
                    None,
                )
                if telegram_account:
                    groups = telegram_account.get("groups", [])
                    chat_id_int = int(chat_id) if isinstance(chat_id, str) else chat_id
                    for group in groups:
                        if (
                            isinstance(group, dict)
                            and group.get("chat_id") == chat_id_int
                        ):
                            existing_title = group.get("title")
                            if (
                                existing_title
                                and existing_title
                                != f"Telegram {group.get('type', 'group')}"
                            ):
                                chat_title = existing_title
                                logger.info(
                                    f"[Telegram] Using tracked group title: {chat_title}"
                                )
                                break
            except Exception as e:
                logger.debug(f"Could not get title from tracked groups: {e}")

        # Log chat type information and store for later use
        if chat_type:
            chat_type_display = {
                "channel": "Channel",
                "group": "Group",
                "supergroup": "Supergroup",
                "private": "Private Chat",
            }.get(chat_type.lower(), chat_type.capitalize())
            logger.info(
                f"Message from Telegram {chat_type_display}"
                + (f": {chat_title}" if chat_title else "")
            )
            # Store the actual chat type (lowercase) for checking
            self.current_chat_type = chat_type.lower()
        else:
            # Try to infer from chat_id (negative IDs are usually groups/channels)
            if chat_id:
                if isinstance(chat_id, (int, str)) and str(chat_id).startswith("-"):
                    chat_type_display = "Group/Channel"
                    self.current_chat_type = "group"  # Assume group for negative IDs
                    logger.info(
                        f"Message from Telegram Group/Channel (inferred from negative chat_id: {chat_id})"
                    )
                else:
                    chat_type_display = "Private"
                    self.current_chat_type = "private"
                    logger.info(
                        f"Message from Telegram Private Chat (inferred from positive chat_id: {chat_id})"
                    )
            else:
                self.current_chat_type = None

        if chat_id:
            self.chat_id = chat_id

            # CRITICAL: Set telegram_user_id ONLY for private chats (positive chat_id)
            # This ensures we always send responses to the private chat, not groups
            if isinstance(chat_id, (int, str)) and not str(chat_id).startswith("-"):
                # This is a private chat - set it as telegram_user_id for sending
                chat_id_int = int(chat_id) if isinstance(chat_id, str) else chat_id
                if not self.telegram_user_id or self.telegram_user_id != chat_id_int:
                    self.telegram_user_id = chat_id_int
                    logger.info(
                        f"[Telegram] ✅ Set telegram_user_id to private chat: {self.telegram_user_id}"
                    )
                    # ALWAYS save telegram_user_id when we set it from a private chat message
                    # This ensures it persists across app restarts so we know the last channel to send to
                    logger.info(
                        f"[Telegram] 💾 Saving telegram_user_id to database for persistence: {self.telegram_user_id}"
                    )
                    # Only pass telegram_user_id, not chat_id, to avoid any confusion
                    self._update_stored_connection_data(
                        telegram_user_id=self.telegram_user_id
                    )

            # Track Telegram groups/channels when we receive messages from them
            # DO NOT update stored connection data with group chat_id - it would overwrite the private chat ID
            if chat_type in ("group", "supergroup", "channel") or (
                chat_id
                and isinstance(chat_id, (int, str))
                and str(chat_id).startswith("-")
            ):
                self._track_telegram_group(
                    chat_id, chat_type or "group", chat_title, chat_description
                )

        # Store chat type for later use in print statement
        self._last_chat_type_display = chat_type_display

        # 4. Handle Media
        media = inner_data.get("media")
        media_handled = False
        # Determine input_type: "voice" for voice/audio media, "text" for everything else
        input_type = "text"
        if media:
            media_type = media.get("type")
            logger.info("Received media message (type: %s)", media_type)
            if media_type == "voice":
                input_type = "voice"
                self._handle_voice_message(media.get("download_url"), "voice", message_id=msg_id)
                media_handled = True
            elif media_type == "audio":
                input_type = "voice"
                self._handle_voice_message(media.get("download_url"), "audio", message_id=msg_id)
                media_handled = True
            elif media_type in ("photo", "document", "video"):
                self._handle_file_message(media, inner_data.get("caption") or inner_data.get("text"))
                media_handled = True
            else:
                # Unhandled media type (sticker, animation, contact, etc.)
                # Don't silently drop — forward a description to the agent so the
                # message isn't lost. If there's a caption, that becomes the text.
                logger.warning(
                    "[Telegram] Unhandled media type %r — forwarding description to agent",
                    media_type,
                )
                caption_text = inner_data.get("caption") or inner_data.get("text")
                fallback_text = caption_text or f"[Telegram {media_type or 'media'} received]"
                # Inject as text so the agent/emit path below picks it up
                inner_data = dict(inner_data)
                inner_data["text"] = fallback_text

        # 5. Emit to App / Agent
        text = inner_data.get("text")
        media = inner_data.get("media")
        caption = inner_data.get("caption")

        # Skip text emission if media was already forwarded to the agent
        # (the caption is included in the file message)
        if media_handled and not text:
            return
        if media_handled and text and text == caption:
            return  # Caption already forwarded with the file

        # Check for "remote control" command - send link instead of forwarding to agent
        if text:
            text_lower = text.lower().strip()
            remote_control_patterns = [
                "remote control",
                "open remote control",
                "remote",
                "show remote",
                "get remote",
            ]

            if any(pattern in text_lower for pattern in remote_control_patterns):
                # Get chat_id for the remote control link
                chat_id = self._get_chat_id()
                if chat_id:
                    hashed = hash_channel_id(chat_id)
                    remote_url = (
                        f"https://www.decisionsai.net/api/remote/?channel={hashed}"
                    )
                    response_text = (
                        f"🔗 Remote Control:\n{remote_url}\n\n"
                        f"⚠️ Do not share this link. It grants full remote access to your machine "
                        f"(mouse, keyboard, screen). The link expires at midnight UTC."
                    )
                    logger.info(
                        f"Sending remote control link for chat_id={chat_id} (hashed={hashed}): {remote_url}"
                    )
                    self.send_to_telegram(response_text)
                    return
                else:
                    error_text = "⚠️ Cannot generate remote control link: chat_id not available yet. Please try again in a moment."
                    self.send_to_telegram(error_text)
                    return

            # Detect mode-switch intent and persist to Settings_Store
            from distr.core.integrations.telegram.response_format import detect_mode_switch_intent
            from distr.core.services.settings_service import update_setting

            mode_intent = detect_mode_switch_intent(text)
            if mode_intent == "text_only":
                update_setting("telegram_text_only_override", True)
                logger.info(
                    "[Telegram] ✅ Detected text-only intent — persisted telegram_text_only_override=True"
                )
            elif mode_intent == "voice":
                update_setting("telegram_text_only_override", False)
                logger.info(
                    "[Telegram] ✅ Detected voice intent — persisted telegram_text_only_override=False"
                )

        # Always log incoming messages for visibility with chat_id
        chat_id_str = f" (chat_id: {chat_id})" if chat_id else " (no chat_id)"
        logger.info(
            f"📩 Processing Telegram message{chat_id_str}: {text[:50] if text else '(media check)'}..."
        )

        # Determine source for display
        source_info = ""
        if hasattr(self, "_last_chat_type_display") and self._last_chat_type_display:
            source_info = f" [{self._last_chat_type_display}]"
        elif chat_id:
            if isinstance(chat_id, (int, str)) and str(chat_id).startswith("-"):
                source_info = " [Group/Channel]"
            else:
                source_info = " [Private]"

        # Log incoming messages so we can see all Telegram activity with chat_id
        chat_id_display = f" chat_id={chat_id}" if chat_id else " (no chat_id)"
        logger.info(
            "[Telegram] Received%s%s: %s",
            source_info,
            chat_id_display,
            text if text else "(media check)",
        )

        # CRITICAL: Check if message is from a group/channel (not private chat)
        is_group_message = False
        if chat_id and isinstance(chat_id, (int, str)) and str(chat_id).startswith("-"):
            is_group_message = True
        elif chat_type and chat_type.lower() in ("group", "supergroup", "channel"):
            is_group_message = True

        if is_group_message:
            # Check if message has any content - skip truly empty messages
            # Note: text, media, and caption are already extracted above (line 665-667)
            has_content = bool(text) or bool(media) or bool(caption)

            if not has_content:
                logger.debug(
                    f"[Telegram] ⏭️ Skipping empty group message (no text, media, or caption) - msg_id: {msg_id}, chat_id: {chat_id}"
                )
                # Still track the group, but don't store the empty message
                if chat_id:
                    chat_info_dict = (
                        inner_data.get("chat", {})
                        if isinstance(inner_data.get("chat"), dict)
                        else {}
                    )
                    chat_title = None
                    if isinstance(chat_info_dict, dict):
                        chat_title = (
                            chat_info_dict.get("title")
                            or chat_info_dict.get("first_name")
                            or chat_info_dict.get("username")
                            or chat_info_dict.get("name")
                        )
                    else:
                        chat_title = (
                            inner_data.get("chat_title")
                            or inner_data.get("chat_first_name")
                            or inner_data.get("chat_username")
                            or inner_data.get("chat_name")
                        )
                    chat_description = None
                    if isinstance(chat_info_dict, dict):
                        chat_description = (
                            chat_info_dict.get("description")
                            or chat_info_dict.get("about")
                            or chat_info_dict.get("bio")
                        )
                    else:
                        chat_description = (
                            inner_data.get("chat_description")
                            or inner_data.get("chat_about")
                            or inner_data.get("chat_bio")
                        )
                    self._track_telegram_group(
                        chat_id, chat_type or "group", chat_title, chat_description
                    )
                return  # Don't store empty messages

            # Store group/channel messages for later processing - don't act on them immediately
            logger.info(
                f"[Telegram] 📦 Storing group/channel message for later processing (chat_id: {chat_id}, type: {chat_type})"
            )

            # Extract comprehensive metadata from Telegram message
            from_user = inner_data.get("from", {})
            chat_info_dict = (
                inner_data.get("chat", {})
                if isinstance(inner_data.get("chat"), dict)
                else {}
            )

            # Store the message with comprehensive metadata (in-memory for quick access)
            stored_message = {
                "message_id": msg_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "chat_title": chat_title,
                "chat_description": chat_description,
                "chat_username": chat_info_dict.get("username")
                or inner_data.get("chat_username"),
                "text": text,
                "media": media,
                "from": {
                    "id": from_user.get("id"),
                    "is_bot": from_user.get("is_bot", False),
                    "first_name": from_user.get("first_name"),
                    "last_name": from_user.get("last_name"),
                    "username": from_user.get("username"),
                    "language_code": from_user.get("language_code"),
                },
                "date": inner_data.get("date"),
                "edit_date": inner_data.get("edit_date"),
                "reply_to_message_id": inner_data.get("reply_to_message_id"),
                "forward_from": inner_data.get("forward_from"),
                "forward_from_chat": inner_data.get("forward_from_chat"),
                "forward_from_message_id": inner_data.get("forward_from_message_id"),
                "entities": inner_data.get(
                    "entities"
                ),  # Text entities (mentions, hashtags, etc.)
                "caption": inner_data.get("caption"),  # Caption for media
                "timestamp": datetime.utcnow().isoformat(),
                "raw_data": inner_data,  # Store full raw data for later processing
            }

            # Add to queue for batch processing
            try:
                self._group_message_queue.put_nowait(stored_message)
            except queue.Full:
                logger.warning(
                    f"[Telegram] Group message queue is full, dropping message {msg_id}"
                )

            # Add to storage list (with size limit)
            self._group_messages_storage.append(stored_message)
            if len(self._group_messages_storage) > self._max_stored_group_messages:
                # Remove oldest messages
                self._group_messages_storage.pop(0)

            # CRITICAL: Also persist to database for access when app is not running
            try:
                from distr.core.db import get_session

                # Import TelegramGroupMessage - this ensures the table exists
                from distr.core.db import TelegramGroupMessage
                import json

                with get_session() as session:
                    # Check if message already exists (deduplication)
                    existing = (
                        session.query(TelegramGroupMessage)
                        .filter_by(telegram_message_id=msg_id, chat_id=str(chat_id))
                        .first()
                    )

                    if not existing:
                        # Prepare data for database storage
                        sender_data_json = json.dumps(
                            {
                                "id": from_user.get("id"),
                                "is_bot": from_user.get("is_bot", False),
                                "first_name": from_user.get("first_name"),
                                "last_name": from_user.get("last_name"),
                                "username": from_user.get("username"),
                                "language_code": from_user.get("language_code"),
                            }
                        )

                        media_data_json = json.dumps(media) if media else None
                        forward_from_json = (
                            json.dumps(inner_data.get("forward_from"))
                            if inner_data.get("forward_from")
                            else None
                        )
                        forward_from_chat_json = (
                            json.dumps(inner_data.get("forward_from_chat"))
                            if inner_data.get("forward_from_chat")
                            else None
                        )
                        entities_json = (
                            json.dumps(inner_data.get("entities"))
                            if inner_data.get("entities")
                            else None
                        )
                        raw_data_json = json.dumps(inner_data)

                        # Ensure we have a title - use fallback if needed
                        final_chat_title = chat_title
                        if not final_chat_title:
                            # Try to get from tracked groups one more time
                            try:
                                from distr.core.settings import (
                                    load_settings_from_db,
                                )

                                settings = load_settings_from_db()
                                connected_accounts = settings.get(
                                    "connected_accounts", []
                                )
                                if isinstance(connected_accounts, str):
                                    connected_accounts = json.loads(connected_accounts)
                                telegram_account = next(
                                    (
                                        acc
                                        for acc in connected_accounts
                                        if isinstance(acc, dict)
                                        and acc.get("provider") == "telegram"
                                    ),
                                    None,
                                )
                                if telegram_account:
                                    groups = telegram_account.get("groups", [])
                                    chat_id_int = (
                                        int(chat_id)
                                        if isinstance(chat_id, str)
                                        else chat_id
                                    )
                                    for group in groups:
                                        if (
                                            isinstance(group, dict)
                                            and group.get("chat_id") == chat_id_int
                                        ):
                                            existing_title = group.get("title")
                                            if existing_title:
                                                final_chat_title = existing_title
                                                break
                            except Exception:
                                pass

                        # Last resort fallback
                        if not final_chat_title:
                            final_chat_title = f"Telegram {chat_type or 'group'}"
                            logger.warning(
                                f"[Telegram] No title found for group {chat_id}, using fallback: {final_chat_title}"
                            )

                        db_message = TelegramGroupMessage(
                            telegram_message_id=msg_id,
                            chat_id=str(chat_id),
                            chat_type=chat_type,
                            chat_title=final_chat_title,
                            chat_username=chat_info_dict.get("username")
                            or inner_data.get("chat_username"),
                            chat_description=chat_description,
                            text=text,
                            caption=inner_data.get("caption"),
                            media_type=media.get("type") if media else None,
                            media_data=media_data_json,
                            sender_data=sender_data_json,
                            telegram_date=inner_data.get("date"),
                            edit_date=inner_data.get("edit_date"),
                            reply_to_message_id=inner_data.get("reply_to_message_id"),
                            forward_from=forward_from_json,
                            forward_from_chat=forward_from_chat_json,
                            entities=entities_json,
                            raw_data=raw_data_json,
                            processed=False,
                        )
                        session.add(db_message)
                        session.commit()
                        # Log sender info for debugging
                        sender_name = (
                            f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip()
                            or from_user.get("username", "")
                            or f"User {from_user.get('id', 'Unknown')}"
                        )
                        logger.info(
                            f"[Telegram] ✅ Persisted group message to database (ID: {db_message.id}, chat_id: {chat_id}, group: {final_chat_title}, sender: {sender_name}, text: {text[:50] if text else 'media'})"
                        )
                    else:
                        logger.debug(
                            f"[Telegram] Message {msg_id} already exists in database, skipping"
                        )
            except Exception as e:
                logger.error(
                    f"[Telegram] ❌ Failed to persist group message to database: {e}",
                    exc_info=True,
                )
                import traceback

                traceback.print_exc()
                # Don't fail the whole operation if DB save fails - in-memory storage still works

            # Emit raw signal for any direct listeners (but don't forward to agent)
            self.message_received.emit(inner_data)

            logger.info(
                f"[Telegram] ✅ Stored group message (in-memory: {len(self._group_messages_storage)}, also persisted to DB)"
            )
            return  # Don't process further - just store and return

        # Only process messages from private chats
        # Emit raw signal for any direct listeners
        self.message_received.emit(inner_data)

        # Mark the message as read so sender sees the double-check
        if msg_id:
            self._mark_message_as_read(msg_id)

        # ── Batch text messages before forwarding to agent ──
        # Instead of emitting each message immediately, buffer them.
        # After 3 seconds of silence the batch is flushed as one combined message.
        if text:
            try:
                self._telegram_batch_thread_id = int(chat_id) if chat_id is not None else None
            except (TypeError, ValueError):
                self._telegram_batch_thread_id = getattr(self, "telegram_user_id", None)
            self._enqueue_telegram_batch(str(text), None, input_type)
            logger.info(
                "[Telegram] 📥 Buffered message (%d in batch): '%s' (chat_id: %s)",
                len(self._telegram_batch_buffer), text[:50], chat_id,
            )

    def _track_telegram_group(
        self,
        chat_id: int,
        chat_type: str,
        chat_title: Optional[str],
        chat_description: Optional[str] = None,
    ):
        """
        Track Telegram groups/channels that the bot receives messages from.
        Stores them in connected_accounts for use in Trello ticket dialog.
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

            # Get or create groups list
            groups = telegram_account.get("groups", [])
            if not isinstance(groups, list):
                groups = []

            # Check if this group is already tracked
            chat_id_int = int(chat_id) if isinstance(chat_id, str) else chat_id
            existing_group = None
            for group in groups:
                if isinstance(group, dict) and group.get("chat_id") == chat_id_int:
                    existing_group = group
                    break

            # Update or add group
            group_data = {
                "chat_id": chat_id_int,
                "type": chat_type,
                "title": chat_title or f"Telegram {chat_type}",
                "first_seen": existing_group.get("first_seen")
                if existing_group
                else datetime.utcnow().isoformat(),
            }

            # Add description if provided (only update if we have new description and existing one is empty)
            if chat_description:
                if not existing_group or not existing_group.get("description"):
                    group_data["description"] = chat_description
                elif existing_group.get("description"):
                    # Keep existing description if we already have one
                    group_data["description"] = existing_group.get("description")

            if existing_group:
                # Update existing group
                existing_group.update(group_data)
                logger.debug(
                    f"[Telegram] Updated tracked group: {chat_id_int} ({chat_type})"
                )
            else:
                # Add new group
                groups.append(group_data)
                telegram_account["groups"] = groups
                logger.info(
                    f"[Telegram] ✅ Tracked new Telegram group: {chat_id_int} ({chat_type}) - {chat_title or 'No title'}"
                )

            # Save updated settings
            settings["connected_accounts"] = connected_accounts
            save_settings_to_db(settings)

        except Exception as e:
            logger.warning(f"Could not track Telegram group: {e}", exc_info=True)

    # ── Telegram message batching ─────────────────────────────────────────

    def _flush_telegram_batch(self):
        """Flush the buffered Telegram messages as a single combined message to the agent.

        Called by the batch timer after BATCH_DELAY_MS of silence.
        If only one message is in the buffer it is sent as-is.
        Multiple messages are joined with newlines so the agent sees the full thread.
        """
        if not self._telegram_batch_buffer:
            return

        # Collect all buffered items
        items = list(self._telegram_batch_buffer)
        self._telegram_batch_buffer.clear()

        # Separate text messages and media messages
        texts = []
        image_path = None
        input_type = "text"  # default; last voice item wins
        for item in items:
            text, is_media, img_path = item[0], item[1], item[2]
            # Extract input_type from 4-element tuples (new format)
            item_input_type = item[3] if len(item) > 3 else "text"
            if text:
                texts.append(text)
            if img_path and not image_path:
                image_path = img_path  # Use first image path for vision
            if item_input_type == "voice":
                input_type = "voice"

        combined = "\n".join(texts) if texts else ""
        if not combined:
            return

        # Store input_type on the instance so downstream consumers
        # (event handlers, LLM service) can read it when building the
        # send_to_telegram event data dict.
        self._current_input_type = input_type

        try:
            from distr.core.integrations.bus import get_integration_message_bus

            logger.info(
                "[Telegram] 📤 Flushing batch (%d messages, input_type=%s) to agent: '%s'",
                len(items), input_type, combined[:80],
            )
            get_integration_message_bus().deliver_telegram_user_input(
                text=combined,
                image_path=image_path,
                telegram_chat_id=getattr(self, "_telegram_batch_thread_id", None),
                speak=None,
            )
            logger.info("[Telegram] ✅ Batch flushed to agent (input_type=%s)", input_type)
        except Exception as e:
            logger.error("Failed to flush Telegram batch: %s", e, exc_info=True)
            # Stop typing on error — otherwise the loop runs forever
            self._stop_typing_loop()

    def _enqueue_telegram_batch(self, text: str, image_path: str = None, input_type: str = "text"):
        """Add a message to the batch buffer and (re)start the flush timer."""
        uid = getattr(self, "telegram_user_id", None)
        if uid is not None:
            try:
                self._telegram_batch_thread_id = int(uid)
            except (TypeError, ValueError):
                pass
        self._start_typing_loop("typing")  # Keep dots alive while batching
        self._telegram_batch_buffer.append((text, bool(image_path), image_path, input_type))
        self._telegram_batch_timer.start(self._TELEGRAM_BATCH_DELAY_MS)

    @pyqtSlot(str, str)
    def _enqueue_telegram_batch_slot(self, text: str, image_path: str):
        """Qt slot wrapper so background threads can enqueue via QMetaObject.invokeMethod."""
        self._enqueue_telegram_batch(text, image_path if image_path else None)

    def _handle_voice_message(self, url, media_type, message_id=None):
        """Trigger background thread used for transcription (requests/STT)."""
        if not url:
            logger.warning("[Telegram] ⚠️ No download_url for %s — skipping transcription", media_type)
            self.send_to_telegram("⚠️ Could not process voice note: no download URL in message. Please resend.")
            return
        # Mark the voice message as read immediately
        if message_id:
            self._mark_message_as_read(message_id)
        # Show "recording" indicator so user knows we're processing a voice note, not typing text
        self._start_typing_loop("record_voice")
        threading.Thread(
            target=self._transcribe_voice_task, args=(url, media_type), daemon=True
        ).start()

    def _transcribe_voice_task(self, media_url, media_type):
        """Background task to download, convert, and transcribe voice notes."""
        if not requests:
            logger.error("[Telegram] ❌ requests library not available — cannot download voice note")
            self._stop_typing_loop()
            self.send_to_telegram("⚠️ Could not process voice note: requests library unavailable.")
            return

        try:
            # Typing loop already started by _handle_voice_message (as record_voice)

            # Download
            logger.info("[Telegram] 🎙️ Downloading %s from %s...", media_type, media_url[:80] if media_url else "None")
            resp = requests.get(media_url, timeout=30)
            if resp.status_code != 200:
                logger.error("[Telegram] ❌ Failed to download voice: HTTP %s", resp.status_code)
                self._stop_typing_loop()
                self.send_to_telegram(f"⚠️ Could not download voice note (HTTP {resp.status_code}). Please try again.")
                return

            temp_dir = Path(tempfile.gettempdir()) / "decisions_ai_telegram"
            temp_dir.mkdir(parents=True, exist_ok=True)
            ext = ".ogg" if media_type == "voice" else ".mp3"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            fpath = temp_dir / f"telegram_{timestamp}{ext}"

            with open(fpath, "wb") as f:
                f.write(resp.content)

            logger.info(
                "[Telegram] 🎙️ Downloaded voice note (%s bytes, %s). Converting & transcribing...",
                len(resp.content), ext,
            )

            # Convert OGG Opus → WAV for STT compatibility (ffmpeg is sub-second)
            wav_path = fpath.with_suffix(".wav")
            try:
                import subprocess
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(fpath), "-ar", "16000", "-ac", "1", str(wav_path)],
                    capture_output=True, timeout=10
                )
                if result.returncode == 0 and wav_path.exists():
                    logger.info("[Telegram] Converted to WAV: %s", wav_path)
                    fpath = wav_path
                else:
                    logger.warning("[Telegram] ffmpeg conversion failed (rc=%d), using original file", result.returncode)
            except FileNotFoundError:
                logger.warning("[Telegram] ffmpeg not found, using original OGG file")
            except Exception as e:
                logger.warning("[Telegram] ffmpeg conversion error: %s, using original file", e)

            # Send typing indicator again before transcription
            # (loop is already running, no need for one-shot)

            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()

            if app and hasattr(app, "agent_command_queue"):
                import uuid
                req_id = str(uuid.uuid4())
                logger.info("[Telegram] 🎙️ Submitting voice note for transcription (request_id: %s, file: %s)", req_id, fpath)
                app.agent_command_queue.put(
                    (
                        "transcribe_file",
                        {"audio_file_path": str(fpath), "request_id": req_id, "input_type": "voice"},
                    ),
                    block=False,
                )
            else:
                logger.warning("[Telegram] ⚠️ Agent queue not available for transcription — voice note will not be processed")
                self._stop_typing_loop()
                self.send_to_telegram("⚠️ Could not process voice note: agent not ready. Please try again.")

        except requests.exceptions.Timeout:
            logger.error("[Telegram] ❌ Voice download timed out after 30s — URL may have expired")
            self._stop_typing_loop()
            self.send_to_telegram("⚠️ Voice note download timed out. The link may have expired — please resend.")
        except Exception as e:
            logger.error("[Telegram] ❌ Voice transcription error: %s", e, exc_info=True)
            self._stop_typing_loop()
            self.send_to_telegram(f"⚠️ Could not process voice note: {e}")

    # -------------------------------------------------------------------------
    #  Incoming file handling (photos, documents, video from Telegram)
    # -------------------------------------------------------------------------

    def _handle_file_message(self, media: dict, caption: Optional[str] = None):
        """Download a photo/document/video from Telegram and save to Downloads.

        After saving, the file path and any caption are forwarded to the agent
        so it can decide what to do (analyse the image, extract text from a PDF,
        create a ticket, etc.).
        """
        threading.Thread(
            target=self._download_and_forward_file,
            args=(media, caption),
            daemon=True,
        ).start()

    def _download_and_forward_file(self, media: dict, caption: Optional[str]):
        """Background thread: download file → save → forward to agent."""
        if not requests:
            logger.warning("[Telegram] requests library not available for file download")
            return

        media_type = media.get("type", "document")
        download_url = media.get("download_url")
        file_name = media.get("file_name")  # documents have this
        mime_type = media.get("mime_type", "")

        if not download_url:
            logger.warning("[Telegram] No download_url for %s media", media_type)
            return

        try:
            logger.info("[Telegram] 📥 Downloading %s from Telegram...", media_type)
            resp = requests.get(download_url, timeout=60)
            if resp.status_code != 200:
                logger.error("[Telegram] Download failed: HTTP %s", resp.status_code)
                return

            # Determine save directory
            save_dir = Path.home() / "Downloads" / "DecisionsAI"
            save_dir.mkdir(parents=True, exist_ok=True)

            # Build filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if file_name:
                # Use original filename, prefix with timestamp to avoid collisions
                safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in file_name)
                dest = save_dir / f"{timestamp}_{safe_name}"
            else:
                # Infer extension from mime or media type
                ext_map = {
                    "photo": ".jpg",
                    "video": ".mp4",
                    "document": ".bin",
                }
                ext = ext_map.get(media_type, ".bin")
                if mime_type:
                    import mimetypes as _mt
                    guessed = _mt.guess_extension(mime_type)
                    if guessed:
                        ext = guessed
                dest = save_dir / f"telegram_{media_type}_{timestamp}{ext}"

            with open(dest, "wb") as f:
                f.write(resp.content)

            file_size = len(resp.content)
            logger.info(
                "[Telegram] ✅ Saved %s (%s bytes) → %s",
                media_type, file_size, dest,
            )

            # Build a message for the agent describing what was received
            parts = []
            if caption:
                parts.append(caption)

            type_label = {"photo": "image", "document": "document", "video": "video"}.get(media_type, "file")
            parts.append(
                f"[Telegram {type_label} saved to {dest}]"
            )

            agent_text = "\n".join(parts) if parts else f"Received a {type_label} from Telegram."

            # Forward to agent via signal
            # Pass image path for photos (enables vision analysis), None for other types
            # (documents/videos are referenced by path in the agent_text itself)
            try:
                from distr.core.signals import signal_manager
                logger.info(
                    "[Telegram] 📤 Forwarding %s to agent: '%s'",
                    media_type, agent_text[:80],
                )
                image_path_for_vision = str(dest) if media_type == "photo" else None
                # Use the batch buffer so file messages are combined with any
                # text messages the user sends in the same burst.
                from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_enqueue_telegram_batch_slot",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, str(agent_text)),
                    Q_ARG(str, image_path_for_vision or ""),
                )
            except Exception as e:
                logger.error("[Telegram] Failed to forward file to agent: %s", e, exc_info=True)

            # Do not auto-ack media receipt in chat; forwarding context to the
            # agent is enough and avoids noisy "Received image/file" messages.

        except Exception as e:
            logger.error("[Telegram] File download error: %s", e, exc_info=True)

    # =========================================================================
    # Sending Logic
    # =========================================================================

