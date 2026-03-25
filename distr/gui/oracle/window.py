from PyQt6.QtCore import QTimer, QParallelAnimationGroup, QPropertyAnimation, QEasingCurve, Qt, QPoint, pyqtProperty
from distr.core.utils import load_settings_from_db, save_settings_to_db, get_screens_hash
from distr.core.paths import AVATARS_DIR
from distr.core.db import get_session, ScreenPosition, Chat
from distr.core.signals import signal_manager
from distr.core.skin_config import SkinConfig
from distr.core.skin_discovery import get_skin_by_name
from distr.core.skin_migration import migrate_selected_oracle
from distr.gui.oracle.animation_player import AnimationPlayer
from distr.gui.oracle.chat_bubble import ChatBubbleWidget
from distr.gui.oracle.event_dispatcher import EventHookDispatcher
from distr.gui.oracle.file_drop import FileDropMixin
from distr.gui.oracle.glow_engine import GlowEngine
from distr.gui.oracle.menu import MenuTrayMixin
from distr.gui.oracle.lifecycle import LifecycleMixin
from distr.gui.oracle.render_strategy import create_renderer, RenderStrategy
from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtWidgets import QApplication
import hashlib
import logging
import os
import platform


logger = logging.getLogger(__name__)


class RoundContainer(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._round = True  # True for oracle (ellipse), False for avatar (square/transparent)

    def set_round(self, is_round: bool):
        self._round = is_round
        if is_round:
            # Reapply ellipse mask
            path = QtGui.QPainterPath()
            path.addEllipse(0, 0, self.width(), self.height())
            self.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))
        else:
            self.clearMask()
        self.update()

    def paintEvent(self, event):
        if not self._round:
            return  # Transparent — nothing to paint
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(QtGui.QColor(255, 255, 255))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())

    def resizeEvent(self, event):
        if not self._round:
            self.clearMask()
            return
        path = QtGui.QPainterPath()
        path.addEllipse(0, 0, self.width(), self.height())
        mask = QtGui.QRegion(path.toFillPolygon().toPolygon())
        self.setMask(mask)

