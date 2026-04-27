"""AnimationPlayer — unified animation player for GIF and WebM formats.

Detects format from file extension and delegates to GifPlayer (QMovie wrapper)
for .gif files or WebMPlayer (frame extraction + ping-pong) for .webm files.

Requirements: 3.5, 4.3, 5.2
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QMovie


# ---------------------------------------------------------------------------
# GifPlayer — wraps QMovie for GIF playback
# ---------------------------------------------------------------------------


class GifPlayer(QObject):
    """Plays GIF animations with ping-pong looping via frame extraction.

    Extracts all frames from the GIF using QMovie, stores them as QPixmap
    objects, then uses a QTimer to drive ping-pong playback (forward then
    backward, repeating).
    """

    frame_ready = pyqtSignal(QPixmap)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._frames: list[QPixmap] = []
        self._current_index: int = 0
        self._forward: bool = True
        self._pingpong: bool = True  # default to pingpong, can be set to False for loop
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._interval_ms: int = 42  # ~24 fps default
        self._size: QSize | None = None

    def load(self, file_path: str, playback: str = "pingpong") -> None:
        """Extract all frames from a GIF file for playback."""
        self.stop()
        self._frames = []
        self._current_index = 0
        self._forward = True
        self._pingpong = (playback == "pingpong")

        movie = QMovie(file_path)
        if not movie.isValid():
            return

        # Extract every frame from the GIF
        movie.jumpToFrame(0)
        frame_count = movie.frameCount()
        if frame_count <= 0:
            # frameCount() can return -1 for some GIFs — iterate until we loop
            frame_count = 1000  # safety cap

        seen_first = None
        for i in range(frame_count):
            pixmap = movie.currentPixmap()
            if pixmap.isNull():
                break

            # Detect loop: if we've seen the first frame's data again, stop
            if i == 0:
                seen_first = pixmap.toImage()
            elif i > 0 and movie.frameCount() <= 0:
                # Only do loop detection when frameCount is unknown
                img = pixmap.toImage()
                if img == seen_first:
                    break

            self._frames.append(pixmap)

            # Get frame delay for timing (use first frame's delay)
            if i == 0:
                delay = movie.nextFrameDelay()
                if delay > 0:
                    self._interval_ms = delay

            if not movie.jumpToNextFrame():
                break

        movie.stop()

    def play(self) -> None:
        if not self._frames:
            return
        self.frame_ready.emit(self._frames[self._current_index])
        self._timer.start(self._interval_ms)

    def stop(self) -> None:
        self._timer.stop()

    def set_size(self, width: int, height: int) -> None:
        self._size = QSize(width, height)

    def _advance(self) -> None:
        """Advance to the next frame using pingpong or loop logic."""
        if not self._frames:
            return
        if self._pingpong:
            from distr.gui.oracle.webm_player import advance_pingpong
            self._current_index, self._forward = advance_pingpong(
                self._current_index, len(self._frames), self._forward
            )
        else:
            # Loop: forward only, restart from 0
            self._current_index = (self._current_index + 1) % len(self._frames)
        self.frame_ready.emit(self._frames[self._current_index])


# ---------------------------------------------------------------------------
# AnimationPlayer — format-detecting facade
# ---------------------------------------------------------------------------


class AnimationPlayer(QObject):
    """Unified animation player that handles both GIF and WebM formats.

    Detects format from file extension and delegates to the appropriate
    internal player.  Emits ``frame_ready`` with each new QPixmap frame.
    """

    frame_ready = pyqtSignal(QPixmap)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player: GifPlayer | None = None
        self._webm_player = None  # WebMPlayer, imported lazily
        self._width: int = 0
        self._height: int = 0
        self._device_pixel_ratio: float = 1.0

    def load(self, file_path: str, playback: str = "loop",
             chroma_key: tuple | None = None, chroma_threshold: int = 35) -> None:
        """Detect format from extension and create the appropriate player."""
        self.stop()

        if file_path.lower().endswith(".gif"):
            player = GifPlayer(self)
            player.frame_ready.connect(self.frame_ready)
            player.load(file_path, playback=playback)
            if self._width and self._height:
                player.set_size(self._width, self._height)
            self._player = player
            self._webm_player = None
        elif file_path.lower().endswith(".webm"):
            from distr.gui.oracle.webm_player import WebMPlayer
            player = WebMPlayer(self)
            player.frame_ready.connect(self.frame_ready)
            player.set_device_pixel_ratio(self._device_pixel_ratio)
            player.load(file_path, playback=playback,
                        chroma_key=chroma_key, chroma_threshold=chroma_threshold)
            if self._width and self._height:
                player.set_size(self._width, self._height)
            self._webm_player = player
            self._player = None
        elif file_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            # Static image — emit a single frame
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self.frame_ready.emit(pixmap)
            self._player = None
            self._webm_player = None

    def play(self) -> None:
        """Start playback on the active player."""
        if self._player is not None:
            self._player.play()
        if self._webm_player is not None:
            self._webm_player.play()

    def stop(self) -> None:
        """Stop playback on the active player."""
        if self._player is not None:
            self._player.stop()
            self._player = None
        if self._webm_player is not None:
            self._webm_player.stop()
            self._webm_player = None

    def set_size(self, width: int, height: int) -> None:
        """Set the display size for the active player."""
        self._width = width
        self._height = height
        if self._player is not None:
            self._player.set_size(width, height)
        if self._webm_player is not None:
            self._webm_player.set_size(width, height)

    def set_device_pixel_ratio(self, dpr: float) -> None:
        """Set device pixel ratio used by WebM playback frames."""
        self._device_pixel_ratio = max(1.0, float(dpr or 1.0))
        if self._webm_player is not None:
            self._webm_player.set_device_pixel_ratio(self._device_pixel_ratio)
