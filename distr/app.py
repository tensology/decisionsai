"""
app.py - Main Application Entry Point

This module serves as the main entry point for the Decisions AI application.
It handles:
- Application initialization and setup
- Window management
- Process management for agent sessions
- Signal handling and cleanup
- Error handling and logging

Key Components:
1. Application class - Main QT application wrapper
2. Agent session management
3. Window initialization and management
4. Resource cleanup and shutdown handling
"""

# ===========================================
# 1. Standard Library Imports
# ===========================================
import multiprocessing
import logging
import atexit
import signal
import time
import sys
import os
import gc

# ===========================================
# 2. Third Party Imports
# ===========================================
from PyQt6.QtCore import QThreadPool, QTimer
from PyQt6 import QtWidgets
import sounddevice
import AppKit
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton

# ===========================================
# 3. Local Imports
# ===========================================
from distr.core.utils import load_settings_from_db
from distr.core.signals import signal_manager
from distr.core.actions import ActionHandler
# from distr.core.dump.sound import SoundPlayer
from distr.core.chat import ChatManager
from distr.core.db import get_session
from distr.core.constants import DB_DIR

from distr.gui.settings.main import SettingsWindow
from distr.gui.player import VoiceBoxWindow
from distr.gui.oracle import OracleWindow
from distr.gui.about import AboutWindow

from distr import AgentSession

# ===========================================
# 4. Logging Setup
# ===========================================

logger = logging.getLogger(__name__)

def setup_logging():
    """Configure application-wide logging"""
    log_dir = os.path.join(DB_DIR, 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        
    log_file = os.path.join(log_dir, 'decisions.log')

    # First, set root logger to ERROR to suppress most logs
    logging.getLogger().setLevel(logging.ERROR)
    
    # Then set up our application logging
    app_logger = logging.getLogger('distr')
    app_logger.setLevel(logging.INFO)
    
    # Create handlers
    file_handler = logging.FileHandler(log_file)
    console_handler = logging.StreamHandler()
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers to our app logger
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    
    # Explicitly silence noisy modules
    silent_loggers = [
        'vosk',
        'sounddevice',
        'httpcore',
        'httpx',
        'pywhispercpp',
        'whisper',
        'ggml',
        'urllib3',
        'matplotlib',
        'PIL'
    ]
    
    for logger_name in silent_loggers:
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)
        # Also silence all child loggers
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
    devices = sounddevice.query_devices()
    input_devices = [d['name'] for d in devices if d['max_input_channels'] > 0]
    output_devices = [d['name'] for d in devices if d['max_output_channels'] > 0]
    return input_devices, output_devices

class DeviceSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Audio Devices")
        self.selected_input = None
        self.selected_output = None
        layout = QVBoxLayout()
        input_devices, output_devices = get_device_choices()
        layout.addWidget(QLabel("Select input device (microphone):"))
        self.input_combo = QComboBox()
        self.input_combo.addItems(input_devices)
        layout.addWidget(self.input_combo)
        layout.addWidget(QLabel("Select output device (speaker/headphones):"))
        self.output_combo = QComboBox()
        self.output_combo.addItems(output_devices)
        layout.addWidget(self.output_combo)
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        layout.addWidget(self.ok_button)
        self.setLayout(layout)
    def get_selection(self):
        return self.input_combo.currentText(), self.output_combo.currentText()

def run_agent_session(settings, input_device=None, output_device=None):
    """Runs the agent session in a separate process with proper error handling"""
    try:
        def exception_handler(exc_type, exc_value, exc_traceback):
            if exc_type == sounddevice.PortAudioError and "PortAudio not initialized" in str(exc_value):
                logger.info("Suppressing PortAudio termination error")
                return
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
        sys.excepthook = exception_handler
        def agent_signal_handler(sig, frame):
            logger.info(f"Agent process received signal {sig}, shutting down...")
            try:
                for handler in logging.getLogger().handlers:
                    handler.flush()
            except:
                pass
            return
        signal.signal(signal.SIGINT, agent_signal_handler)
        signal.signal(signal.SIGTERM, agent_signal_handler)
        def cleanup_at_exit():
            logger.info("Agent process exiting via atexit")
            try:
                sounddevice.stop()
            except:
                pass
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except:
                    pass
        atexit.register(cleanup_at_exit)

        try:
            agent_session = AgentSession(input_device=input_device, output_device=output_device, settings=settings)
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
        logger.info("Agent session process exiting")
        try:
            sounddevice.stop()
        except:
            pass
        time.sleep(0.5)
        gc.collect()