class OracleWindow(FileDropMixin, MenuTrayMixin, LifecycleMixin, QtWidgets.QMainWindow):

    def __init__(self, about_window, player_window, chat_manager, eula_window=None, parent=None):
        super().__init__(parent)
        self._updating_menu = False
        self.is_exiting = False  # Flag to track exit state

        # Check for DEBUG environment variable
        debug_env = os.environ.get('DEBUG', '').strip().upper()
        self.debug_mode = debug_env == 'TRUE'
        
        self.settings = load_settings_from_db()
        
        # Initialize size from settings
        self.content_size = self.settings.get('sphere_size', 120)  # Default to 120px
        self.shadow_size = int(self.content_size * 0.022)  # ~3px at 120px
        self.stroke_width = int(self.content_size * 0.033)  # ~4px at 120px
        
        self.total_size = self.content_size + 2 * (self.shadow_size + self.stroke_width)
        
        # Setup window flags and attributes
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Enable drag and drop
        self.setAcceptDrops(True)
        
        # Add screen change detection
        self.screen_watcher = QTimer()
        self.screen_watcher.timeout.connect(self.check_screen_changes)
        self.screen_watcher.start(1000)  # Check every second
        self.current_screens_hash = get_screens_hash()
        logging.debug(f"Oracle init - Current screens hash: {self.current_screens_hash}")
        
        self.player_window = player_window
        self.chat_manager = chat_manager
        self.about_window = about_window
        self.eula_window = eula_window  # Will be set after EULA window is created
        # Connect the OracleWindow's move event to trigger PlayerWindow position update
        self.moveEvent = self.on_move_event

        signal_manager.change_oracle.connect(self.cycle_oracle)
        signal_manager.show_oracle.connect(self.show_oracle)
        signal_manager.hide_oracle.connect(self.hide_oracle)

        signal_manager.enable_tray.connect(self.enable_tray)
        signal_manager.disable_tray.connect(self.disable_tray)
        signal_manager.enable_hands_free.connect(self.enable_hands_free)
        signal_manager.disable_hands_free.connect(self.disable_hands_free)
        signal_manager.hands_free_mode_changed.connect(self._on_hands_free_mode_changed)
        
        # Connect EULA acceptance signal to update menu
        signal_manager.eula_accepted.connect(self.on_eula_accepted)
        
        # Connect to STT state signals - GUI reacts to STT, not controls it
        signal_manager.stt_ready.connect(self.on_stt_ready)
        signal_manager.stt_capture_started.connect(self.on_stt_capture_started)
        signal_manager.stt_capture_stopped.connect(self.on_stt_capture_stopped)
        signal_manager.stt_hands_free_glow_on.connect(self.on_hands_free_glow_on)
        signal_manager.stt_hands_free_glow_off.connect(self.on_hands_free_glow_off)
        
        # Reset stt_ready when agent reloads (new STT service needs to initialize)
        signal_manager.agent_reload_started.connect(self.on_agent_reload)
        
        # Connect to dictation signals
        signal_manager.dictation_started.connect(self.on_dictation_started)
        signal_manager.dictation_stopped.connect(self.on_dictation_stopped)
        
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        self._shadow_color = QtGui.QColor(0, 0, 0, 100)

        # Legacy animation group — kept as empty stub for lifecycle.py cleanup
        self.animation_group = QParallelAnimationGroup(self)

        screen = QtWidgets.QApplication.primaryScreen().geometry()
        x_position = screen.width() - self.total_size - 40
        y_position = (screen.height() - self.total_size) // 2

        self.setGeometry(x_position, y_position, self.total_size, self.total_size)

        self.dragging = False
        self.offset = QtCore.QPoint()
        
        # Hold-to-talk state
        self.hold_to_talk_active = False
        self.ptt_pulse_timer = QTimer()  # kept for stop() calls in drag/release handlers
        self.ptt_requested = False
        self.stt_ready = False
        
        # Dictation state
        self.is_dictating = False
        self._hands_free_before_dictation = False  # Track hands-free state before dictation
        
        # Delay timer for PTT interrupt - prevents accidental interrupts from quick clicks/double-clicks
        self.ptt_delay_timer = QTimer()
        self.ptt_delay_timer.setSingleShot(True)  # Only fire once
        self.ptt_delay_timer.timeout.connect(self._on_ptt_delay_complete)
        self.ptt_delay_ms = 300  # Delay in milliseconds before sending interrupt

        self.round_container = RoundContainer(self)
        self.round_container.setGeometry(self.shadow_size + self.stroke_width, 
                                         self.shadow_size + self.stroke_width, 
                                         self.content_size, 
                                         self.content_size)
        # Make round_container transparent to mouse events so they pass through to OracleWindow
        self.round_container.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.gif_label = QtWidgets.QLabel(self.round_container)
        self.gif_label.setGeometry(0, 0, self.content_size, self.content_size)
        self.gif_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        # Don't use setScaledContents — frame handlers do manual scaling/cropping
        # Make gif_label transparent to mouse events so they pass through to OracleWindow
        self.gif_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.movie = None  # Will be set by load_oracle_animation if needed

        # Initialize state variables BEFORE creating menu
        self.is_listening = self.settings.get('last_listening_state', True)
        self.is_hands_free = self.settings.get('hands_free_mode', True)

        # Initialize menu items before creating menu
        self.menu = None
        self.chat_id_menu_item = None
        
        # Connect to chat manager signals with proper order
        self.chat_manager = chat_manager
        self.chat_manager.current_chat_changed.connect(self.update_chat_id_menu)
        
        # Also connect to global signal manager for cross-component updates
        signal_manager.current_chat_changed.connect(self.update_chat_id_menu)
        
        # Create the shared menu first
        self.menu = self.create_menu()
        
        # Then create tray icon with the menu
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        self.create_tray_icon()
        
        # Update initial chat ID display
        current_chat_id = self.chat_manager.get_current_chat()
        if current_chat_id:
            QTimer.singleShot(0, lambda: self.update_chat_id_menu(current_chat_id))

        self.oracle_visible = True
        
        # ── SKIN-DRIVEN COMPONENTS ──────────────────────────────────────
        # Active skin config (loaded from skin.json)
        self._skin_config = None  # type: SkinConfig | None
        self._skin_folder = "oracle"  # current skin folder name

        # Instantiate skin-driven components
        self._glow_engine = GlowEngine(self)
        self._glow_engine.glow_updated.connect(self._on_glow_updated)

        self._animation_player = AnimationPlayer(self)
        self._animation_player.frame_ready.connect(self._on_skin_frame_ready)

        # WebM view for avatar skins (uses QWebEngineView for VP9 alpha transparency)
        self._webm_view = None  # Created lazily when an avatar skin is loaded

        self._chat_bubble = ChatBubbleWidget()

        self._event_dispatcher = EventHookDispatcher(signal_manager, self)
        self._event_dispatcher.event_hook_fired.connect(self._on_event_hook_fired)

        self._render_strategy = None  # type: RenderStrategy | None

        # Load skin config on init
        self._load_skin_config()

        # Connect the direct oracle change signal (skin change from settings)
        signal_manager.direct_oracle_change.connect(self._on_direct_oracle_change)

        # Connect dispatcher signals now that signal_manager is available
        self._event_dispatcher.connect_signals()

        if self.settings.get('restore_position'):
            self.restore_screen_position()
        else:
            # If restore_position is disabled, use default position (60px left of center)
            self.set_default_position()

        self._recording_just_stopped = False  # Flag to prevent menu after stopping recording
        
        # Connect to recording signals
        signal_manager.action_recording_started.connect(self._on_recording_started)
        signal_manager.action_recording_stopped.connect(self._on_recording_stopped)

        # Connect position and size signals
        signal_manager.oracle_position_changed.connect(self.handle_position_change)
        # signal_manager.sphere_size_changed.connect(self.update_sphere_size)

        self.show()
        self.raise_()
        self.activateWindow()
        
        # On macOS, ensure the app comes to front
        if platform.system() == 'Darwin':
            QTimer.singleShot(100, lambda: (self.raise_(), self.activateWindow()))
        
        # Position player window after oracle is shown and settled
        QTimer.singleShot(500, self._position_player_after_show)

        signal_manager.oracle_size_changed.connect(self.update_size)

        # Initialize the state properly
        self.initialize_listening_state()

    def initialize_listening_state(self):
        """Initialize the listening and hands-free states based on saved settings."""
        startup_state = self.settings.get('startup_listening_state', 'remember')
        
        if startup_state == 'remember':
            # Use the last saved state
            should_listen = self.settings.get('last_listening_state', True)
        elif startup_state == 'stop':
            should_listen = False
        elif startup_state == 'start':
            should_listen = True
        else:
            should_listen = True  # Default to listening if something goes wrong
        
        # Initialize hands-free state
        should_hands_free = self.settings.get('hands_free_mode', True)
        
        # Update the UI and states
        if should_listen:
            self.enable_tray()
        else:
            self.disable_tray()
        
        if should_hands_free:
            self.enable_hands_free()
        else:
            self.disable_hands_free()
        
        logging.info(f"Initialized listening state: {should_listen}")
        logging.info(f"Initialized hands-free state: {should_hands_free} (PTT mode: {not should_hands_free})")

    # ------------------------------------------------------------------
    # Skin-driven component methods
    # ------------------------------------------------------------------

    def _load_skin_config(self):
        """Load the active skin config from settings, run migration, set up components."""
        selected = self.settings.get('selected_oracle', '')
        migrated = migrate_selected_oracle(selected)
        self._skin_folder = migrated

        result = get_skin_by_name(AVATARS_DIR, migrated)
        if result is not None:
            _folder, config = result
            self._skin_config = config
        else:
            # Fallback to oracle skin
            logger.warning("Skin '%s' not found, falling back to 'oracle'", migrated)
            self._skin_folder = "oracle"
            result = get_skin_by_name(AVATARS_DIR, "oracle")
            if result is not None:
                _folder, config = result
                self._skin_config = config
            else:
                logger.error("Default oracle skin not found — skin system disabled")
                self._skin_config = None
                return

        # Set up renderer
        self._render_strategy = create_renderer(self._skin_config)

        # Apply geometry (padding, mask, container shape) for the skin type
        self._apply_skin_geometry()

        # Configure dispatcher with the loaded config
        self._event_dispatcher.set_skin_config(self._skin_config)

        # Load the idle animation
        idle_resp = self._skin_config.events.get("idle")
        if idle_resp:
            self._play_animation(idle_resp)

        logger.info("Loaded skin config: %s (type=%s)", self._skin_config.name, self._skin_config.type)

    def _on_direct_oracle_change(self, skin_name: str):
        """Handle skin change from settings — reload config and recreate components."""
        logger.info("Direct oracle change to skin: %s", skin_name)
        migrated = migrate_selected_oracle(skin_name)
        self._skin_folder = migrated

        result = get_skin_by_name(AVATARS_DIR, migrated)
        if result is None:
            logger.warning("Skin '%s' not found on change, ignoring", migrated)
            return

        _folder, config = result
        self._skin_config = config

        # Recreate renderer
        self._render_strategy = create_renderer(self._skin_config)

        # Update dispatcher config
        self._event_dispatcher.set_skin_config(self._skin_config)

        # Stop current glow and animation
        self._glow_engine.stop()
        self._stop_all_animation()

        # Load idle animation for the new skin
        idle_resp = self._skin_config.events.get("idle")
        if idle_resp:
            self._play_animation(idle_resp)

        # Reset glow visual
        self._shadow_color = QtGui.QColor(0, 0, 0, 50)

        # Apply geometry (padding, mask, container shape) for the new skin type
        self._apply_skin_geometry()

        self.update()

        logger.info("Switched to skin: %s (type=%s)", self._skin_config.name, self._skin_config.type)

    def _on_event_hook_fired(self, new_hook: str, previous_hook: str):
        """Execute the Event_Response for the newly fired hook.

        This is the main integration point — when the EventHookDispatcher
        fires a hook, we look up the Event_Response and apply all its fields:
        animation, glow, show_player, show_chat_bubble.
        """
        if self._skin_config is None:
            return

        response = self._event_dispatcher.get_event_response(new_hook)
        if response is None:
            logger.info("[ORACLE] No Event_Response for hook '%s' — keeping current state", new_hook)
            return

        logger.info("[ORACLE] Executing hook '%s' (prev='%s') → animation=%s, glow=%s",
                     new_hook, previous_hook, response.animation, response.glow)

        # 1. Check for transition animation
        transition_file = self._event_dispatcher.get_transition(previous_hook, new_hook)
        if transition_file:
            trans_path = os.path.join(AVATARS_DIR, self._skin_folder, transition_file)
            if os.path.exists(trans_path):
                # Play transition first, then switch to main animation
                # For now, play transition inline (could be async later)
                logger.debug("Playing transition: %s", transition_file)

        # 2. Switch main animation (skip if same file already playing)
        anim_path = os.path.join(AVATARS_DIR, self._skin_folder, response.animation)
        current_anim = getattr(self, '_current_anim_path', None)
        if anim_path != current_anim:
            self._play_animation(response)

        # 3. Apply glow via GlowEngine
        self._glow_engine.apply(response)

        # 4. Show player window (hiding is handled by the event queue on TTS completion)
        if response.show_player:
            if hasattr(self, 'player_window') and self.player_window:
                self.position_player_window()
                signal_manager.show_player_window.emit()

        # 5. Show/hide chat bubble
        if response.show_chat_bubble:
            self._chat_bubble.show_text("...")
            self._chat_bubble.reposition(self.geometry())
        else:
            self._chat_bubble.hide_bubble()

        # 6. Update tray icon based on tray_icon field
        if response.tray_icon and response.tray_icon != "default":
            self._update_tray_icon_for_event(response.tray_icon)

    def _on_glow_updated(self, color_tuple, alpha):
        """Receive glow tick from GlowEngine and repaint the glow ring."""
        r, g, b = color_tuple
        self._shadow_color = QtGui.QColor(r, g, b, int(255 * alpha))
        self.update()

    def _on_skin_frame_ready(self, pixmap):
        """Receive a frame from the AnimationPlayer and display it."""
        if pixmap.isNull() or self.content_size <= 0:
            return

        scale = 1.0
        offset_x = 0
        offset_y = 0
        if self._skin_config and self._skin_config.rendering:
            r = self._skin_config.rendering
            scale = r.image_scale
            offset_x = r.image_offset_x
            offset_y = r.image_offset_y

        if scale <= 1.0:
            fitted = pixmap.scaled(
                self.content_size, self.content_size,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            self.gif_label.setPixmap(fitted)
        else:
            scaled_size = int(self.content_size + (self.content_size * (scale - 1.0)))
            scaled = pixmap.scaled(
                scaled_size, scaled_size,
                QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            center = scaled.rect().center()
            center += QtCore.QPoint(offset_x, offset_y)
            target_rect = QtCore.QRect(0, 0, self.content_size, self.content_size)
            target_rect.moveCenter(center)
            target_rect = target_rect.intersected(scaled.rect())
            cropped = scaled.copy(target_rect)
            self.gif_label.setPixmap(cropped)


    def _stop_legacy_movie(self):
        """Stop and disconnect the legacy QMovie so it doesn't fight with AnimationPlayer."""
        if hasattr(self, 'movie') and self.movie:
            try:
                self.movie.stop()
                try:
                    self.movie.frameChanged.disconnect()
                except TypeError:
                    pass
            except Exception as e:
                logger.warning("Error stopping legacy movie: %s", e)
            self.gif_label.setMovie(None)
            self.movie = None

    def _stop_all_animation(self):
        """Stop all animation sources (AnimationPlayer, WebMView, legacy QMovie)."""
        self._animation_player.stop()
        self._stop_legacy_movie()
        if self._webm_view is not None:
            self._webm_view.stop()
            self._webm_view.hide()
        self._current_anim_path = None

    def _play_animation(self, response):
        """Play the animation from an EventResponse.

        Passes chroma_key from the skin rendering config to the AnimationPlayer
        so WebM frames get background removal at extraction time.
        """
        anim_path = os.path.join(AVATARS_DIR, self._skin_folder, response.animation)
        if not os.path.exists(anim_path):
            return

        # Stop any WebMView if it was previously used
        if self._webm_view is not None:
            self._webm_view.stop()
            self._webm_view.hide()

        self.gif_label.show()
        self._stop_legacy_movie()

        # Get chroma-key from skin config
        chroma_key = None
        chroma_threshold = 35
        if self._skin_config and self._skin_config.rendering:
            chroma_key = self._skin_config.rendering.chroma_key
            chroma_threshold = self._skin_config.rendering.chroma_threshold

        self._animation_player.set_size(self.content_size, self.content_size)
        self._animation_player.load(
            anim_path,
            playback=response.playback,
            chroma_key=chroma_key,
            chroma_threshold=chroma_threshold,
        )
        self._animation_player.play()
        self._current_anim_path = anim_path

    def _apply_skin_geometry(self):
        """Recalculate padding, window size, and container geometry based on skin config.

        Reads rendering config fields (border, shadow, shape) to determine
        padding, mask shape, container clipping, and background transparency.
        """
        if self._skin_config is None:
            return

        r = self._skin_config.rendering
        has_border = r.border
        has_shadow = r.shadow
        is_round = r.shape == "round"

        if has_border or has_shadow:
            self.shadow_size = int(self.content_size * 0.022)
            self.stroke_width = int(self.content_size * 0.033)
        else:
            self.shadow_size = 0
            self.stroke_width = 0

        self.total_size = self.content_size + 2 * (self.shadow_size + self.stroke_width)

        # Resize window
        self.setFixedSize(self.total_size, self.total_size)

        # Reposition container and label
        offset = self.shadow_size + self.stroke_width
        self.round_container.setGeometry(offset, offset, self.content_size, self.content_size)
        self.gif_label.setGeometry(0, 0, self.content_size, self.content_size)

        # Resize WebMView if it exists
        if self._webm_view is not None:
            self._webm_view.set_size(self.content_size, self.content_size)

        # Update round container shape (drives ellipse clip vs no clip)
        self.round_container.set_round(is_round)

        # Transparent label background when skin config says no border
        if not has_border:
            self.gif_label.setStyleSheet("background: transparent;")
        else:
            self.gif_label.setStyleSheet("")

        # Apply mask
        if self._render_strategy is not None:
            mask = self._render_strategy.create_mask(self.total_size, self.total_size)
            self.setMask(mask)

    def _update_tray_icon_for_event(self, tray_icon_name: str):
        """Update tray icon based on Event_Response tray_icon field."""
        from distr.core.paths import ICONS_DIR
        icon_map = {
            "recording": "tray-recording.png",
            "disabled": "tray-disabled.png",
            "default": "tray.png",
        }
        icon_file = icon_map.get(tray_icon_name, "tray.png")
        icon_path = os.path.join(ICONS_DIR, icon_file)
        if os.path.exists(icon_path) and hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.setIcon(QtGui.QIcon(icon_path))

    def enable_tray(self):
        """Enable listening and update tray icon (respects recording state)"""
        self.is_listening = True
        self.listen_action.setChecked(True)
        self.listen_action.setText("Listening")
        signal_manager.voice_set_is_listening.emit(True)
        self._update_tray_icon()
        self.save_listening_state()

    def disable_tray(self):
        """Disable listening and update tray icon (respects recording state)"""
        self.is_listening = False
        self.listen_action.setChecked(False)
        self.listen_action.setText("Not Listening")
        signal_manager.voice_set_is_listening.emit(False)
        # Fire idle hook to reset visuals via skin system
        self._event_dispatcher.fire_hook("idle")
        self._update_tray_icon()
        self.save_listening_state()

    def save_listening_state(self):
        """Save the current listening state to settings"""
        try:
            settings = load_settings_from_db()
            settings['last_listening_state'] = self.is_listening
            save_settings_to_db(settings)
            logging.debug(f"Saved listening state: {self.is_listening}")
        except Exception as e:
            logging.error(f"Error saving listening state: {e}")

    def enable_hands_free(self):
        """Enable hands-free mode"""
        if not self.is_listening:
            logging.warning("Cannot enable hands-free mode when listening is disabled")
            return
        self.is_hands_free = True
        if hasattr(self, 'hands_free_action'):
            self.hands_free_action.setChecked(True)
            self.hands_free_action.setText("Hands-Free Mode: ON")
        signal_manager.hands_free_mode_changed.emit(True)
        self.save_hands_free_state()

    def disable_hands_free(self):
        """Disable hands-free mode"""
        self.is_hands_free = False
        if hasattr(self, 'hands_free_action'):
            self.hands_free_action.setChecked(False)
            self.hands_free_action.setText("Hands-Free Mode: OFF")
        # Revert the hands-free hook now that the mode is off
        self._event_dispatcher.revert_hook("hands_free_listening")
        signal_manager.hands_free_mode_changed.emit(False)
        self.save_hands_free_state()

    def save_hands_free_state(self):
        """Save the current hands-free state to settings"""
        try:
            settings = load_settings_from_db()
            settings['hands_free_mode'] = self.is_hands_free
            save_settings_to_db(settings)
            logging.debug(f"Saved hands-free state: {self.is_hands_free}")
        except Exception as e:
            logging.error(f"Error saving hands-free state: {e}")

    def toggle_hands_free(self):
        """Toggle hands-free mode"""
        if self.hands_free_action.isChecked():
            self.enable_hands_free()
        else:
            self.disable_hands_free()
        self.save_hands_free_state()

    def start_hold_to_talk(self):
        """
        Request STT to start capturing (PTT activated).
        
        When mouse is clicked down, this triggers:
        1. Visual feedback immediately (UI update)
        2. After a short delay (to prevent double-click interrupts), sends:
           - STT interruption (via stt_service.set_ptt_active(True))
           - TTS/LLM interruption (InterruptionFrame sent to KILL audio)
        
        ONLY works when hands-free mode is OFF (PTT mode).
        When hands-free mode is ON, VAD handles interruptions automatically.
        """
        logging.info(f"[ORACLE] start_hold_to_talk called: hands_free={self.is_hands_free}, dragging={self.dragging}")
        
        # Check if agent process is alive
        app = QtWidgets.QApplication.instance()
        if hasattr(app, 'agent_process'):
            if app.agent_process is None or not app.agent_process.is_alive():
                logging.warning("[ORACLE] Agent process is dead or missing. Reloading agent session...")
                self.stt_ready = False  # Reset STT ready state before reload
                app.reload_agent_session()
                return
        
        # Disable PTT if Hands-Free is active - VAD handles interruptions in hands-free mode
        if self.is_hands_free:
            logging.info("[ORACLE] Push-to-talk ignored - Hands-Free mode is active (VAD handles interruptions)")
            return

        # Check if STT service has finished loading (model ready)
        if not self.stt_ready:
            logging.warning("[ORACLE] Push-to-talk ignored - STT service not ready yet (model still loading or agent reloading)")
            return

        if self.ptt_requested:
            logging.info("[ORACLE] Push-to-talk ignored - PTT already requested (stuck state?)")
            return
        if not self.is_listening or self.dragging:
            logging.info(f"[ORACLE] Push-to-talk NOT requested - conditions not met (listening={self.is_listening}, dragging={self.dragging})")
            return
        
        # Visual feedback via skin system
        logging.info("[ORACLE] PTT: firing ptt_active hook")
        self.hold_to_talk_active = True
        self._event_dispatcher.fire_hook("ptt_active")
        
        # Start delay timer - only emit interrupt signal after delay completes
        logging.info(f"[ORACLE] Starting PTT delay timer ({self.ptt_delay_ms}ms) before sending interrupt")
        self.ptt_delay_timer.start(self.ptt_delay_ms)
    
    def _on_ptt_delay_complete(self):
        """
        Called when PTT delay timer completes - actually sends the interrupt signal.
        Only called if the mouse button is still held down after the delay.
        """
        # Check if we're still in a valid state to send the interrupt
        if self.ptt_requested:
            # Already sent, ignore
            return
        if self.dragging:
            # User started dragging, don't send interrupt
            logging.info("[ORACLE] PTT delay completed but dragging started - not sending interrupt")
            return
        
        # Emit signal that triggers:
        # - STT interruption (via stt_service.set_ptt_active(True))
        # - TTS/LLM interruption (InterruptionFrame sent to KILL audio)
        # ONLY when hands-free mode is OFF (PTT mode)
        logging.info(f"[ORACLE] PTT delay completed - emitting push_to_talk_start (will send InterruptionFrame to interrupt)")
        signal_manager.push_to_talk_start.emit()
        self.ptt_requested = True
        logging.info("[ORACLE] Sent push_to_talk_start request to STT")

    def stop_hold_to_talk(self):
        """Request STT to stop capturing (PTT released)."""
        logging.info(f"[ORACLE] stop_hold_to_talk: hold_to_talk={self.hold_to_talk_active}, "
                     f"ptt_requested={self.ptt_requested}, current_hook={self._event_dispatcher.get_current_hook()}")
        
        # Cancel delay timer if still running
        if self.ptt_delay_timer.isActive():
            logging.info("[ORACLE] stop_hold_to_talk: cancelling delay timer")
            self.ptt_delay_timer.stop()
        
        # Emit stop signal if PTT was actually requested
        if self.ptt_requested:
            self.ptt_requested = False
            logging.info("[ORACLE] stop_hold_to_talk: emitting push_to_talk_stop")
            signal_manager.push_to_talk_stop.emit()
        
        # Always clean up PTT state and revert hook
        self._cleanup_ptt()

    def _apply_immediate_release_visual(self):
        """Reset the glow and border instantly when the user releases the mouse."""
        self._cleanup_ptt()

    def _cleanup_ptt(self):
        """Clean up all PTT state — reverts hook, resets flags."""
        current = self._event_dispatcher.get_current_hook()
        prev = self._event_dispatcher.get_previous_hook()
        logging.info(f"[ORACLE] _cleanup_ptt: current_hook={current}, previous_hook={prev}, "
                     f"hold_to_talk={self.hold_to_talk_active}, ptt_requested={self.ptt_requested}")
        self.hold_to_talk_active = False
        # Force revert — if we're on ptt_active, go back to previous
        # If we're on something else (shouldn't happen), force idle
        if current == "ptt_active":
            self._event_dispatcher.force_revert()
            logging.info(f"[ORACLE] _cleanup_ptt: reverted to {self._event_dispatcher.get_current_hook()}")
        self.update()
    
    def on_stt_ready(self):
        """React to STT service being fully initialized (model loaded) - PTT is now safe to use."""
        was_ready = self.stt_ready
        self.stt_ready = True
        # Cancel safety timer if it's running
        if hasattr(self, '_stt_ready_safety_timer') and self._stt_ready_safety_timer.isActive():
            self._stt_ready_safety_timer.stop()
        if not was_ready:
            logging.info("[ORACLE] ✅ STT service ready - PTT is now ENABLED (was disabled)")
        else:
            logging.info("[ORACLE] ✓ STT service ready - PTT already enabled")
    
    def on_agent_reload(self):
        """React to agent session being reloaded - reset STT ready state."""
        logging.info("[ORACLE] Agent reloading - resetting STT ready state (will wait for new stt_ready event)")
        self.stt_ready = False
        self.ptt_requested = False  # Reset stuck PTT state on reload
        # Safety timer: if stt_ready event doesn't arrive within 10s, force-enable PTT
        # This prevents PTT from being permanently blocked if the event is lost
        if not hasattr(self, '_stt_ready_safety_timer'):
            self._stt_ready_safety_timer = QTimer()
            self._stt_ready_safety_timer.setSingleShot(True)
            self._stt_ready_safety_timer.timeout.connect(self._on_stt_ready_safety_timeout)
        self._stt_ready_safety_timer.start(10000)  # 10 seconds

    def _on_stt_ready_safety_timeout(self):
        """Force-enable PTT if stt_ready event was never received after reload."""
        if not self.stt_ready:
            logging.warning("[ORACLE] ⚠️ STT ready safety timeout - force-enabling PTT (stt_ready event was lost)")
            self.stt_ready = True
    
    def on_stt_capture_started(self):
        """React to STT confirming it started capturing - confirm visual feedback"""
        logging.info("[ORACLE] ✓ STT confirmed capture started")
        # Skin system already handling visuals via ptt_active hook
        if not self.hold_to_talk_active:
            self.hold_to_talk_active = True
            self._event_dispatcher.fire_hook("ptt_active")
    
    def on_stt_capture_stopped(self):
        """React to STT confirming it stopped capturing - stop visual feedback"""
        logging.info("[ORACLE] ✓ STT confirmed capture stopped")
        
        # CRITICAL: Only stop the glow if the user has actually released the button
        # If hold_to_talk_active is still True, the user is still holding down PTT
        # and we should keep the glow active (STT might have stopped for other reasons like interruption)
        if not self.hold_to_talk_active:
            # User already released - glow should already be stopped
            logging.info("[ORACLE] User already released PTT - glow already stopped")
            return
        
        # Check if PTT is still requested (user still holding button)
        # If ptt_requested is True, the user is still holding down the button
        # and we should NOT stop the glow
        if self.ptt_requested:
            logging.info("[ORACLE] PTT still active (user holding button) - keeping glow active despite STT stop")
            return
        
        # Only stop if PTT was actually released
        logging.info("[ORACLE] PTT was released - stopping visual pulse")
        self.hold_to_talk_active = False
        self.ptt_pulse_timer.stop()
        logging.info("[ORACLE] Push-to-talk pulsing animation stopped (from STT)")

    def on_hands_free_glow_on(self):
        """Enable the slow glow when STT reports hands-free listening."""
        logging.info("[ORACLE] STT requested hands-free glow ON")
        self._event_dispatcher.fire_hook("hands_free_listening")

    def on_hands_free_glow_off(self):
        """Disable the glow when STT reports hands-free listening off.
        
        Only revert the hook if hands-free mode was actually disabled.
        If still in hands-free mode, this is just a speech pause — keep the hook active.
        """
        logging.info("[ORACLE] STT requested hands-free glow OFF")
        if not self.is_hands_free:
            # Hands-free was disabled — fully revert
            self._event_dispatcher.revert_hook("hands_free_listening")
        else:
            # Still in hands-free mode — just stop the glow, keep the animation
            self._glow_engine.stop()



    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        content_rect = QtCore.QRect(self.shadow_size + self.stroke_width, 
                                    self.shadow_size + self.stroke_width, 
                                    self.content_size, 
                                    self.content_size)

        # Render strategy drives round vs square painting
        if self._render_strategy is not None:
            self._render_strategy.paint(painter, content_rect)

        # Glow ring — only for oracle type skins
        is_oracle = self._skin_config and self._skin_config.type == "oracle"
        if is_oracle and self._shadow_color.alpha() > 0:
            glow_pen = QtGui.QPen(
                QtGui.QColor(self._shadow_color),
                max(1, int(self.stroke_width * 1.5)),
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin
            )
            painter.setPen(glow_pen)
            painter.drawEllipse(content_rect)



    def resizeEvent(self, event):
        if self._render_strategy is not None:
            mask = self._render_strategy.create_mask(self.total_size, self.total_size)
            self.setMask(mask)
        else:
            path = QtGui.QPainterPath()
            path.addEllipse(0, 0, self.total_size, self.total_size)
            self.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))

    def mousePressEvent(self, event):
        logging.info(f"mousePressEvent: button={event.button()}, hands_free={self.is_hands_free}")
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.offset = event.position().toPoint()
            self.dragging = False
            self.start_hold_to_talk()
        elif event.button() == QtCore.Qt.MouseButton.RightButton:
            # Update menu state before showing to ensure it's current
            self._update_recording_menu_state()
            self.menu.exec(event.globalPosition().toPoint())

    def cycle_oracle(self):
        """Cycle to the next oracle GIF animation via the skin system."""
        if not self._check_eula_accepted():
            return
        if self._skin_config is None or self._skin_config.type != "oracle":
            return

        oracle_dir = os.path.join(AVATARS_DIR, "oracle")
        gif_files = sorted(
            [f for f in os.listdir(oracle_dir) if f.endswith('.gif')],
            key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else float('inf')
        )
        if not gif_files:
            return

        current_anim = self._skin_config.events.get("idle", None)
        current_file = current_anim.animation if current_anim else "0.gif"
        try:
            idx = gif_files.index(current_file)
            next_file = gif_files[(idx + 1) % len(gif_files)]
        except ValueError:
            next_file = gif_files[0]

        # Update all events in the config to use the new GIF
        for hook in self._skin_config.events:
            self._skin_config.events[hook].animation = next_file

        # Write updated config to disk
        from distr.core.skin_config import to_json
        skin_json_path = os.path.join(oracle_dir, "skin.json")
        with open(skin_json_path, "w", encoding="utf-8") as f:
            f.write(to_json(self._skin_config))

        # Trigger reload
        self._on_direct_oracle_change("oracle")
        logging.debug(f"Cycled oracle to: {next_file}")


    def mouseMoveEvent(self, event):
        if not self.dragging and self.is_hands_free and hasattr(self, 'offset'):
            move_distance = (event.position().toPoint() - self.offset).manhattanLength()
            if move_distance > 5:
                logging.info(f"[PTT GUI] Switching to drag mode (moved {move_distance}px)")
                self.dragging = True
                if self.ptt_delay_timer.isActive():
                    self.ptt_delay_timer.stop()
                if self.hold_to_talk_active or self.ptt_requested:
                    self._cleanup_ptt()

        if self.dragging:
            if not hasattr(self, '_drag_notified'):
                self._drag_notified = True
            
            new_position = event.globalPosition().toPoint() - self.offset
            self.move(new_position)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            logging.info(f"[ORACLE] mouseReleaseEvent: dragging={self.dragging}, "
                         f"hold_to_talk={self.hold_to_talk_active}, "
                         f"current_hook={self._event_dispatcher.get_current_hook()}")
            if self.ptt_delay_timer.isActive():
                self.ptt_delay_timer.stop()
            if not self.dragging:
                self.stop_hold_to_talk()
            else:
                # Drag ended — emit stop signal if needed, then clean up
                if self.ptt_requested:
                    self.ptt_requested = False
                    signal_manager.push_to_talk_stop.emit()
                self._cleanup_ptt()
            
            # Safety: if hook is still ptt_active after cleanup, force idle
            if self._event_dispatcher.get_current_hook() == "ptt_active":
                logging.warning("[ORACLE] mouseReleaseEvent: hook still ptt_active after cleanup — forcing idle")
                self._event_dispatcher._current_hook = "idle"
                self._event_dispatcher._previous_hook = "idle"
                self._event_dispatcher.event_hook_fired.emit("idle", "ptt_active")
            
            self.dragging = False
            if hasattr(self, '_drag_notified'):
                delattr(self, '_drag_notified')

        if self.settings.get('restore_position'):
            self.save_current_position()


    def on_move_event(self, event):
        super().moveEvent(event)
        # When oracle moves, position the player window directly (we know our position)
        if hasattr(self, 'player_window') and self.player_window:
            self.position_player_window()
    
    def position_player_window(self, animate=False):
        """Position the player window relative to this oracle window"""
        try:
            if not hasattr(self, 'player_window') or not self.player_window:
                return

            if not self.isVisible():
                return

            # CRITICAL: Always use total_size for dimensions - it's calculated from settings and always accurate
            # The window geometry might not be accurate on initial load
            # Defensive programming: check if total_size is properly initialized
            if not hasattr(self, 'total_size'):
                logger.debug("[Oracle] Cannot position player - total_size attribute missing")
                return

            # Check if total_size is a valid integer
            if not isinstance(self.total_size, int) or self.total_size <= 0:
                logger.debug(f"[Oracle] Cannot position player - total_size is invalid: {self.total_size} (type: {type(self.total_size)})")
                return

            # Ensure window is properly sized before positioning
            current_size = self.size()
            if current_size.width() != self.total_size or current_size.height() != self.total_size:
                logger.debug(f"[Oracle] Window size incorrect ({current_size.width()}x{current_size.height()}), setting to total_size: {self.total_size}")
                self.setFixedSize(self.total_size, self.total_size)
                QtWidgets.QApplication.processEvents()

            oracle_width = self.total_size
            oracle_height = self.total_size

            # Get global position of Oracle content top-left - most accurate method
            # mapToGlobal(QPoint(0,0)) returns the screen coordinate of the client area top-left
            try:
                oracle_global_pos = self.mapToGlobal(QtCore.QPoint(0, 0))
                oracle_x = oracle_global_pos.x()
                oracle_y = oracle_global_pos.y()
            except Exception as e:
                logger.error(f"[Oracle] Error getting global position: {e}")
                # Fallback to pos()
                oracle_pos = self.pos()
                oracle_x = oracle_pos.x()
                oracle_y = oracle_pos.y()

            # Calculate center from position and total_size (always accurate)
            oracle_center_y = oracle_y + (oracle_height / 2.0)
            oracle_center = QtCore.QPoint(oracle_x + oracle_width // 2, int(oracle_center_y))

            logger.debug(f"[Oracle] Positioning player: oracle at ({oracle_x}, {oracle_y}), size {oracle_width}x{oracle_height}, center_y={oracle_center_y}")

            # Force fixed size for player window to avoid initialization issues
            # The player window is always 300x60
            player_width = 300
            player_height = 60

            # Get the screen that contains the oracle window
            screen = QApplication.screenAt(oracle_center)
            if not screen:
                screen = QApplication.primaryScreen()

            screen_geo = screen.geometry()

            # Calculate player position: to the right of oracle, vertically centered
            oracle_right = oracle_x + oracle_width
            x = oracle_right + 20

            # Position player vertically centered on oracle
            y = int(oracle_center_y - (player_height / 2.0))

            # If it would go off screen, position to the left instead
            if x + player_width > screen_geo.right():
                x = oracle_x - player_width - 20

            # Ensure within screen bounds
            x = max(screen_geo.left(), min(x, screen_geo.right() - player_width))
            y = max(screen_geo.top(), min(y, screen_geo.bottom() - player_height))

            # Position and size the player window (while it's hidden)
            # using setGeometry is more robust than move() for initial placement
            if animate and self.player_window.isVisible():
                if hasattr(self, 'player_pos_anim'):
                    self.player_pos_anim.stop()

                self.player_pos_anim = QPropertyAnimation(self.player_window, b"pos")
                self.player_pos_anim.setDuration(200)
                self.player_pos_anim.setStartValue(self.player_window.pos())
                self.player_pos_anim.setEndValue(QtCore.QPoint(x, y))
                self.player_pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self.player_pos_anim.start()
            else:
                self.player_window.setGeometry(x, y, player_width, player_height)

            logger.info(f"[Oracle] Positioned player at ({x}, {y}), oracle at ({oracle_x}, {oracle_y}) size {oracle_width}x{oracle_height}, center_y={oracle_center_y}, player_size={player_width}x{player_height}")

        except Exception as e:
            logger.error(f"[Oracle] Error in position_player_window: {e}")
            import traceback
            traceback.print_exc()
            # Continue silently - this is not a critical error

    def show_about_window(self):
        """Show the About window if EULA is accepted"""
        # Check if EULA is accepted
        if not self._check_eula_accepted():
            return
        
        # Position the about window relative to the oracle window
        self.about_window.center_on_screen(self)
        self.about_window.show()
        self.about_window.raise_()
        self.about_window.activateWindow()


    def show_settings_web(self):
        """Open the web-based settings page in the default browser"""
        try:
            from distr.gui.web.server import get_unified_server
            import webbrowser

            unified_server = get_unified_server()
            if unified_server and unified_server.is_ready():
                settings_url = unified_server.get_settings_url()
                webbrowser.open(settings_url)
                logger.info(f"Opened web settings at {settings_url}")
            else:
                logger.warning("Unified server is not ready, cannot open web settings")
                # Show error message to user
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Server Not Ready",
                    "The web server is not ready yet. Please try again in a moment.",
                    QMessageBox.StandardButton.Ok
                )
        except Exception as e:
            logger.error(f"Failed to open web settings: {e}", exc_info=True)

    def _open_web_url(self, path):
        """Open a path on the unified web server in the default browser."""
        try:
            from distr.gui.web.server import get_unified_server
            import webbrowser
            unified_server = get_unified_server()
            if unified_server and unified_server.is_ready():
                url = f"{unified_server.get_url()}{path}" if path.startswith("/") else f"{unified_server.get_url()}/{path}"
                webbrowser.open(url)
                logger.info(f"Opened {url}")
            else:
                logger.warning("Unified server is not ready, cannot open web URL")
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Server Not Ready",
                    "The web server is not ready yet. Please try again in a moment.",
                    QMessageBox.StandardButton.Ok
                )
        except Exception as e:
            logger.error(f"Failed to open web URL: {e}", exc_info=True)

    def show_oracle(self):
        self.oracle_visible = True
        logger.debug(f"Oracle shown. self.isVisible(): {self.isVisible()}, oracle_visible: {self.oracle_visible}")
        QTimer.singleShot(0, self.show)
        QTimer.singleShot(0, self.gif_label.show)
        # Position player window after oracle is shown and settled
        QTimer.singleShot(500, self._position_player_after_show)
    
    def _position_player_after_show(self):
        """Position player window after oracle has been shown and settled"""
        try:
            if self.isVisible() and hasattr(self, 'player_window') and self.player_window:
                # CRITICAL: Ensure window is properly sized before positioning
                # Force the window to use total_size if it's not already set
                if hasattr(self, 'total_size') and isinstance(self.total_size, int) and self.total_size > 0:
                    current_size = self.size()
                    if current_size.width() != self.total_size or current_size.height() != self.total_size:
                        logger.debug(f"[Oracle] Window size incorrect ({current_size.width()}x{current_size.height()}), setting to total_size: {self.total_size}")
                        self.setFixedSize(self.total_size, self.total_size)
                        QApplication.processEvents()
                else:
                    logger.debug(f"[Oracle] Cannot position player - total_size is invalid: {getattr(self, 'total_size', 'missing')}")
                    return

                # Force update to ensure geometry is accurate
                self.update()
                QApplication.processEvents()
                self.repaint()
                QApplication.processEvents()
                # Wait a bit more for geometry to settle
                QTimer.singleShot(100, lambda: self.position_player_window())
            QTimer.singleShot(10, self.update)
            QTimer.singleShot(20, self.update_menu)
        except Exception as e:
            logger.error(f"[Oracle] Error in _position_player_after_show: {e}")
            import traceback
            traceback.print_exc()

    def hide_oracle(self):
        self.oracle_visible = False
        logger.debug(f"Oracle hidden. self.isVisible(): {self.isVisible()}, oracle_visible: {self.oracle_visible}")
        QTimer.singleShot(0, self.hide)
        QTimer.singleShot(10, self.update_menu)

    # Standard window size for settings/about dialogs
    STANDARD_WINDOW_WIDTH = 1000
    STANDARD_WINDOW_HEIGHT = 600
    

    def handle_new_chat(self):
        """Handle New Chat action - creates new chat without opening the chat window."""
        # Check if EULA is accepted
        if not self._check_eula_accepted():
            return
        
        # Send interruption signal to stop TTS/LLM before creating new chat
        # Use interrupt_tts instead of push_to_talk to avoid interfering with STT
        # This interrupts only TTS/LLM, not STT, so STT can continue after chat is created
        try:
            logger.info("Oracle: New chat requested - interrupting TTS/LLM before creating new chat")
            signal_manager.interrupt_tts.emit()
        except Exception as e:
            logger.error(f"Error emitting interrupt_tts signal: {e}")
        
        # Create new chat directly via chat_manager (don't open/show chat window)
        # After this, STT should continue normally without further interruptions
        try:
            if self.chat_manager:
                new_chat_id = self.chat_manager.create_chat("New Conversation", is_new=True)
                logger.info(f"Oracle: Created new chat {new_chat_id} without opening chat window")
            else:
                logger.warning("Oracle: Cannot create new chat - chat_manager not available")
        except Exception as e:
            logger.error(f"Oracle: Error creating new chat: {e}", exc_info=True)
    

    def show_chat_window(self, center_on_screen=False):
        """Open web chat (desktop Chat window removed)."""
        if self._check_eula_accepted():
            self._open_web_url("/chat/")

    def _has_unsubmitted_new_chat(self):
        """Check if there's a new chat that hasn't been submitted yet (no messages)"""
        try:
            session = get_session()
            try:
                # Find any chat that is_new=True and has no content
                unsubmitted_chat = session.query(Chat).filter(
                    Chat.is_new == True,
                    Chat.parent_id.is_(None)
                ).first()
                
                if unsubmitted_chat:
                    # Check if it has any content (filter out hidden children)
                    visible_children = [c for c in (unsubmitted_chat.children or []) if not (hasattr(c, 'is_hidden') and c.is_hidden)] if unsubmitted_chat.children else []
                    has_content = bool(
                        (unsubmitted_chat.input and unsubmitted_chat.input.strip()) or
                        (unsubmitted_chat.response and unsubmitted_chat.response.strip()) or
                        len(visible_children) > 0
                    )
                    # Return True if it's new and has no content
                    return not has_content
                return False
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error checking for unsubmitted new chat: {e}")
            return False

    # Add this new method
    def closeEvent(self, event):
        if self.settings.get('restore_position'):
            self.save_current_position()
        event.accept()

    @pyqtProperty(QtGui.QColor)
    def shadow_color(self):
        return self._shadow_color

    @shadow_color.setter
    def shadow_color(self, color):
        self._shadow_color = color
        self.update()




    


    def reset_color_animation(self):
        """Reset glow to off state immediately."""
        self.animation_group.stop()
        self._glow_engine.stop()
        self._shadow_color = QtGui.QColor(0, 0, 0, 50)
        self.update()


    
    def _trigger_drop_success_glow(self):
        """Trigger file drop success visual via skin system."""
        self._event_dispatcher.fire_hook("file_drop_success")
        # Auto-revert after 4 seconds (flash glow handles its own timing for oracle)
        QTimer.singleShot(4000, lambda: self._event_dispatcher.revert_hook("file_drop_success"))

    

    def check_screen_changes(self):
        """Handle screen configuration changes"""        
        new_hash = get_screens_hash()
        if new_hash != self.current_screens_hash:
            logger = logging.getLogger(__name__)
            logger.info("\n=== Screen Configuration Changed ===")
            logger.info(f"Old hash: {self.current_screens_hash}")
            logger.info(f"New hash: {new_hash}")
            
            # Update screen info cache immediately when hardware changes are detected
            try:
                app = QtWidgets.QApplication.instance()
                if app and hasattr(app, '_update_screen_info_cache'):
                    logger.info("Updating screen info cache due to hardware change")
                    app._update_screen_info_cache()
                else:
                    # Fallback: update cache directly
                    from distr.core.screen_utils import get_all_screens_info, update_screen_info_cache
                    screen_info_list = get_all_screens_info()
                    update_screen_info_cache(screen_info_list)
                    logger.debug(f"Updated screen info cache directly with {len(screen_info_list)} screen(s)")
            except Exception as e:
                logger.error(f"Error updating screen info cache: {e}", exc_info=True)
                        
            # Update hash after saving position
            self.current_screens_hash = new_hash
                
            with get_session() as session:
                position = session.query(ScreenPosition).filter_by(
                    screens_id=self.current_screens_hash,
                ).first()
                                        
                if position:
                    logger.info(f"Restoring oracle position: ({position.pos_x}, {position.pos_y})")
                    self.move(int(position.pos_x), int(position.pos_y))
                    
                    # Update cache again after moving to ensure current screen is correct
                    try:
                        app = QtWidgets.QApplication.instance()
                        if app and hasattr(app, '_update_screen_info_cache'):
                            app._update_screen_info_cache()
                    except Exception as e:
                        logger.debug(f"Error updating cache after move: {e}")


        
    def reload_settings(self):
        self.settings = load_settings_from_db()


    def save_current_position(self):
        """Save the current window position"""
        # Don't save position during exit
        if hasattr(self, 'is_exiting') and self.is_exiting:
            return
            
        pos = self.pos()
        current_screen = QApplication.screenAt(pos + QPoint(self.total_size // 2, self.total_size // 2))
        
        if not current_screen:
            logger.debug("Could not determine current screen")
            return
        
        screens_id = get_screens_hash()
                
        with get_session() as session:
            # Only look for screens_id since it's the primary key
            position = session.query(ScreenPosition).filter_by(screens_id=screens_id).first()
            
            if position:
                # Update existing record
                position.screen_name = current_screen.name()
                position.pos_x = pos.x() 
                position.pos_y = pos.y() 
            else:
                # Create new record
                position = ScreenPosition(
                    screens_id=screens_id,
                    screen_name=current_screen.name(),
                    pos_x=pos.x(),
                    pos_y=pos.y()
                )
                session.add(position)
            
            try:
                session.commit()
                logger.debug(f"Saved position for {current_screen.name()} in configuration {screens_id}")
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to save position: {e}")
                raise


    def restore_screen_position(self):
        """Restore the window position from saved settings"""
        logger.debug("\n=== Starting position restoration ===")
        screens_id = get_screens_hash()
        current_screen = QApplication.primaryScreen()  # Start with primary screen
        
        with get_session() as session:
            # Get position for current screen configuration
            position = session.query(ScreenPosition).filter_by(screens_id=screens_id).first()
            
            if position:                                
                logger.debug(f"Found saved position for {position.screen_name}: ({position.pos_x}, {position.pos_y})")
                self.move(int(position.pos_x), int(position.pos_y))
            else:
                logger.debug("No saved position found")
                self.set_default_position()


    def set_default_position(self, screen=None):
        """Set the default position on the specified screen or primary screen"""
        if not screen:
            screen = QApplication.primaryScreen()
        
        screen_geo = screen.geometry()
        # Position at bottom left
        x = screen_geo.left() + 20
        y = screen_geo.bottom() - self.total_size - 20
        self.move(x, y)

    def mouseDoubleClickEvent(self, event):
        """Handle double click behavior based on hands-free mode"""
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.is_hands_free:
                # When hands-free is OFF, double-click enables drag mode
                logging.info("[ORACLE] Double-click detected - resetting border color (non-hands-free)")
                # Reset border colors immediately on double-click
                self._apply_immediate_release_visual()
                self.dragging = True
                self.offset = event.position().toPoint()
            else:
                # When hands-free is ON, double-click opens chat window
                # Don't reset glow - keep the animation running
                logging.info("[ORACLE] Double-click detected - opening chat window (hands-free mode, keeping glow)")
                if self._check_eula_accepted():
                    self.show_chat_window(center_on_screen=True)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def update_chat_id_menu(self, chat_id):
        """Update Oracle menu with chat info"""
        if self._updating_menu:
            return
        
        try:
            self._updating_menu = True
            logger.debug(f"Oracle: Updating menu with chat ID: {chat_id}")
            if chat_id:
                # Convert chat_id to string and create MD5 hash
                chat_id_str = str(chat_id)
                md5_hash = hashlib.md5(chat_id_str.encode()).hexdigest()
                # Take first 6 characters of the hash
                short_hash = md5_hash[:6]
                # Update the shared menu item
                text = f"Chat: #{short_hash}"
                if self.chat_id_menu_item:
                    self.chat_id_menu_item.setText(text)
                    # Force menu update
                    self.menu.update()
                    if self.tray_icon and self.tray_icon.contextMenu():
                        self.tray_icon.contextMenu().update()
            else:
                if self.chat_id_menu_item:
                    self.chat_id_menu_item.setText("No active chat")
        finally:
            self._updating_menu = False

    def handle_position_change(self, position):
        """Handle position changes from settings window"""
        if position == "Custom":
            return  # Don't change position for custom

        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry()
        window_size = self.size()

        positions = {
            "Top Left": (screen_geo.left(), screen_geo.top()),
            "Top Right": (screen_geo.right() - window_size.width(), screen_geo.top()),
            "Middle Left": (screen_geo.left(), screen_geo.center().y() - window_size.height() // 2),
            "Middle Right": (screen_geo.right() - window_size.width(), screen_geo.center().y() - window_size.height() // 2),
            "Bottom Left": (screen_geo.left(), screen_geo.bottom() - window_size.height()),
            "Bottom Right": (screen_geo.right() - window_size.width(), screen_geo.bottom() - window_size.height())
        }

        if position in positions:
            x, y = positions[position]
            self.move(x, y)
            if self.settings.get('restore_position'):
                self.save_current_position()

    def update_size(self, new_size):
        """Update the oracle size while maintaining proportions"""
        self.content_size = new_size
        self.shadow_size = int(new_size * 0.022)
        self.stroke_width = int(new_size * 0.033)
        
        self.total_size = self.content_size + 2 * (self.shadow_size + self.stroke_width)
        
        # Update window size
        self.setFixedSize(self.total_size, self.total_size)
        
        # Update container and label geometries
        self.round_container.setGeometry(
            self.shadow_size + self.stroke_width,
            self.shadow_size + self.stroke_width,
            self.content_size,
            self.content_size
        )
        
        self.gif_label.setGeometry(0, 0, self.content_size, self.content_size)
        
        # Reload the current image to ensure proper scaling
        if hasattr(self, 'current_movie'):
            self.current_movie.setScaledSize(QtCore.QSize(self.content_size, self.content_size))
        
        # Force a repaint
        self.update()
        
        # Save the new size to settings (only place where we save)
        settings = load_settings_from_db()
        settings['sphere_size'] = new_size
        save_settings_to_db(settings)
        logging.debug(f"Updated oracle size to: {new_size}px")
        
        # Reposition player window with animation to keep it centered
        self.position_player_window(animate=True)

    def _check_eula_accepted(self):
        """Check if EULA is accepted and show EULA window if not"""
        settings = load_settings_from_db()
        eula_accepted = settings.get('accepted_eula', False)
        
        if not eula_accepted:
            # Show a message to the user about accepting EULA
            QtWidgets.QMessageBox.information(
                self,
                "EULA Acceptance Required",
                "You need to accept the End User License Agreement to use this feature.\n\nOpening EULA window.",
                QtWidgets.QMessageBox.StandardButton.Ok
            )
            
            # Show EULA window
            if self.eula_window:
                self.eula_window.show()
                self.eula_window.raise_()
                self.eula_window.activateWindow()
            return False
            
        return True
    
    def on_dictation_started(self):
        """Handle dictation started signal"""
        logging.info("[ORACLE] Dictation started")
        self.is_dictating = True
        self._update_dictation_menu_state()
        
        self._hands_free_before_dictation = self.is_hands_free
        if self.is_hands_free:
            self.disable_hands_free()
        
        # Skin system handles the visual (yellow glow for oracle, working.webm for avatars)
        self._event_dispatcher.fire_hook("dictation")
    
    def _on_hands_free_mode_changed(self, enabled: bool):
        """Handle hands-free mode changed signal (from agent during dictation)"""
        if enabled:
            if not self.is_hands_free:
                self.enable_hands_free()
        else:
            if self.is_hands_free:
                self.disable_hands_free()
    
    def on_dictation_stopped(self):
        """Handle dictation stopped signal"""
        logging.info("[ORACLE] Dictation stopped")
        self.is_dictating = False
        self._update_dictation_menu_state()
        
        # Skin system reverts to previous state
        self._event_dispatcher.revert_hook("dictation")
        
        # Restore hands-free mode if it was enabled before dictation
        if self._hands_free_before_dictation and self.is_listening:
            self.is_hands_free = True
            if hasattr(self, 'hands_free_action'):
                self.hands_free_action.setChecked(True)
                self.hands_free_action.setText("Hands-Free Mode: ON")
            signal_manager.hands_free_mode_changed.emit(True)
        
        self._hands_free_before_dictation = False
    
    def stop_dictating(self):
        """Stop dictation from menu"""
        if self.is_dictating:
            # Send command to agent to stop dictation
            # The agent will process this and emit dictation_stopped event
            app = QtWidgets.QApplication.instance()
            if hasattr(app, '_send_command_to_agent'):
                app._send_command_to_agent('stop_dictation', {})
                logging.info("[ORACLE] Stop dictating command sent to agent")
            else:
                # Fallback: emit signal directly (less reliable)
                signal_manager.dictation_stopped.emit()
                logging.info("[ORACLE] Stop dictating requested from menu (fallback)")
    
    def _update_dictation_menu_state(self):
        """Update the dictation menu items based on current dictation state"""
        if hasattr(self, 'stop_dictating_action') and self.stop_dictating_action:
            self.stop_dictating_action.setVisible(self.is_dictating)
            self.stop_dictating_action.setEnabled(self.is_dictating)
    
    def _update_recording_menu_state(self):
        """Update the recording menu items based on current recording state (recorder host)."""
        is_recording = self._is_recording_active()
        if hasattr(self, 'record_action_action') and self.record_action_action:
            self.record_action_action.setVisible(not is_recording)
            self.record_action_action.setEnabled(not is_recording)
        if hasattr(self, 'stop_recording_action') and self.stop_recording_action:
            self.stop_recording_action.setVisible(is_recording)
            self.stop_recording_action.setEnabled(is_recording)
    
    def _on_recording_started(self, action_id):
        """
        Handle recording started signal.
        
        When recording starts:
        - Update tray icon to tray-recording.png
        - Update menu state to show "Stop Recording" option
        """
        logger.info(f"Oracle: Recording started signal received for action {action_id}")
        self._update_recording_menu_state()
        # Update tray icon to show recording state
        self._update_tray_icon()
        # Update tray menu if it exists
        if hasattr(self, 'tray_icon') and self.tray_icon:
            try:
                # Recreate tray menu to update it (menu state changes)
                self.create_tray_icon()
            except Exception as e:
                logger.debug(f"Could not update tray menu: {e}")
    
    def _on_recording_stopped(self, action_id):
        """
        Handle recording stopped signal.
        
        When recording stops:
        - Restore tray icon based on listening state (tray.png if listening, tray-disabled.png if not)
        - Update menu state to show "Start Recording" option
        - Reset the _recording_just_stopped flag after a delay (allows menu on next click)
        """
        logger.info(f"Oracle: Recording stopped signal received for action {action_id}")
        self._update_recording_menu_state()
        # Restore tray icon based on listening state
        self._update_tray_icon()
        # Update tray menu if it exists
        if hasattr(self, 'tray_icon') and self.tray_icon:
            try:
                # Recreate tray menu to update it (menu state changes)
                self.create_tray_icon()
            except Exception as e:
                logger.debug(f"Could not update tray menu: {e}")
        
        # Reset the flag after a delay - this ensures the next click after stopping will show menu
        # The flag is set when user clicks to stop, and reset here after recording actually stops
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(500, lambda: setattr(self, '_recording_just_stopped', False))
    
    def start_recording_action(self):
        """Start recording from context menu; headless recorder host handles it."""
        if not self._check_eula_accepted():
            return
        signal_manager.start_action_recording.emit()
    
    def stop_recording_action_handler(self):
        """Stop recording from context menu; headless recorder host handles it."""
        signal_manager.stop_action_recording.emit()
        self._update_recording_menu_state()
