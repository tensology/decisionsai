"""WhatsApp WebSocket Manager — connects to DecisionsAI relay server and
receives inbound WhatsApp messages, routing them to the agent.

Architecture mirrors the Telegram integration:
  [WhatsApp Servers] <-> [Baileys Node.js service] <-> [Relay server /ws/whatsapp] <-> [This class] <-> [Agent]

This class handles:
  - WebSocket connection to the relay server
  - Reconnection with exponential backoff
  - QR code retrieval for linking (via REST proxy)
  - Inbound message routing to the agent via signal_manager
  - Media download and relay
  - Connection status tracking
"""

import hashlib
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer, QUrl

try:
    from PyQt6.QtWebSockets import QWebSocket
except ImportError:
    QWebSocket = None

logger = logging.getLogger(__name__)


class WhatsAppWebSocketManager(QObject):
    """
    Manages WebSocket connection to the DecisionsAI WhatsApp relay server.

    Connects to wss://www.decisionsai.net/ws/whatsapp, receives inbound
    WhatsApp messages from the Baileys service, and routes them to the agent.
    """

    # Signals
    message_received = pyqtSignal(dict)
    connection_status_changed = pyqtSignal(bool, str)  # connected, status_text
    qr_code_received = pyqtSignal(str)  # QR code string for scanning

    def __init__(self, server_url: str = "wss://www.decisionsai.net/ws/whatsapp"):
        super().__init__()

        # Allow env override
        env_url = os.environ.get("DECISIONSAI_WA_WS_URL")
        if env_url:
            self.server_url = env_url
        elif os.environ.get("DEBUG", "").upper() == "TRUE":
            self.server_url = "ws://localhost:8090/ws/whatsapp"
        else:
            self.server_url = server_url

        # REST API base URL (for QR code, status, etc.)
        self.api_base = os.environ.get(
            "DECISIONSAI_WA_API_BASE",
            "https://www.decisionsai.net/api/whatsapp"
            if not os.environ.get("DEBUG", "").upper() == "TRUE"
            else "http://localhost:8090/api/whatsapp",
        )

        # QWebSocket
        if QWebSocket:
            self.socket = QWebSocket()
            self.socket.connected.connect(self._on_connected)
            self.socket.disconnected.connect(self._on_disconnected)
            self.socket.textMessageReceived.connect(self._on_message)
            self.socket.error.connect(self._on_error)
        else:
            self.socket = None
            logger.warning("QWebSocket not available — WhatsApp integration disabled")

        # State
        self._connected = False
        self._active_disconnect = False
        self._reconnect_timer = QTimer()
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._do_reconnect)
        self._reconnect_delay_ms = 3000
        self._reconnect_delay_max_ms = 60000
        self._reconnect_delay_current_ms = 3000
        self._reconnect_attempts = 0

        # Message stats
        self._last_message_time = time.time()
        self._messages_received = 0

        # Connection info
        self.phone_info: Optional[Dict[str, Any]] = None
        self.current_qr: Optional[str] = None

        # Logging
        self._log_dir = Path.home() / ".decisionsai" / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ═════════════════════════════════════════════════════════════════════════
    # Connection Management
    # ═════════════════════════════════════════════════════════════════════════

    def connect(self):
        """Connect to the WhatsApp relay server."""
        if self.socket is None:
            logger.warning("WhatsApp: QWebSocket not available, cannot connect")
            return

        if self.socket.isValid():
            logger.info("WhatsApp: Closing existing connection before reconnecting")
            self._active_disconnect = True
            self.socket.close()
            self._active_disconnect = False

        logger.info(f"WhatsApp: Connecting to {self.server_url}")
        self._active_disconnect = False
        self.socket.open(QUrl(self.server_url))

    def disconnect(self):
        """Disconnect from the WhatsApp relay server."""
        self._active_disconnect = True
        self._reconnect_timer.stop()
        if self.socket and self.socket.isValid():
            self.socket.close()
        logger.info("WhatsApp: Disconnected")

    def is_connected(self) -> bool:
        """Check if connected to the relay server."""
        return self.socket is not None and self.socket.isValid()

    def _do_reconnect(self):
        """Timer callback for reconnection with exponential backoff."""
        if not self._active_disconnect:
            self._reconnect_attempts += 1
            logger.info(f"WhatsApp: Reconnect attempt #{self._reconnect_attempts}")
            self.connect()

    # ═════════════════════════════════════════════════════════════════════════
    # WebSocket Slots
    # ═════════════════════════════════════════════════════════════════════════

    def _on_connected(self):
        """Called when WebSocket connection is established."""
        logger.info("WhatsApp: Connected to relay server")
        self._connected = True
        self._reconnect_timer.stop()
        self._reconnect_attempts = 0
        self._reconnect_delay_current_ms = self._reconnect_delay_ms
        self.connection_status_changed.emit(True, "Connected")

        # Request current status
        self._request_status()

    def _on_disconnected(self):
        """Called when WebSocket disconnects."""
        self._connected = False
        self.phone_info = None
        self.current_qr = None

        if not self._active_disconnect:
            logger.warning("WhatsApp: Disconnected unexpectedly, scheduling reconnect")
            self._reconnect_delay_current_ms = min(
                self._reconnect_delay_current_ms * 2, self._reconnect_delay_max_ms
            )
            self._reconnect_timer.start(self._reconnect_delay_current_ms)
            self.connection_status_changed.emit(False, "Reconnecting...")
        else:
            self.connection_status_changed.emit(False, "Disconnected")

    def _on_error(self, error_code):
        """Handle WebSocket errors."""
        err_str = self.socket.errorString() if self.socket else "Unknown"
        logger.error(f"WhatsApp: WebSocket Error: {err_str} (code: {error_code})")

        if not self._active_disconnect:
            self._reconnect_delay_current_ms = self._reconnect_delay_ms
            if not self._reconnect_timer.isActive():
                self._reconnect_timer.start(min(self._reconnect_delay_ms, 1000))

    def _on_message(self, message: str):
        """Handle incoming WebSocket messages from the relay."""
        self._last_message_time = time.time()

        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "whatsapp_status":
                self._handle_status_update(data.get("data", {}))

            elif msg_type == "whatsapp_message":
                self._handle_inbound_message(data.get("data", {}))

            elif msg_type == "whatsapp_media":
                self._handle_inbound_media(data.get("data", {}))

            elif msg_type == "whatsapp_media_error":
                logger.warning(
                    f"WhatsApp: Media download error: {data.get('data', {}).get('error')}"
                )

            elif msg_type == "ping":
                # Respond to ping
                if self.socket and self.socket.isValid():
                    self.socket.sendTextMessage(json.dumps({"type": "pong"}))

        except json.JSONDecodeError:
            logger.error("WhatsApp: Failed to decode JSON from WebSocket")

    # ═════════════════════════════════════════════════════════════════════════
    # REST API Methods (for QR code, status, disconnect)
    # ═════════════════════════════════════════════════════════════════════════

    def request_qr_code(self):
        """Request the current QR code from the Baileys service via REST API.
        Called from the Settings UI when the user clicks the WhatsApp connect button.
        """
        try:
            import requests
            resp = requests.get(f"{self.api_base}/qr", timeout=5)
            data = resp.json()
            status = data.get("status", "unknown")
            qr_code = data.get("qr_code")
            phone = data.get("phone")

            if qr_code:
                self.current_qr = qr_code
                self.qr_code_received.emit(qr_code)

            if status == "connected" and phone:
                self.phone_info = phone
                self.connection_status_changed.emit(True, f"Connected as {phone.get('name', phone.get('jid', ''))}")
            elif status == "qr_ready" and qr_code:
                self.connection_status_changed.emit(False, "Scan QR code")
            elif status == "service_down":
                self.connection_status_changed.emit(False, "WhatsApp service unavailable")
            else:
                self.connection_status_changed.emit(False, f"Status: {status}")

            return data

        except Exception as e:
            logger.error(f"WhatsApp: QR code request failed: {e}")
            self.connection_status_changed.emit(False, "Connection failed")
            return {"status": "error", "error": str(e)}

    def get_status(self) -> dict:
        """Get current connection status from the Baileys service."""
        try:
            import requests
            resp = requests.get(f"{self.api_base}/status", timeout=5)
            return resp.json()
        except Exception as e:
            logger.error(f"WhatsApp: Status check failed: {e}")
            return {"status": "error", "error": str(e)}

    def disconnect_session(self):
        """Disconnect and clear the WhatsApp session (forces new QR on next connect)."""
        try:
            import requests
            resp = requests.post(f"{self.api_base}/disconnect", timeout=5)
            data = resp.json()
            if data.get("success"):
                self.phone_info = None
                self.current_qr = None
                self.connection_status_changed.emit(False, "Disconnected")
            return data
        except Exception as e:
            logger.error(f"WhatsApp: Disconnect failed: {e}")
            return {"success": False, "error": str(e)}

    def _request_status(self):
        """Internal: poll status on connect."""
        try:
            import requests
            resp = requests.get(f"{self.api_base}/status", timeout=5)
            data = resp.json()
            self._handle_status_update(data)
        except Exception as e:
            logger.debug(f"WhatsApp: Status poll failed: {e}")

    # ═════════════════════════════════════════════════════════════════════════
    # Message Handling
    # ═════════════════════════════════════════════════════════════════════════

    def _handle_status_update(self, data: dict):
        """Handle status updates from the Baileys service."""
        status = data.get("status", "unknown")

        if status == "connected":
            self.phone_info = data.get("phone")
            phone_name = self.phone_info.get("name", self.phone_info.get("jid", "")) if self.phone_info else ""
            logger.info(f"WhatsApp: Connected! Phone: {phone_name}")
            self.connection_status_changed.emit(True, f"Connected: {phone_name}")
            # Save to connected_accounts
            self._save_connection(data)

        elif status == "qr_ready":
            qr_code = data.get("qr_code")
            if qr_code:
                self.current_qr = qr_code
                self.qr_code_received.emit(qr_code)
            self.connection_status_changed.emit(False, "Scan QR code")

        elif status == "disconnected":
            self.phone_info = None
            self.connection_status_changed.emit(False, "Disconnected")

    def _handle_inbound_message(self, data: dict):
        """Handle an inbound WhatsApp text/media message."""
        self._messages_received += 1

        jid = data.get("jid", "")
        chat_type = data.get("chat_type", "private")
        sender = data.get("sender", {})
        text_content = data.get("text", "")
        caption = data.get("caption", "")
        media = data.get("media")
        message_id = data.get("message_id", "")
        wa_timestamp = data.get("timestamp")
        from_me = data.get("from_me", False)

        # Determine display text
        display_text = text_content or caption or ""
        if media and not display_text:
            media_type = media.get("type", "media") if isinstance(media, dict) else "media"
            display_text = f"[WhatsApp {media_type}]"

        if not display_text and not message_id:
            return  # Skip empty messages

        # Persist to database first
        db_id = self._persist_message({
            "message_id": message_id,
            "jid": jid,
            "jid_phone": data.get("jid_phone", jid.split("@")[0].split(":")[0] if jid else ""),
            "chat_type": chat_type,
            "sender_jid": sender.get("jid", ""),
            "sender_phone": sender.get("phone", ""),
            "sender_push_name": sender.get("push_name", ""),
            "text": text_content,
            "caption": caption,
            "media_type": media.get("type") if isinstance(media, dict) else None,
            "media_mime_type": media.get("mime_type") if isinstance(media, dict) else None,
            "media_filename": media.get("filename") if isinstance(media, dict) else None,
            "whatsapp_timestamp": wa_timestamp,
            "from_me": from_me,
            "raw_data": data,
        })

        if not display_text:
            return

        # Prefix with sender info
        sender_phone = sender.get("phone", "Unknown")
        sender_name = sender.get("push_name", "")
        if chat_type == "group":
            group_jid = jid
            group_name = group_jid.split("@")[0]
            prefix = f"[WhatsApp Group: {group_name}] {sender_name or sender_phone}: "
        else:
            prefix = f"[WhatsApp: {sender_name or sender_phone}] "

        full_text = prefix + display_text

        logger.info(f"WhatsApp: Message from {sender_phone} ({chat_type}): {display_text[:80]}... (db_id={db_id})")

        # Emit to the agent — is_external=True (suppresses TTS like Telegram)
        try:
            from distr.core.signals import signal_manager
            signal_manager.send_text_input.emit(full_text, True, None, None)
        except Exception as e:
            logger.error(f"WhatsApp: Failed to emit to agent: {e}")

        # Also emit raw signal for any direct listeners
        self.message_received.emit(data)

    def _handle_inbound_media(self, data: dict):
        """Handle an inbound WhatsApp media file (photo, audio, document)."""
        message_id = data.get("message_id", "")
        media_type = data.get("media_type", "unknown")
        mime_type = data.get("mime_type", "application/octet-stream")
        filename = data.get("filename", "")
        data_b64 = data.get("data_b64", "")
        caption = data.get("caption", "")
        jid = data.get("jid", "")
        sender_jid = data.get("sender_jid", "")
        file_length = data.get("file_length", 0)

        if not data_b64:
            logger.warning(f"WhatsApp: Media message with no data: {message_id}")
            return

        # Save media to disk
        local_path = None
        try:
            import base64
            from datetime import datetime

            media_bytes = base64.b64decode(data_b64)
            save_dir = Path.home() / "Downloads" / "DecisionsAI"
            save_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Determine extension from mime type
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
                "audio/opus": ".opus",
                "video/mp4": ".mp4", "video/3gpp": ".3gp",
                "application/pdf": ".pdf",
                "application/octet-stream": ".bin",
            }
            ext = ext_map.get(mime_type, ".bin")
            if filename and "." in filename:
                ext = ""
            dest = save_dir / f"whatsapp_{media_type}_{timestamp}{ext}"

            with open(dest, "wb") as f:
                f.write(media_bytes)

            local_path = str(dest)
            logger.info(f"WhatsApp: Saved {media_type} ({len(media_bytes)} bytes) → {dest}")

        except Exception as e:
            logger.error(f"WhatsApp: Failed to save media: {e}", exc_info=True)
            return

        # Update the existing message record with local path and file length
        self._update_media_info(message_id, local_path, file_length)

        # Build agent message
        sender_phone = sender_jid.split("@")[0].split(":")[0] if sender_jid else "Unknown"
        size_str = f"{file_length / 1024:.1f} KB" if file_length < 1024 * 1024 else f"{file_length / 1024 / 1024:.1f} MB"
        agent_text = f"[WhatsApp: {sender_phone}] Received {media_type}: {Path(local_path).name if local_path else filename} ({size_str})"
        if caption:
            agent_text += f" — {caption}"

        # Send to agent with image path for vision (photos)
        image_path = local_path if media_type == "photo" else None

        try:
            from distr.core.signals import signal_manager
            signal_manager.send_text_input.emit(agent_text, True, image_path, None)
        except Exception as e:
            logger.error(f"WhatsApp: Failed to emit media to agent: {e}")

    # ═════════════════════════════════════════════════════════════════════════
    # Database Persistence
    # ═════════════════════════════════════════════════════════════════════════

    def _persist_message(self, msg_data: dict) -> Optional[int]:
        """Store a WhatsApp message in the whatsapp_messages table.
        Returns the database row ID, or None on failure.
        """
        try:
            from distr.core.db import get_session, WhatsAppMessage
            with get_session() as session:
                # Deduplicate by message_id
                existing = session.query(WhatsAppMessage).filter_by(
                    message_id=msg_data.get("message_id", "")
                ).first()
                if existing:
                    return existing.id

                row = WhatsAppMessage(
                    message_id=msg_data.get("message_id", ""),
                    jid=msg_data.get("jid", ""),
                    jid_phone=msg_data.get("jid_phone", ""),
                    chat_type=msg_data.get("chat_type"),
                    sender_jid=msg_data.get("sender_jid", ""),
                    sender_phone=msg_data.get("sender_phone", ""),
                    sender_push_name=msg_data.get("sender_push_name", ""),
                    text=msg_data.get("text"),
                    caption=msg_data.get("caption"),
                    media_type=msg_data.get("media_type"),
                    media_mime_type=msg_data.get("media_mime_type"),
                    media_filename=msg_data.get("media_filename"),
                    whatsapp_timestamp=msg_data.get("whatsapp_timestamp"),
                    from_me=msg_data.get("from_me", False),
                    raw_data=json.dumps(msg_data.get("raw_data", {}), default=str),
                    processed=False,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                return row.id
        except Exception as e:
            logger.error(f"WhatsApp: Failed to persist message: {e}", exc_info=True)
            return None

    def _update_media_info(self, message_id: str, local_path: str, file_length: int):
        """Update an existing message record with media file info after download."""
        try:
            from distr.core.db import get_session, WhatsAppMessage
            with get_session() as session:
                row = session.query(WhatsAppMessage).filter_by(message_id=message_id).first()
                if row:
                    row.media_local_path = local_path
                    row.media_file_length = file_length
                    session.commit()
        except Exception as e:
            logger.error(f"WhatsApp: Failed to update media info: {e}")

    # ═════════════════════════════════════════════════════════════════════════
    # Settings Persistence
    # ═════════════════════════════════════════════════════════════════════════

    def _save_connection(self, status_data: dict):
        """Save WhatsApp connection info to connected_accounts in settings."""
        try:
            from distr.core.settings import load_settings_from_db, save_settings_to_db

            phone = status_data.get("phone", {})
            if not phone:
                return

            settings = load_settings_from_db()
            connected_accounts = settings.get("connected_accounts", [])
            if isinstance(connected_accounts, str):
                connected_accounts = json.loads(connected_accounts)

            # Find or create WhatsApp account
            wa_account = None
            for account in connected_accounts:
                if isinstance(account, dict) and account.get("provider") == "whatsapp":
                    wa_account = account
                    break

            if not wa_account:
                wa_account = {"provider": "whatsapp"}
                connected_accounts.append(wa_account)

            wa_account["jid"] = phone.get("jid", "")
            wa_account["name"] = phone.get("name", "")
            wa_account["push_name"] = phone.get("pushName", "")
            wa_account["connected_at"] = datetime.utcnow().isoformat()
            wa_account["status"] = "connected"

            settings["connected_accounts"] = connected_accounts
            save_settings_to_db(settings)
            logger.info("WhatsApp: Connection saved to settings")

        except Exception as e:
            logger.error(f"WhatsApp: Failed to save connection: {e}", exc_info=True)