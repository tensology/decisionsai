"""
app.py - Main Application Entry Point

# LOGGING POLICY:
# Only call setup_logging() in the main process (in run()).
# Do NOT call setup_logging() in any other module or at import time.
#
# Env (optional):
#   DECISIONSAI_CONSOLE_LOG_LEVEL — DEBUG|INFO|WARNING|ERROR for the main stderr handler (default WARNING).
#   DECISIONSAI_LOG_CONSOLE_INFO=1 — shorthand to set console to INFO.
#   DECISIONSAI_AGENT_ACTIVITY_CONSOLE=0 — disable stderr lines for each agent tool completion (file still logs).

This module serves as the main entry point for the Decisions AI application.
It handles:
- Application initialization and setup
- Window management
- Signal handling and cleanup
- Error handling and logging

Key Components:
1. Application class - Main QT application wrapper
2. Window initialization and management
3. Resource cleanup and shutdown handling
"""

# ===========================================
# 1. Standard Library Imports
# ===========================================
import sys
import os

# Before transformers/sentence_transformers (tool retriever, etc.): fork-safe tokenizers.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Fix for Qt WebEngine rendering on macOS - must be set BEFORE Qt imports
if sys.platform == 'darwin':
    os.environ.setdefault('QT_MAC_WANTS_LAYER', '1')

import multiprocessing
# Fix for macOS multiprocessing spawn issues - must be done early
if sys.platform == 'darwin':
    try:
        multiprocessing.set_start_method('spawn', force=False)
    except RuntimeError:
        pass  # Already set
multiprocessing.freeze_support()  # Required for frozen/packaged applications

import logging
import time
import gc
import json
import subprocess
import threading

# Large local speech/model graphs make a generation-2 cyclic-GC scan visible
# as a frozen desktop. Reference counting still releases normal short-lived
# objects, and shutdown already performs an explicit full collection. Keep
# automatic collections cheap during interactive operation.
gc.set_threshold(5_000, 50, 1_000)

# ===========================================
# 2. Third Party Imports
# ===========================================
from PyQt6.QtCore import QThreadPool, QTimer, Qt, QRunnable, pyqtSlot, QObject, pyqtSignal, QThread
from PyQt6 import QtWidgets, QtGui  # Added QtGui import
from PyQt6.QtWidgets import QDialog, QApplication
import sounddevice as sd
import platform

from distr.core.rubicon_arm64_fix import apply_rubicon_arm64_fix

apply_rubicon_arm64_fix()

# PyAutoGUI - import at module level and disable FAILSAFE
try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

# Platform-specific imports
if platform.system() == 'Darwin':  # macOS
    try:
        import AppKit
        APPKIT_AVAILABLE = True
    except ImportError:
        APPKIT_AVAILABLE = False
else:
    APPKIT_AVAILABLE = False

# ===========================================
# 3. Local Imports
# ===========================================
from distr.core.utils import load_settings_from_db, save_settings_to_db
from distr.core.dock_app import configure_qt_dock_identity, ensure_macos_dock_visible, is_dock_app, persist_dock_launch_preference, resolve_app_bundle_path, wants_dock_icon
from distr.core.signals import signal_manager
from distr.core.chat import ChatService
from distr.core.chat_manager import ChatManagerCore
from distr.core.chat_qt_adapter import ChatManagerQt
from distr.core.integrations.telegram import TelegramWebSocketManager
from distr.core.db import get_session, Chat, Action
from distr.core.paths import DB_DIR, CORE_DIR
from distr.core.runtime_lifecycle import append_runtime_event, clear_exit_intent, read_exit_intent, write_exit_intent

from distr.gui.player import PlayerWindow
from distr.gui.oracle import OracleWindow
from distr.gui.dialogs.about import AboutWindow
from distr.gui.dialogs.eula import EulaWindow
from distr.gui.dialogs.audio import DeviceSelectionDialog
from distr.core.audio.utils import (
    get_current_device_list_hash,
    get_system_default_device_fingerprint,
    is_system_default_device_name,
    restore_locked_devices,
    detect_devices,
)

from distr.core.agent.session import AgentSession


class DeviceCheckWorkerSignals(QObject):
    """Signals for DeviceCheckWorker"""
    result = pyqtSignal(str)

class DeviceCheckWorker(QRunnable):
    """Worker thread for checking audio device changes"""
    def __init__(self):
        super().__init__()
        self.signals = DeviceCheckWorkerSignals()
        
    def _safe_emit(self, value):
        """Safely emit signal, handling case where QObject is deleted during shutdown"""
        try:
            # Check if application is still running
            app = QtWidgets.QApplication.instance()
            if app is None or (hasattr(app, '_quitting') and app._quitting):
                return
            # Check if signals object still exists
            if self.signals is None:
                return
            self.signals.result.emit(value)
        except RuntimeError as e:
            # Suppress RuntimeError about deleted QObject during shutdown
            if "wrapped C/C++ object" in str(e) or "has been deleted" in str(e):
                logging.getLogger(__name__).debug(f"DeviceCheckWorker: Suppressing signal emit during shutdown: {e}")
            else:
                raise
        except Exception as e:
            logging.getLogger(__name__).debug(f"DeviceCheckWorker: Error emitting signal: {e}")
        
    @pyqtSlot()
    def run(self):
        try:
            payload = {
                "device_hash": get_current_device_list_hash(),
                "default_fingerprint": get_system_default_device_fingerprint(),
            }
            self._safe_emit(json.dumps(payload))
        except Exception as e:
            logging.getLogger(__name__).debug(f"DeviceCheckWorker error: {e}")
            self._safe_emit("")

# ===========================================
# 4. Logging Setup
# ===========================================

logger = logging.getLogger(__name__)

def clear_log_files():
    """Clear all log files at startup to start fresh"""
    # Clear logs from main db/logs directory
    log_dir = os.path.join(DB_DIR, 'logs')
    if os.path.exists(log_dir):
        for filename in os.listdir(log_dir):
            file_path = os.path.join(log_dir, filename)
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    # Silently fail if we can't remove a log file
                    pass
    
    # Also clear logs from playground/db/logs directory if it exists
    playground_log_dir = os.path.join(CORE_DIR, 'playground', 'db', 'logs')
    if os.path.exists(playground_log_dir):
        for filename in os.listdir(playground_log_dir):
            file_path = os.path.join(playground_log_dir, filename)
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    # Silently fail if we can't remove a log file
                    pass

# Module-level ref so faulthandler's file stays open for crash dumps
_crash_log_file = None


