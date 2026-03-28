"""
app.py - Main Application Entry Point

# LOGGING POLICY:
# Only call setup_logging() in the main process (in run()).
# Do NOT call setup_logging() in any other module or at import time.

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
import subprocess
import threading
from queue import Queue

# ===========================================
# 2. Third Party Imports
# ===========================================
from PyQt6.QtCore import QThreadPool, QTimer, Qt, QRunnable, pyqtSlot, QObject, pyqtSignal, QThread
from PyQt6 import QtWidgets, QtGui  # Added QtGui import
from PyQt6.QtWidgets import QDialog, QApplication
import sounddevice as sd
import platform

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
from distr.core.signals import signal_manager
from distr.core.chat import ChatService
from distr.core.chat_manager import ChatManagerCore
from distr.core.chat_qt_adapter import ChatManagerQt
from distr.core.integrations.telegram import TelegramWebSocketManager
from distr.core.db import get_session, Chat
from distr.core.paths import DB_DIR, CORE_DIR

from distr.gui.player import PlayerWindow
from distr.gui.oracle import OracleWindow
from distr.gui.dialogs.about import AboutWindow
from distr.gui.dialogs.eula import EulaWindow
from distr.gui.dialogs.audio import DeviceSelectionDialog
from distr.core.audio.utils import get_current_device_list_hash, restore_locked_devices, detect_devices

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
            # Run the heavy subprocess call in this background thread
            current_hash = get_current_device_list_hash()
            self._safe_emit(current_hash)
        except Exception as e:
            logging.getLogger(__name__).debug(f"DeviceCheckWorker error: {e}")
            # Emit empty string on error
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
                except Exception as e:
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
                except Exception as e:
                    # Silently fail if we can't remove a log file
                    pass

# Module-level ref so faulthandler's file stays open for crash dumps
_crash_log_file = None


def _setup_crash_logging():
    """Enable faulthandler to dump tracebacks on SIGSEGV/SIGABRT etc. to ~/.decisions/logs/"""
    global _crash_log_file
    try:
        import faulthandler
        crash_dir = os.path.expanduser("~/.decisionsai/logs")
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
    console_handler.setLevel(logging.WARNING) # Only show warnings/errors in console
    app_logger.setLevel(logging.INFO)
    logging.getLogger().setLevel(logging.INFO)
    
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
        'PIL'
    ]
    for logger_name in silent_loggers:
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)
        for name in logging.root.manager.loggerDict:
            if name.startswith(logger_name):
                logging.getLogger(name).setLevel(logging.CRITICAL)

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

def run_agent_session(settings, input_device=None, output_device=None, command_queue=None, event_queue=None, confirmation_results_dict=None, skip_welcome=False, screen_info_cache=None, agent_current_chat_id=None):
    """Runs the agent session in a separate process with proper error handling"""
    # Suppress MallocStackLogging warnings in agent subprocess
    if sys.platform == 'darwin':
        if "MallocStackLogging" in os.environ:
            del os.environ["MallocStackLogging"]
        if "MallocStackLoggingDirectory" in os.environ:
            del os.environ["MallocStackLoggingDirectory"]
    # Suppress "coroutine was never awaited" from pipecat/transport during process exit
    import warnings
    warnings.filterwarnings("ignore", message=r".*coroutine.*was never awaited", category=RuntimeWarning)
    # Suppress CUDA-not-available warnings from torch autocast (Kanade uses @cuda.amp.autocast on CPU)
    warnings.filterwarnings("ignore", message=r".*CUDA is not available.*", category=UserWarning)
    # Suppress FlashAttention fallback warnings from kanade_tokenizer
    warnings.filterwarnings("ignore", message=r".*FlashAttention.*", category=UserWarning)
    # Suppress whisper.cpp memory allocation logs (whisper_init_state: kv pad / compute buffer)
    # These come from the C library via stderr — redirect through logging
    os.environ.setdefault("WHISPER_LOG_LEVEL", "3")  # 3 = ERROR only
    setup_logging(clear_logs=False)  # Don't clear logs in agent subprocess
    
    # Initialize screen cache in agent process
    if screen_info_cache:
        from distr.core.screen_utils import init_screen_cache_manager
        init_screen_cache_manager(screen_info_cache)
        logger.info("Initialized screen cache in agent process")
    
    # Ensure QCoreApplication exists for signals to work in the agent process
    # This prevents "wrapped C/C++ object has been deleted" errors
    from PyQt6.QtCore import QCoreApplication
    app = None
    if not QCoreApplication.instance():
        # Create a QCoreApplication (no GUI) for the agent process
        app = QCoreApplication(sys.argv)
        
    agent_session = None
    try:
        def exception_handler(exc_type, exc_value, exc_traceback):
            if exc_type == sd.PortAudioError and "PortAudio not initialized" in str(exc_value):
                logger.info("Suppressing PortAudio termination error")
                return
            # Suppress RuntimeError about closed event loop during shutdown
            if exc_type == RuntimeError and "Event loop is closed" in str(exc_value):
                logger.debug("Suppressing event loop closed error during shutdown")
                return
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
        sys.excepthook = exception_handler

        try:
            agent_session = AgentSession(
                input_device=input_device, 
                output_device=output_device, 
                settings=settings,
                command_queue=command_queue,
                event_queue=event_queue,
                confirmation_results_dict=confirmation_results_dict,
                skip_welcome=skip_welcome,
                agent_current_chat_id=agent_current_chat_id
            )
            agent_session.start()
        except Exception as e:
            logger.error(f"Error initializing or running agent session: {e}")
            import traceback
            traceback.print_exc()
    except Exception as e:
        logger.error(f"Error in agent session process: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up agent session before exiting
        if agent_session:
            try:
                agent_session.stop()
            except (RuntimeError, Exception) as e:
                # Suppress RuntimeError and other exceptions during shutdown
                # These often occur when event loops are closing
                if "Event loop is closed" not in str(e) and "coroutine" not in str(e).lower():
                    logger.debug(f"Error stopping agent session: {e}")
        
        logger.info("Agent session process exiting")
        try:
            time.sleep(0.5)
        except (RuntimeError, KeyboardInterrupt):
            # Suppress errors during shutdown
            pass
        gc.collect()
        # Use os._exit(0) to avoid ggml Metal crash on macOS during normal Python shutdown.
        # GGML Metal destructor (ggml_metal_device_free) asserts in rsets->data count; bypassing
        # Python's atexit/__del__/C++ destructors prevents the crash.
        os._exit(0)

# ===========================================
# 5. Application Class
# ===========================================
from distr.app.step_runner import StepRunnerMixin
from distr.app.signals import SignalBridgeMixin
from distr.app.events import EventHandlerMixin
from distr.app.agent_lifecycle import AgentLifecycleMixin


class Application(EventHandlerMixin, AgentLifecycleMixin, StepRunnerMixin, SignalBridgeMixin, QtWidgets.QApplication):
    """Main application class handling window management and lifecycle"""
    
    def __init__(self, argv):
        super().__init__(argv)
        
        # Force standard font on macOS to prevent potential crash with system fonts
        if sys.platform == 'darwin':
            font = QtGui.QFont("Arial", 12)
            font.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferAntialias)
            self.setFont(font)
            
        self._quitting = False
        self.agent_process = None
        self.selected_input_device = None
        self.selected_output_device = None

        # Use explicit spawn context for Queue/Manager to avoid semaphore issues on macOS
        # This ensures proper serialization when passing to spawned child processes
        self.mp_context = multiprocessing.get_context('spawn')
        self.agent_command_queue = self.mp_context.Queue()
        self.agent_event_queue = self.mp_context.Queue()
        self._splash_sound_played = False  # Flag to prevent double-playing splash sound

        # Create shared manager for cross-process screen info cache
        self.screen_info_manager = self.mp_context.Manager()
        self.screen_info_cache = self.screen_info_manager.dict()
        # Initialize screen cache in utils module
        from distr.core.screen_utils import init_screen_cache_manager
        init_screen_cache_manager(self.screen_info_cache)

        # Create shared manager for confirmation results (cross-process communication)
        self.confirmation_manager = self.mp_context.Manager()
        self.confirmation_results_dict = self.confirmation_manager.dict()
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
        
        # Connect Telegram connection signal to start WebSocket
        signal_manager.telegram_connected.connect(self._on_telegram_connected)
        
        # Check if Telegram is already connected and connect WebSocket on startup
        QTimer.singleShot(500, self._check_and_connect_telegram_websocket)
        
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
        signal_manager.reload_agent.connect(self.reload_agent_session)
        signal_manager.audio_devices_changed.connect(self.update_agent_audio_devices)
        signal_manager.stt_model_changed.connect(self.update_agent_stt_model)
        # macOS-specific: set activation policy and dock icon (only on macOS)
        if platform.system() == 'Darwin' and APPKIT_AVAILABLE:
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            # Set the application icon so macOS shows it properly when pinned to dock
            # (instead of the generic glass square)
            self._set_macos_dock_icon()
        
        # Initialize windows
        self._initialize_windows()
        self._setup_window_connections()
                
        # Configure startup behavior
        self._configure_startup()
        
        # Start polling for agent events (500ms — 2 polls/sec)
        self.event_timer = QTimer()
        self.event_timer.timeout.connect(self.check_agent_events)
        self.event_timer.start(500)
        
        # Start periodic health check (every 30 seconds - detect crashes quickly)
        self.health_check_timer = QTimer()
        self.health_check_timer.timeout.connect(self.check_agent_health)
        self.health_check_timer.start(30000)  # 30 seconds in milliseconds
        logger.info("Started periodic agent health check (interval: 30 seconds)")
        
        # Start periodic screen info update (every 2 seconds) to keep cache fresh
        self.screen_info_timer = QTimer()
        self.screen_info_timer.timeout.connect(self._update_screen_info_cache)
        self.screen_info_timer.start(2000)  # 2 seconds
        # Update immediately
        QTimer.singleShot(100, self._update_screen_info_cache)
        
        # Initialize device check timer but don't start it yet - will start after initialization
        self.device_check_timer = QTimer()
        self.device_check_timer.timeout.connect(self.check_audio_device_changes)
        self._last_device_hash = None
        self._device_check_enabled = False  # Will be enabled after initialization

        # Step Runner: check for due scheduled sessions every minute
        self.step_runner_scheduler_timer = QTimer()
        self.step_runner_scheduler_timer.timeout.connect(self._run_step_runner_scheduled)
        self.step_runner_scheduler_timer.start(60000)  # 60 seconds
        logger.info("Started Step Runner scheduler (interval: 60 seconds)")

        # Step Runner orchestration: run steps in sequence, wait for completion, retry on failure
        self._step_runner_orchestration = None
        self._pending_single_step = None
        signal_manager.step_runner_run_all_requested.connect(self._on_step_runner_run_all_requested)
        signal_manager.step_runner_execute_requested.connect(self._on_step_runner_execute_requested)
        signal_manager.step_runner_cancel_requested.connect(self._on_step_runner_cancel_requested)
        signal_manager.step_runner_skip_step_requested.connect(self._on_step_runner_skip_step_requested)
        signal_manager.step_runner_continue_requested.connect(self._on_step_runner_continue_requested)
        # Check for missed scheduled runs on startup (delayed to let agent initialize first)
        QTimer.singleShot(10000, self._run_step_runner_scheduled)
        
    
    
    def _enable_device_check_timer(self):
        """Enable the device check timer after initialization is complete."""
        if not self._device_check_enabled:
            self._device_check_enabled = True
            # Check every 5 seconds
            self.device_check_timer.start(5000)  # 5 seconds in milliseconds
            logger.info("Enabled periodic audio device change detection (interval: 5 seconds)")
            
            # Run initial check immediately to set baseline hash
            QTimer.singleShot(500, self.check_audio_device_changes)  # Small delay to ensure everything is ready
    
    def check_audio_device_changes(self):
        """Check for audio device changes using a background thread."""
        # Don't check if not enabled or if windows aren't initialized
        if not self._device_check_enabled:
            return
        
        if not hasattr(self, 'oracle_window') or not self.oracle_window:
            return
        
        # Run if "Remember my Audio Settings" is enabled with locked devices, OR we have device lists to keep updated (for web UI)
        settings = load_settings_from_db()
        lock_sound_enabled = settings.get('lock_sound', False)
        locked_input = settings.get('locked_input')
        locked_output = settings.get('locked_output')
        has_locked_lists = bool(settings.get('locked_output_list') or settings.get('locked_input_list'))

        if lock_sound_enabled and (locked_input or locked_output):
            pass  # Run: user has remembered devices
        elif has_locked_lists:
            pass  # Run: we have device lists to keep updated (e.g. for web UI dropdown refresh)
        else:
            return
            
        # Use QThreadPool to run the check in background
        worker = DeviceCheckWorker()
        # Force QueuedConnection to ensure it runs on main thread
        worker.signals.result.connect(self._on_device_check_result, type=Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker)
            
    def _on_device_check_result(self, current_hash):
        """Handle result from device check worker (runs on main thread)."""
        try:
            # Verify we are on main thread
            if QThread.currentThread() != QtWidgets.QApplication.instance().thread():
                logger.error("CRITICAL: _on_device_check_result called on wrong thread!")
                return

            # Skip if empty hash (error occurred)
            if not current_hash:
                logger.debug("Device check returned empty hash")
                return
                
            # Skip first check (initialize baseline)
            if self._last_device_hash is None:
                self._last_device_hash = current_hash
                logger.info(f"Initialized device hash baseline (MD5): {current_hash}")
                return
            
            # Check if devices changed
            if current_hash != self._last_device_hash:
                logger.debug(f"Device hash changed: {self._last_device_hash} -> {current_hash}")
                
                # Detect and add new devices to the locked lists so they appear in dropdowns
                newly_added_outputs, newly_added_inputs, _, _ = detect_devices()
                
                # Emit signal to notify AudioTab to refresh if new devices were added
                if newly_added_outputs or newly_added_inputs:
                    logger.info(f"New devices detected - outputs: {[d['name'] for d in newly_added_outputs]}, inputs: {[d['name'] for d in newly_added_inputs]}")
                    signal_manager.audio_device_lists_updated.emit(newly_added_outputs, newly_added_inputs)
                # Always increment version when hash changes so web UI can refresh (devices added OR removed)
                try:
                    from distr.gui.web.audio_events import increment_audio_devices_updated
                    increment_audio_devices_updated()
                except Exception as e:
                    logger.debug("Could not increment audio devices version for web: %s", e)
                
                # Load settings and restore locked devices
                settings = load_settings_from_db()
                restore_locked_devices(settings)
                
                # Show popup notification only if oracle window is visible
                if self.oracle_window.isVisible():
                    # Use singleShot to decouple from current stack
                    QTimer.singleShot(0, lambda: self._show_device_change_popup())
                    
                # Update hash
                self._last_device_hash = current_hash
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
            icon_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'decisions.app', 'Contents', 'Resources', 'icon.icns'
            )
            if not os.path.exists(icon_path):
                # Fallback: try tray icon
                icon_path = os.path.join(ICONS_DIR, 'tray.png')
            if os.path.exists(icon_path):
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
    
    def _play_splash_sound(self):
        """Play the splash sound file in a separate thread."""
        def play_sound():
            try:
                # Get the path to the sound file — main.py is at distr/app/main.py,
                # assets are at project_root/assets/
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                sound_path = os.path.join(base_dir, "assets", "sounds", "decisions.mp3")
                
                if not os.path.exists(sound_path):
                    logger.warning(f"Splash sound file not found: {sound_path}")
                    return
                
                logger.info(f"Playing splash sound: {sound_path}")
                
                # Play sound using system player (non-blocking)
                if sys.platform == "darwin":  # macOS
                    subprocess.Popen(['afplay', sound_path], 
                                    stdout=subprocess.DEVNULL, 
                                    stderr=subprocess.DEVNULL)
                elif sys.platform.startswith("linux"):
                    # Try common Linux audio players
                    players = [['paplay', sound_path], ['aplay', sound_path], 
                              ['mpg123', sound_path], ['ffplay', '-nodisp', '-autoexit', sound_path]]
                    for player_cmd in players:
                        try:
                            subprocess.Popen(player_cmd, 
                                            stdout=subprocess.DEVNULL, 
                                            stderr=subprocess.DEVNULL)
                            break
                        except FileNotFoundError:
                            continue
                elif sys.platform == "win32":
                    # Use ffplay (bundled with ffmpeg) — runs as a separate process
                    # so it won't conflict with the app's sounddevice audio pipeline
                    subprocess.Popen(
                        ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', sound_path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    logger.warning(f"Unsupported platform for audio playback: {sys.platform}")
            except Exception as e:
                logger.error(f"Error playing splash sound: {e}")
        
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
            vp = (settings.get("tts_provider") or "kokoro").strip().lower()
            voice_provider = "Kokoro" if "kokoro" in vp else "OpenAI" if "openai" in vp else "ElevenLabs" if "elevenlabs" in vp else vp or "Kokoro"
            voice_model = (settings.get("kokoro_voice") or settings.get("openai_voice") or settings.get("elevenlabs_voice") or "").strip() or None
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
        from distr.core.utils import load_settings_from_db, save_settings_to_db
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
        logger.info("Starting application shutdown and cleanup...")
        
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
            if hasattr(self, 'step_runner_scheduler_timer'):
                self.step_runner_scheduler_timer.stop()
            
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
            
            # Cleanup multiprocessing managers
            if hasattr(self, 'screen_info_manager'):
                try:
                    self.screen_info_manager.shutdown()
                except Exception as e:
                    logger.debug(f"Error shutting down screen_info_manager: {e}")
            
            if hasattr(self, 'confirmation_manager'):
                try:
                    self.confirmation_manager.shutdown()
                except Exception as e:
                    logger.debug(f"Error shutting down confirmation_manager: {e}")
            
            # Close database connections
            self._cleanup_database_connections()
            
            # Force garbage collection
            gc.collect()
            
            # Add a small delay to allow cleanup
            time.sleep(0.5)
            
            # Now quit
            super().quit()
    
    def _cleanup_all_processes(self):
        """Cleanup all child processes"""
        logger = logging.getLogger(__name__)
        logger.info("Cleaning up all child processes...")
        
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
    from PyQt6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting application")
    
    app = Application(sys.argv)
    sys.exit(app.exec())

if __name__ == "__main__":
    run()