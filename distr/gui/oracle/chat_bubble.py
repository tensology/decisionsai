"""ChatBubbleWidget — floating speech bubble overlay near the oracle window.

A frameless, transparent, always-on-top QWidget that displays text in a
rounded-rectangle bubble with a triangular tail pointing toward the oracle.
Positioned relative to the oracle window rect and clamped to screen bounds.

Requirements: 5.5, 5.6, 6.2, 6.5
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QRectF, QPointF
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QFontMetrics
from PyQt6.QtWidgets import QApplication, QWidget


# Layout constants
_BUBBLE_MAX_WIDTH = 260
_BUBBLE_PADDING = 12
_TAIL_WIDTH = 14
_TAIL_HEIGHT = 10
_CORNER_RADIUS = 10
_MARGIN = 8  # gap between bubble and oracle


class ChatBubbleWidget(QWidget):
    """Floating speech-bubble overlay positioned relative to the oracle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._text: str = ""
        self._font = QFont("Segoe UI", 10)
        self._bg_color = QColor(255, 255, 255, 240)
        self._text_color = QColor(30, 30, 30)
        self._border_color = QColor(180, 180, 180)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_text(self, text: str) -> None:
        """Display the bubble with *text*."""
        self._text = text
        self._recalc_size()
        self.update()
        self.show()

    def hide_bubble(self) -> None:
        """Hide the bubble."""
        self.hide()

    def reposition(self, oracle_rect: QRect) -> None:
        """Position the bubble relative to *oracle_rect*, clamped to screen."""
        self._recalc_size()

        # Place bubble above the oracle, horizontally centred
        bw = self.width()
        bh = self.height()
        x = oracle_rect.center().x() - bw // 2
        y = oracle_rect.top() - bh - _MARGIN

        # Clamp to screen bounds
        screen_rect = self._available_screen_rect()
        if x < screen_rect.left():
            x = screen_rect.left()
        if x + bw > screen_rect.right():
            x = screen_rect.right() - bw
        if y < screen_rect.top():
            # Not enough room above — place below the oracle instead
            y = oracle_rect.bottom() + _MARGIN
        if y + bh > screen_rect.bottom():
            y = screen_rect.bottom() - bh

        self.move(x, y)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()

        # Bubble body rect (leave room for tail at bottom)
        body_rect = QRectF(1, 1, w - 2, h - _TAIL_HEIGHT - 2)

        # Build path: rounded rect + triangular tail
        path = QPainterPath()
        path.addRoundedRect(body_rect, _CORNER_RADIUS, _CORNER_RADIUS)

        # Tail pointing down (toward oracle)
        tail_cx = w / 2.0
        tail_top = body_rect.bottom()
        tail_path = QPainterPath()
        tail_path.moveTo(QPointF(tail_cx - _TAIL_WIDTH / 2, tail_top))
        tail_path.lineTo(QPointF(tail_cx, tail_top + _TAIL_HEIGHT))
        tail_path.lineTo(QPointF(tail_cx + _TAIL_WIDTH / 2, tail_top))
        tail_path.closeSubpath()
        path = path.united(tail_path)

        # Fill + border
        painter.setPen(QPen(self._border_color, 1.0))
        painter.setBrush(self._bg_color)
        painter.drawPath(path)

        # Draw wrapped text inside body
        painter.setPen(self._text_color)
        painter.setFont(self._font)
        text_rect = body_rect.adjusted(
            _BUBBLE_PADDING, _BUBBLE_PADDING,
            -_BUBBLE_PADDING, -_BUBBLE_PADDING,
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            self._text,
        )
        painter.end()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _recalc_size(self) -> None:
        """Resize the widget to fit the current text."""
        fm = QFontMetrics(self._font)
        text_width = min(
            fm.horizontalAdvance(self._text),
            _BUBBLE_MAX_WIDTH - 2 * _BUBBLE_PADDING,
        )
        text_width = max(text_width, 40)  # minimum text area width

        bounding = fm.boundingRect(
            0, 0,
            text_width, 0,
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            self._text,
        )

        content_w = max(bounding.width(), 40)
        total_w = content_w + 2 * _BUBBLE_PADDING + 2  # +2 for border
        total_h = bounding.height() + 2 * _BUBBLE_PADDING + _TAIL_HEIGHT + 2
        self.setFixedSize(int(total_w), int(total_h))

    @staticmethod
    def _available_screen_rect() -> QRect:
        """Return the available geometry of the primary screen."""
        app = QApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                return screen.availableGeometry()
        # Fallback — large rect so clamping is effectively a no-op
        return QRect(0, 0, 4096, 4096)