def _setup_crash_logging():
    """Enable faulthandler to dump tracebacks on SIGSEGV/SIGABRT etc. to ~/.decisions/logs/"""
    global _crash_log_file
    try:
        import faulthandler
        crash_dir = os.path.expanduser("~/.decisions/logs")
        os.makedirs(crash_dir, exist_ok=True)
        crash_file = os.path.join(crash_dir, f"crash_{os.getpid()}.log")
        _crash_log_file = open(crash_file, "a")
        _crash_log_file.write(f"\n{'='*60}\nProcess {os.getpid()} started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        _crash_log_file.flush()
        faulthandler.enable(_crash_log_file, all_threads=True)
    except Exception as e:
        # Non-fatal - just skip crash logging
        logging.getLogger("distr").warning("Could not enable crash logging: %s", e)


def setup_logging(clear_logs=True):
    """Configure application-wide logging"""

    def _parse_console_level() -> int:
        """Console verbosity for the main ``distr`` StreamHandler."""
        explicit = (os.environ.get("DECISIONSAI_CONSOLE_LOG_LEVEL") or "").strip().upper()
        names = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        if explicit in names:
            return names[explicit]
        flag = (os.environ.get("DECISIONSAI_LOG_CONSOLE_INFO") or "").strip().lower()
        if flag in ("1", "true", "yes", "on"):
            return logging.INFO
        return logging.WARNING

    # Enable crash logging first (catches SIGSEGV, SIGABRT etc.)
    _setup_crash_logging()

    # Clear existing log files at startup (only in main process, not agent subprocess)
    if clear_logs:
        clear_log_files()
    
    log_dir = os.path.join(DB_DIR, 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    log_file = os.path.join(log_dir, 'decisions.log')

    # Remove all handlers from root logger and 'distr' logger to prevent duplicates
    for logger_name in ('distr', ''):
        logger = logging.getLogger(logger_name)
        while logger.handlers:
            handler = logger.handlers[0]
            logger.removeHandler(handler)
            handler.close()

    # Dedicated agent activity logger (tool completions) — reset handlers each setup
    activity_logger = logging.getLogger("distr.agent.activity")
    while activity_logger.handlers:
        _h = activity_logger.handlers[0]
        activity_logger.removeHandler(_h)
        _h.close()
    activity_logger.setLevel(logging.INFO)
    activity_logger.propagate = False
        
    # Then set up our application logging
    app_logger = logging.getLogger('distr')
    
    # Create handlers
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    # On Windows the console may be cp1252 — use errors='replace' to avoid UnicodeEncodeError on emoji
    import sys as _sys
    _console_stream = _sys.stderr
    if hasattr(_console_stream, 'reconfigure'):
        try:
            _console_stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    console_handler = logging.StreamHandler(_console_stream)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Set log levels to reduce spam
    file_handler.setLevel(logging.INFO)      # Only log INFO and above to file
    console_level = _parse_console_level()
    console_handler.setLevel(console_level)
    app_logger.setLevel(logging.INFO)
    logging.getLogger().setLevel(logging.INFO)

    # Pipecat and a few native-adjacent dependencies use Loguru directly,
    # bypassing the handlers above. Its default DEBUG sink was writing every
    # pipeline frame to the dock launcher's stderr log. Keep normal launches
    # quiet while retaining an explicit opt-in for diagnostics.
    try:
        from loguru import logger as loguru_logger

        loguru_level = (
            os.environ.get("DECISIONSAI_LOGURU_LEVEL") or "WARNING"
        ).strip().upper()
        loguru_logger.remove()
        loguru_logger.add(_console_stream, level=loguru_level)
    except Exception:
        pass

    # Agent tool completions: always INFO to file + stderr (unless opted out), independent of console_level
    activity_show_stderr = (os.environ.get("DECISIONSAI_AGENT_ACTIVITY_CONSOLE") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    activity_logger.addHandler(file_handler)
    if activity_show_stderr:
        activity_stderr = logging.StreamHandler(_console_stream)
        activity_stderr.setFormatter(formatter)
        activity_stderr.setLevel(logging.INFO)
        activity_logger.addHandler(activity_stderr)
    
    # Add handlers to our app logger
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    # Prevent distr logger from propagating to root (root also has file_handler,
    # which would cause every distr.* message to be written to the log file twice).
    app_logger.propagate = False
    # Also add file handler to root logger to catch all logs from all modules
    logging.getLogger().addHandler(file_handler)
    
    # Explicitly silence noisy modules
    silent_loggers = [
        'httpcore',
        'httpx',
        'urllib3',
        'matplotlib',
        'PIL',
        'LiteLLM',
        'litellm',
        'sentence_transformers',
    ]
    for logger_name in silent_loggers:
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)
        for name in logging.root.manager.loggerDict:
            if name.startswith(logger_name):
                logging.getLogger(name).setLevel(logging.CRITICAL)

    try:
        from distr.core.litellm_utils import configure_litellm

        configure_litellm()
    except Exception:
        pass

    # Disable propagation for all loggers except our app
    for name in logging.root.manager.loggerDict:
        if not name.startswith('distr'):
            logging.getLogger(name).propagate = False


# ===========================================
# 4. Agent Session Management
# ===========================================
def get_device_choices():
    """Return lists of input and output device names using sounddevice."""
    devices = sd.query_devices()
    input_devices = ['System Default'] + [d['name'] for d in devices if d['max_input_channels'] > 0]
    output_devices = ['System Default'] + [d['name'] for d in devices if d['max_output_channels'] > 0]
    return input_devices, output_devices

# ===========================================
# 5. Application Class
# ===========================================
from distr.app.workflow import WorkflowOrchestrationMixin
from distr.app.signals import SignalBridgeMixin
from distr.app.events import EventHandlerMixin
from distr.app.agent_lifecycle import AgentLifecycleMixin


class Application(EventHandlerMixin, AgentLifecycleMixin, WorkflowOrchestrationMixin, SignalBridgeMixin, QtWidgets.QApplication):
    """Main application class handling window management and lifecycle"""
    
    def __init__(self, argv):
        super().__init__(argv)
        self._startup_exit_intent = clear_exit_intent()
        append_runtime_event(
            "app_start",
            argv=list(argv),
            dock_app=is_dock_app(),
            restarting=os.environ.get("DECISIONS_RESTARTING") == "1",
            prior_exit_intent=self._startup_exit_intent,
        )
        
        # Force standard font on macOS to prevent potential crash with system fonts
        if sys.platform == 'darwin':
            font = QtGui.QFont("Arial", 12)
            font.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferAntialias)
            self.setFont(font)
            
        self._quitting = False
        if is_dock_app():
            persist_dock_launch_preference(resolve_app_bundle_path(CORE_DIR))
        self.agent_process = None
        self.selected_input_device = None
        self.selected_output_device = None

        # Kill any orphaned worker processes from a previous session, and
        # register atexit/signal handlers to clean up on this session's exit.
        try:
            from distr.core.process_tracker import setup as _pt_setup
            _pt_setup(shutdown_callback=self.quit)
        except Exception as _pt_err:
            logging.getLogger(__name__).debug("process_tracker setup failed: %s", _pt_err)

        # Also kill any detached DecisionsAI leftovers not covered by tracked worker PID file.
        try:
            from distr.core.process_tracker import kill_rogue_decisions_processes as _kill_rogue
            _kill_rogue()
        except Exception as _rogue_err:
            logging.getLogger(__name__).debug("rogue process cleanup failed: %s", _rogue_err)

        # Use explicit spawn context for Queue/Manager to avoid semaphore issues on macOS
        # This ensures proper serialization when passing to spawned child processes
        self.mp_context = multiprocessing.get_context('spawn')
        self.agent_command_queue = self.mp_context.Queue()
        self.agent_event_queue = self.mp_context.Queue()
        try:
            from distr.core.signals import set_agent_event_queue

            set_agent_event_queue(self.agent_event_queue)
        except Exception as exc:
            logging.getLogger(__name__).debug("agent event queue registration skipped: %s", exc)
        self._splash_sound_played = False  # Flag to prevent double-playing splash sound
        self._startup_splash = None
        self._splash_sound_lock = threading.Lock()
        self._splash_sound_process = None

        # Create shared manager for cross-process screen info cache
        self.screen_info_manager = self.mp_context.Manager()
        self.screen_info_cache = self.screen_info_manager.dict()
        # Track the manager's server process PID
        try:
            from distr.core.process_tracker import register_child_pid as _reg_pid
            if hasattr(self.screen_info_manager, '_process') and self.screen_info_manager._process:
                _reg_pid(self.screen_info_manager._process.pid)
        except Exception:
            pass
        # Initialize screen cache in utils module
        from distr.core.screen_utils import init_screen_cache_manager
        init_screen_cache_manager(self.screen_info_cache)

        # Create shared manager for confirmation results (cross-process communication)
        self.confirmation_manager = self.mp_context.Manager()
        self.confirmation_results_dict = self.confirmation_manager.dict()
        # Track the confirmation manager's server process PID
        try:
            from distr.core.process_tracker import register_child_pid as _reg_pid
            if hasattr(self.confirmation_manager, '_process') and self.confirmation_manager._process:
                _reg_pid(self.confirmation_manager._process.pid)
        except Exception:
            pass
        # Prevent agent->app current_chat_changed events from being relayed back to agent.
        self._suppress_current_chat_relay = False
        
        # Initialize core components first to load settings
        # Don't create a persistent session - use load_settings_from_db() which manages its own session
        self.settings = load_settings_from_db()
        self.current_playback_speed = self.settings.get('playback_speed', 1.0)
        
        # Note: hide_player_window debounce is handled by SignalManager.emit_hide_player_window()
        # which uses timestamp-based debouncing (simpler and race-condition free)
        
        # Fetch and cache models at startup (synchronous)
        logger.info("Fetching model caches at startup...")
        try:
            from distr.core.fetch_all_models import fetch_all_models
            fetch_all_models()
        except Exception as e:
            logger.error(f"Error fetching models at startup: {e}", exc_info=True)
            # Don't block startup if model fetching fails
        
        # Device selection will happen after EULA is accepted (in initialize_app)
        # This ensures EULA can be accepted before any device selection dialog
 
        self._chat_core = ChatManagerCore()
        self.chat_manager = ChatManagerQt(self._chat_core)
        
        # Initialize Telegram WebSocket manager
        self.telegram_manager = TelegramWebSocketManager()
        
        # Initialize WhatsApp WebSocket manager
        try:
            from distr.core.integrations.whatsapp import WhatsAppWebSocketManager
            self.whatsapp_manager = WhatsAppWebSocketManager()
        except Exception as e:
            logger.warning(f"WhatsApp manager not available: {e}")
            self.whatsapp_manager = None
        
        # Connect Telegram connection signal to start WebSocket
        signal_manager.telegram_connected.connect(self._on_telegram_connected)
        
        # Check if Telegram is already connected and connect WebSocket on startup
        QTimer.singleShot(500, self._check_and_connect_telegram_websocket)
        # Check if WhatsApp is already connected and connect WebSocket on startup
        QTimer.singleShot(800, self._check_and_connect_whatsapp_websocket)
        QTimer.singleShot(1100, self._maybe_start_discord_bot_background)
        QTimer.singleShot(1300, self._maybe_start_slack_outbound_worker)
        
        # Initialize action playback service (independent of UI)
        from distr.core.actions.playback_service import ActionPlaybackService
        self.action_playback_service = ActionPlaybackService()
        
        # Track pending action name (when waiting for user to name an action)
        self.pending_action_name_id = None
        
        # Guard to prevent concurrent agent reloads
        self._reloading_agent = False
        self._reload_lock = threading.Lock()
        
        # Set up application behavior
        signal_manager.exit_app.connect(self.quit)
        signal_manager.restart_app.connect(lambda: self.oracle_window.restart_app() if hasattr(self, 'oracle_window') and self.oracle_window else None)
        self.aboutToQuit.connect(self._log_about_to_quit)
        signal_manager.show_about_window.connect(self._on_show_about_from_web)
        signal_manager.reload_agent.connect(self.reload_agent_session)
        signal_manager.audio_devices_changed.connect(self.update_agent_audio_devices)
        signal_manager.stt_model_changed.connect(self.update_agent_stt_model)
        # macOS-specific: set activation policy and dock icon (only on macOS)
        if platform.system() == 'Darwin' and APPKIT_AVAILABLE:
            ensure_macos_dock_visible(self)
            self._set_macos_dock_icon()
            QTimer.singleShot(0, lambda: ensure_macos_dock_visible(self))
            QTimer.singleShot(1500, lambda: ensure_macos_dock_visible(self))
        configure_qt_dock_identity(self)
        
        # Initialize windows
        self._initialize_windows()
        self._setup_window_connections()
                
        # Configure startup behavior
        self._configure_startup()
        
        # Drain agent events frequently enough for fluid streaming.  The
        # handler has its own per-tick time budget to protect Qt painting.
        self.event_timer = QTimer()
        self.event_timer.timeout.connect(self.check_agent_events)
        self.event_timer.start(50)

        # Main-thread responsiveness watchdog.  A Qt timer only fires when the
        # event loop gets control, so drift is a cheap, reliable indication
        # that a GUI-thread callback (or native call holding the GIL) stalled
        # painting, animation, input, and agent-event delivery together.
        self._ui_watchdog_interval_ms = 250
        self._ui_watchdog_expected_at = time.monotonic() + (self._ui_watchdog_interval_ms / 1000.0)
        self._ui_watchdog_last_warning_at = 0.0
        self.ui_watchdog_timer = QTimer(self)
        self.ui_watchdog_timer.setTimerType(Qt.TimerType.PreciseTimer)

        def _check_ui_event_loop_lag():
            now = time.monotonic()
            lag = max(0.0, now - self._ui_watchdog_expected_at)
            self._ui_watchdog_expected_at = now + (self._ui_watchdog_interval_ms / 1000.0)
            if lag < 0.75 or now - self._ui_watchdog_last_warning_at < 2.0:
                return
            self._ui_watchdog_last_warning_at = now
            try:
                agent_queue_depth = self.agent_event_queue.qsize()
            except (AttributeError, NotImplementedError):
                agent_queue_depth = "unknown"
            web_queue = getattr(self, "_web_chat_event_queue", None)
            try:
                web_queue_depth = web_queue.qsize() if web_queue is not None else 0
            except (AttributeError, NotImplementedError):
                web_queue_depth = "unknown"
            logger.warning(
                "[UI WATCHDOG] Qt event loop stalled for %.3fs "
                "(agent_queue=%s web_queue=%s threads=%d gc=%s)",
                lag,
                agent_queue_depth,
                web_queue_depth,
                threading.active_count(),
                gc.get_count(),
            )

        self.ui_watchdog_timer.timeout.connect(_check_ui_event_loop_lag)
        self.ui_watchdog_timer.start(self._ui_watchdog_interval_ms)
        
        # Start periodic health check (every 30 seconds - detect crashes quickly)
        self.health_check_timer = QTimer()
        self.health_check_timer.timeout.connect(
            lambda: self._run_ui_callback_timed("agent_health_check", self.check_agent_health)
        )
        self.health_check_timer.start(30000)  # 30 seconds in milliseconds
        logger.info("Started periodic agent health check (interval: 30 seconds)")
        
        # Start periodic screen info update (every 2 seconds) to keep cache fresh
        self.screen_info_timer = QTimer()
        self.screen_info_timer.timeout.connect(
            lambda: self._run_ui_callback_timed("screen_info_cache", self._update_screen_info_cache)
        )
        self.screen_info_timer.start(2000)  # 2 seconds
        # Update immediately
        QTimer.singleShot(100, self._update_screen_info_cache)
        
        # Initialize device check timer but don't start it yet - will start after initialization
        self.device_check_timer = QTimer()
        self.device_check_timer.timeout.connect(
            lambda: self._run_ui_callback_timed("audio_device_check", self.check_audio_device_changes)
        )
        self._last_device_hash = None
        self._last_default_device_fingerprint = None
        self._device_check_enabled = False  # Will be enabled after initialization

        # Run one-time StepRunner → Workflow data migration before any workflow operations
        from distr.core.workflow.service import migrate_step_runner_data
        migration_ok = migrate_step_runner_data()
        if migration_ok:
            logger.info("StepRunner migration check passed — ready for workflow operations.")
        else:
            logger.warning("StepRunner migration failed — running in degraded mode. Will retry on next startup.")

        # Cancel any workflow runs that were left in "running" or "waiting" state from a
        # previous session (crash, force-quit, etc.).  Without this, those zombie runs block
        # every subsequent "Send to Workflow" for the same ticket forever.
        try:
            from distr.core.workflow.dispatcher import _cleanup_orphaned_runs_on_startup
            _cleanup_orphaned_runs_on_startup()
        except Exception as _cleanup_err:
            logger.warning("Orphaned workflow run cleanup failed: %s", _cleanup_err)

        try:
            from distr.core.orchestrator_memory import run_weekly_machine_activity_compaction

            run_weekly_machine_activity_compaction()
        except Exception as _memory_compaction_err:
            logger.debug("Hermes memory compaction skipped: %s", _memory_compaction_err)

        # Workflow scheduler: adaptive poll interval (idle when nothing scheduled, faster only for sub-minute schedules)
        from distr.core.workflow.scheduler import apply_workflow_scheduler_timer_interval
        from distr.gui.web.workflow_events import register_workflow_updated_callback

        self.workflow_scheduler_timer = QTimer()
        self.workflow_scheduler_timer.timeout.connect(
            lambda: self._run_ui_callback_timed("workflow_scheduler", self._run_workflow_scheduled)
        )

        def _sync_workflow_scheduler_timer():
            previous_ms = self.workflow_scheduler_timer.interval()
            interval_ms = apply_workflow_scheduler_timer_interval(self.workflow_scheduler_timer)
            if interval_ms != previous_ms:
                logger.info("Workflow scheduler poll interval set to %d ms", interval_ms)

        register_workflow_updated_callback(_sync_workflow_scheduler_timer)
        scheduler_interval_ms = apply_workflow_scheduler_timer_interval(self.workflow_scheduler_timer)
        self.workflow_scheduler_timer.start(scheduler_interval_ms)
        logger.info("Started Workflow scheduler (interval: %d ms)", scheduler_interval_ms)

        # Workflow orchestration: run steps in sequence, wait for completion, retry on failure
        self._workflow_orchestrations: dict[int, dict] = {}
        self._pending_single_step = None
        signal_manager.workflow_run_all_requested.connect(self._on_workflow_run_all_requested)
        signal_manager.workflow_execute_step_requested.connect(self._on_workflow_execute_step_requested)
        signal_manager.workflow_cancel_requested.connect(self._on_workflow_cancel_requested)
        signal_manager.workflow_skip_step_requested.connect(self._on_workflow_skip_step_requested)
        signal_manager.workflow_continue_requested.connect(self._on_workflow_continue_requested)
        signal_manager.step_waiting_for_feedback.connect(self._on_step_waiting_for_feedback)
        signal_manager.waiting_for_action_name.connect(self._on_waiting_for_action_name)
        # Check for missed scheduled runs on startup (delayed to let agent initialize first)
        QTimer.singleShot(10000, self._run_workflow_scheduled)

        # Initialize Initiative Service
        from distr.core.initiative.service import InitiativeService
        self.initiative_service = InitiativeService(
            telegram_manager=self.telegram_manager,
            chat_manager=self.chat_manager,
            event_queue=self.agent_event_queue,
        )
        self.initiative_service.start()
        
    
    
    def _run_ui_callback_timed(self, name, callback):
        """Run a periodic GUI-thread callback and report user-visible stall risk."""
        started = time.perf_counter()
        try:
            return callback()
        finally:
            elapsed = time.perf_counter() - started
            if elapsed >= 0.100:
                logger.warning(
                    "[UI WATCHDOG] Slow periodic callback: name=%s elapsed=%.3fs",
                    name,
                    elapsed,
                )

    def _enable_device_check_timer(self):
        """Enable the device check timer after initialization is complete."""
        if not self._device_check_enabled:
            self._device_check_enabled = True
            # Check every 5 seconds
            self.device_check_timer.start(5000)  # 5 seconds in milliseconds
            logger.info("Enabled periodic audio device change detection (interval: 5 seconds)")
            
            # Run initial check immediately to set baseline hash
            QTimer.singleShot(500, self.check_audio_device_changes)  # Small delay to ensure everything is ready

    def _on_waiting_for_action_name(self, action_id: int):
        """Show confirmation/input popup when a recording stops and needs naming."""
        try:
            current_title = ""
            with get_session() as session:
                action = session.query(Action).get(action_id)
                if action:
                    current_title = (action.title or "").strip()

            prompt_text = (
                "Recording saved.\nConfirm this action name or enter a new one:"
                if current_title
                else "Recording saved.\nEnter a name for this action:"
            )
            text, ok = QtWidgets.QInputDialog.getText(
                self.oracle_window if hasattr(self, "oracle_window") else None,
                "Name Recorded Action",
                prompt_text,
                text=current_title,
            )

            chosen = (text or "").strip()
            if ok and chosen:
                signal_manager.set_action_name.emit(action_id, chosen)
                return

            if not ok:
                if hasattr(self, "recorder_host") and self.recorder_host:
                    self.recorder_host.cancel_recorded_action(action_id)
                return

            # OK with blank text: keep the auto-generated title when one exists.
            if current_title:
                signal_manager.set_action_name.emit(action_id, current_title)
            elif hasattr(self, "recorder_host") and self.recorder_host:
                self.recorder_host.cancel_recorded_action(action_id)
        except Exception as e:
            logger.error("Failed to show action naming popup: %s", e, exc_info=True)
    
    def check_audio_device_changes(self):
        """Check for audio device changes using a background thread."""
        if not self._device_check_enabled:
            return

        if not hasattr(self, 'oracle_window') or not self.oracle_window:
            return

        settings = load_settings_from_db()
        lock_sound_enabled = settings.get('lock_sound', False)
        locked_input = settings.get('locked_input')
        locked_output = settings.get('locked_output')
        has_locked_lists = bool(settings.get('locked_output_list') or settings.get('locked_input_list'))
        uses_system_default = (
            is_system_default_device_name(settings.get('input_device'))
            or is_system_default_device_name(settings.get('output_device'))
        )

        if lock_sound_enabled and (locked_input or locked_output):
            pass  # Remember my Audio Settings: restore named devices when they reappear
        elif has_locked_lists:
            pass  # Keep merged device lists fresh for the web UI
        elif uses_system_default:
            pass  # Follow OS default route even when the device list hash is unchanged
        else:
            return

        worker = DeviceCheckWorker()
        worker.signals.result.connect(self._on_device_check_result, type=Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker)

    def _sync_agent_audio_from_settings(self, settings: dict) -> None:
        """Hot-swap the running agent to the saved input/output selections."""
        input_device = settings.get('input_device', 'System Default')
        output_device = settings.get('output_device', 'System Default')
        self.settings['input_device'] = input_device
        self.settings['output_device'] = output_device
        self.selected_input_device = input_device
        self.selected_output_device = output_device
        if self.agent_command_queue:
            try:
                self.agent_command_queue.put(('update_audio_devices', {
                    'input_device': input_device,
                    'output_device': output_device,
                }))
                logger.info(
                    "Queued agent audio refresh: input='%s' output='%s'",
                    input_device,
                    output_device,
                )
            except Exception as e:
                logger.error(f"Error sending update_audio_devices command: {e}")
        else:
            logger.warning("Agent command queue not available, cannot refresh audio devices")

    def _on_device_check_result(self, payload_raw):
        """Handle result from device check worker (runs on main thread)."""
        try:
            if QThread.currentThread() != QtWidgets.QApplication.instance().thread():
                logger.error("CRITICAL: _on_device_check_result called on wrong thread!")
                return

            if not payload_raw:
                logger.debug("Device check returned empty payload")
                return

            try:
                payload = json.loads(payload_raw)
                device_hash = payload.get('device_hash') or ''
                default_fingerprint = payload.get('default_fingerprint') or ''
            except (json.JSONDecodeError, TypeError):
                device_hash = payload_raw
                default_fingerprint = ''

            settings = load_settings_from_db()
            uses_system_default = (
                is_system_default_device_name(settings.get('input_device'))
                or is_system_default_device_name(settings.get('output_device'))
            )
            track_defaults = uses_system_default or settings.get('lock_sound', False)

            if self._last_device_hash is None:
                self._last_device_hash = device_hash or None
                if track_defaults:
                    self._last_default_device_fingerprint = default_fingerprint or None
                logger.info(
                    "Initialized audio monitor baseline (devices=%s, defaults=%s)",
                    self._last_device_hash,
                    self._last_default_device_fingerprint,
                )
                return

            list_changed = bool(device_hash) and device_hash != self._last_device_hash
            default_changed = (
                track_defaults
                and default_fingerprint
                and default_fingerprint != self._last_default_device_fingerprint
            )

            if not list_changed and not default_changed:
                return

            if list_changed:
                logger.debug(
                    "Device hash changed: %s -> %s",
                    self._last_device_hash,
                    device_hash,
                )
                newly_added_outputs, newly_added_inputs, _, _ = detect_devices()
                if newly_added_outputs or newly_added_inputs:
                    logger.info(
                        "New devices detected - outputs: %s, inputs: %s",
                        [d['name'] for d in newly_added_outputs],
                        [d['name'] for d in newly_added_inputs],
                    )
                    signal_manager.audio_device_lists_updated.emit(newly_added_outputs, newly_added_inputs)
                try:
                    from distr.gui.web.audio_events import increment_audio_devices_updated
                    increment_audio_devices_updated()
                except Exception as e:
                    logger.debug("Could not increment audio devices version for web: %s", e)
                self._last_device_hash = device_hash

            if default_changed:
                logger.info(
                    "System default audio route changed: %s -> %s",
                    self._last_default_device_fingerprint,
                    default_fingerprint,
                )
                self._last_default_device_fingerprint = default_fingerprint

            restore_result = restore_locked_devices(settings)
            self._sync_agent_audio_from_settings(settings)
            logger.info(
                "Audio monitor applied changes (list_changed=%s, default_changed=%s, restore=%s)",
                list_changed,
                default_changed,
                restore_result,
            )

            if self.oracle_window.isVisible() and (list_changed or default_changed):
                QTimer.singleShot(0, lambda: self._show_device_change_popup())
        except Exception as e:
            logger.debug(f"Error handling device check result: {e}")

    def _show_device_change_popup(self):
        """Show the device change popup (safe method - using non-modal notification)."""
        try:
            # Use None as parent to avoid potential window state issues
            # Also ensure we're on the main thread
            if QThread.currentThread() != QtWidgets.QApplication.instance().thread():
                return
                
            # Process events first to ensure UI is ready
            QtWidgets.QApplication.processEvents()
            
            # Use a non-modal QMessageBox with show() instead of exec() to avoid blocking
            # This is safer on macOS when audio is active
            msg_box = QtWidgets.QMessageBox()
            msg_box.setWindowTitle("Audio Device Change")
            msg_box.setText("Audio input/output detected")
            msg_box.setIcon(QtWidgets.QMessageBox.Icon.Information)
            msg_box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
            msg_box.setWindowModality(Qt.WindowModality.NonModal)  # Non-modal instead of ApplicationModal
            
            # Show it non-modally
            msg_box.show()
            msg_box.raise_()
            msg_box.activateWindow()
            
            # Auto-close after 3 seconds if user doesn't click
            QTimer.singleShot(3000, msg_box.close)
        except Exception as e:
            logger.error(f"Error showing popup: {e}")
        
    def _set_macos_dock_icon(self):
        """Set the application icon via AppKit so macOS shows it when pinned to dock."""
        try:
            candidates = []
            app_bundle = os.environ.get("DECISIONS_APP_BUNDLE", "").strip()
            if app_bundle:
                candidates.append(
                    os.path.join(app_bundle, "Contents", "Resources", "icon.icns")
                )
            candidates.extend([
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "decisions.app",
                    "Contents",
                    "Resources",
                    "icon.icns",
                ),
                os.path.join(
                    CORE_DIR,
                    "installer",
                    "decisions-app-template",
                    "Contents",
                    "Resources",
                    "icon.icns",
                ),
                os.path.join(ICONS_DIR, "tray.png"),
            ])
            icon_path = next((p for p in candidates if os.path.exists(p)), None)
            if icon_path:
                icon_image = AppKit.NSImage.alloc().initWithContentsOfFile_(icon_path)
                if icon_image:
                    AppKit.NSApp.setApplicationIconImage_(icon_image)
                    logger.info(f"Set macOS dock icon from: {icon_path}")
        except Exception as e:
            logger.debug(f"Could not set macOS dock icon: {e}")

    def _initialize_windows(self):
        """Initialize all application windows"""
        # Start unified GUI server for Flow Logic and Board UIs
        self._start_unified_gui_server()
        
        self.player_window = PlayerWindow()
        self.about_window = AboutWindow()
        # Initialize oracle window first (needed for EULA positioning)
        self.oracle_window = OracleWindow(
            self.about_window,
            self.player_window,
            self.chat_manager,
            None  # EULA window not yet created
        )
        # Initialize EULA window with oracle window reference
        self.eula_window = EulaWindow(oracle_window=self.oracle_window)
        # Now set the EULA window reference in oracle window
        self.oracle_window.eula_window = self.eula_window
        # Hide oracle window until EULA is accepted
        self.oracle_window.hide()
        self.player_window.set_oracle_window(self.oracle_window)
        
        # Headless action recorder host (no GUI); handles voice/agent/web start/stop recording
        from distr.core.actions.recorder_host import ActionRecorderHost
        self.recorder_host = ActionRecorderHost(self)
    
    def _start_unified_gui_server(self):
        """Start the unified GUI server for Flow Logic and Board UIs"""
        try:
            from distr.gui.web.server import UnifiedGuiServer
            self.unified_gui_server = UnifiedGuiServer()
            self.unified_gui_server.start()
        except Exception as e:
            logger.error(f"Failed to start unified GUI server: {e}")
            self.unified_gui_server = None
    
    def _stop_unified_gui_server(self):
        """Stop the unified GUI server"""
        if hasattr(self, 'unified_gui_server') and self.unified_gui_server:
            try:
                self.unified_gui_server.stop()
            except Exception as e:
                logger.warning(f"Error stopping unified GUI server: {e}")
        
    def _setup_window_connections(self):
        """Set up signal connections between windows"""
        if self.chat_manager:
            signal_manager.trigger_new_chat.connect(self._on_trigger_new_chat)
        # Connect EULA acceptance signal to handler - triggered when EULA is accepted
        signal_manager.eula_accepted.connect(self.on_eula_accepted)
        # Connect EULA window acceptance signal
        self.eula_window.eula_accepted.connect(self.on_eula_window_accepted)
        
    
    def _on_trigger_new_chat(self):
        """Create a new chat when trigger_new_chat is emitted (replaces Chat GUI new-chat)."""
        if hasattr(self, 'chat_manager') and self.chat_manager:
            try:
                self.chat_manager.create_chat("New Conversation", is_new=True)
            except Exception as e:
                logger.warning("Failed to create new chat on trigger: %s", e)

    def on_eula_accepted(self):
        """
        Handle EULA acceptance event - called when EULA is accepted via signal.
        """
        logger.info("EULA accepted signal received, reloading settings")
        
        # Force a small delay to ensure database commit is visible
        import time
        time.sleep(0.05)
        
        # Reload settings since they've just been updated - force fresh read
        self.settings = load_settings_from_db()
        
        # Verify EULA acceptance status after reload
        eula_accepted = self.settings.get("accepted_eula", False)
        logger.info(f"Application: EULA acceptance status after reload: {eula_accepted}")
        
        if not eula_accepted:
            logger.warning("Application: EULA status still False after reload - database may not have updated")
        
        # Start initialization sequence: device selection first, then everything else
        self._start_post_eula_initialization()
    
    def on_eula_window_accepted(self, accepted):
        """
        Handle EULA window acceptance event.
        
        Args:
            accepted (bool): Whether EULA was accepted
        """
        if accepted:
            logger.info("EULA accepted via standalone window, proceeding with normal startup")
            # Reload settings from database to get latest EULA status
            self.settings = load_settings_from_db()
            eula_status = self.settings.get('accepted_eula', False)
            logger.info(f"Application settings reloaded after EULA acceptance: accepted_eula={eula_status}")
            # Hide EULA window
            self.eula_window.hide()
            # Start initialization sequence: device selection first, then everything else
            self._start_post_eula_initialization()
    
    def _configure_startup(self):
        """
        Configure startup behavior and timing based on EULA acceptance
        
        This is called on EVERY application startup.
        """
        # Check EULA acceptance first before proceeding - safely handle if column doesn't exist yet
        eula_accepted = self.settings.get("accepted_eula", False)
        
        if not eula_accepted:
            # If EULA hasn't been accepted, show ONLY the EULA window
            # Do NOT show oracle, do NOT load models, do NOT initialize anything
            logger.info("EULA not accepted yet, showing standalone EULA window - blocking all initialization")
            # Position EULA window before showing to prevent flicker
            self.eula_window.position_at_oracle()
            # Show EULA window centered on screen (oracle not visible yet)
            # Delay showing to let event loop start
            QTimer.singleShot(100, self.eula_window.show)
            
            # DO NOT initialize app or show oracle until EULA is accepted
            logger.info("Waiting for EULA acceptance before initializing application")
        else:
            # EULA already accepted - follow normal startup flow
            logger.info("EULA already accepted, proceeding with normal startup")
            # Start initialization sequence: device selection first, then everything else
            QTimer.singleShot(100, self._start_post_eula_initialization)
    
    def _start_post_eula_initialization(self):
        """Start the initialization sequence after EULA acceptance: devices first, then everything else."""
        logger.info("Starting post-EULA initialization sequence")
        # Step 1: Select devices first (blocking if dialog is shown)
        # Check if devices are already selected, otherwise call select_devices which will check settings
        if not self.selected_input_device or not self.selected_output_device:
            logger.info("Selecting audio devices after EULA acceptance")
            self.select_devices()
            # After select_devices, check if we still don't have devices (user cancelled)
            if not self.selected_input_device or not self.selected_output_device:
                logger.warning("No devices selected, cannot continue initialization")
                return
        
        # Step 2: Now that devices are selected, continue with the rest
        self._continue_startup_after_eula()
        # Step 3: Initialize the app (load models, start agent session, etc.)
        QTimer.singleShot(100, self.initialize_app)
    
    def _continue_startup_after_eula(self):
        """Continue with normal startup flow after EULA and device selection."""
        # Show oracle window now that EULA is accepted and devices are selected
        logger.info("Showing oracle window after EULA acceptance and device selection")
        # Update menu before showing to ensure all items are enabled
        self.oracle_window.update_menu()
        self.oracle_window.show()

        if is_dock_app():
            self._show_startup_splash_if_needed()
            
        # Only show about window if that setting is enabled
        if self.settings.get("show_about", False):  # Default to False
            logger.info("Showing about window based on settings")
            # Position at oracle ball before showing
            self.about_window.center_on_screen(self.oracle_window)
            self.about_window.show()
        else:
            logger.info("About window disabled in settings")

        # Play splash sound if enabled (only once)
        if self.settings.get("load_splash_sound", False) and not self._splash_sound_played:
            self._play_splash_sound()
            self._splash_sound_played = True
            
        # Enable device check timer after initialization is complete
        # Use a delay to ensure everything is fully initialized
        QTimer.singleShot(2000, self._enable_device_check_timer)

    def _show_startup_splash_if_needed(self):
        if self._startup_splash is not None:
            return
        try:
            from distr.gui.dialogs.startup_splash import show_startup_splash

            self._startup_splash = show_startup_splash()
            QTimer.singleShot(20000, self._dismiss_startup_splash)
        except Exception as exc:
            logging.getLogger(__name__).debug("Startup splash unavailable: %s", exc)

    def _dismiss_startup_splash(self):
        splash = getattr(self, "_startup_splash", None)
        if splash is not None:
            try:
                splash.close()
            except Exception:
                pass
            self._startup_splash = None

    def _offer_macos_permissions_setup(self):
        """After boot settles, guide the user through macOS desktop permissions if needed."""
        self._dismiss_startup_splash()
        if self._quitting or not is_dock_app():
            return
        try:
            from distr.gui.dialogs.macos_permissions import offer_macos_permissions_setup

            parent = self.oracle_window if hasattr(self, "oracle_window") else None
            offer_macos_permissions_setup(parent=parent)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "macOS permission setup skipped: %s", exc, exc_info=True
            )
    
    def _on_show_about_from_web(self):
        """Show the about window and play splash sound (triggered from web UI)."""
        if hasattr(self, 'oracle_window') and self.oracle_window:
            self.oracle_window.show_about_window()
        self._play_splash_sound()

    def _play_splash_sound(self):
        """Play the splash sound file in a separate thread."""
        def play_sound():
            process = None
            try:
                # Get the path to the sound file — main.py is at distr/app/main.py,
                # assets are at project_root/assets/
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                sound_path = os.path.join(base_dir, "assets", "sounds", "decisions.mp3")
                
                if not os.path.exists(sound_path):
                    logger.warning(f"Splash sound file not found: {sound_path}")
                    return

                logger.info(f"Playing splash sound: {sound_path}")
                with self._splash_sound_lock:
                    current_process = self._splash_sound_process
                    if current_process is not None and current_process.poll() is None:
                        logger.debug("Splash sound already playing; skipping duplicate request")
                        return

                    # Play sound using system player (non-blocking)
                    if sys.platform == "darwin":  # macOS
                        process = subprocess.Popen(
                            ['afplay', sound_path],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    elif sys.platform.startswith("linux"):
                        # Try common Linux audio players
                        players = [['paplay', sound_path], ['aplay', sound_path],
                                  ['mpg123', sound_path], ['ffplay', '-nodisp', '-autoexit', sound_path]]
                        for player_cmd in players:
                            try:
                                process = subprocess.Popen(
                                    player_cmd,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                                break
                            except FileNotFoundError:
                                continue
                    elif sys.platform == "win32":
                        # Use ffplay (bundled with ffmpeg) — runs as a separate process
                        # so it won't conflict with the app's sounddevice audio pipeline
                        process = subprocess.Popen(
                            ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', sound_path],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        logger.warning(f"Unsupported platform for audio playback: {sys.platform}")
                        return

                    if process is None:
                        logger.warning("No splash sound player available on this platform")
                        return
                    self._splash_sound_process = process
            except Exception as e:
                logger.error(f"Error playing splash sound: {e}")
                return

            try:
                process.wait()
            except Exception:
                pass
            finally:
                with self._splash_sound_lock:
                    if self._splash_sound_process is process:
                        self._splash_sound_process = None

        # Play in a separate thread to avoid blocking
        sound_thread = threading.Thread(target=play_sound, daemon=True)
        sound_thread.start()
        
    def _ensure_default_chat(self):
        """If there are no chats, create one and set it as current so the agent and web UI have a chat on launch."""
        try:
            with get_session() as session:
                root_count = session.query(Chat).filter(Chat.parent_id.is_(None)).count()
                if root_count > 0:
                    return
            settings = load_settings_from_db()
            provider = (settings.get("llm_provider") or settings.get("conversational_llm_provider") or "ollama")
            model_name = (settings.get("llm_model") or settings.get("conversational_llm_model") or "").strip() or None
            vp = (settings.get("tts_provider") or "kokoro").strip()
            from distr.core.agent.constants import normalize_voice_provider
            from distr.core.agent.services.tts.registry import tts_registry
            vp_id = normalize_voice_provider(vp)
            _display = {d.id: d.name.split(" (")[0] for d in tts_registry.all_providers()}
            voice_provider = _display.get(vp_id, vp_id.title())
            from distr.core.chat import resolve_voice_model_from_global_settings

            voice_model = resolve_voice_model_from_global_settings(vp, settings)
            ChatService.create_new_chat(llm_provider=provider, llm_model=model_name, tts_provider=voice_provider, tts_voice=voice_model, title="New Chat", starting_question=None)
            logger.info("Created default chat on launch (no chats existed)")
        except Exception as e:
            logger.warning("Could not ensure default chat on launch: %s", e)

    def initialize_app(self):
        """Initialize the application and start the agent session"""
        # Devices should already be selected at this point (done in _start_post_eula_initialization)
        logger.info("Initializing application: loading models and starting agent session")
        self._ensure_default_chat()
        thread_pool = QThreadPool.globalInstance()
        thread_pool.waitForDone()
        # Bridge signals to agent command queue
        self._bridge_signals_to_agent()
        QTimer.singleShot(500, self.start_agent_session)
        if is_dock_app():
            # Wait for sidecar/agent boot before permission probes (not an installer step).
            QTimer.singleShot(4000, self._offer_macos_permissions_setup)
    

    

    def select_devices(self):
        """Load devices from settings, or show popup if not set."""
        # Reload settings to ensure we have the latest values
        self.settings = load_settings_from_db()
        
        # Get saved devices from settings
        saved_input_device = self.settings.get('input_device')
        saved_output_device = self.settings.get('output_device')
        
        logger.debug(f"select_devices: saved_input_device='{saved_input_device}', saved_output_device='{saved_output_device}'")
        
        # If either device is None or empty string, show dialog (user hasn't selected yet)
        # Check if devices are None, empty, or just whitespace
        input_is_empty = not saved_input_device or (isinstance(saved_input_device, str) and not saved_input_device.strip())
        output_is_empty = not saved_output_device or (isinstance(saved_output_device, str) and not saved_output_device.strip())
        
        if input_is_empty or output_is_empty:
            logger.info(f"No devices saved (input={saved_input_device}, output={saved_output_device}), showing device selection dialog")
            oracle_window = getattr(self, 'oracle_window', None)
            dialog = DeviceSelectionDialog(oracle_window=oracle_window)
            dialog.position_at_oracle()
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.selected_input_device, self.selected_output_device = dialog.get_selection()
                # Save the selected devices to settings
                self.settings['input_device'] = self.selected_input_device
                self.settings['output_device'] = self.selected_output_device
                from distr.core.utils import save_settings_to_db
                save_settings_to_db(self.settings)
                logger.info(f"Saved selected devices - Input: {self.selected_input_device}, Output: {self.selected_output_device}")
            else:
                # Fallback to defaults if dialog is cancelled
                input_devices, output_devices = get_device_choices()
                self.selected_input_device = input_devices[0] if input_devices else None
                self.selected_output_device = output_devices[0] if output_devices else None
                # Save defaults to settings
                if self.selected_input_device:
                    self.settings['input_device'] = self.selected_input_device
                if self.selected_output_device:
                    self.settings['output_device'] = self.selected_output_device
                from distr.core.utils import save_settings_to_db
                save_settings_to_db(self.settings)
            return
        
        # Get available devices to validate saved devices
        input_devices, output_devices = get_device_choices()
        
        logger.debug(f"select_devices: Found {len(input_devices)} input devices, {len(output_devices)} output devices")
        logger.debug(f"select_devices: input_devices={input_devices[:5]}...")  # Log first 5
        logger.debug(f"select_devices: output_devices={output_devices[:5]}...")  # Log first 5
        
        # Check if saved devices exist and are valid (case-insensitive comparison)
        input_valid = False
        output_valid = False
        
        # Try exact match first
        if saved_input_device in input_devices:
            input_valid = True
            logger.debug(f"select_devices: Found exact match for input device '{saved_input_device}'")
        else:
            # Try case-insensitive match
            saved_input_lower = saved_input_device.lower().strip()
            for device in input_devices:
                if device.lower().strip() == saved_input_lower:
                    # Use the actual device name from the list (correct case)
                    saved_input_device = device
                    input_valid = True
                    logger.debug(f"select_devices: Found case-insensitive match for input device '{saved_input_device}'")
                    break
        
        # Try exact match first
        if saved_output_device in output_devices:
            output_valid = True
            logger.debug(f"select_devices: Found exact match for output device '{saved_output_device}'")
        else:
            # Try case-insensitive match
            saved_output_lower = saved_output_device.lower().strip()
            for device in output_devices:
                if device.lower().strip() == saved_output_lower:
                    # Use the actual device name from the list (correct case)
                    saved_output_device = device
                    output_valid = True
                    logger.debug(f"select_devices: Found case-insensitive match for output device '{saved_output_device}'")
                    break
        
        # If both devices are saved and valid, use them
        if input_valid and output_valid:
            self.selected_input_device = saved_input_device
            self.selected_output_device = saved_output_device
            logger.info(f"Using saved devices - Input: {saved_input_device}, Output: {saved_output_device}")
            # Update settings with correct case if needed
            if self.settings.get('input_device') != saved_input_device or self.settings.get('output_device') != saved_output_device:
                self.settings['input_device'] = saved_input_device
                self.settings['output_device'] = saved_output_device
                from distr.core.utils import save_settings_to_db
                save_settings_to_db(self.settings)
            return
        
        # Otherwise, show the popup dialog (devices saved but not found in available devices)
        logger.info(f"Saved devices not found in available devices (input_valid={input_valid}, output_valid={output_valid}), showing device selection dialog")
        # oracle_window might not exist yet during initialization, pass it if available
        oracle_window = getattr(self, 'oracle_window', None)
        dialog = DeviceSelectionDialog(oracle_window=oracle_window)
        # Position dialog before showing to prevent flicker
        dialog.position_at_oracle()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.selected_input_device, self.selected_output_device = dialog.get_selection()
            # Save the selected devices to settings
            self.settings['input_device'] = self.selected_input_device
            self.settings['output_device'] = self.selected_output_device
            from distr.core.utils import save_settings_to_db
            save_settings_to_db(self.settings)
            logger.info(f"Saved selected devices - Input: {self.selected_input_device}, Output: {self.selected_output_device}")
        else:
            # Fallback to defaults if dialog is cancelled
            self.selected_input_device = input_devices[0] if input_devices else None
            self.selected_output_device = output_devices[0] if output_devices else None
            # Save defaults to settings
            if self.selected_input_device:
                self.settings['input_device'] = self.selected_input_device
            if self.selected_output_device:
                self.settings['output_device'] = self.selected_output_device
            from distr.core.utils import save_settings_to_db
            save_settings_to_db(self.settings)


    def update_agent_audio_devices(self, input_device, output_device):
        """Update agent audio devices and hot-swap the running agent."""
        logger.info(f"Updating agent audio devices: Input='{input_device}', Output='{output_device}'")

        # Keep in-memory and selected devices in sync so restarts use the new devices
        self.settings['input_device'] = input_device
        self.settings['output_device'] = output_device
        self.selected_input_device = input_device
        self.selected_output_device = output_device

        # Send command to agent so it hot-swaps input/output without restart
        if self.agent_command_queue:
            try:
                self.agent_command_queue.put(('update_audio_devices', {
                    "input_device": input_device,
                    "output_device": output_device
                }))
            except Exception as e:
                logger.error(f"Error sending update_audio_devices command: {e}")
        else:
            logger.warning("Agent command queue not available, cannot update audio devices")

    def update_agent_stt_model(self, transcription_model):
        """Update agent STT model when settings are saved."""
        logger = logging.getLogger(__name__)
        logger.info(f"🎤 Updating STT model to: '{transcription_model}'")

        # Update settings in DB
        from distr.core.utils import load_settings_from_db
        settings = load_settings_from_db()
        settings['transcription_model'] = transcription_model
        save_settings_to_db(settings)
        logger.info(f"✅ Saved transcription_model to database: '{transcription_model}'")

        # Update in-memory settings
        self.settings['transcription_model'] = transcription_model

        # Check if agent is running
        agent_running = (hasattr(self, 'agent_process') and 
                        self.agent_process and 
                        self.agent_process.is_alive())
        
        if not agent_running:
            logger.info("ℹ️  Agent not running - new STT model will be used when agent starts")
            return

        # Send command to agent via queue to update STT
        if self.agent_command_queue:
            try:
                # Use put_nowait to avoid blocking, with timeout fallback
                try:
                    self.agent_command_queue.put_nowait(('update_stt_model', {
                        "transcription_model": transcription_model
                    }))
                    logger.info(f"✅ Sent update_stt_model command to agent: '{transcription_model}'")
                    logger.info(f"✅ Agent should now use: {transcription_model}")
                except Exception:
                    # Queue might be full, try blocking put with timeout
                    import queue
                    try:
                        self.agent_command_queue.put(('update_stt_model', {
                            "transcription_model": transcription_model
                        }), timeout=1.0)
                        logger.info(f"✅ Sent update_stt_model command to agent (blocking): '{transcription_model}'")
                    except queue.Full:
                        logger.error("❌ Command queue is full - reloading agent instead")
                        signal_manager.reload_agent.emit()
            except Exception as e:
                logger.error(f"❌ Error sending update_stt_model command: {e}")
                logger.warning("⚠️  Falling back to agent reload")
                signal_manager.reload_agent.emit()
        else:
            logger.warning("⚠️  Agent command queue not available - reloading agent")
            # Fallback to full reload if queue is not available
            signal_manager.reload_agent.emit()



    def _map_speed(self, ui_speed):
        """Map UI speed (0.5-2.0) to narrower internal effective speed (0.8-1.4)."""
        # User request: 1.5 should feel like 1.2.
        # Formula: real = 1.0 + (ui - 1.0) * 0.4
        return 1.0 + (ui_speed - 1.0) * 0.4

    def _update_screen_info_cache(self):
        """Update the global screen info cache for cross-process access"""
        try:
            from distr.core.screen_utils import get_all_screens_info, update_screen_info_cache
            
            # Get screen info using Qt (only works in main process)
            screen_info_list = get_all_screens_info()
            
            # Update the Manager dict directly (cross-process shared)
            if screen_info_list and hasattr(self, 'screen_info_cache'):
                self.screen_info_cache['screens'] = screen_info_list
                logger.debug(f"Updated Manager dict with {len(screen_info_list)} screen(s)")
            
            # Also update the module-level cache (for backwards compatibility)
            update_screen_info_cache(screen_info_list)
            logger.debug(f"Screen info cache updated: {len(screen_info_list)} screen(s)")
        except Exception as e:
            logger.error(f"Error updating screen info cache: {e}", exc_info=True)
    
    def get_current_mouse_screen_info(self):
        """
        Get information about the screen that contains the mouse cursor.
        This runs in the main GUI process where QApplication is properly initialized.
        
        Returns:
            dict with screen info: {'screen_number': int, 'screen_name': str, 'geometry': dict}
            or None if detection fails
        """
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QCursor, QScreen
            from PyQt6.QtCore import QPoint
            if not pyautogui:
                return None
            
            app = QApplication.instance()
            if not app:
                logger.warning("get_current_mouse_screen_info: QApplication not available")
                return None
            
            # Get mouse position
            current_x, current_y = pyautogui.position()
            
            # Get screens
            screens = []
            try:
                screens = QScreen.availableScreens()
            except Exception:
                if hasattr(app, 'screens'):
                    screens = app.screens()
            
            if not screens:
                logger.warning("get_current_mouse_screen_info: No screens found")
                return None
            
            # Sort screens by X position (left to right)
            screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
            
            # Try app.screenAt() first
            if hasattr(app, 'screenAt'):
                try:
                    cursor_pos = QCursor.pos()
                    screen = app.screenAt(cursor_pos)
                    if screen:
                        # Find screen number (1-indexed, left to right)
                        screen_number = screens_sorted.index(screen) + 1 if screen in screens_sorted else 1
                        geo = screen.geometry()
                        return {
                            'screen_number': screen_number,
                            'screen_name': screen.name(),
                            'geometry': {
                                'x': geo.x(),
                                'y': geo.y(),
                                'width': geo.width(),
                                'height': geo.height()
                            }
                        }
                except Exception as e:
                    logger.debug(f"app.screenAt() failed: {e}")
            
            # Fallback: geometry check
            cursor_point = QPoint(int(current_x), int(current_y))
            for i, screen in enumerate(screens_sorted):
                geo = screen.geometry()
                if geo.contains(cursor_point):
                    return {
                        'screen_number': i + 1,
                        'screen_name': screen.name(),
                        'geometry': {
                            'x': geo.x(),
                            'y': geo.y(),
                            'width': geo.width(),
                            'height': geo.height()
                        }
                    }
            
            # Final fallback: primary screen
            primary = app.primaryScreen() if hasattr(app, 'primaryScreen') else screens_sorted[0]
            geo = primary.geometry()
            return {
                'screen_number': 1,
                'screen_name': primary.name(),
                'geometry': {
                    'x': geo.x(),
                    'y': geo.y(),
                    'width': geo.width(),
                    'height': geo.height()
                }
            }
        except Exception as e:
            logger.error(f"Error getting current mouse screen info: {e}", exc_info=True)
            return None
    


    

    def quit(self):
        """Clean up resources and quit the application"""
        logger = logging.getLogger(__name__)
        
        if self._quitting:
            return
            
        self._quitting = True
        if not read_exit_intent():
            write_exit_intent(
                "app_quit",
                source="application.quit",
                slow_path=os.environ.get("DECISIONS_SLOW_QUIT", "0"),
            )

        if os.environ.get("DECISIONS_SLOW_QUIT", "").lower() not in ("1", "true", "yes"):
            self._quit_fast()
            return

        logger.info("Starting application shutdown and cleanup (slow path)...")
        
        try:
            # Stop all timers first
            if hasattr(self, 'event_timer'):
                self.event_timer.stop()
            if hasattr(self, 'health_check_timer'):
                self.health_check_timer.stop()
            if hasattr(self, 'screen_info_timer'):
                self.screen_info_timer.stop()
            if hasattr(self, 'device_check_timer'):
                self.device_check_timer.stop()
            if hasattr(self, 'workflow_scheduler_timer'):
                self.workflow_scheduler_timer.stop()
            if hasattr(self, 'initiative_service'):
                self.initiative_service.stop()
            
            # First cleanup agent process
            self._cleanup_agent_process()
            
            # Cleanup all child processes
            self._cleanup_all_processes()
            
            # Save any necessary state
            if hasattr(self, 'oracle_window') and self.oracle_window:
                try:
                    self.oracle_window.save_listening_state()
                except Exception as e:
                    logger.warning(f"Error saving listening state: {e}")
            
            # Emit signal to stop sound playback
            try:
                signal_manager.stop_sound_player.emit()
            except Exception as e:
                logger.debug(f"Error emitting stop_sound_player: {e}")
            
            # Stop action playback service
            if hasattr(self, 'action_playback_service') and self.action_playback_service:
                try:
                    self.action_playback_service.stop()
                except Exception as e:
                    logger.warning(f"Error stopping action playback service: {e}")
            
            # Stop unified GUI server
            self._stop_unified_gui_server()
            
            # Wait for thread pool tasks to complete
            try:
                QThreadPool.globalInstance().waitForDone(2000)
            except Exception as e:
                logger.debug(f"Error waiting for thread pool: {e}")
            
            # Cleanup all threads
            self._cleanup_all_threads()
            
            # Process any pending events before closing windows
            self.processEvents()
            
            # Now close all windows
            for window in self.topLevelWindows():
                try:
                    window.close()
                except Exception as e:
                    logger.debug(f"Error closing window: {e}")
                
            # Process events one more time to handle window closing
            self.processEvents()
            
        except Exception as e:
            logger.error(f"Error during application shutdown: {e}", exc_info=True)
        finally:
            # Disconnect Telegram WebSocket
            if hasattr(self, 'telegram_manager') and self.telegram_manager:
                try:
                    self.telegram_manager.disconnect()
                except Exception as e:
                    logger.debug(f"Error disconnecting Telegram: {e}")
            # Disconnect WhatsApp WebSocket
            if hasattr(self, 'whatsapp_manager') and self.whatsapp_manager:
                try:
                    self.whatsapp_manager.disconnect()
                except Exception as e:
                    logger.debug(f"Error disconnecting WhatsApp: {e}")
            
            self._cleanup_multiprocessing_resources()
            
            # Close database connections
            self._cleanup_database_connections()

            # Final rogue-process sweep only after normal shutdown work completes.
            # This ordering avoids killing anything still needed during teardown.
            try:
                from distr.core.process_tracker import kill_rogue_decisions_processes
                kill_rogue_decisions_processes(timeout=2.0)
            except Exception as e:
                logger.debug(f"rogue process cleanup error: {e}")
            
            # Force garbage collection
            gc.collect()
            
            # Add a small delay to allow cleanup
            time.sleep(0.5)
            
            # Now quit
            super().quit()

    def _quit_fast(self):
        """Exit quickly; heavy process cleanup runs in bin/decisions-cleanup.sh."""
        logger = logging.getLogger(__name__)
        logger.info("Fast shutdown — handing process cleanup to external script")
        append_runtime_event("app_quit_fast", source="application._quit_fast")

        agent_pid = None
        try:
            if (
                hasattr(self, "agent_process")
                and self.agent_process
                and self.agent_process.is_alive()
            ):
                agent_pid = self.agent_process.pid
        except Exception:
            pass

        try:
            if hasattr(self, "event_timer"):
                self.event_timer.stop()
            if hasattr(self, "health_check_timer"):
                self.health_check_timer.stop()
            if hasattr(self, "screen_info_timer"):
                self.screen_info_timer.stop()
            if hasattr(self, "device_check_timer"):
                self.device_check_timer.stop()
            if hasattr(self, "workflow_scheduler_timer"):
                self.workflow_scheduler_timer.stop()
            if hasattr(self, "initiative_service"):
                self.initiative_service.stop()

            if hasattr(self, "oracle_window") and self.oracle_window:
                try:
                    self.oracle_window.save_listening_state()
                except Exception as e:
                    logger.warning("Error saving listening state: %s", e)

            try:
                signal_manager.stop_sound_player.emit()
            except Exception as e:
                logger.debug("Error emitting stop_sound_player: %s", e)

            if hasattr(self, "action_playback_service") and self.action_playback_service:
                try:
                    self.action_playback_service.stop()
                except Exception as e:
                    logger.warning("Error stopping action playback service: %s", e)

            self._signal_agent_shutdown_no_wait()
            self._stop_unified_gui_server()
            self._cleanup_multiprocessing_resources()
            self._cleanup_database_connections()
        except Exception as e:
            logger.error("Error during fast shutdown prep: %s", e, exc_info=True)

        try:
            from distr.core.external_cleanup import spawn_detached_cleanup

            spawn_detached_cleanup(main_pid=os.getpid(), agent_pid=agent_pid)
        except Exception as e:
            logger.warning("Could not spawn external cleanup: %s", e)

        try:
            super().quit()
            self.processEvents()
        except Exception:
            pass

        try:
            from distr.core.process_tracker import run_multiprocessing_finalizers

            run_multiprocessing_finalizers()
        except Exception:
            pass

        # Avoid ggml Metal crash during normal Python/Qt teardown on macOS.
        os._exit(0)

    def _cleanup_multiprocessing_resources(self):
        """Close queues and managers whose finalizers ``os._exit`` bypasses."""
        try:
            from distr.core.signals import set_agent_event_queue

            set_agent_event_queue(None)
        except Exception:
            pass

        queues = [
            getattr(self, "agent_command_queue", None),
            getattr(self, "agent_event_queue", None),
        ]
        managers = [
            getattr(self, "screen_info_manager", None),
            getattr(self, "confirmation_manager", None),
        ]
        self.agent_command_queue = None
        self.agent_event_queue = None
        self.screen_info_manager = None
        self.confirmation_manager = None

        try:
            from distr.core.process_tracker import close_multiprocessing_resources

            close_multiprocessing_resources(queues=queues, managers=managers)
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "Multiprocessing resource cleanup failed: %s", exc
            )

    def _log_about_to_quit(self):
        append_runtime_event(
            "qt_about_to_quit",
            source="application.aboutToQuit",
            exit_intent=read_exit_intent(),
        )
    
    def _cleanup_all_processes(self):
        """Cleanup all child processes"""
        logger = logging.getLogger(__name__)
        logger.info("Cleaning up all child processes...")

        # Kill all tracked worker PIDs (covers orphaned processes psutil can't see)
        try:
            from distr.core.process_tracker import kill_tracked_pids
            kill_tracked_pids(timeout=3.0)
        except Exception as e:
            logger.debug(f"process_tracker cleanup error: {e}")

        # Cleanup agent process (already handled by _cleanup_agent_process, but ensure it's done)
        self._cleanup_agent_process()
        
        # Cleanup action recorder process (headless host)
        if hasattr(self, 'recorder_host') and self.recorder_host:
            rp = getattr(self.recorder_host, 'recorder_process', None)
            if rp and getattr(rp, 'is_alive', lambda: False)():
                try:
                    logger.info("Terminating action recorder process...")
                    rp.stop()
                except Exception as e:
                    logger.warning(f"Error stopping recorder process: {e}")
        # Force cleanup of any remaining processes using psutil if available
        try:
            import psutil
            current_process = psutil.Process()
            children = current_process.children(recursive=True)
            for child in children:
                try:
                    if 'python' in child.name().lower() or 'decisions' in ' '.join(child.cmdline()).lower():
                        logger.info(f"Terminating child process: {child.pid} ({child.name()})")
                        child.terminate()
                        child.wait(timeout=2)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                    pass
                except Exception as e:
                    logger.debug(f"Error terminating child process: {e}")
        except ImportError:
            logger.debug("psutil not available, skipping advanced process cleanup")
        except Exception as e:
            logger.debug(f"Error during process cleanup: {e}")
    
    def _cleanup_all_threads(self):
        """Cleanup all threads"""
        logger = logging.getLogger(__name__)
        logger.info("Cleaning up threads...")
        
        # Get all threads
        all_threads = threading.enumerate()
        main_thread = threading.main_thread()
        
        for thread in all_threads:
            if thread is main_thread:
                continue
            
            try:
                if thread.is_alive():
                    # Only try to stop daemon threads or threads we created
                    if thread.daemon:
                        logger.debug(f"Thread {thread.name} is daemon and will exit with main")
                    else:
                        logger.debug(f"Thread {thread.name} is non-daemon, may need manual cleanup")
            except Exception as e:
                logger.debug(f"Error checking thread {thread.name}: {e}")
    
    def _cleanup_database_connections(self):
        """Cleanup all database connections"""
        logger = logging.getLogger(__name__)
        logger.info("Cleaning up database connections...")
        
        try:
            from distr.core.db import engine
            # Dispose of all connections in the pool
            engine.dispose()
            logger.info("Database connections disposed")
        except Exception as e:
            logger.warning(f"Error disposing database connections: {e}")
    
    def _check_and_connect_telegram_websocket(self):
        """Check if Telegram is already connected and connect WebSocket on app startup"""
        try:
            from distr.core.settings import load_settings_from_db
            import json
            settings = load_settings_from_db()
            
            connected_accounts = []
            if settings.get('connected_accounts'):
                try:
                    accounts_data = settings.get('connected_accounts', '[]')
                    if isinstance(accounts_data, str):
                        connected_accounts = json.loads(accounts_data)
                    else:
                        connected_accounts = accounts_data
                    
                    if isinstance(connected_accounts, dict):
                        connected_accounts = [connected_accounts]
                    elif not isinstance(connected_accounts, list):
                        connected_accounts = []
                except Exception as e:
                    logger.warning(f"Failed to parse connected_accounts on startup: {e}")
                    return
            
            # Find Telegram account
            telegram_account = None
            for account in connected_accounts:
                if isinstance(account, dict) and account.get('provider') == 'telegram':
                    telegram_account = account
                    break
            
            if telegram_account:
                app_user_id = telegram_account.get('app_user_id')
                telegram_user_id = telegram_account.get('user_id')
                
                # CRITICAL: Ensure telegram_user_id is an integer (Telegram user IDs are integers like 984897897)
                # If it's a string starting with "session_", it's actually an app_user_id, not telegram_user_id!
                if telegram_user_id:
                    try:
                        # Check if it's already an integer
                        if isinstance(telegram_user_id, int):
                            # Good, it's already an integer
                            pass
                        elif isinstance(telegram_user_id, str):
                            # Check if it's an app_user_id (starts with "session_")
                            if telegram_user_id.startswith('session_'):
                                logger.error(f"❌ ERROR: Stored 'user_id' is actually an app_user_id: {telegram_user_id}")
                                logger.error(f"   This is a database corruption issue. user_id should be Telegram user ID (integer), not app_user_id!")
                                telegram_user_id = None
                            else:
                                # Try to convert to integer
                                telegram_user_id = int(telegram_user_id)
                        else:
                            telegram_user_id = int(telegram_user_id)
                    except (ValueError, TypeError) as e:
                        logger.error(f"❌ Invalid telegram_user_id format: {telegram_user_id} ({e})")
                        logger.error(f"   Expected integer (e.g., 984897897), got: {type(telegram_user_id)}")
                        telegram_user_id = None
                
                if app_user_id or telegram_user_id:
                    logger.info(f"Found existing Telegram connection on startup:")
                    logger.info(f"  app_user_id: {app_user_id} (type: {type(app_user_id)})")
                    logger.info(f"  telegram_user_id: {telegram_user_id} (type: {type(telegram_user_id)})")
                    
                    # Connect WebSocket with existing connection info
                    # For private bot chats, chat_id = telegram_user_id, so we pass it to initialize chat_id
                    self.telegram_manager.connect(
                        short_code=None,  # Not used for WebSocket
                        app_user_id=app_user_id,
                        telegram_user_id=telegram_user_id  # This will initialize chat_id in the manager
                    )
                else:
                    logger.warning("No valid app_user_id or telegram_user_id found in stored connection")
        except Exception as e:
            logger.error(f"Error checking Telegram connection on startup: {e}", exc_info=True)
    
    def _on_telegram_connected(self, short_code: str, app_user_id: str, telegram_user_id: int):
        """
        Handle Telegram connection - start or update WebSocket connection.
        
        Args:
            short_code: Short code from connection token (not used for WebSocket)
            app_user_id: App user ID from server (permanent, reliable)
            telegram_user_id: Telegram user ID (permanent, after linking)
        """
        logger.info(f"Telegram connection event: app_user_id={app_user_id}, telegram_user_id={telegram_user_id}")
        
        # If we already have a connection, update it (reconnect with new IDs)
        # Note: connect() handles closing existing connection internally without sending "shut down" message
        if self.telegram_manager.is_connected():
            logger.info("WebSocket already connected, checking staleness before refresh")
            # Only disconnect if stale, effectively silencing the 10-min loop if active
            self.telegram_manager.disconnect(check_staleness=True)
        
        # Connect WebSocket using app_user_id (always available) and telegram_user_id (after linking)
        self.telegram_manager.connect(
            short_code=None,  # Not used for WebSocket (temporary)
            app_user_id=app_user_id if app_user_id else None,
            telegram_user_id=telegram_user_id if telegram_user_id else None
        )
    
    def _check_and_connect_whatsapp_websocket(self):
        """Check if WhatsApp is already connected and connect WebSocket on app startup"""
        if not self.whatsapp_manager:
            return
        try:
            from distr.core.settings import load_settings_from_db
            import json
            settings = load_settings_from_db()
            
            connected_accounts = []
            if settings.get('connected_accounts'):
                try:
                    accounts_data = settings.get('connected_accounts', '[]')
                    if isinstance(accounts_data, str):
                        connected_accounts = json.loads(accounts_data)
                    else:
                        connected_accounts = accounts_data
                    if isinstance(connected_accounts, dict):
                        connected_accounts = [connected_accounts]
                    elif not isinstance(connected_accounts, list):
                        connected_accounts = []
                except Exception as e:
                    logger.warning(f"Failed to parse connected_accounts for WhatsApp: {e}")
                    return
            
            whatsapp_account = None
            for account in connected_accounts:
                if isinstance(account, dict) and account.get('provider') == 'whatsapp' and account.get('status') == 'connected':
                    whatsapp_account = account
                    break
            
            if whatsapp_account:
                logger.info(f"Found existing WhatsApp connection on startup: jid={whatsapp_account.get('jid')}")
                self.whatsapp_manager.connect()
            else:
                logger.info("No existing WhatsApp connection found on startup")
        except Exception as e:
            logger.error(f"Error checking WhatsApp connection on startup: {e}", exc_info=True)

    def _maybe_start_discord_bot_background(self):
        """Starts discord.py bot thread when ``DECISIONSAI_DISCORD_BOT_TOKEN`` is set (TASK 16)."""
        try:
            from distr.core.integrations.discord.runner import start_discord_bot_background

            start_discord_bot_background()
        except Exception as e:
            logger.warning(f"Discord bot startup skipped or failed: {e}", exc_info=True)

    def _maybe_start_slack_outbound_worker(self):
        """Drain Slack outbound queue when ``DECISIONSAI_SLACK_BOT_TOKEN`` is set (TASK 17)."""
        try:
            from distr.core.integrations.slack.outbound import start_slack_outbound_worker_background

            start_slack_outbound_worker_background()
        except Exception as e:
            logger.warning("Slack outbound worker startup skipped or failed: %s", e, exc_info=True)


# ===========================================
# 6. Application Entry Point
# ===========================================
def run():
    """Main application entry point with error handling"""
    if sys.platform == 'darwin':
        # MallocStackLogging is already suppressed in start.py before imports
        # This is just a fallback to ensure it's completely removed
        if "MallocStackLogging" in os.environ:
            del os.environ["MallocStackLogging"]
        if "MallocStackLoggingDirectory" in os.environ:
            del os.environ["MallocStackLoggingDirectory"]
    
    # Qt WebEngine requires OpenGL context sharing - must be set BEFORE QApplication
    from PyQt6.QtCore import Qt
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    
    setup_logging()
    logger = logging.getLogger(__name__)
    try:
        from distr.core.integrations.relay_auth import ensure_relay_env_loaded

        ensure_relay_env_loaded()
    except Exception:
        pass
    logger.info("Starting application")
    
    app = Application(sys.argv)
    sys.exit(app.exec())

if __name__ == "__main__":
    run()
