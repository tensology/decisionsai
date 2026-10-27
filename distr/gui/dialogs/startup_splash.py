"""Startup splash: plays the Tensology intro video in a rounded white-bordered window.

No padding, no title, no orange chrome — just the video. Falls back to a plain line
when the video file or the Qt multimedia stack is unavailable, so boot never hangs.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

logger = logging.getLogger(__name__)

_VIDEO_RELATIVE = "assets/video/tensology-intro.mp4"
_VIDEO_WIDTH = 720
_BORDER = 3
_CARD_RADIUS = 18
_VIDEO_RADIUS = 15


def _video_path() -> str:
    # distr/gui/dialogs/startup_splash.py -> project root
    root = Path(__file__).resolve().parents[3]
    return str(root / _VIDEO_RELATIVE)


class StartupSplash(QtWidgets.QWidget):
    finished = QtCore.pyqtSignal()

    def __init__(self, message: str = "", video_path: str | None = None):
        super().__init__(
            None,
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("startupSplash")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._player = None
        self._audio = None
        self._video = None
        self._closing = False

        # No outer padding: the card is exactly the video plus its border.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._card = QtWidgets.QWidget(self)
        self._card.setObjectName("startupSplashCard")
        outer.addWidget(self._card)

        card = QtWidgets.QVBoxLayout(self._card)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(0)
        self._card_layout = card

        self._fallback = QtWidgets.QLabel("Starting Decisions…", self._card)
        self._fallback.setObjectName("startupSplashFallback")
        self._fallback.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._fallback.setVisible(False)
        card.addWidget(self._fallback)

        self._card.setStyleSheet(
            f"""
            QWidget#startupSplashCard {{
                background-color: transparent;
                border: {_BORDER}px solid #ffffff;
                border-radius: {_CARD_RADIUS}px;
            }}
            QLabel#startupSplashFallback {{
                color: #ffffff;
                font-size: 14px;
                background: transparent;
                padding: 40px 24px;
            }}
            """
        )

        self._start_video(video_path or _video_path())
        self.adjustSize()
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )

    # ── video ───────────────────────────────────────────────────────────────

    def _start_video(self, path: str) -> None:
        if not Path(path).is_file():
            self._show_fallback("Starting Decisions…")
            return
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            self._video = QVideoWidget(self._card)
            self._video.setMinimumSize(_VIDEO_WIDTH, int(_VIDEO_WIDTH * 9 / 16))
            self._video.setStyleSheet("background: black;")
            self._card_layout.addWidget(self._video)

            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._audio.setVolume(1.0)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self._video)
            self._player.mediaStatusChanged.connect(self._on_status)
            self._player.errorOccurred.connect(lambda *_: self._show_fallback("Starting Decisions…"))
            self._player.setSource(QtCore.QUrl.fromLocalFile(path))
            self._player.play()
            self._apply_video_mask()
        except Exception as exc:  # multimedia missing or broken — never block boot
            logger.warning("Startup video unavailable: %s", exc)
            self._show_fallback("Starting Decisions…")

    def _apply_video_mask(self) -> None:
        if self._video is None:
            return
        size = self._video.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        path = QtGui.QPainterPath()
        path.addRoundedRect(
            QtCore.QRectF(0, 0, size.width(), size.height()),
            _VIDEO_RADIUS,
            _VIDEO_RADIUS,
        )
        self._video.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._apply_video_mask()

    def _on_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer

        if status in (
            QMediaPlayer.MediaStatus.EndOfMedia,
            QMediaPlayer.MediaStatus.InvalidMedia,
        ):
            self._finish()

    def _show_fallback(self, message: str) -> None:
        if self._video is not None:
            self._video.setVisible(False)
        self._fallback.setText(message)
        self._fallback.setVisible(True)
        self.adjustSize()

    def _finish(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if self._player is not None:
                self._player.stop()
        except Exception:
            pass
        self.close()
        self.finished.emit()


def show_startup_splash(message: str = "", video_path: str | None = None) -> StartupSplash:
    splash = StartupSplash(message=message, video_path=video_path)
    splash.show()
    QtWidgets.QApplication.processEvents()
    return splash
