import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
import platform
import subprocess
from collections import deque
from urllib.parse import quote

# Qt Imports
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QUrl

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
from distr.core.integrations.telegram.utils import relay_internal_token

from distr.core.integrations.telegram.messages import TelegramMessagesMixin
from distr.core.integrations.telegram.sender import TelegramSenderMixin
from distr.core.integrations.telegram.remote_control import TelegramRemoteControlMixin
from distr.core.integrations.base import IntegrationReconnectMixin

# How often to repeat the same "not connected" summary in the main log.
_CONNECT_FAILURE_LOG_INTERVAL_S = 300.0
_IMMEDIATE_CLOSE_WINDOW_S = 3.0
_TELEGRAM_ONLINE_NOTICE_COOLDOWN_S = 6 * 60 * 60
_TELEGRAM_ONLINE_NOTICE_STATE_KEY = "telegram_online_notice"


def relay_endpoint_label(server_url: str) -> str:
    """Human-readable relay host from a WebSocket URL."""
    base = server_url.split("/ws/")[0]
    return (
        base.replace("wss://", "")
        .replace("ws://", "")
        .replace("https://", "")
        .replace("http://", "")
    )


def redact_telegram_log_secrets(value: str) -> str:
    """Remove relay credentials accidentally persisted by older app versions."""
    clean = re.sub(
        r"(?i)([?&]token=)[^\s&]+",
        r"\1[REDACTED]",
        str(value or ""),
    )
    return re.sub(
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        "[REDACTED_JWT]",
        clean,
    )


def friendly_telegram_connect_error(exc: Exception, *, endpoint: str = "") -> str:
    """Map a requests/network exception to a short user-facing offline reason."""
    msg = str(exc).lower()
    host = endpoint or "Telegram relay"
    if any(
        token in msg
        for token in (
            "nodename nor servname",
            "name resolution",
            "failed to resolve",
            "getaddrinfo failed",
            "name or service not known",
        )
    ):
        return f"cannot reach {host} — check internet or DNS"
    if "can't assign requested address" in msg:
        return "network unavailable — Telegram not connected (will retry automatically)"
    if "connection reset" in msg or "connection aborted" in msg:
        return "Telegram relay closed the connection — reconnecting automatically"
    if "timed out" in msg or "timeout" in msg:
        return f"Telegram relay timed out ({host}) — reconnecting automatically"
    return f"Telegram relay unreachable — not connected"


def friendly_telegram_socket_error(err_str: str, *, endpoint: str = "") -> str:
    """Map a Qt WebSocket error string to a short user-facing offline reason."""
    lowered = (err_str or "").lower()
    host = endpoint or "Telegram relay"
    if "host not found" in lowered or "can't assign requested address" in lowered:
        return f"cannot reach {host} — check internet or DNS"
    if "remote host closed" in lowered or "connection closed" in lowered:
        return "Telegram relay dropped the connection — reconnecting automatically"
    if "tls" in lowered or "ssl" in lowered:
        return "secure connection to Telegram relay failed — reconnecting automatically"
    if err_str:
        return f"Telegram not connected ({err_str})"
    return "Telegram not connected"


def friendly_telegram_immediate_close_reason(*, endpoint: str = "") -> str:
    """User-facing reason when the relay accepts auth, then drops the socket immediately."""
    host = endpoint or "Telegram relay"
    return (
        f"{host} accepted the session token but closed the Telegram socket immediately — "
        "the stored Telegram session is likely stale, or the relay backend is unhealthy"
    )


def telegram_online_notice_enabled() -> bool:
    """Return whether the initial Telegram online notice should be sent."""
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        return bool(settings.get("telegram_send_online_notice", False))
    except Exception:
        # Lifecycle chatter should fail closed. A missing settings database must
        # never turn repeated reconnects/restarts into outbound Telegram spam.
        return False


