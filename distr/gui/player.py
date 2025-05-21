from distr.core.constants import IMAGES_DIR, ICONS_DIR
from distr.core.signals import signal_manager
from PyQt6.QtGui import QMovie, QImageReader
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import Qt, QSize, QTimer
import logging
import os

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
        
        # Set window flags to ensure it stays on top independently
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.Tool
        )
        
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
        signal_manager.update_player_window_position.connect(self.update_position)
        signal_manager.show_player_window.connect(self.show_window)
        signal_manager.hide_player_window.connect(self.hide_window)
        signal_manager.sound_started.connect(self.on_sound_started)
        signal_manager.sound_finished.connect(self.on_sound_finished)
        signal_manager.sound_stopped.connect(self.on_sound_stopped)
        signal_manager.reset_player_window.connect(self.reset)

    def set_oracle_window(self, oracle_window):
        self.oracle_window = oracle_window

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
        """Update position relative to Oracle window"""
        if not self.oracle_window or not self.oracle_window.isVisible():
            return
        
        oracle_rect = self.oracle_window.geometry()
        screen = QtWidgets.QApplication.screenAt(oracle_rect.center())
        if not screen:
            screen = QtWidgets.QApplication.primaryScreen()
        
        screen_geo = screen.geometry()
        
        # Position to the right of Oracle
        x = oracle_rect.right() + 20
        y = oracle_rect.top() + (oracle_rect.height() - self.height()) // 2
        
        # If it would go off screen, position to the left instead
        if x + self.width() > screen_geo.right():
            x = oracle_rect.left() - self.width() - 20
        
        # Ensure within screen bounds
        x = max(screen_geo.left(), min(x, screen_geo.right() - self.width()))
        y = max(screen_geo.top(), min(y, screen_geo.bottom() - self.height()))
        
        self.move(x, y)

    # def update_with_speech(self, speech):
    #     QTimer.singleShot(0, lambda: self._update_with_speech(speech))

    # def _update_with_speech(self, speech):
    #     # Update the VoiceBox window with the recognized speech
    #     if hasattr(self, 'speech_label'):
    #         self.speech_label.setText(f"Last recognized: {speech}")

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
                print(f"Error: Invalid image dimensions: {original_size.width()}x{original_size.height()}")
                self.voice_label.setText("Invalid Image")
        else:
            print(f"Error: Unable to read image from {gif_path}")
            print(f"Error string: {reader.errorString()}")
            self.voice_label.setText("Image Load Error")
        
        self.voice_label.setStyleSheet("color: white; font-size: 14px;")

    def setup_stop_button(self):
        self.stop_button = QtWidgets.QPushButton(self.voice_container)
        self.stop_button.setFixedSize(32, 32)
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border-radius: 16px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
        """)
        
        icon_path = os.path.join(ICONS_DIR, "stop.png")
        self.stop_button.setIcon(QtGui.QIcon(icon_path))
        self.stop_button.setIconSize(QSize(24, 24))
        
        self.stop_button.clicked.connect(self.on_stop_clicked)
        self.stop_button.move(260, 14)

    def update_animation(self):
        if self.movie.state() == QtGui.QMovie.MovieState.Running:
            self.movie.jumpToNextFrame()
            self.update()
        else:
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
        self.logger.info("[ACTION] Stop button clicked")
        signal_manager.duck_playback.emit({
            "volume_ratio": 0.3,
            "wait_time": 0.0,
            "transition_duration": 0.3,
            "fallout_duration": 0.3
        })
        self.reset()
        self.hide_window()

    def on_sound_started(self):
        self.logger.info("[PlayerWindow] on_sound_started called (legacy, no-op)")
        # No-op: handled by start_gif

    def on_sound_finished(self):
        self.logger.info("[PlayerWindow] on_sound_finished called (legacy, no-op)")
        # No-op: handled by stop_and_reset_gif_and_hide

    def on_sound_stopped(self):
        print("Sound stopped manually")
        self.hide_window()

    def show_window(self):
        if not self.oracle_window:
            print("Warning: Oracle window not set")
            return
        self.show()
        self.update_position()

    def hide_window(self):
        self.reset()
        self.hide()

    def closeEvent(self, event):
        self.reset()
        event.ignore()
        self.hide()

    def play_gif(self):
        """Start the GIF animation if available."""
        if self.movie:
            self.movie.start()
            self.animation_timer.start(33)

    def stop_gif(self):
        """Stop the GIF animation if available."""
        if self.movie:
            self.movie.stop()
            self.animation_timer.stop()

    def show_pending(self):
        self.logger.info("[PlayerWindow] show_pending: show window, reset GIF to frame 144 paused (before show)")
        print("[PlayerWindow] show_pending: called")
        self.show()
        self.raise_()
        self.activateWindow()
        self.logger.info("[PlayerWindow] show_pending: after self.show() and raise_()")
        # Fallback: if oracle_window is not set or not visible, center on screen
        if not self.oracle_window or not self.oracle_window.isVisible():
            screen = QtWidgets.QApplication.primaryScreen()
            if screen:
                screen_geo = screen.geometry()
                x = screen_geo.left() + (screen_geo.width() - self.width()) // 2
                y = screen_geo.top() + (screen_geo.height() - self.height()) // 2
                self.move(x, y)
        else:
            self.update_position()
        if self.movie:
            self.movie.stop()
            self.animation_timer.stop()
            self.movie.jumpToFrame(144)
            self.movie.setPaused(True)

    def start_gif(self):
        self.logger.info("[PlayerWindow] start_gif: start GIF animation")
        if self.movie:
            self.movie.start()
            self.animation_timer.start(33)

    def stop_and_reset_gif_and_hide(self):
        self.logger.info("[PlayerWindow] stop_and_reset_gif_and_hide: stop GIF, reset to frame 144 paused, hide window")
        if self.movie:
            self.movie.stop()
            self.animation_timer.stop()
            self.movie.jumpToFrame(144)
            self.movie.setPaused(True)
        self.hide()



