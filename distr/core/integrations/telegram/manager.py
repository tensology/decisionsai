import hashlib
import json
import logging
import os
import queue
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import platform
import subprocess

# Qt Imports
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer, QUrl, QThread

try:
    from PyQt6.QtWebSockets import QWebSocket
except ImportError:
    from PyQt6.QtWebSockets import QWebSocket

import requests

# PyAutoGUI - import at module level and disable FAILSAFE
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0
except ImportError:
    pyautogui = None

# Configure logging
logger = logging.getLogger(__name__)

# Re-export for backward compatibility
from distr.core.integrations.telegram.utils import hash_channel_id

from distr.core.integrations.telegram.messages import TelegramMessagesMixin
from distr.core.integrations.telegram.sender import TelegramSenderMixin
from distr.core.integrations.telegram.remote_control import TelegramRemoteControlMixin
from distr.core.integrations.base import IntegrationReconnectMixin

class TelegramWebSocketManager(
    TelegramMessagesMixin,
    TelegramSenderMixin,
    TelegramRemoteControlMixin,
    IntegrationReconnectMixin,
    QObject,
):
    """
    Manages the WebSocket connection to the Tensology Telegram endpoint using QWebSocket.
    Run on the main Qt event loop for stability and proper signal handling.
    """

    # Signals
    message_received = pyqtSignal(
        dict
    )  # Emitted when a message (including voice) is received
    connection_status_changed = pyqtSignal(
        bool, str
    )  # Emitted when connection status changes (connected/disconnected, status_text)

    # Remote Control Signals
    _send_ws_text_signal = pyqtSignal(
        str
    )  # Thread-safe signal to send text message via WebSocket
    _send_ws_binary_signal = pyqtSignal(
        bytes
    )  # Thread-safe signal to send binary data via WebSocket
    remote_control_command_received = pyqtSignal(
        dict
    )  # Emitted when a remote control command is received
    _request_screen_update_signal = (
        pyqtSignal()
    )  # Signal to request screen info update on main thread

    def __init__(self, server_url: str = "wss://www.decisionsai.net/ws/telegram"):
        super().__init__()
        use_local_relay = str(os.environ.get("DECISIONSAI_USE_LOCAL_RELAY", "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        # Allow env override: DECISIONSAI_WS_URL=ws://localhost:8090/ws/telegram
        # Local relay mode is controlled independently from DEBUG.
        env_url = os.environ.get("DECISIONSAI_WS_URL")
        if env_url:
            self.server_url = env_url
        elif use_local_relay:
            self.server_url = "ws://localhost:8090/ws/telegram"
        else:
            self.server_url = server_url

        # QWebSocket Setup
        self.socket = QWebSocket()
        # Default to direct WebSocket networking (no system proxy auto-detection).
        # On some macOS setups, PAC/proxy resolution causes intermittent
        # HostNotFound/Can't assign requested address errors for long-lived WS.
        try:
            from PyQt6.QtNetwork import QNetworkProxy, QNetworkProxyFactory

            use_system_proxy = str(os.environ.get("TELEGRAM_USE_SYSTEM_PROXY", "")).strip().lower() in ("1", "true", "yes", "on")
            if not use_system_proxy:
                QNetworkProxyFactory.setUseSystemConfiguration(False)
                self.socket.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
                logger.info("[Telegram] Using direct WebSocket networking (system proxy disabled)")
            else:
                logger.info("[Telegram] TELEGRAM_USE_SYSTEM_PROXY enabled; using system proxy settings")
        except Exception as e:
            logger.debug("[Telegram] Could not configure explicit proxy mode (non-critical): %s", e)
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.textMessageReceived.connect(self._on_message)
        self.socket.binaryMessageReceived.connect(self._on_binary_message)
        self.socket.error.connect(self._on_error)

        # SSL error handling — Qt on macOS sometimes rejects valid certs from
        # new intermediates (e.g. Let's Encrypt E8) that aren't yet in the
        # system trust store.  Log the errors but allow the connection so
        # the WebSocket handshake can proceed.
        try:
            self.socket.sslErrors.connect(self._on_ssl_errors)
        except AttributeError:
            pass  # PyQt5 compat — signal name may differ

        # Connect internal thread-safe send signal
        self._send_ws_text_signal.connect(self.socket.sendTextMessage)
        self._send_ws_binary_signal.connect(self._send_binary_from_signal)

        # Connect screen update signal
        self._request_screen_update_signal.connect(self._refresh_qt_screens)

        # Identity / State
        self.short_code: Optional[str] = None
        self.app_user_id: Optional[str] = None
        self.telegram_user_id: Optional[int] = None
        self.chat_id: Optional[int] = None
        self._chat_id_confirmed = False
        self.current_chat_type: Optional[str] = (
            None  # Track if current chat is 'private', 'group', 'supergroup', or 'channel'
        )

        # Logging
        self._detailed_log_file = None
        self._setup_detailed_logging()

        # Reconnection
        self._reconnect_timer = QTimer()
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._do_reconnect)
        self._reconnect_delay_ms = 3000  # initial 3 seconds
        self._reconnect_delay_max_ms = 60000  # cap at 60 seconds
        self._reconnect_delay_current_ms = 3000  # current delay (grows with backoff)
        self._reconnect_attempts = 0
        self._active_disconnect = False  # True if we intentionally disconnected
        self._init_reconnect_state(initial_delay_ms=3000, max_delay_ms=60000)

        # Rate Limiting & Dedup
        self._last_send_time = 0
        self._min_send_interval = 0.5
        self._recent_messages = {}
        self._dedup_window = 10.0
        # Caches for inbound dedup
        self._processed_message_ids = set()
        self._processed_message_hashes = set()
        self._max_processed_cache_size = 100

        # Health Check
        self._last_message_time = time.time()
        self._health_check_timer = QTimer()
        self._health_check_timer.timeout.connect(self._check_health)
        self._health_check_timer.start(3 * 60 * 1000)  # Every 3 minutes

        # Message Queue (for offline/burst handling)
        self._message_queue = queue.Queue()
        self._send_timer = QTimer()
        self._send_timer.timeout.connect(self._process_message_queue)
        self._send_timer.start(100)  # Check queue every 100ms

        # Group Message Storage (for messages from groups/channels - stored for later processing)
        self._group_message_queue = (
            queue.Queue()
        )  # Queue of group messages waiting to be processed
        self._group_messages_storage = []  # List of stored group messages (for retrieval)
        self._max_stored_group_messages = (
            1000  # Maximum number of stored group messages
        )

        # State tracking
        self._remote_control_lock = threading.Lock()
        self._online_message_sent = False
        self._last_connection_status = (
            None  # Track connection status for polling detection
        )
        self._is_auto_reconnecting = False  # Track if we're in an auto-reconnect cycle

        # ── Telegram message batching ──
        # Buffer incoming private-chat messages for a short window so the agent
        # receives a single combined message instead of responding to each one
        # individually.  After BATCH_DELAY_MS of silence the buffer is flushed.
        self._telegram_batch_buffer: list = []  # list of (text, is_media, image_path, input_type)
        self._current_input_type: str = "text"  # last flushed input type ("text" or "voice")
        self._telegram_batch_thread_id: int | None = None  # Telegram chat id for R15 bus mapping

        # Sleep prevention — keep the machine awake while Telegram is connected
        self._sleep_inhibit_proc = None  # subprocess handle (macOS caffeinate) or ctypes ref (Windows)
        self._telegram_batch_timer = QTimer()
        self._telegram_batch_timer.setSingleShot(True)
        self._telegram_batch_timer.timeout.connect(self._flush_telegram_batch)
        self._TELEGRAM_BATCH_DELAY_MS = 3000  # 3 seconds

        # ── Persistent typing indicator timer ──
        self._typing_timer: Optional[threading.Timer] = None
        self._typing_timer_lock = threading.Lock()
        self._typing_action: str = "typing"

    def _get_agent_name(self) -> str:
        """Get the agent display name from the current chat's voice setting, falling back to global."""
        try:
            from distr.core.settings import load_settings_from_db
            from distr.core.agent.constants import normalize_voice_provider
            settings = load_settings_from_db()

            # Try current chat's voice first
            provider = None
            voice_id = None
            chat_id = settings.get("agent_current_chat_id") or settings.get("last_chat_id")
            if chat_id:
                try:
                    from distr.core.db import get_session, Chat
                    with get_session() as session:
                        chat = session.get(Chat, int(chat_id))
                        if chat and chat.voice_provider:
                            provider = normalize_voice_provider(chat.voice_provider)
                            voice_id = (chat.voice_model or "").strip()
                except Exception:
                    pass

            # Fall back to global settings
            if not provider:
                provider = normalize_voice_provider(settings.get("voice_provider", "kokoro"))
            if not voice_id:
                from distr.core.agent.services.tts.registry import tts_registry
                voice_keys = {d.id: d.settings_key for d in tts_registry.all_providers()}
                voice_id = settings.get(voice_keys.get(provider, "kokoro_voice"), "")

            # Resolve display name
            from distr.core.agent.service_factory import resolve_voice_to_display_name
            return resolve_voice_to_display_name(provider, voice_id, settings)
        except Exception as e:
            logger.debug(f"Could not resolve agent name from voice: {e}")
            return "Agent"

    def _setup_detailed_logging(self):
        """Setup logging to ~/.decisions/logs/telegram_websocket_detailed.log"""
        try:
            log_dir = Path.home() / ".decisions" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._detailed_log_file = log_dir / "telegram_websocket_detailed.log"
            # Ensure file exists
            if not self._detailed_log_file.exists():
                with open(self._detailed_log_file, "w") as f:
                    f.write(
                        f"=== Telegram WebSocket Log Created at {datetime.now()} ===\n"
                    )
        except Exception as e:
            logger.error(f"Failed to setup detailed logging: {e}")

    def _log_detailed(self, message: str):
        """Write to detailed log file if initialized."""
        if self._detailed_log_file:
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
                with open(self._detailed_log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}] {message}\n")
            except Exception:
                pass

    # =========================================================================
    # Connection Management
    # =========================================================================

    def connect(
        self,
        short_code: Optional[str] = None,
        app_user_id: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
    ):
        """
        Connect to the Telegram WebSocket endpoint.
        """
        # Close existing if open
        if self.socket.isValid():
            logger.info("Closing existing connection before reconnecting")
            self._active_disconnect = True  # Mark as intentional so we don't auto-reconnect immediately during the close
            self.socket.close()
            self._active_disconnect = False

        # State updates
        if app_user_id:
            self.app_user_id = app_user_id

        # CRITICAL: Load telegram_user_id from settings FIRST if not provided
        if not telegram_user_id and not self.telegram_user_id:
            logger.info(
                f"[Telegram] 🔍 No telegram_user_id provided, loading from settings in connect()..."
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
                                    telegram_user_id = user_id_int
                                    logger.info(
                                        f"[Telegram] ✅ Loaded telegram_user_id from settings in connect(): {telegram_user_id}"
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
                    f"[Telegram] ❌ Could not load telegram_user_id from settings in connect(): {e}",
                    exc_info=True,
                )

        if telegram_user_id:
            # Validate integer and ensure it's positive (private chat, not group/channel)
            try:
                telegram_user_id_int = None
                if isinstance(telegram_user_id, int):
                    telegram_user_id_int = telegram_user_id
                elif isinstance(
                    telegram_user_id, str
                ) and not telegram_user_id.startswith("session_"):
                    telegram_user_id_int = int(telegram_user_id)

                # CRITICAL: Only accept positive telegram_user_id (private chats)
                # Negative IDs are groups/channels - we should never use them as telegram_user_id
                if telegram_user_id_int is not None:
                    if telegram_user_id_int > 0:
                        self.telegram_user_id = telegram_user_id_int
                        logger.info(
                            f"[Telegram] ✅ Set telegram_user_id in connect(): {self.telegram_user_id}"
                        )
                    else:
                        logger.warning(
                            f"[Telegram] ❌ REJECTED negative telegram_user_id (group/channel): {telegram_user_id_int}. Only positive IDs (private chats) are allowed."
                        )
                else:
                    logger.warning(
                        f"Invalid telegram_user_id format: {telegram_user_id}"
                    )
            except (ValueError, TypeError):
                logger.warning(f"Invalid telegram_user_id ignored: {telegram_user_id}")

        if short_code:
            self.short_code = short_code

        # Validate credentials
        if not self.app_user_id and not self.telegram_user_id:
            logger.error(
                "Cannot connect: at least one of app_user_id or telegram_user_id required."
            )
            return

        # Prepare URL
        url_str = self.server_url
        params = []
        if self.telegram_user_id:
            params.append(f"telegram_user_id={self.telegram_user_id}")
        elif self.app_user_id:
            params.append(f"app_user_id={self.app_user_id}")

        if params:
            url_str = f"{url_str}?{'&'.join(params)}"

        logger.info(f"Connecting to Telegram WebSocket: {url_str}")
        self._log_detailed(f"CONNECTING: {url_str}")

        self._active_disconnect = False

        # Pre-configure SSL to skip peer verification during the wss:// handshake.
        #
        # Qt6 QWebSocket on macOS may reject valid Let's Encrypt E8 intermediates
        # that the system trust store hasn't learned yet.  The sslErrors signal
        # arrives too late — the handshake is already aborted.  Disabling peer
        # verify lets TLS complete; auth is handled at websocket/session layer.
        try:
            from PyQt6.QtNetwork import QSslConfiguration

            # Get current default config, set VerifyNone, apply to this socket
            ssl_config = QSslConfiguration.defaultConfiguration()
            # PyQt6 exposes PeerVerifyMode under QSslSocket (not QSsl)
            try:
                from PyQt6.QtNetwork import QSslSocket
                ssl_config.setPeerVerifyMode(QSslSocket.PeerVerifyMode.VerifyNone)
            except (ImportError, AttributeError):
                try:
                    from PyQt6.QtNetwork import QSsl
                    ssl_config.setPeerVerifyMode(QSsl.PeerVerifyMode.VerifyNone)
                except (ImportError, AttributeError):
                    # Fallback: use the integer value directly
                    ssl_config.setPeerVerifyMode(0)  # VerifyNone = 0
            self.socket.setSslConfiguration(ssl_config)
            logger.debug("[Telegram] SSL configured: peer verify disabled for wss:// handshake")
        except Exception as e:
            logger.debug("[Telegram] Could not pre-configure SSL (non-critical): %s", e)

        self.socket.open(QUrl(url_str))

    def disconnect(self, check_staleness: bool = False):
        """
        Disconnect from the WebSocket intentionally.

        Args:
            check_staleness: If True, only disconnect if connection is stale (no messages for > 60s).
                             Also suppresses the "shut down" message if disconnecting to refresh.
        """
        if getattr(self, "_is_disconnecting", False):
            return

        if check_staleness and self.is_connected():
            # Check if connection is active (messages within last 60 seconds)
            time_since = time.time() - self._last_message_time
            if time_since < 60:
                logger.debug(
                    f"Connection active (last msg {time_since:.1f}s ago), skipping disconnect/refresh"
                )
                return
            else:
                logger.info(
                    f"Connection stale ({time_since:.1f}s), refreshing silently..."
                )

        self._is_disconnecting = True

        logger.info("Disconnecting Telegram WebSocket...")

        # Stop reconnect timer first to prevent auto-reconnect
        if hasattr(self, "_reconnect_timer"):
            self._reconnect_timer.stop()

        # Send shutdown message BEFORE closing the socket
        # CRITICAL: Only send shutdown message if NOT checking staleness (manual app exit)
        if not check_staleness and self.is_connected():
            try:
                logger.info("Sending shutdown message to Telegram...")
                # Bypass rate limiting and deduplication for shutdown message
                # Build message directly to ensure it's sent
                msg = {"type": "send_message", "text": f"{self._get_agent_name()} says goodbye! DecisionsAI has shut down."}

                # Get valid chat id
                effective_chat_id = self.chat_id
                if not effective_chat_id and self.telegram_user_id:
                    effective_chat_id = self.telegram_user_id

                if effective_chat_id:
                    msg["chat_id"] = effective_chat_id
                elif self.app_user_id:
                    msg["app_user_id"] = self.app_user_id
                else:
                    logger.warning(
                        "No destination (chat_id/user_id) for shutdown message"
                    )

                # Send message directly via WebSocket
                if self.socket and self.socket.isValid():
                    self._send_websocket_message(msg)
                    logger.info("Shutdown message sent to Telegram")

                    # Process events and wait a bit to ensure message is sent
                    from PyQt6.QtCore import QCoreApplication

                    QCoreApplication.processEvents()
                    time.sleep(0.3)  # Give time for message to be sent
                    self.socket.flush()
                    QCoreApplication.processEvents()
            except Exception as e:
                logger.error(f"Error sending shutdown message: {e}", exc_info=True)

        self._active_disconnect = True
        if self.socket:
            self.socket.close()

        # CRITICAL: Only reset _online_message_sent on manual disconnect (app shutdown)
        # Do NOT reset it on auto-disconnects or staleness checks - this prevents spam on reconnects
        # The flag will persist across auto-reconnects, preventing "come back online" spam
        if not check_staleness:
            self._online_message_sent = False  # Reset only on manual disconnect
            self._log_detailed("DISCONNECT called manually")
        else:
            self._log_detailed("DISCONNECT called (refresh/stale)")

        self._is_disconnecting = False

    def is_connected(self) -> bool:
        return self.socket.isValid()

    def _do_reconnect(self):
        """Called by timer to retry connection with exponential backoff."""
        if not self._active_disconnect:
            self._reconnect_attempts += 1
            logger.info(
                "[Telegram] 🔄 Auto-reconnect attempt #%d (delay was %dms)...",
                self._reconnect_attempts, self._reconnect_delay_current_ms,
            )
            self._is_auto_reconnecting = True  # Mark as auto-reconnect
            self.connect(self.short_code, self.app_user_id, self.telegram_user_id)

    def _check_health(self):
        """Periodically check connection health and force reconnect if truly dead."""
        if not self.is_connected():
            # If we're not connected and not actively disconnecting, trigger reconnect
            if not self._active_disconnect and not self._reconnect_timer.isActive():
                logger.warning("[Telegram] 🔄 Health check: not connected, triggering reconnect")
                self._reconnect_delay_current_ms = self._reconnect_delay_ms  # reset backoff
                self._reconnect_timer.start(self._reconnect_delay_current_ms)
            return

        time_since = time.time() - self._last_message_time
        if time_since > 600:  # 10 minutes silence
            logger.info(
                "[Telegram] Health check: connection idle for %.0fs — sending ping to verify",
                time_since,
            )
            # Send a ping to verify the connection is still alive
            try:
                self._send_websocket_message({"type": "ping"})
            except Exception as e:
                logger.warning("[Telegram] Health check ping failed: %s — forcing reconnect", e)
                self._active_disconnect = False
                self.socket.close()

    # =========================================================================
    # WebSocket Slots
    # =========================================================================

    def _on_connected(self):
        """Called when valid connection is established."""
        is_auto_reconnect = self._is_auto_reconnecting

        # CRITICAL: Load telegram_user_id from settings if not already set
        # This ensures we can send messages immediately after connection, even before receiving any messages
        if not self.telegram_user_id:
            logger.info(
                f"[Telegram] 🔍 _on_connected: telegram_user_id is None, loading from database..."
            )
            try:
                from distr.core.settings import load_settings_from_db

                settings = load_settings_from_db()
                connected_accounts = settings.get("connected_accounts", [])
                if isinstance(connected_accounts, str):
                    connected_accounts = json.loads(connected_accounts)

                logger.info(
                    f"[Telegram] 🔍 Looking for telegram_user_id in settings... (found {len(connected_accounts)} accounts)"
                )

                for account in connected_accounts:
                    if (
                        isinstance(account, dict)
                        and account.get("provider") == "telegram"
                    ):
                        user_id = account.get("user_id")
                        logger.info(
                            f"[Telegram] 🔍 Found Telegram account, user_id: {user_id} (type: {type(user_id)})"
                        )
                        if user_id:
                            try:
                                # Only use positive IDs (private chats)
                                user_id_int = (
                                    int(user_id)
                                    if isinstance(user_id, str)
                                    else user_id
                                )
                                if user_id_int > 0:
                                    self.telegram_user_id = user_id_int
                                    logger.info(
                                        f"[Telegram] ✅ Loaded telegram_user_id from settings: {self.telegram_user_id}"
                                    )
                                else:
                                    logger.warning(
                                        f"[Telegram] ⚠️ Stored user_id is negative (group): {user_id_int}, ignoring"
                                    )
                            except (ValueError, TypeError) as e:
                                logger.warning(
                                    f"[Telegram] ⚠️ Could not parse user_id: {user_id} ({e})"
                                )
                        else:
                            logger.warning(
                                f"[Telegram] ⚠️ No user_id found in Telegram account settings"
                            )
                        break
                else:
                    logger.warning(
                        f"[Telegram] ⚠️ No Telegram account found in connected_accounts"
                    )
            except Exception as e:
                logger.warning(
                    f"Could not load telegram_user_id from settings: {e}", exc_info=True
                )

        if is_auto_reconnect:
            logger.info(
                "✅ Telegram WebSocket reconnected (auto-reconnect, attempt #%d)",
                self._reconnect_attempts,
            )
        else:
            logger.info("✅ Telegram WebSocket connected")

        self.connection_status_changed.emit(True, "Connected")

        self._reset_reconnect_state("Telegram")
        self._is_auto_reconnecting = False  # Reset flag after connection

        # Send Subscribe Message
        msg = {"type": "subscribe"}
        if self.telegram_user_id:
            msg["telegram_user_id"] = self.telegram_user_id
        if self.app_user_id:
            msg["app_user_id"] = self.app_user_id

        self._send_websocket_message(msg)
        # Update last message time when sending subscribe
        self._last_message_time = time.time()

        # Reset connection status tracking
        self._last_connection_status = None

        # Only send 'Online' message on initial connection, NOT on auto-reconnects
        # CRITICAL: Never send "come back online" on auto-reconnects - this prevents spam
        if is_auto_reconnect:
            logger.debug(
                "[Telegram] ⏭️ Skipping 'come back online' message (auto-reconnect)"
            )
            # Don't reset _online_message_sent on auto-reconnect - keep it True to prevent sending again
        elif not self._online_message_sent:
            # We delay slightly or just send it. Let's send via queue to rate limit cleanly.
            logger.info(
                "[Telegram] 📤 Sending 'come back online' message (initial connection only)"
            )
            self.send_to_telegram(f"{self._get_agent_name()} is back online and ready to help!", None, None)
            self._online_message_sent = True
        else:
            logger.debug(
                "[Telegram] ⏭️ Skipping 'come back online' message (already sent)"
            )

        # Process any queued messages that accumulated during disconnection
        QTimer.singleShot(500, self._process_message_queue)

        # Prevent system sleep while Telegram is connected
        self._inhibit_sleep()

    def _on_disconnected(self):
        """Called when socket disconnects."""
        # Release sleep prevention
        self._release_sleep()

        if not self._active_disconnect:
            logger.warning(
                "[Telegram] ⚠️ WebSocket disconnected unexpectedly (will auto-reconnect, attempt #%d)",
                self._reconnect_attempts + 1,
            )
        else:
            logger.info("[Telegram] WebSocket disconnected (intentional)")

        self.connection_status_changed.emit(False, "Disconnected")

        # Reset connection status tracking
        self._last_connection_status = None

        if not self._active_disconnect:
            self._schedule_reconnect("Telegram")

    def _on_error(self, error_code):
        """Handle socket errors."""
        err_str = self.socket.errorString()
        logger.error(
            "[Telegram] ❌ WebSocket Error: %s (Code: %s, connected: %s, attempts: %d)",
            err_str, error_code, self.is_connected(), self._reconnect_attempts,
        )
        self._log_detailed(f"ERROR: {err_str} (code={error_code})")

        # If host resolution for the www subdomain is flaky, fall back to apex.
        # Both hosts terminate on the same endpoint; this avoids repeated
        # reconnect loops when local DNS intermittently fails on one name.
        try:
            lowered = (err_str or "").lower()
            host_resolution_err = ("host not found" in lowered) or ("can't assign requested address" in lowered)
            if host_resolution_err and "www.decisionsai.net" in self.server_url:
                fallback = self.server_url.replace("www.decisionsai.net", "decisionsai.net")
                if fallback != self.server_url:
                    logger.warning("[Telegram] DNS fallback: switching WS host to %s", fallback)
                    self.server_url = fallback
        except Exception:
            pass

        # Trigger immediate reconnect for connection-level errors (TLS, remote closed, etc.)
        # Only schedule if no reconnect is already pending — don't reset backoff on repeated errors.
        if not self._active_disconnect:
            # On the *first* error of a disconnect cycle, schedule a fast retry.
            # Subsequent errors (same cycle) should be ignored to avoid resetting backoff.
            if not self._reconnect_timer.isActive():
                self._reconnect_delay_current_ms = self._reconnect_delay_ms  # reset to base for fast first retry
                logger.info("[Telegram] 🔄 Triggering immediate reconnect after socket error")
                self._reconnect_timer.start(min(self._reconnect_delay_ms, 1000))  # 1s initial retry

    def _on_ssl_errors(self, errors):
        """Handle Qt SSL verification errors during WebSocket handshake."""
        error_descriptions = []
        for err in errors:
            error_descriptions.append(err.errorString())
        logger.warning(
            "[Telegram] ⚠️ SSL errors during handshake (%d): %s — ignoring and proceeding",
            len(errors), "; ".join(error_descriptions),
        )
        # Allow the connection despite SSL errors — our server uses a valid
        # Let's Encrypt cert, but Qt's SSL backend on macOS may not yet trust
        # new intermediates (E8).  Ignoring is safe here because we verify
        # the host and rely on websocket/session auth.
        self.socket.ignoreSslErrors()

    # ── Sleep prevention ──

    def _inhibit_sleep(self):
        """Prevent the system from sleeping while Telegram is connected."""
        if self._sleep_inhibit_proc is not None:
            return  # already inhibited

        try:
            if platform.system() == "Darwin":
                # macOS: caffeinate -di (prevent display sleep + idle sleep)
                self._sleep_inhibit_proc = subprocess.Popen(
                    ["caffeinate", "-di"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info("[Telegram] ☕ Sleep prevention enabled (caffeinate pid=%d)", self._sleep_inhibit_proc.pid)
            elif platform.system() == "Windows":
                import ctypes
                # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                ES_DISPLAY_REQUIRED = 0x00000002
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                )
                self._sleep_inhibit_proc = True  # sentinel
                logger.info("[Telegram] ☕ Sleep prevention enabled (SetThreadExecutionState)")
        except Exception as e:
            logger.warning("[Telegram] Could not inhibit sleep: %s", e)

    def _release_sleep(self):
        """Allow the system to sleep again."""
        if self._sleep_inhibit_proc is None:
            return

        try:
            if platform.system() == "Darwin":
                if hasattr(self._sleep_inhibit_proc, 'terminate'):
                    self._sleep_inhibit_proc.terminate()
                    self._sleep_inhibit_proc.wait(timeout=5)
                    logger.info("[Telegram] 😴 Sleep prevention released (caffeinate terminated)")
            elif platform.system() == "Windows":
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                logger.info("[Telegram] 😴 Sleep prevention released (SetThreadExecutionState reset)")
        except Exception as e:
            logger.warning("[Telegram] Could not release sleep inhibit: %s", e)
        finally:
            self._sleep_inhibit_proc = None

    def _on_binary_message(self, data):
        """Handle incoming binary WebSocket message (file upload chunks from server)."""
        import os
        try:
            # Convert QByteArray to bytes
            if hasattr(data, 'data'):
                raw = bytes(data.data())
            else:
                raw = bytes(data)

            # Check for UPLD prefix — file upload chunks from server
            if raw[:4] == b"UPLD":
                rest = raw[4:]
                null_idx = rest.find(b"\x00")
                if null_idx < 0:
                    return
                req_id = rest[:null_idx].decode("utf-8", errors="replace")
                chunk = rest[null_idx + 1:]

                if not hasattr(self, '_pending_uploads'):
                    self._pending_uploads = {}

                upload = self._pending_uploads.get(req_id)
                if not upload:
                    logger.warning(f"Binary UPLD for unknown request_id: {req_id}")
                    return

                if len(chunk) == 0:
                    # Empty chunk = completion signal
                    try:
                        upload["file"].close()
                    except Exception:
                        pass
                    final_size = upload["received"]
                    dest_path = upload["path"]
                    del self._pending_uploads[req_id]
                    logger.info(f"File upload complete: {dest_path} ({final_size} bytes)")
                    self._send_websocket_message({
                        "type": "remote_control_response",
                        "command": "file_upload_complete",
                        "request_id": req_id,
                        "data": {"status": "complete", "path": dest_path, "size": final_size},
                    })
                else:
                    # Write chunk to file
                    upload["file"].write(chunk)
                    upload["received"] += len(chunk)
            else:
                logger.debug(f"Unknown binary frame prefix: {raw[:4]}")
        except Exception as e:
            logger.error(f"Binary message handling error: {e}", exc_info=True)

    def _on_message(self, message: str):
        """Handle incoming text message."""
        self._last_message_time = time.time()

        try:
            data = json.loads(message)
            msg_type = data.get("type")

            # Log ALL incoming messages for debugging
            # logger.info(f"[Telegram WebSocket] 📥 Received message type: {msg_type}") # Reduced noise

            if msg_type == "message_sent":
                # Log success receipt from server - interesting for debugging uploads
                logger.info(
                    f"[Telegram WebSocket] 📥 Server confirmed message sent: {data}"
                )
            elif msg_type == "error":
                logger.error(f"[Telegram WebSocket] ❌ Server returned ERROR: {data}")
            else:
                pass  # Too noisy to print every type

            # Handle connection polling messages silently (ping/pong, connection status)
            if msg_type == "ping":
                # Respond to ping silently - this is normal polling from remote app
                self._send_websocket_message({"type": "pong"})
                return

            if msg_type == "telegram_message":
                logger.info(f"[Telegram WebSocket] 📨 Processing telegram_message")
                self._handle_telegram_message(data)
            elif msg_type == "connection":
                # Connection status update from server (polling/confirmation)
                status = data.get("status")
                # Only log if status actually changed or it's a new connection
                if status == "connected" and not hasattr(
                    self, "_last_connection_status"
                ):
                    logger.info(f"Connection confirmed by server: {status}")
                elif status != getattr(self, "_last_connection_status", None):
                    logger.debug(f"Connection status update: {status}")
                self._last_connection_status = status

                # Update chat_id if provided
                if data.get("telegram_chat_id"):
                    new_chat_id = int(data.get("telegram_chat_id"))
                    if new_chat_id != self.chat_id:
                        logger.info(f"Chat ID updated: {self.chat_id} → {new_chat_id}")
                        self.chat_id = new_chat_id
                        self._chat_id_confirmed = True
                        self._update_stored_connection_data(chat_id=self.chat_id)
            elif msg_type == "chat_id_update":
                chat_id = data.get("chat_id")
                if chat_id:
                    new_chat_id = int(chat_id)
                    if new_chat_id != self.chat_id:
                        logger.info(
                            f"Chat ID updated from server: {self.chat_id} → {new_chat_id}"
                        )
                        self.chat_id = new_chat_id
                        self._chat_id_confirmed = True
                        self._update_stored_connection_data(chat_id=self.chat_id)

            elif msg_type == "remote_control":
                # Handle in background thread to avoid freezing UI during screenshots/uploads
                threading.Thread(
                    target=self._handle_remote_control_command,
                    args=(data,),
                    daemon=True,
                ).start()

        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from WebSocket")

    # =========================================================================
    # Message Logic (Deduplication / Handling)
    # =========================================================================

    def _start_typing_loop(self, action: str = "typing"):
        """Start a recurring typing indicator that fires every 4 s until stopped."""
        with self._typing_timer_lock:
            self._typing_action = action
            self._cancel_typing_loop_unlocked()
            self._schedule_next_typing()
        # Send initial indicator outside the lock to avoid potential deadlock
        # with Qt signal dispatch
        self._send_websocket_message({"type": "typing_indicator", "action": action})

    def _schedule_next_typing(self):
        """Schedule the next typing ping in 4 seconds (Telegram expires after 5)."""
        t = threading.Timer(4.0, self._typing_tick)
        t.daemon = True
        t.start()
        self._typing_timer = t

    def _typing_tick(self):
        with self._typing_timer_lock:
            if self._typing_timer is None:
                return  # was cancelled
            action = self._typing_action
            self._schedule_next_typing()
        # Send outside the lock to avoid potential deadlock with Qt signal dispatch
        self._send_websocket_message({"type": "typing_indicator", "action": action})

    def _stop_typing_loop(self):
        """Cancel the recurring typing indicator."""
        with self._typing_timer_lock:
            self._cancel_typing_loop_unlocked()

    def _cancel_typing_loop_unlocked(self):
        if self._typing_timer is not None:
            self._typing_timer.cancel()
            self._typing_timer = None

    def _send_typing_indicator(self, action: str = "typing"):
        """Send typing indicator to Telegram so user sees '...' dots. Call periodically during long operations."""
        self._send_websocket_message({"type": "typing_indicator", "action": action})

    def _mark_message_as_read(self, message_id: int):
        """Tell the server to mark a Telegram message as read (send read receipt).

        The server translates this into a Telegram ``readHistory`` / ``readMessageContents``
        API call so the sender sees the double-check (read) indicator.
        """
        if not message_id:
            return
        chat_id = self._get_chat_id()
        if not chat_id:
            logger.debug("[Telegram] Cannot mark message %s as read — no chat_id", message_id)
            return
        logger.info("[Telegram] 📖 Marking message %s as read (chat_id: %s)", message_id, chat_id)
        self._send_websocket_message({
            "type": "mark_as_read",
            "chat_id": chat_id,
            "message_id": message_id,
        })

    def _send_websocket_message(self, message: dict):
        """Send a message via WebSocket (thread-safe)."""
        if self.socket and self.is_connected():
            import threading

            payload = json.dumps(message)

            if threading.current_thread() is threading.main_thread():
                self.socket.sendTextMessage(payload)
            else:
                self._send_ws_text_signal.emit(payload)
            return True
        return False

    def _send_binary_from_signal(self, data: bytes):
        """Slot to send binary data from the signal (runs on main thread)."""
        from PyQt6.QtCore import QByteArray
        if self.socket and self.is_connected():
            self.socket.sendBinaryMessage(QByteArray(data))

    def _send_websocket_binary(self, data: bytes):
        """Send raw binary data via WebSocket (thread-safe)."""
        import threading
        from PyQt6.QtCore import QByteArray
        if self.socket and self.is_connected():
            if threading.current_thread() is threading.main_thread():
                self.socket.sendBinaryMessage(QByteArray(data))
            else:
                self._send_ws_binary_signal.emit(data)
            return True
        return False

    def _refresh_qt_screens(self):
        """Slot to refresh screen cache from main thread (safe Qt access)."""
        try:
            from distr.core.screen_utils import get_all_screens_info

            screens = get_all_screens_info()
            logger.info(
                f"Refreshed screen cache via signal: {len(screens)} screens found"
            )
        except Exception as e:
            logger.error(f"Error refreshing screens: {e}")