def claim_telegram_online_notice(*, now: float | None = None) -> bool:
    """Durably rate-limit the optional startup notice across app processes."""
    if not telegram_online_notice_enabled():
        return False
    timestamp = float(time.time() if now is None else now)
    try:
        from distr.core.db import get_session
        from distr.core.db.orchestrator import OrchestratorMaintenanceState

        with get_session() as db:
            state = (
                db.query(OrchestratorMaintenanceState)
                .filter(OrchestratorMaintenanceState.key == _TELEGRAM_ONLINE_NOTICE_STATE_KEY)
                .first()
            )
            try:
                payload = json.loads(state.value_json or "{}") if state else {}
            except Exception:
                payload = {}
            last_sent_at = float(payload.get("last_sent_at") or 0.0)
            if last_sent_at and timestamp - last_sent_at < _TELEGRAM_ONLINE_NOTICE_COOLDOWN_S:
                return False
            value_json = json.dumps({"last_sent_at": timestamp})
            if state:
                state.value_json = value_json
            else:
                db.add(
                    OrchestratorMaintenanceState(
                        key=_TELEGRAM_ONLINE_NOTICE_STATE_KEY,
                        value_json=value_json,
                    )
                )
            db.commit()
        return True
    except Exception:
        logger.warning("Could not persist Telegram online-notice cooldown; skipping notice", exc_info=True)
        return False


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
    _ws_token_ready_signal = pyqtSignal(int, object)

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
        self._ws_token_ready_signal.connect(self._finish_ws_token_request)

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

        # Quiet offline logging — one friendly summary, then silence until interval elapses.
        self._connect_failure_reason: Optional[str] = None
        self._connect_failure_logged_at: float = 0.0
        self._outage_announced: bool = False
        self._last_socket_error_log_at: float = 0.0
        self._dns_fallback_applied: bool = False
        self._connected_at: float = 0.0
        self._immediate_close_count: int = 0
        self._ws_token_request_id: int = 0
        self._ws_token_fetch_in_progress: bool = False

        # Rate Limiting & Dedup
        self._last_send_time = 0
        self._min_send_interval = 0.5
        self._recent_messages = {}
        self._dedup_window = 10.0
        # Caches for inbound dedup
        self._processed_message_ids = set()
        self._processed_message_hashes = set()
        self._processed_message_id_order = deque()
        self._processed_message_hash_order = deque()
        self._max_processed_cache_size = 100

        # Health Check
        self._last_message_time = time.time()
        self._init_socket_heartbeat_state("Telegram", interval_ms=30_000, timeout_ms=8_000)
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
        self._cancelled_remote_audio_requests: set[str] = set()
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
            else:
                # Older builds logged the complete WebSocket URL. Scrub those
                # expired credentials once so retained diagnostics are safe.
                existing = self._detailed_log_file.read_text(
                    encoding="utf-8", errors="replace"
                )
                redacted = redact_telegram_log_secrets(existing)
                if redacted != existing:
                    self._detailed_log_file.write_text(redacted, encoding="utf-8")
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

    def _relay_endpoint_label(self) -> str:
        return relay_endpoint_label(self.server_url)

    def _announce_connect_failure(self, reason: str, *, force: bool = False) -> None:
        """Log a human-readable offline status; repeat at most every few minutes."""
        self._connect_failure_reason = reason
        now = time.time()
        if (
            not force
            and self._outage_announced
            and (now - self._connect_failure_logged_at) < _CONNECT_FAILURE_LOG_INTERVAL_S
        ):
            self._log_detailed(f"NOT CONNECTED (suppressed): {reason}")
            return

        self._connect_failure_logged_at = now
        self._outage_announced = True
        logger.warning("[Telegram] Not connected — %s", reason)
        self._log_detailed(f"NOT CONNECTED: {reason}")
        self.connection_status_changed.emit(False, reason)

    def _clear_connect_failure(self) -> None:
        """Reset offline state after a successful connection."""
        if self._outage_announced:
            logger.info(
                "[Telegram] Connected to relay (%s)",
                self._relay_endpoint_label(),
            )
        self._connect_failure_reason = None
        self._outage_announced = False
        self._connect_failure_logged_at = 0.0
        self._last_socket_error_log_at = 0.0
        self._dns_fallback_applied = False

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

        # Relay authentication performs DNS + HTTPS and can take up to ten
        # seconds during an outage.  Never perform it on Qt's event-loop thread:
        # that used to freeze the entire UI on idle reconnects.  The completion
        # signal is queued back to this QObject's thread before touching QWebSocket.
        if self._ws_token_fetch_in_progress:
            logger.debug("[Telegram] Relay token request already in progress")
            return
        self._ws_token_request_id += 1
        request_id = self._ws_token_request_id
        self._ws_token_fetch_in_progress = True

        def fetch_token() -> None:
            token = self._fetch_ws_token()
            try:
                self._ws_token_ready_signal.emit(request_id, token)
            except RuntimeError:
                logger.debug("[Telegram] Manager deleted before relay token completed")

        threading.Thread(
            target=fetch_token,
            daemon=True,
            name="TelegramRelayToken",
        ).start()

    def _finish_ws_token_request(self, request_id: int, ws_token: object) -> None:
        """Finish relay authentication on the Qt thread."""
        if request_id != self._ws_token_request_id:
            logger.debug("[Telegram] Ignoring stale relay token response id=%s", request_id)
            return
        self._ws_token_fetch_in_progress = False
        self._open_websocket_with_token(str(ws_token or ""))

    def _open_websocket_with_token(self, ws_token: Optional[str]) -> None:
        """Open QWebSocket after the blocking token request has completed."""
        if not ws_token:
            reason = (
                self._connect_failure_reason
                or "could not obtain relay token — Telegram not connected"
            )
            self._announce_connect_failure(reason)
            if not self._active_disconnect:
                self._schedule_reconnect("Telegram")
            return
        url_str = self.server_url
        params = []
        params.append(f"token={quote(ws_token, safe='')}")

        if params:
            url_str = f"{url_str}?{'&'.join(params)}"

        # Never persist the short-lived relay JWT embedded in the query string.
        endpoint_label = self._relay_endpoint_label()
        logger.info("Connecting to Telegram WebSocket: %s", endpoint_label)
        self._log_detailed(f"CONNECTING: {endpoint_label}")

        self._active_disconnect = False

        self.socket.open(QUrl(url_str))

    def _fetch_ws_token(self) -> Optional[str]:
        """Request a short-lived relay WebSocket JWT using the internal relay token."""
        if requests is None:
            return None
        base = self.server_url.split("/ws/")[0]
        base = base.replace("wss://", "https://").replace("ws://", "http://")
        api_url = f"{base}/api/telegram/ws-token"
        relay_token = relay_internal_token()
        headers = {"Content-Type": "application/json"}
        if relay_token:
            headers["X-Relay-Internal-Token"] = relay_token
        payload = {"app_user_id": self.app_user_id or ""}
        if self.telegram_user_id:
            payload["telegram_user_id"] = self.telegram_user_id
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=10)
            if response.status_code != 200:
                reason = (
                    f"Telegram relay rejected connection (HTTP {response.status_code})"
                )
                self._connect_failure_reason = reason
                logger.debug(
                    "[Telegram] WS token request failed: HTTP %s %s",
                    response.status_code,
                    response.text[:200],
                )
                self._log_detailed(
                    f"WS TOKEN HTTP {response.status_code}: {response.text[:500]}"
                )
                return None
            token = (response.json().get("token") or "").strip()
            if not token:
                self._connect_failure_reason = (
                    "Telegram relay did not return a connection token"
                )
                return None
            return token
        except Exception as e:
            endpoint = self._relay_endpoint_label()
            self._connect_failure_reason = friendly_telegram_connect_error(
                e, endpoint=endpoint
            )
            logger.debug("[Telegram] WS token request failed: %s", e)
            self._log_detailed(f"WS TOKEN ERROR: {e}")
            return None

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
        # Invalidate any relay-token worker so a late HTTPS response cannot
        # reopen the socket after an intentional disconnect.
        self._ws_token_request_id += 1
        self._ws_token_fetch_in_progress = False

        logger.info("Disconnecting Telegram WebSocket...")

        # Stop reconnect timer first to prevent auto-reconnect
        if hasattr(self, "_reconnect_timer"):
            self._reconnect_timer.stop()
        self._stop_socket_heartbeat()

        # Process lifecycle is an implementation detail. Do not bypass engagement
        # policy to send "Goodbye" on shutdown, and do not block the Qt/UI thread
        # while flushing a low-value message.

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
            if self._reconnect_attempts <= 2 or self._reconnect_attempts % 10 == 0:
                logger.info(
                    "[Telegram] Reconnecting (attempt #%d)...",
                    self._reconnect_attempts,
                )
            else:
                logger.debug(
                    "[Telegram] Reconnecting (attempt #%d, delay %dms)",
                    self._reconnect_attempts,
                    self._reconnect_delay_current_ms,
                )
            self._is_auto_reconnecting = True  # Mark as auto-reconnect
            self.connect(self.short_code, self.app_user_id, self.telegram_user_id)

    def _check_health(self):
        """Periodically check connection health and force reconnect if truly dead."""
        if not self.is_connected():
            # If we're not connected and not actively disconnecting, trigger reconnect
            if not self._active_disconnect and not self._reconnect_timer.isActive():
                logger.debug(
                    "[Telegram] Health check: not connected, scheduling reconnect"
                )
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

        self._clear_connect_failure()
        self.connection_status_changed.emit(True, "Connected")
        self._connected_at = time.time()

        self._reset_reconnect_state("Telegram")
        self._is_auto_reconnecting = False  # Reset flag after connection
        self._mark_socket_heartbeat_seen()
        self._start_socket_heartbeat()

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
            if claim_telegram_online_notice():
                logger.info(
                    "[Telegram] 📤 Sending online message (enabled in settings)"
                )
                self.send_to_telegram("I'm back online.", None, None)
            else:
                logger.debug("[Telegram] ⏭️ Skipping online message (disabled)")
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
        self._stop_socket_heartbeat()

        if not self._active_disconnect:
            lived_for = (
                time.time() - self._connected_at if self._connected_at > 0 else None
            )
            if lived_for is not None and lived_for <= _IMMEDIATE_CLOSE_WINDOW_S:
                self._immediate_close_count += 1
                reason = friendly_telegram_immediate_close_reason(
                    endpoint=self._relay_endpoint_label()
                )
            else:
                self._immediate_close_count = 0
                reason = "Telegram relay disconnected — reconnecting automatically"
            if not self._outage_announced:
                self._announce_connect_failure(reason)
            else:
                logger.debug("[Telegram] WebSocket disconnected (reconnect pending)")
                self._connect_failure_reason = reason
        else:
            logger.info("[Telegram] WebSocket disconnected (intentional)")
            self.connection_status_changed.emit(False, "Disconnected")

        # Reset connection status tracking
        self._last_connection_status = None
        self._connected_at = 0.0

        if not self._active_disconnect:
            self._schedule_reconnect("Telegram")

    def _on_error(self, error_code):
        """Handle socket errors."""
        err_str = self.socket.errorString()
        endpoint = self._relay_endpoint_label()
        friendly = friendly_telegram_socket_error(err_str, endpoint=endpoint)
        lived_for = time.time() - self._connected_at if self._connected_at > 0 else None
        if (
            lived_for is not None
            and lived_for <= _IMMEDIATE_CLOSE_WINDOW_S
            and (
                "tls" in (err_str or "").lower()
                or "ssl" in (err_str or "").lower()
                or "remote host closed" in (err_str or "").lower()
                or "connection closed" in (err_str or "").lower()
            )
        ):
            friendly = friendly_telegram_immediate_close_reason(endpoint=endpoint)
        self._log_detailed(f"SOCKET ERROR: {err_str} (code={error_code})")

        now = time.time()
        if (now - self._last_socket_error_log_at) >= 60.0 or not self._outage_announced:
            self._last_socket_error_log_at = now
            self._announce_connect_failure(friendly)
        else:
            self._log_detailed(f"SOCKET ERROR (suppressed): {err_str}")

        # If host resolution for the www subdomain is flaky, fall back to apex.
        # Both hosts terminate on the same endpoint; this avoids repeated
        # reconnect loops when local DNS intermittently fails on one name.
        try:
            lowered = (err_str or "").lower()
            host_resolution_err = ("host not found" in lowered) or ("can't assign requested address" in lowered)
            if host_resolution_err and "www.decisionsai.net" in self.server_url:
                fallback = self.server_url.replace("www.decisionsai.net", "decisionsai.net")
                if fallback != self.server_url:
                    if not self._dns_fallback_applied:
                        logger.info(
                            "[Telegram] Trying alternate relay host: %s",
                            relay_endpoint_label(fallback),
                        )
                        self._dns_fallback_applied = True
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
                logger.debug("[Telegram] Scheduling immediate reconnect after socket error")
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
        self._mark_socket_heartbeat_seen()

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

            if msg_type == "pong":
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

    def _mark_message_as_read(self, message_id: int, *, chat_id=None):
        """Tell the relay the desktop accepted this inbound message.

        Relay may call readBusinessMessage for business-bot links; standard bots
        only get typing/record_voice indicators (no Bot API read receipts).
        """
        if not message_id:
            return
        chat_id = chat_id or self._get_chat_id() or getattr(self, "telegram_user_id", None)
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
