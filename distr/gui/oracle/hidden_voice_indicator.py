"""Pointer-following voice state indicator shown while the Oracle is hidden."""

from __future__ import annotations

import ctypes
import math
import platform
import time

from PyQt6 import QtCore, QtGui, QtWidgets


def _macos_cursor_visible() -> bool:
    """True when macOS is drawing the system pointer.

    The pointer hides after idle, and Qt still reports the last coordinates.
    """
    try:
        core_graphics = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        core_graphics.CGCursorIsVisible.restype = ctypes.c_bool
        return bool(core_graphics.CGCursorIsVisible())
    except Exception:
        return True


class HiddenVoiceIndicator(QtWidgets.QWidget):
    """Render compact voice feedback beside the pointer."""

    STATES = {"idle", "listening", "dictating", "hands_free", "loading", "thinking", "speaking"}

    def __init__(self) -> None:
        flags = (
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.WindowTransparentForInput
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if platform.system() == "Windows":
            flags |= QtCore.Qt.WindowType.Tool
        super().__init__(None, flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(28, 22)

        self._state: str | None = None
        self._oracle_hidden = False
        self._started_at = time.monotonic()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    @property
    def state(self) -> str | None:
        return self._state

    def set_oracle_hidden(self, hidden: bool) -> None:
        self._oracle_hidden = hidden
        self._sync_visibility()

    def set_state(self, state: str | None) -> None:
        self._state = state if state in self.STATES else None
        self._started_at = time.monotonic()
        self._sync_visibility()

    def _system_cursor_visible(self) -> bool:
        if platform.system() != "Darwin":
            return True
        return _macos_cursor_visible()

    def _sync_visibility(self) -> None:
        tracking = self._oracle_hidden and self._state is not None
        should_show = tracking and self._system_cursor_visible()
        if tracking:
            # Keep ticking while the system pointer is hidden so the mark
            # returns on the next mouse move. Do not restart from inside
            # the timeout; Qt can crash if start() runs on the active timer.
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        if should_show:
            self._move_beside_pointer()
            if not self.isVisible():
                self.show()
                self.raise_()
                self._ensure_macos_overlay_level()
        elif self.isVisible():
            self.hide()

    def _tick(self) -> None:
        self._sync_visibility()
        if self.isVisible():
            self.update()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)

    def _move_beside_pointer(self) -> None:
        pointer = QtGui.QCursor.pos()
        screen = QtGui.QGuiApplication.screenAt(pointer)
        available = (
            screen.availableGeometry()
            if screen
            else QtGui.QGuiApplication.primaryScreen().availableGeometry()
        )

        x = pointer.x() + 16
        y = pointer.y() + 16
        if x + self.width() > available.right():
            x = pointer.x() - self.width() - 12
        if y + self.height() > available.bottom():
            y = pointer.y() - self.height() - 12
        self.move(x, y)

    def _ensure_macos_overlay_level(self) -> None:
        if (
            platform.system() != "Darwin"
            or QtGui.QGuiApplication.platformName() != "cocoa"
        ):
            return
        try:
            import objc
            from AppKit import (
                NSStatusWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorStationary,
            )

            native_view = objc.objc_object(c_void_p=int(self.winId()))
            native_window = native_view.window()
            native_window.setLevel_(NSStatusWindowLevel)
            native_window.setIgnoresMouseEvents_(True)
            native_window.setHidesOnDeactivate_(False)
            native_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
            )
            native_window.orderFrontRegardless()
        except Exception:
            pass

    def paintEvent(self, event) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        phase = time.monotonic() - self._started_at

        if self._state == "idle":
            self._paint_idle(painter)
        elif self._state == "listening":
            self._paint_listening(painter, phase)
        elif self._state == "dictating":
            self._paint_dictating(painter, phase)
        elif self._state == "hands_free":
            self._paint_hands_free(painter, phase)
        elif self._state == "loading":
            self._paint_loading(painter, phase)
        elif self._state == "thinking":
            self._paint_thinking(painter, phase)
        elif self._state == "speaking":
            self._paint_speaking(painter, phase)

    def _paint_idle(self, painter: QtGui.QPainter, color: QtGui.QColor | None = None) -> None:
        points = (
            QtCore.QPointF(14, 3),
            QtCore.QPointF(16, 8),
            QtCore.QPointF(21, 11),
            QtCore.QPointF(16, 13),
            QtCore.QPointF(14, 19),
            QtCore.QPointF(12, 13),
            QtCore.QPointF(7, 11),
            QtCore.QPointF(12, 8),
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(color or QtGui.QColor(74, 167, 255))
        painter.drawPolygon(QtGui.QPolygonF(points))

    def _paint_listening(self, painter: QtGui.QPainter, phase: float) -> None:
        alpha = 205 + int(50 * ((math.sin(phase * 3.2) + 1) / 2))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(74, 167, 255, alpha))
        for index, height in enumerate((3, 4, 5, 4, 3)):
            painter.drawRoundedRect(QtCore.QRectF(5 + index * 4, 11 - height / 2, 2, height), 1, 1)

    @staticmethod
    def _dictation_color(phase: float) -> QtGui.QColor:
        return QtGui.QColor.fromHsvF((0.58 + phase * 0.12) % 1.0, 0.72, 1.0)

    def _paint_dictating(self, painter: QtGui.QPainter, phase: float) -> None:
        self._paint_idle(painter, self._dictation_color(phase))

    @staticmethod
    def _hands_free_color(phase: float) -> QtGui.QColor:
        progress = (1 - math.cos(phase * math.tau * 0.12)) / 2
        return QtGui.QColor.fromHsvF(0.54 + progress * 0.08, 0.72, 1.0)

    def _paint_hands_free(self, painter: QtGui.QPainter, phase: float) -> None:
        self._paint_idle(painter, self._hands_free_color(phase))

    def _paint_loading(self, painter: QtGui.QPainter, phase: float) -> None:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        active = int(phase * 7) % 4
        for index, (x, y, angle) in enumerate(((14, 5, 0), (20, 11, 90), (14, 17, 0), (8, 11, 90))):
            distance = (index - active) % 4
            painter.setBrush(QtGui.QColor(124, 196, 255, 255 - distance * 55))
            painter.save()
            painter.translate(x, y)
            painter.rotate(angle)
            painter.drawRoundedRect(QtCore.QRectF(-1.5, -3, 3, 6), 1.5, 1.5)
            painter.restore()

    def _paint_thinking(self, painter: QtGui.QPainter, phase: float) -> None:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        active = int(phase * 5) % 3
        for index, x in enumerate((8, 14, 20)):
            alpha = 255 if index == active else 85
            painter.setBrush(QtGui.QColor(74, 167, 255, alpha))
            painter.save()
            painter.translate(x, 11)
            painter.rotate(45)
            painter.drawRect(QtCore.QRectF(-2, -2, 4, 4))
            painter.restore()

    def _paint_speaking(self, painter: QtGui.QPainter, phase: float) -> None:
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(74, 167, 255))
        for index, x in enumerate((5, 9, 13, 17, 21)):
            wave = (math.sin(phase * 8 + index * 1.15) + 1) / 2
            height = 4 + wave * (7 if index in (1, 2, 3) else 4)
            painter.drawRoundedRect(QtCore.QRectF(x, 11 - height / 2, 2, height), 1, 1)
