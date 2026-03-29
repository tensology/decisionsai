from distr.core.paths import IMAGES_DIR, ICONS_DIR
from distr.core.signals import signal_manager
from PyQt6.QtGui import QMovie, QImageReader
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve
import logging
import os
import threading

class PlayerWindow(QtWidgets.QWidget):
    """
    PlayerWindow: Always-on-top floating window for voice activity and controls.
    Formerly VoiceBoxWindow.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.oracle_window = None
        
        self.logger = logging.getLogger(__name__)

        # Initialize animation timer first
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.update_animation)
        self.movie = None  # Initialize movie as None
        
        # Fade-out animation for hiding
        self.fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self.fade_animation.setDuration(300)  # 300ms fade
        self.fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Connect signal using Qt.UniqueConnection to prevent duplicate connections
        self.fade_animation.finished.connect(self._on_fade_finished, QtCore.Qt.ConnectionType.UniqueConnection)
        
        # Guard to prevent _on_fade_finished from being called multiple times
        self._fade_finished_processing = False
        self._fade_finished_lock = threading.Lock()
        
        # Guard to prevent hide() from being called multiple times
        self._hide_processing = False
        self._hide_lock = threading.Lock()
        
        # Guard to prevent hide_window() from being called multiple times
        self._hide_window_processing = False
        self._hide_window_lock = threading.Lock()
        
        # Guard to prevent closeEvent() from being called multiple times
        self._close_event_processing = False
        self._close_event_lock = threading.Lock()
        
        # Set window flags — Tool flag hides from taskbar on Windows
        _flags = (
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint
        )
        import sys
        if sys.platform == 'win32':
            _flags |= Qt.WindowType.Tool
        else:
            _flags |= Qt.WindowType.Window  # macOS needs Window flag for proper layering
        self.setWindowFlags(_flags)
        
        # Critical attributes for window behavior
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_X11DoNotAcceptFocus, True)  # Helps on some systems
        
        # Setup UI components after flags
        self.setup_ui()
        self.setup_voice_graphic()
        self.setup_stop_button()
        
        # Connect signals after UI setup
        self.connect_signals()

    def connect_signals(self):
        # Positioning is handled by Oracle, not by player
        signal_manager.show_player_window.connect(self.show_window)
        # Disconnect first to prevent duplicate connections
        try:
            signal_manager.hide_player_window.disconnect(self.hide_window)
        except (TypeError, RuntimeError):
            pass  # Not connected, that's fine
        signal_manager.hide_player_window.connect(self.hide_window, QtCore.Qt.ConnectionType.UniqueConnection)
        signal_manager.reset_player_window.connect(self.reset)
        signal_manager.player_play.connect(self.on_player_play)
        signal_manager.player_pause.connect(self.on_player_pause)
        signal_manager.player_stop.connect(self.on_player_stop)

    def set_oracle_window(self, oracle_window):
        self.oracle_window = oracle_window
        # Don't position during initialization - oracle will position us when it's ready
        # The oracle will call position_player_window() after it's shown and settled

    def setup_ui(self):
        # Set size
        self.setFixedSize(300, 60)

        # Update font stack to use only system fonts
        self.setStyleSheet("""
            * {
                font-family: Arial, sans-serif;
            }
        """)

        # Create layout
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create and set up the voice container
        self.voice_container = QtWidgets.QWidget(self)
        self.voice_container.setObjectName("voiceContainer")
        self.voice_container.setStyleSheet("""
            #voiceContainer {
                background-color: black;
                border: 1px solid black;
                border-radius: 30px;
            }
        """)
        layout.addWidget(self.voice_container)

    def ensure_visibility(self):
        self.show()
        self.windowHandle().setFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Draw rounded rectangle
        path = QtGui.QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 30, 30)
        painter.setClipPath(path)
        painter.fillPath(path, QtGui.QColor(0, 0, 0))

    def update_position(self):
        """Update position - delegate to Oracle since it knows its own position"""
        if self.oracle_window and hasattr(self.oracle_window, 'position_player_window'):
            self.oracle_window.position_player_window()
        else:
            # Fallback: center on screen if oracle not available
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                screen_geo = screen.geometry()
                x = screen_geo.left() + (screen_geo.width() - 300) // 2
                y = screen_geo.top() + (screen_geo.height() - 60) // 2
                self.move(x, y)
                self.logger.debug(f"[PlayerWindow] Oracle not available, positioned at screen center: ({x}, {y})")


    def setup_voice_graphic(self):
        self.voice_label = QtWidgets.QLabel(self.voice_container)
        self.voice_label.setGeometry(0, 0, 300, 60)
        self.voice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        gif_path = os.path.join(IMAGES_DIR, "voice.gif")        
        reader = QImageReader(gif_path)
        if reader.canRead():
            original_size = reader.size()
            if original_size.isValid() and original_size.height() > 0:
                # Set the height to 150% of the voice box height
                new_height = int(self.voice_label.height() * 2)
                # Calculate the width while maintaining aspect ratio
                new_width = int(new_height * original_size.width() / original_size.height())
                
                self.movie = QMovie(gif_path)
                self.movie.setScaledSize(QSize(new_width, new_height))
                self.voice_label.setMovie(self.movie)
                
                # Center the GIF horizontally and vertically
                x_offset = (self.voice_label.width() - new_width) // 2
                y_offset = ((self.voice_label.height() - new_height) // 2) - 3
                self.voice_label.setGeometry(x_offset, y_offset, new_width, new_height)
                
                self.total_frames = self.movie.frameCount()
            else:
                self.logger.error("Invalid image dimensions: %dx%d", original_size.width(), original_size.height())
                self.voice_label.setText("Invalid Image")
        else:
            self.logger.error("Unable to read image from %s: %s", gif_path, reader.errorString())
            self.voice_label.setText("Image Load Error")
        
        self.voice_label.setStyleSheet("color: white; font-size: 14px;")

    def setup_stop_button(self):
        self.stop_button = QtWidgets.QPushButton(self.voice_container)
        self.stop_button.setFixedSize(32, 32)
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border-radius: 16px;
                border: none;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.15);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.30);
            }
        """)
        
        icon_path = os.path.join(ICONS_DIR, "stop.png")
        self.stop_button.setIcon(QtGui.QIcon(icon_path))
        self.stop_button.setIconSize(QSize(24, 24))
        
        self.stop_button.clicked.connect(self.on_stop_clicked)
        self.stop_button.move(260, 14)

    def update_animation(self):
        """Update GIF animation frame"""
        if not self.movie:
            self.animation_timer.stop()
            return
        
        movie_state = self.movie.state()
        if movie_state == QtGui.QMovie.MovieState.Running:
            self.movie.jumpToNextFrame()
            self.update()
        elif movie_state == QtGui.QMovie.MovieState.Paused:
            # If paused, keep timer running but don't advance frames
            # This prevents the timer from stopping unexpectedly
            pass
        else:
            # Only stop timer if movie is truly stopped/not running
            self.animation_timer.stop()

    def reset(self):
        if self.movie:
            self.movie.stop()
            self.animation_timer.stop()
            if self.movie.state() == QMovie.MovieState.Paused:
                self.movie.jumpToFrame(0)
                for _ in range(144):
                    self.movie.jumpToNextFrame()
                self.movie.setPaused(True)

    def on_stop_clicked(self):
        self.logger.info("[ACTION] Stop button clicked - sending interrupt signal")
        # Send interrupt signal to stop TTS playback
        signal_manager.interrupt_tts.emit()
        # Hide the player IMMEDIATELY on click — don't wait for the round-trip
        # through the agent command queue and back via tts_stopped event.
        # This gives the user instant visual feedback.
        self.hide_window()


    def show_window(self):
        """Show the player window and position it correctly relative to oracle"""
        # Stop any ongoing fade animation
        if self.fade_animation.state() == QPropertyAnimation.State.Running:
            self.fade_animation.stop()
        
        # Set opacity to fully visible
        self.setWindowOpacity(1.0)
        
        # CRITICAL: Reset GIF to frame 144 BEFORE showing to prevent flicker
        if self.movie:
            self.movie.stop()
            self.animation_timer.stop()
            # Reset to frame 0 first, then advance to frame 144
            self.movie.jumpToFrame(0)
            for _ in range(144):
                self.movie.jumpToNextFrame()
            self.movie.setPaused(True)
            self.logger.debug("[PlayerWindow] GIF reset to frame 144 before showing")
        
        # FIRST LOAD HACK: Move offscreen and show to force window creation/mapping
        # This ensures accurate geometry calculations for the first time
        if not self.isVisible():
            self.move(-10000, -10000)
            self.show()
            QtWidgets.QApplication.processEvents()
        
        # Position - delegate to Oracle (it knows its own position)
        if self.oracle_window and hasattr(self.oracle_window, 'position_player_window'):
            self.oracle_window.position_player_window()
        
        # CRITICAL: Process events again after positioning
        QtWidgets.QApplication.processEvents()
        
        # Ensure visible and on top (it might be offscreen if position failed, but oracle should handle it)
        if not self.isVisible():
            self.show()
        # Don't call raise_() - it can steal focus. Window already has WindowStaysOnTopHint
        # and WA_ShowWithoutActivating, so it will stay on top without activating
        
        # Force a repaint to ensure window is fully rendered
        self.update()
        
        self.logger.info("[PlayerWindow] Window shown and positioned (GIF at frame 144)")

    def hide_window(self):
        """Hide the player window with fade-out animation"""
        # CRITICAL: Check guard IMMEDIATELY and atomically - silently prevent duplicates
        with self._hide_window_lock:
            if self._hide_window_processing:
                # Already processing - silently skip duplicate call
                return
            # Set flag IMMEDIATELY while holding the lock
            self._hide_window_processing = True
            
            # If already hidden, skip
            if not self.isVisible():
                # Reset flag since we're not processing
                QTimer.singleShot(500, lambda: setattr(self, '_hide_window_processing', False))
                return
            
            # If fade finish is already processing, skip starting a new animation
            if self._fade_finished_processing:
                # Reset flag since we're not processing
                QTimer.singleShot(500, lambda: setattr(self, '_hide_window_processing', False))
                return
            
            # Check if animation is already running - prevent duplicate animation starts
            if self.fade_animation.state() == QPropertyAnimation.State.Running:
                # Reset flag since we're not processing
                QTimer.singleShot(500, lambda: setattr(self, '_hide_window_processing', False))
                return
            
            try:
                
                # Block animation signals while stopping to prevent duplicate finished signals
                # NEVER disconnect/reconnect - keep signal connected always to avoid race conditions
                self.fade_animation.blockSignals(True)
                try:
                    # Stop any ongoing fade animation
                    if self.fade_animation.state() == QPropertyAnimation.State.Running:
                        self.fade_animation.stop()
                        # Reset the guard flag if we stopped an animation
                        self._fade_finished_processing = False
                finally:
                    # Unblock signals before starting new animation
                    self.fade_animation.blockSignals(False)
                
                # Start fade-out animation
                self.fade_animation.setStartValue(1.0)
                self.fade_animation.setEndValue(0.0)
                self.fade_animation.start()
                
                # Reset will be called after fade completes in _on_fade_finished
            finally:
                # Reset guard flag after a delay to allow for any queued signals (500ms to match debounce window)
                QTimer.singleShot(500, lambda: setattr(self, '_hide_window_processing', False))
    
    def _on_fade_finished(self):
        """Called when fade-out animation completes"""
        # CRITICAL: Check guard IMMEDIATELY and atomically - before any other code
        # This must be the very first thing to prevent race conditions
        with self._fade_finished_lock:
            if self._fade_finished_processing:
                # Already processing - skip immediately without any logging to avoid duplicate logs
                return
            # Set flag IMMEDIATELY while holding the lock
            self._fade_finished_processing = True
        
        # Check if window is already hidden - if so, skip
        if not self.isVisible():
            # Reset flag since we're not processing (500ms to match debounce window)
            QTimer.singleShot(500, lambda: setattr(self, '_fade_finished_processing', False))
            return
        
        try:
            self.reset()
            # Call our tracked hide method
            self._hide_with_logging()
            self.setWindowOpacity(1.0)  # Reset opacity for next show
        finally:
            # Reset guard flag after a delay to allow for any queued signals (500ms to match debounce window)
            QTimer.singleShot(500, lambda: setattr(self, '_fade_finished_processing', False))
    
    def _hide_with_logging(self):
        """Internal method to hide window with logging"""
        # CRITICAL: Check guard IMMEDIATELY and atomically - silently prevent duplicates
        with self._hide_lock:
            if self._hide_processing:
                # Already processing - silently skip duplicate call
                return
            # Set flag IMMEDIATELY while holding the lock
            self._hide_processing = True
        
        # Check if window is already hidden - if so, skip
        if not self.isVisible():
            # Reset flag since we're not processing (500ms to match debounce window)
            QTimer.singleShot(500, lambda: setattr(self, '_hide_processing', False))
            return
        
        try:
            super().hide()
        finally:
            # Reset guard flag after a delay to allow for any queued signals (500ms to match debounce window)
            QTimer.singleShot(500, lambda: setattr(self, '_hide_processing', False))
    
    def hide(self):
        """Override hide() to add logging"""
        self._hide_with_logging()
    
    def setVisible(self, visible):
        """Override setVisible() to track visibility changes"""
        super().setVisible(visible)

    def closeEvent(self, event):
        # CRITICAL: Check guard IMMEDIATELY and atomically - silently prevent duplicates
        with self._close_event_lock:
            if self._close_event_processing:
                # Already processing - silently ignore duplicate call
                event.ignore()
                return
            # Set flag IMMEDIATELY while holding the lock
            self._close_event_processing = True
        
        try:
            self.reset()
            event.ignore()
            self.hide()
        finally:
            # Reset guard flag after a delay to allow for any queued signals (500ms to match debounce window)
            QTimer.singleShot(500, lambda: setattr(self, '_close_event_processing', False))




    def on_player_play(self):
        """Handle player_play signal - start GIF animation"""
        self.logger.info("[PlayerWindow] on_player_play: starting GIF animation")
        if self.movie:
            # Ensure movie is in a good state before starting
            movie_state = self.movie.state()
            if movie_state == QtGui.QMovie.MovieState.Paused:
                # If paused, resume from current frame
                self.movie.setPaused(False)
            elif movie_state == QtGui.QMovie.MovieState.NotRunning:
                # If not running, start from beginning
                self.movie.start()
            elif movie_state == QtGui.QMovie.MovieState.Running:
                # Already running, just ensure timer is active
                pass
            else:
                # Unknown state, restart
                self.movie.start()
            
            # Ensure animation timer is running
            if not self.animation_timer.isActive():
                self.animation_timer.start(33)
            self.logger.debug(f"[PlayerWindow] Animation started, movie state: {self.movie.state()}")
    
    def on_player_pause(self):
        """Handle player_pause signal - pause GIF animation"""
        self.logger.info("[PlayerWindow] on_player_pause: pausing GIF animation")
        if self.movie:
            self.movie.setPaused(True)
            self.animation_timer.stop()
    
    def on_player_stop(self):
        """Handle player_stop signal - stop and reset GIF animation"""
        self.logger.info("[PlayerWindow] on_player_stop: stopping and resetting GIF animation")
        if self.movie:
            # Stop the animation first
            self.movie.stop()
            self.animation_timer.stop()
            # Reset to frame 144 (middle of animation) and pause
            self.movie.jumpToFrame(0)
            for _ in range(144):
                self.movie.jumpToNextFrame()
            self.movie.setPaused(True)
            self.logger.info("[PlayerWindow] Animation stopped and reset to frame 144")



