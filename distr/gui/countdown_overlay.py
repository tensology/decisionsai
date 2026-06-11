"""
3-2-1-GO countdown overlay — transparent frameless window with a big black circle
and countdown number, centered on the screen where the mouse cursor is.
Auto-dismisses after the countdown completes.

Also provides a PauseOverlay (same style) that shows ⏸ when recording/playback is paused,
and a HintOverlay that shows keyboard shortcuts below the countdown/pause circle.
"""
import logging
import os
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QPainter, QColor, QFont, QCursor, QGuiApplication
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

CIRCLE_SIZE = 200
COUNTDOWN_STEPS = ["3", "2", "1", "GO"]
STEP_DURATION_MS = 800  # ms per step
HINT_HEIGHT = 30
HINT_WIDTH = 320

# Cached blip player — loaded once, reused across countdowns
_blip_player: QMediaPlayer | None = None
_blip_audio_output: QAudioOutput | None = None

def _get_blip_player() -> QMediaPlayer | None:
    """Return a cached QMediaPlayer for the countdown blip."""
    global _blip_player, _blip_audio_output
    if _blip_player is not None:
        return _blip_player
    try:
        blip_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'sounds', 'blip.mp3')
        blip_path = os.path.abspath(blip_path)
        if os.path.exists(blip_path):
            _blip_audio_output = QAudioOutput()
            _blip_audio_output.setVolume(0.7)
            _blip_player = QMediaPlayer()
            _blip_player.setAudioOutput(_blip_audio_output)
            _blip_player.setSource(QUrl.fromLocalFile(blip_path))
            return _blip_player
    except Exception as e:
        logger.debug("Could not load blip sound: %s", e)
    return None


class HintOverlay(QWidget):
    """
    Small transparent overlay that shows keyboard shortcut hints below the circle.
    """

    def __init__(self, text="Esc to stop  ·  Ctrl+Space to pause", parent=None):
        super().__init__(parent)
        self._text = text
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(HINT_WIDTH, HINT_HEIGHT)

    def set_text(self, text: str):
        self._text = text
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Semi-transparent dark background pill
        bg = QColor(0, 0, 0, 160)
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 12, 12)

        # White text
        painter.setPen(QColor(255, 255, 255, 200))
        font = QFont("Arial", 11)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class CountdownOverlay(QWidget):
    """
    Transparent frameless overlay that shows 3 → 2 → 1 → GO in a big black circle,
    centered on the screen where the mouse currently is. Calls `on_complete` when done.
    Shows a hint bar below with keyboard shortcuts.
    """

    def __init__(self, on_complete=None, parent=None):
        super().__init__(parent)
        self.on_complete = on_complete
        self._step_index = 0
        self._current_text = COUNTDOWN_STEPS[0]
        self._opacity = 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        self._hint = HintOverlay("Esc to stop  ·  Ctrl+Space to pause")

    def start(self):
        """Position on the mouse's current screen and begin countdown."""
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()
        geo = screen.geometry()
        x = geo.x() + (geo.width() - CIRCLE_SIZE) // 2
        y = geo.y() + (geo.height() - CIRCLE_SIZE) // 2
        self.move(x, y)

        # Position hint below the circle
        hint_x = geo.x() + (geo.width() - HINT_WIDTH) // 2
        hint_y = y + CIRCLE_SIZE + 12
        self._hint.move(hint_x, hint_y)

        self._step_index = 0
        self._current_text = COUNTDOWN_STEPS[0]
        self._opacity = 1.0
        self.show()
        self._hint.show()
        self.raise_()
        self._hint.raise_()
        self._play_blip()
        self._timer.start(STEP_DURATION_MS)

    def _advance(self):
        self._step_index += 1
        if self._step_index >= len(COUNTDOWN_STEPS):
            self._timer.stop()
            self.hide()
            self._hint.hide()
            self._hint.deleteLater()
            self.deleteLater()
            if self.on_complete:
                self.on_complete()
            return
        self._current_text = COUNTDOWN_STEPS[self._step_index]
        self._opacity = 1.0
        if self._current_text != "GO":
            self._play_blip()
        self.update()

    def _play_blip(self):
        """Play the cached blip sound effect."""
        try:
            player = _get_blip_player()
            if player:
                player.setPosition(0)
                player.play()
        except Exception:
            pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        circle_color = QColor(0, 0, 0, int(220 * self._opacity))
        painter.setBrush(circle_color)
        painter.setPen(Qt.PenStyle.NoPen)
        margin = 4
        painter.drawEllipse(margin, margin, CIRCLE_SIZE - 2 * margin, CIRCLE_SIZE - 2 * margin)

        text_color = QColor(255, 255, 255, int(255 * self._opacity))
        painter.setPen(text_color)
        font_size = 48 if self._current_text == "GO" else 72
        font = QFont("Arial", font_size, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._current_text)
        painter.end()


class PauseOverlay(QWidget):
    """
    Same black circle as the countdown but shows a ⏸ pause symbol.
    Shown when recording or playback is paused, hidden on resume.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)

        self._hint = HintOverlay("Esc to stop  ·  Ctrl+Space to resume")

    def show_on_cursor_screen(self):
        """Center on the screen where the mouse cursor currently is."""
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()
        geo = screen.geometry()
        x = geo.x() + (geo.width() - CIRCLE_SIZE) // 2
        y = geo.y() + (geo.height() - CIRCLE_SIZE) // 2
        self.move(x, y)

        hint_x = geo.x() + (geo.width() - HINT_WIDTH) // 2
        hint_y = y + CIRCLE_SIZE + 12
        self._hint.move(hint_x, hint_y)

        self.show()
        self._hint.show()
        self.raise_()
        self._hint.raise_()

    def dismiss(self):
        """Hide the pause overlay."""
        self.hide()
        self._hint.hide()

    def cleanup(self):
        """Fully remove the overlay widgets."""
        self.dismiss()
        self._hint.deleteLater()
        self.deleteLater()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Black circle
        painter.setBrush(QColor(0, 0, 0, 220))
        painter.setPen(Qt.PenStyle.NoPen)
        margin = 4
        painter.drawEllipse(margin, margin, CIRCLE_SIZE - 2 * margin, CIRCLE_SIZE - 2 * margin)

        # Draw two vertical pause bars
        bar_w = 20
        bar_h = 64
        gap = 24
        total_w = bar_w * 2 + gap
        left_x = (CIRCLE_SIZE - total_w) // 2
        top_y = (CIRCLE_SIZE - bar_h) // 2

        painter.setBrush(QColor(255, 255, 255, 240))
        painter.drawRoundedRect(left_x, top_y, bar_w, bar_h, 4, 4)
        painter.drawRoundedRect(left_x + bar_w + gap, top_y, bar_w, bar_h, 4, 4)

        painter.end()