# ===========================================
# 5. Application Class
# ===========================================
class Application(QtWidgets.QApplication):
    """Main application class handling window management and lifecycle"""
    
    def __init__(self, argv):
        super().__init__(argv)
        self._quitting = False
        self.agent_process = None
        self.selected_input_device = None
        self.selected_output_device = None
        # Prompt for device selection at startup
        self.select_devices()
        
        # Initialize core components
        self.db_session = get_session()
        self.settings = load_settings_from_db()
        # Debug print for ElevenLabs config
        eleven_enabled = self.settings.get('elevenlabs_enabled')
        eleven_key = self.settings.get('elevenlabs_key')
        tts_provider = self.settings.get('tts_provider')
        print(f"[LOAD] ElevenLabs: enabled={eleven_enabled}, key='{eleven_key}', tts_provider='{tts_provider}'")
        self.action_handler = ActionHandler()
        self.chat_manager = ChatManager()
        
        # Set up application behavior
        signal_manager.exit_app.connect(self.quit)
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        
        # Initialize windows
        self._initialize_windows()
        self._setup_window_connections()
        
        # Connect settings_saved to playback duck/reset
        if hasattr(self, 'settings_window'):
            self.settings_window.settings_saved.connect(self._on_settings_saved)
        
        # Configure startup behavior
        self._configure_startup()
        
    def _initialize_windows(self):
        """Initialize all application windows"""
        self.voice_box = VoiceBoxWindow()
        self.about_window = AboutWindow()
        self.settings_window = SettingsWindow()
        self.oracle_window = OracleWindow(
            self.settings_window,
            self.about_window,
            self.voice_box,
            self.chat_manager
        )
        self.voice_box.set_oracle_window(self.oracle_window)
        
    def _setup_window_connections(self):
        """Set up signal connections between windows"""
        if self.chat_manager:
            self.chat_manager.chat_created.connect(self.oracle_window.chat_window.on_chat_created)
            self.chat_manager.chat_updated.connect(self.oracle_window.chat_window.on_chat_updated)
            self.chat_manager.chat_deleted.connect(self.oracle_window.chat_window.on_chat_deleted)
        
        # Connect EULA acceptance signal to handler - triggered when Save is clicked and EULA is accepted for first time
        signal_manager.eula_accepted.connect(self.on_eula_accepted)
    
    def on_eula_accepted(self):
        """
        Handle EULA acceptance event - called ONLY when Save button is clicked
        and EULA is accepted for the first time.
        
        This method is NOT called during normal startup when EULA was previously accepted.
        """
        logger.info("EULA accepted for first time via Save button, proceeding with normal startup")
        
        # Hide the settings window since EULA is now accepted
        self.settings_window.hide()
        
        # Reload settings since they've just been updated
        self.settings = load_settings_from_db()
        
        # Verify EULA acceptance status after reload
        eula_accepted = self.settings.get("accepted_eula", False)
        logger.info(f"EULA acceptance status after save: {eula_accepted}")
        
        # Show about window only if that setting is enabled
        if self.settings.get("show_about", False):  # Default to False
            logger.info("Showing about window based on settings")
            self.about_window.show()
        else:
            logger.info("About window disabled in settings")
            
        # Continue with normal startup
        if self.settings.get("load_splash_sound", False):
            QTimer.singleShot(500, self.initialize_voice_box)
        
        # Re-enable other windows
        signal_manager.eula_check_required.emit(False)
    
    def _configure_startup(self):
        """
        Configure startup behavior and timing based on EULA acceptance
        
        This is called on EVERY application startup.
        """
        # Check EULA acceptance first before proceeding - safely handle if column doesn't exist yet
        eula_accepted = self.settings.get("accepted_eula", False)
        
        if not eula_accepted:
            # If EULA hasn't been accepted, show only settings window first and force EULA tab
            logger.info("EULA not accepted yet, showing settings window with EULA tab")
            self.settings_window.show()
            self.settings_window.tab_widget.setCurrentIndex(0)  # Set to EULA tab
            
            # Disable other windows until EULA is accepted
            signal_manager.eula_check_required.emit(True)
        else:
            # EULA already accepted - follow normal startup flow
            logger.info("EULA already accepted, proceeding with normal startup")
            
            # Only show about window if that setting is enabled
            if self.settings.get("show_about", False):  # Default to False
                logger.info("Showing about window based on settings")
                self.about_window.show()
            else:
                logger.info("About window disabled in settings")

            if self.settings.get("load_splash_sound", False):
                # self.sound_player.play_decisions_sound()
                QTimer.singleShot(500, self.initialize_voice_box)

        # Always initialize the app after checking EULA status
        QTimer.singleShot(100, self.initialize_app)
        
    def initialize_app(self):
        """Initialize the application and start the agent session"""
        thread_pool = QThreadPool.globalInstance()
        thread_pool.waitForDone()
        signal_manager.sound_finished.connect(self.voice_box.on_sound_finished)
        QTimer.singleShot(500, self.start_agent_session)

    def select_devices(self):
        dialog = DeviceSelectionDialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.selected_input_device, self.selected_output_device = dialog.get_selection()
        else:
            # Fallback to defaults if dialog is cancelled
            input_devices, output_devices = get_device_choices()
            self.selected_input_device = input_devices[0] if input_devices else None
            self.selected_output_device = output_devices[0] if output_devices else None

    def start_agent_session(self):
        """Start the agent session in a separate process"""
        try:
            logger = logging.getLogger(__name__)
            
            if hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive():
                logger.info("Terminating existing agent process")
                self.agent_process.terminate()
                self.agent_process.join(timeout=1.0)
            
            self.agent_process = multiprocessing.Process(
                target=run_agent_session,
                args=(self.settings, self.selected_input_device, self.selected_output_device),
                daemon=False
            )
            
            self.agent_process.start()
            logger.info(f"Agent process started with PID: {self.agent_process.pid}")
            
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error starting agent session process: {e}")

    def initialize_voice_box(self):
        """Initialize and show the voice box window"""
        if self.voice_box:
            self.voice_box.show_window()
            self.voice_box.on_sound_started()

    def _cleanup_agent_process(self):
        """Clean up the agent process with proper signal handling"""
        logger = logging.getLogger(__name__)
        
        if hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive():
            logger.info("Terminating agent process")
            try:
                # Send SIGTERM for graceful shutdown
                os.kill(self.agent_process.pid, signal.SIGTERM)
                
                # Give more time for graceful shutdown (5 seconds instead of 2)
                self.agent_process.join(timeout=5.0)
                
                if self.agent_process.is_alive():
                    logger.warning("Agent process didn't terminate gracefully, forcing termination")
                    self.agent_process.terminate()
                    self.agent_process.join(timeout=1.0)
                    
                    if self.agent_process.is_alive():
                        logger.warning("Agent process still not terminated, using SIGKILL")
                        os.kill(self.agent_process.pid, signal.SIGKILL)
                        self.agent_process.join(timeout=1.0)
            except Exception as e:
                logger.error(f"Error stopping agent process: {e}")
            
            # Close the process connection completely to release resources
            if hasattr(self.agent_process, 'close'):
                try:
                    self.agent_process.close()
                except:
                    pass
                    
            # Clear the reference to help garbage collection
            self.agent_process = None
            
            logger.info("Agent process cleanup completed")
            
            # Force garbage collection to clean up remaining resources
            gc.collect()

    def quit(self):
        """Clean up resources and quit the application"""
        logger = logging.getLogger(__name__)
        
        if self._quitting:
            return
            
        self._quitting = True
        
        try:
            # First cleanup agent process
            self._cleanup_agent_process()
            
            # Save any necessary state
            if hasattr(self, 'oracle_window'):
                self.oracle_window.save_listening_state()
            
            # Emit signal to stop sound playback
            signal_manager.stop_sound_player.emit()
            
            # Stop action handler
            if self.action_handler:
                self.action_handler.stop()

            # Wait for thread pool tasks to complete
            # Use a shorter timeout (2 seconds instead of 5)
            QThreadPool.globalInstance().waitForDone(2000)
            
            # Process any pending events before closing windows
            self.processEvents()
            
            # Now close all windows
            for window in self.topLevelWindows():
                window.close()
                
            # Process events one more time to handle window closing
            self.processEvents()
            
            # Try to clean up any multiprocessing queues, but safely handle errors
            try:
                # Import here to avoid any module level reference issues
                import multiprocessing.queues
                # Find all queue objects and close them
                for obj in gc.get_objects():
                    if isinstance(obj, multiprocessing.queues.Queue):
                        try:
                            obj.close()
                            obj.join_thread()
                        except:
                            pass
            except Exception as e:
                logger.warning(f"Error during queue cleanup: {e}")
            
        except Exception as e:
            logger.error(f"Error during application shutdown: {e}")
        finally:
            # Add a small delay to allow cleanup
            time.sleep(0.5)
            
            # Force garbage collection one more time
            gc.collect()
            
            # Now quit
            super().quit()

    def _on_settings_saved(self):
        # Try to duck and stop playback
        try:
            # Try to access playback via agent session or voice box
            if hasattr(self, 'voice_box') and hasattr(self.voice_box, 'playback'):
                pb = self.voice_box.playback
            elif hasattr(self, 'oracle_window') and hasattr(self.oracle_window, 'playback'):
                pb = self.oracle_window.playback
            elif hasattr(self, 'agent_process') and hasattr(self.agent_process, 'playback'):
                pb = self.agent_process.playback
            else:
                pb = None
            if pb:
                pb.duck_volume()
                pb.stop_playback()
                pb.clear_playlist()
                print("[DEBUG] Playback ducked and reset on settings save.")
            else:
                print("[DEBUG] No playback instance found to duck/reset on settings save.")
        except Exception as e:
            print(f"[DEBUG] Error ducking/resetting playback on settings save: {e}")

# ===========================================
# 6. Application Entry Point
# ===========================================
def run():
    """Main application entry point with error handling"""
    if sys.platform == 'darwin':
        multiprocessing.set_start_method('spawn')
    
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting application")
    
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    app = Application(sys.argv)
    
    try:
        def exception_handler(exc_type, exc_value, exc_traceback):
            if exc_type == sounddevice.PortAudioError and "PortAudio not initialized" in str(exc_value):
                logger.info("Suppressing PortAudio termination error")
                return
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
        
        sys.excepthook = exception_handler
        sys.exit(app.exec())
    except Exception as e:
        if "PortAudio not initialized" in str(e):
            logger.info("Suppressing PortAudio termination error")
            sys.exit(0)
        raise

if __name__ == "__main__":
    run()
