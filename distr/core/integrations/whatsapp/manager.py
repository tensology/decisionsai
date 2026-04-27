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

import base64
import json
import logging
import os
import queue
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer, QUrl
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
    _open_socket_requested = pyqtSignal(str)

    @staticmethod
    def _extract_group_display_name(raw_data: dict, jid: str) -> str:
        """Resolve a human-readable group name from payload fields."""
        if not isinstance(raw_data, dict):
            raw_data = {}

        for key in ("group_name", "group_subject", "chat_name", "chat_subject", "subject", "name", "push_name"):
            value = raw_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        chat_obj = raw_data.get("chat")
        if isinstance(chat_obj, dict):
            for key in ("name", "subject", "title"):
                value = chat_obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return (jid or "").split("@")[0] or "Group Chat"


    def __init__(self, server_url: str = "wss://www.decisionsai.net/ws/whatsapp"):
        super().__init__()
        use_local_relay = str(os.environ.get("DECISIONSAI_USE_LOCAL_RELAY", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        # Allow env override
        env_url = os.environ.get("DECISIONSAI_WA_WS_URL")
        if env_url:
            self.server_url = env_url
        elif use_local_relay:
            self.server_url = "ws://localhost:8090/ws/whatsapp"
        else:
            self.server_url = server_url

        # REST API base URL (for QR code, status, etc.)
        self.api_base = os.environ.get(
            "DECISIONSAI_WA_API_BASE",
            "https://www.decisionsai.net/api/whatsapp"
            if not use_local_relay
            else "http://localhost:8090/api/whatsapp",
        )

        # QWebSocket
        if QWebSocket:
            self.socket = QWebSocket()
            self.socket.connected.connect(self._on_connected)
            self.socket.disconnected.connect(self._on_disconnected)
            self.socket.textMessageReceived.connect(self._on_message)
            self.socket.error.connect(self._on_error)
            try:
                self.socket.sslErrors.connect(self._on_ssl_errors)
            except AttributeError:
                pass
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
        self._device_identity_path = Path.home() / ".decisionsai" / "device_identity.json"
        self._ws_auth_bundle: Optional[Dict[str, Any]] = None
        self._connect_lock = threading.Lock()
        self._connect_worker_running = False
        self._open_socket_requested.connect(self._open_socket_on_main_thread)

    # ═════════════════════════════════════════════════════════════════════════
    # Connection Management
    # ═════════════════════════════════════════════════════════════════════════

    def _load_or_create_device_identity(self) -> dict:
        """Load device identity, creating a local Ed25519 keypair on first run."""
        try:
            if self._device_identity_path.exists():
                obj = json.loads(self._device_identity_path.read_text())
                if obj.get("device_id") and obj.get("private_key"):
                    return obj
        except Exception:
            pass
        priv = Ed25519PrivateKey.generate()
        priv_raw = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        ident = {
            "device_id": f"dev-{int(time.time())}-{secrets.token_hex(8)}",
            "private_key": base64.b64encode(priv_raw).decode(),
        }
        self._device_identity_path.parent.mkdir(parents=True, exist_ok=True)
        self._device_identity_path.write_text(json.dumps(ident))
        return ident

    def _collect_subscribe_phones(self, limit: int = 500) -> list[str]:
        """Collect known conversation phones for scoped subscriptions."""
        try:
            phones = []
            try:
                import requests
                chats_resp = requests.get(
                    f"{self.api_base}/chats",
                    params={"limit": limit, "offset": 0, "search": ""},
                    headers=self._relay_auth_headers(""),
                    timeout=10,
                )
                chats = (chats_resp.json() or {}).get("chats") or []
                for c in chats:
                    jid = str(c.get("jid") or "").strip()
                    phone = jid.split("@")[0].split(":")[0] if jid else ""
                    if phone and phone not in phones:
                        phones.append(phone)
            except Exception:
                pass

            data = self.get_stored_messages(limit=limit, offset=0, jid_phone="")
            for msg in (data.get("messages") or []):
                p = str(msg.get("jid_phone") or "").strip()
                if p and p not in phones:
                    phones.append(p)
            return phones[:limit]
        except Exception:
            return []

    def _relay_auth_headers(self, payload: str = "") -> dict:
        """Build auth headers for relay REST endpoints."""
        token = (os.environ.get("RELAY_INTERNAL_TOKEN", "") or "").strip()
        if token:
            return {"X-Relay-Internal-Token": token}
        ws_token = str((self._ws_auth_bundle or {}).get("ws_token") or "").strip()
        if ws_token:
            return {"Authorization": f"Bearer {ws_token}"}
        return {}

    def _request_ws_auth_bundle(self, subscribe_phones: Optional[list[str]] = None) -> Optional[dict]:
        """Get short-lived ws/subscription tokens (internal first, device fallback)."""
        try:
            import requests
            phones = subscribe_phones if subscribe_phones is not None else self._collect_subscribe_phones()
            payload = {"app_user_id": "local-ui", "subscribe_phones": phones}
            payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            headers = self._relay_auth_headers(payload_str)
            if headers:
                r = requests.post(f"{self.api_base}/ws-auth", json=payload, headers=headers, timeout=10)
                obj = r.json()
                if r.status_code == 200 and obj.get("success") and obj.get("ws_token"):
                    self._ws_auth_bundle = obj
                    return obj

            # No local secret path: prove possession of local device keypair.
            ident = self._load_or_create_device_identity()
            priv_raw = base64.b64decode(str(ident.get("private_key") or "").encode())
            priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
            pub_raw = priv.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            ch = requests.post(f"{self.api_base}/device/challenge", json={
                "device_id": ident.get("device_id"),
                "public_key": base64.b64encode(pub_raw).decode(),
            }, timeout=10)
            ch_obj = ch.json()
            if ch.status_code != 200 or not ch_obj.get("success"):
                logger.warning("WhatsApp: device challenge failed: %s", ch_obj.get("error") or ch.text[:200])
                return None
            msg = str(ch_obj.get("challenge_message") or "")
            sig = priv.sign(msg.encode())
            r2 = requests.post(f"{self.api_base}/device/ws-auth", json={
                "device_id": ident.get("device_id"),
                "challenge_id": ch_obj.get("challenge_id"),
                "signature": base64.b64encode(sig).decode(),
                "subscribe_phones": phones,
            }, timeout=10)
            obj2 = r2.json()
            if r2.status_code == 200 and obj2.get("success") and obj2.get("ws_token"):
                self._ws_auth_bundle = obj2
                return obj2
            logger.warning("WhatsApp: device ws-auth failed: %s", obj2.get("error") if isinstance(obj2, dict) else r2.text[:200])
            return None
        except Exception as e:
            logger.error("WhatsApp: ws-auth bundle request failed: %s", e)
            return None

    def connect(self):
        """Connect to the WhatsApp relay server."""
        if self.socket is None:
            logger.warning("WhatsApp: QWebSocket not available, cannot connect")
            return

        with self._connect_lock:
            if self._connect_worker_running:
                logger.debug("WhatsApp: connect already in progress, skipping duplicate request")
                return
            self._connect_worker_running = True

        t = threading.Thread(
            target=self._connect_worker,
            name="WhatsAppConnectWorker",
            daemon=True,
        )
        t.start()

    def _connect_worker(self):
        """Resolve auth bundle off the UI thread, then request socket open on UI thread."""
        try:
            bundle = self._request_ws_auth_bundle()
            ws_url = self.server_url
            if bundle and bundle.get("ws_token"):
                sep = "&" if "?" in ws_url else "?"
                ws_url = f"{ws_url}{sep}ws_token={bundle.get('ws_token')}"
            self._open_socket_requested.emit(ws_url)
        finally:
            with self._connect_lock:
                self._connect_worker_running = False

    @pyqtSlot(str)
    def _open_socket_on_main_thread(self, ws_url: str):
        """Open QWebSocket only from the QObject's owning thread."""
        if self.socket is None:
            return
        if self._active_disconnect:
            logger.debug("WhatsApp: skipping socket open while active disconnect is set")
            return
        if self.socket.isValid():
            logger.info("WhatsApp: Closing existing connection before reconnecting")
            self._active_disconnect = True
            self.socket.close()
            self._active_disconnect = False
        logger.info(f"WhatsApp: Connecting to {ws_url.split('?')[0]}")
        self.socket.open(QUrl(ws_url))

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

        # Initialize authenticated desktop subscription session.
        try:
            sub_tokens = [x.get("token") for x in ((self._ws_auth_bundle or {}).get("subscription_tokens") or []) if isinstance(x, dict) and x.get("token")]
            if self.socket and self.socket.isValid():
                self.socket.sendTextMessage(json.dumps({
                    "client_type": "desktop",
                    "subscribe_tokens": sub_tokens,
                }))
        except Exception as e:
            logger.debug(f"WhatsApp: initial subscribe send failed: {e}")

        # Request current status
        self._request_status()

    def _on_disconnected(self):
        """Called when WebSocket disconnects."""
        self._connected = False
        self.phone_info = None
        self.current_qr = None

        if not self._active_disconnect:
            logger.warning("WhatsApp: Disconnected unexpectedly, scheduling reconnect")
            # Only schedule if no reconnect already pending (e.g. from _on_error)
            if not self._reconnect_timer.isActive():
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

    def _on_ssl_errors(self, errors):
        """Handle Qt SSL verification errors during WhatsApp WebSocket handshake."""
        error_descriptions = [err.errorString() for err in errors]
        logger.warning(
            "WhatsApp: SSL errors during handshake (%d): %s — ignoring and proceeding",
            len(errors), "; ".join(error_descriptions),
        )
        self.socket.ignoreSslErrors()

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
            resp = requests.get(f"{self.api_base}/qr", headers=self._relay_auth_headers(""), timeout=5)
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
            resp = requests.get(f"{self.api_base}/status", headers=self._relay_auth_headers(""), timeout=5)
            return resp.json()
        except Exception as e:
            logger.error(f"WhatsApp: Status check failed: {e}")
            return {"status": "error", "error": str(e)}

    def disconnect_session(self):
        """Disconnect and clear the WhatsApp session (forces new QR on next connect)."""
        try:
            import requests
            resp = requests.post(f"{self.api_base}/disconnect", headers=self._relay_auth_headers(""), timeout=5)
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
            resp = requests.get(f"{self.api_base}/status", headers=self._relay_auth_headers(""), timeout=5)
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
            "media_duration": media.get("duration") if isinstance(media, dict) else None,
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
            group_name = self._extract_group_display_name(data, jid)
            prefix = f"[WhatsApp Group: {group_name}] {sender_name or sender_phone}: "
        else:
            prefix = f"[WhatsApp: {sender_name or sender_phone}] "

        full_text = prefix + display_text

        logger.info(f"WhatsApp: Message from {sender_phone} ({chat_type}): {display_text[:80]}... (db_id={db_id})")

        # For media messages, defer relay processed ack until media bytes are cached locally.
        # For text-only messages, ack immediately.
        if message_id and not media:
            self._mark_relay_processed_by_message_id(message_id)

        # Do NOT auto-route inbound WhatsApp messages into agent chat input.
        # Agent actions on WhatsApp must be explicit via tools/user intent.
        logger.debug("WhatsApp: inbound text stored only (no agent injection)")

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

        # Save media to disk (inside project DB_DIR so it's accessible by agent + API)
        local_path = None
        try:
            import base64
            from datetime import datetime
            from distr.core.paths import DB_DIR

            media_bytes = base64.b64decode(data_b64)
            save_dir = Path(DB_DIR) / "whatsapp_media"
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

            # Store absolute path in DB for backward compat with existing code,
            # but also compute relative path for the serving endpoint
            local_path = str(dest)
            logger.info(f"WhatsApp: Saved {media_type} ({len(media_bytes)} bytes) → {dest}")

            # ── Create M4A transcode for Safari/iOS audio playback ──────
            if media_type in ("voice", "audio", "ptt") and mime_type in ("audio/ogg", "audio/opus", "audio/ogg; codecs=opus"):
                try:
                    import shutil, subprocess
                    ffmpeg_path = shutil.which("ffmpeg")
                    if ffmpeg_path:
                        m4a_dest = save_dir / f"whatsapp_{media_type}_{timestamp}.m4a"
                        result = subprocess.run(
                            [ffmpeg_path, "-y", "-i", str(dest), "-c:a", "aac", "-b:a", "64k", "-vn", str(m4a_dest)],
                            capture_output=True, timeout=30
                        )
                        if result.returncode == 0 and m4a_dest.exists():
                            logger.info(f"WhatsApp: Created M4A transcode for Safari: {m4a_dest.name}")
                        else:
                            logger.debug(f"WhatsApp: M4A transcode skipped (ffmpeg rc={result.returncode})")
                except Exception as e:
                    logger.debug(f"WhatsApp: M4A transcode failed: {e}")

        except Exception as e:
            logger.error(f"WhatsApp: Failed to save media: {e}", exc_info=True)
            return

        # Update the existing message record with local path and file length
        self._update_media_info(message_id, local_path, file_length, duration=data.get("duration"))

        # -- Transcribe voice notes --------------------------------------------
        voice_transcription = None
        if media_type in ("voice", "audio", "ptt") and local_path:
            try:
                from distr.core.audio.voice_cloning import transcribe_audio_file
                voice_transcription = transcribe_audio_file(local_path)
                if voice_transcription:
                    logger.info(f"WhatsApp: Transcribed voice note ({media_type}): {voice_transcription[:100]}")
                    # Store transcription in the message caption field
                    self._update_message_caption(message_id, f"[Transcription] {voice_transcription}")
                else:
                    logger.info(f"WhatsApp: No transcription returned for {media_type}")
            except Exception as e:
                logger.warning(f"WhatsApp: Voice transcription failed: {e}")

        # Mark as processed on the relay server after local cache succeeds.
        if message_id:
            self._mark_relay_processed_by_message_id(
                message_id,
                purge_media=False,  # Don't purge relay media — web UI needs it
                client_media_local_path=local_path or "",
            )

        # Build agent message
        sender_phone = sender_jid.split("@")[0].split(":")[0] if sender_jid else "Unknown"
        size_str = f"{file_length / 1024:.1f} KB" if file_length < 1024 * 1024 else f"{file_length / 1024 / 1024:.1f} MB"
        agent_text = f"[WhatsApp: {sender_phone}] Received {media_type}: {Path(local_path).name if local_path else filename} ({size_str})"
        if voice_transcription:
            agent_text += f"\n[Voice transcription: {voice_transcription}]"
        if caption:
            agent_text += f" — {caption}"

        # Send to agent with image path for vision (photos)
        image_path = local_path if media_type == "photo" else None

        # Do NOT auto-route inbound WhatsApp media into agent chat input.
        # Agent actions on WhatsApp must be explicit via tools/user intent.
        logger.debug("WhatsApp: inbound media stored only (no agent injection)")

    # ═════════════════════════════════════════════════════════════════════════
    # Database Persistence
    # ═════════════════════════════════════════════════════════════════════════

    def _update_message_caption(self, message_id: str, caption: str):
        """Update the caption field of a stored WhatsApp message (with transcription)."""
        try:
            from distr.core.db import get_session, WhatsAppMessage
            with get_session() as session:
                msg = session.query(WhatsAppMessage).filter_by(message_id=message_id).first()
                if msg:
                    # Prepend transcription to existing caption or set as caption
                    if msg.caption:
                        msg.caption = caption + "\n\n" + msg.caption
                    else:
                        msg.caption = caption
                    session.commit()
                    logger.info(f"WhatsApp: Updated caption for message {message_id}")
        except Exception as e:
            logger.error(f"WhatsApp: Failed to update message caption: {e}")

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
                    media_duration=msg_data.get("media_duration"),
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

    def _update_media_info(self, message_id: str, local_path: str, file_length: int, duration: int = None):
        """Update an existing message record with media file info after download."""
        try:
            from distr.core.db import get_session, WhatsAppMessage
            with get_session() as session:
                row = session.query(WhatsAppMessage).filter_by(message_id=message_id).first()
                if row:
                    row.media_local_path = local_path
                    row.media_file_length = file_length
                    if duration is not None:
                        row.media_duration = duration
                    session.commit()
        except Exception as e:
            logger.error(f"WhatsApp: Failed to update media info: {e}")

    # =====================================================================
    # Chat & Contact Retrieval
    # =====================================================================

    def get_chats(self, limit=100, offset=0, search="") -> dict:
        """Fetch the WhatsApp chat list from the Baileys service (like WhatsApp Web left panel)."""
        try:
            import requests
            resp = requests.get(
                f"{self.api_base}/chats",
                params={"limit": limit, "offset": offset, "search": search},
                headers=self._relay_auth_headers(""),
                timeout=10,
            )
            return resp.json()
        except Exception as e:
            logger.error(f"WhatsApp: get_chats failed: {e}")
            return {"chats": [], "total": 0, "error": str(e)}

    def get_contacts(self, limit=500, search="") -> dict:
        """Fetch the WhatsApp contact list from the Baileys service."""
        try:
            import requests
            resp = requests.get(
                f"{self.api_base}/contacts",
                params={"limit": limit, "search": search},
                headers=self._relay_auth_headers(""),
                timeout=10,
            )
            return resp.json()
        except Exception as e:
            logger.error(f"WhatsApp: get_contacts failed: {e}")
            return {"contacts": [], "total": 0, "error": str(e)}

    def get_stored_messages(self, jid_phone: str = None, limit=100, offset=0, unprocessed_only=False) -> dict:
        """Fetch stored WhatsApp messages from the local database."""
        try:
            from distr.core.db import get_session, WhatsAppMessage
            from distr.core.db.kanban import KanbanTicket

            with get_session() as session:
                query = session.query(WhatsAppMessage)
                if jid_phone:
                    query = query.filter(WhatsAppMessage.jid_phone == jid_phone)
                if unprocessed_only:
                    query = query.filter(WhatsAppMessage.processed == False)
                query = query.order_by(WhatsAppMessage.whatsapp_timestamp.asc())
                total = query.count()
                rows = query.offset(offset).limit(limit).all()

                # Check which messages are already linked to tickets
                msg_ids = [r.id for r in rows]
                ticketed_ids = set()
                ticket_map = {}
                if msg_ids:
                    from distr.core.db.kanban import KanbanTicket
                    ticketed = session.query(KanbanTicket.whatsapp_message_id, KanbanTicket.id).filter(
                        KanbanTicket.whatsapp_message_id.in_(msg_ids)
                    ).all()
                    ticketed_ids = {t[0] for t in ticketed if t[0] is not None}
                    ticket_map = {t[0]: t[1] for t in ticketed if t[0] is not None and t[1] is not None}

                # Snapshot batches set snapshot_group on every selected message, but KanbanTicket only
                # stores whatsapp_message_id for one message. Map each snapshot_group -> ticket id so all
                # messages in the batch get has_ticket / ticket_id in the API.
                snapshot_group_to_ticket_id = {}
                groups_in_page = {(r.snapshot_group or "").strip() for r in rows if r.snapshot_group}
                groups_in_page.discard("")
                if groups_in_page:
                    sg_rows = (
                        session.query(WhatsAppMessage.snapshot_group, KanbanTicket.id)
                        .join(KanbanTicket, KanbanTicket.whatsapp_message_id == WhatsAppMessage.id)
                        .filter(WhatsAppMessage.snapshot_group.in_(groups_in_page))
                        .all()
                    )
                    for sg, tid in sg_rows:
                        if sg and tid is not None:
                            snapshot_group_to_ticket_id[sg] = tid

                messages = []
                for r in rows:
                    raw_data = {}
                    if r.raw_data:
                        try:
                            raw_data = json.loads(r.raw_data)
                        except Exception:
                            raw_data = {}
                    group_name = None
                    if (r.chat_type or "").lower() == "group" or (r.jid or "").endswith("@g.us"):
                        group_name = self._extract_group_display_name(raw_data, r.jid or "")

                    tid = ticket_map.get(r.id)
                    has_t = r.id in ticketed_ids
                    sg_key = (r.snapshot_group or "").strip()
                    if not has_t and sg_key and sg_key in snapshot_group_to_ticket_id:
                        has_t = True
                        if tid is None:
                            tid = snapshot_group_to_ticket_id[sg_key]
                    msg = {
                        "id": r.id,
                        "message_id": r.message_id,
                        "jid": r.jid,
                        "jid_phone": r.jid_phone,
                        "chat_type": r.chat_type,
                        "sender_phone": r.sender_phone,
                        "sender_push_name": r.sender_push_name,
                        "text": r.text,
                        "caption": r.caption,
                        "media_type": r.media_type,
                        "media_mime_type": r.media_mime_type,
                        "media_filename": r.media_filename,
                        "media_local_path": r.media_local_path,
                        "media_file_length": r.media_file_length,
                        "media_duration": r.media_duration,
                        "whatsapp_timestamp": r.whatsapp_timestamp,
                        "from_me": r.from_me,
                        "processed": r.processed,
                        "snapshot_group": r.snapshot_group,
                        "group_name": group_name,
                        "has_ticket": has_t,
                        "ticket_id": tid,
                        "processed_date": r.processed_date.isoformat() if r.processed_date else None,
                        "created_date": r.created_date.isoformat() if r.created_date else None,
                    }
                    messages.append(msg)
                return {"messages": messages, "total": total, "offset": offset, "limit": limit}
        except Exception as e:
            logger.error(f"WhatsApp: get_stored_messages failed: {e}", exc_info=True)
            return {"messages": [], "total": 0, "error": str(e)}

    # ═════════════════════════════════════════════════════════════════════════
    # ═════════════════════════════════════════════════════════════════════════
    # Relay Sync
    # ═════════════════════════════════════════════════════════╒══════════════

    def sync_from_relay(self, mark_processed: bool = True) -> dict:
        """Pull stored messages from the relay server and save to local DB.

        Fallback sync for messages that arrived while the desktop app was offline.
        After syncing, messages are marked processed on the relay so they
        won't appear in future syncs.
        """
        try:
            import requests
            unprocessed_only = "true" if mark_processed else "false"
            resp = requests.get(
                f"{self.api_base}/messages",
                params={"limit": 1000, "unprocessed_only": unprocessed_only},
                headers=self._relay_auth_headers(""),
                timeout=15,
            )
            data = resp.json()
            messages = data.get("messages", [])
            synced = 0

            from distr.core.db import get_session, WhatsAppMessage
            with get_session() as session:
                for msg in messages:
                    existing = session.query(WhatsAppMessage).filter_by(
                        message_id=msg.get("message_id", "")
                    ).first()
                    if existing:
                        # Keep synced rows enriched with relay metadata so UI can
                        # resolve group display names from local DB without
                        # requiring a live /chats lookup.
                        if not (existing.raw_data or "").strip():
                            try:
                                existing.raw_data = json.dumps(msg, default=str)
                            except Exception:
                                pass
                        if not (existing.sender_push_name or "").strip():
                            existing.sender_push_name = msg.get("sender_push_name", "") or existing.sender_push_name
                        if not (existing.chat_type or "").strip():
                            existing.chat_type = msg.get("chat_type", "private") or existing.chat_type
                        if not (existing.jid or "").strip():
                            existing.jid = msg.get("jid", "") or existing.jid
                        if not (existing.jid_phone or "").strip():
                            existing.jid_phone = msg.get("jid_phone", "") or existing.jid_phone
                        if mark_processed and not (msg.get("media_type") and not (existing.media_local_path or "").strip()):
                            self._mark_relay_processed(msg.get("id"))
                        continue

                    relay_media_path = msg.get("media_local_path")
                    local_media_path = None
                    if relay_media_path:
                        relay_media_path = str(relay_media_path)
                        # Relay stores paths like "whatsapp/<file>" that are not local desktop paths.
                        # Keep local path empty so UI will use /relay-media fallback and cache locally.
                        if Path(relay_media_path).is_absolute() and os.path.exists(relay_media_path):
                            local_media_path = relay_media_path

                    row = WhatsAppMessage(
                        message_id=msg.get("message_id", ""),
                        jid=msg.get("jid", ""),
                        jid_phone=msg.get("jid_phone", ""),
                        chat_type=msg.get("chat_type", "private"),
                        sender_jid=msg.get("sender_jid", ""),
                        sender_phone=msg.get("sender_phone", ""),
                        sender_push_name=msg.get("sender_push_name", ""),
                        text=msg.get("text"),
                        caption=msg.get("caption"),
                        media_type=msg.get("media_type"),
                        media_mime_type=msg.get("media_mime_type"),
                        media_filename=msg.get("media_filename"),
                        media_local_path=local_media_path,
                        media_file_length=msg.get("media_file_length"),
                        whatsapp_timestamp=msg.get("whatsapp_timestamp"),
                        from_me=msg.get("from_me", False),
                        raw_data=json.dumps(msg, default=str),
                        processed=True,
                    )
                    session.add(row)
                    synced += 1
                    # Only mark processed when media is definitely locally available.
                    if mark_processed and not (msg.get("media_type") and not (local_media_path or "").strip()):
                        self._mark_relay_processed(msg.get("id"))

                session.commit()

            logger.info(f"WhatsApp: Synced {synced} messages from relay ({len(messages)} total)")
            return {"synced": synced, "total": len(messages)}

        except Exception as e:
            logger.error(f"WhatsApp: sync_from_relay failed: {e}", exc_info=True)
            return {"synced": 0, "total": 0, "error": str(e)}

    def _mark_relay_processed(self, relay_id: int):
        """Mark a message as processed on the relay server by relay DB ID."""
        try:
            import requests
            resp = requests.post(
                f"{self.api_base}/messages/{relay_id}/processed",
                headers=self._relay_auth_headers(""),
                timeout=5,
            )
            return 200 <= int(resp.status_code) < 300
        except Exception as e:
            logger.debug(f"WhatsApp: Failed to mark relay message {relay_id} as processed: {e}")
            return False

    def _mark_relay_processed_by_message_id(
        self,
        wa_message_id: str,
        purge_media: bool = False,
        client_media_local_path: str = "",
    ):
        """Mark a message as processed on the relay server by WhatsApp message ID.
        Called when a message is received via WebSocket in real-time, so the
        relay knows it doesn't need to keep it for future sync pulls."""
        try:
            import requests
            resp = requests.post(
                f"{self.api_base}/messages/by-wa-id/{wa_message_id}/processed",
                params={
                    "purge_media": "true" if purge_media else "false",
                    "client_media_local_path": (client_media_local_path or "").strip(),
                },
                headers=self._relay_auth_headers(""),
                timeout=5,
            )
            return 200 <= int(resp.status_code) < 300
        except Exception as e:
            logger.debug(f"WhatsApp: Could not mark relay message {wa_message_id} as processed: {e}")
            return False

    # ══════════════════════════════════════════════════════════╒═══════════════\n    # Settings Persistence
    # ═══════════════════════════════════════════╒═════════════════╒═════════════\n

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