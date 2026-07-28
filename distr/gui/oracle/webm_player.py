"""WebMPlayer — WebM playback with ping-pong looping via frame extraction.

Extracts frames from WebM files using imageio (or cv2 as fallback), stores
them as QPixmap objects, and uses a QTimer to drive a ping-pong loop
(forward 0→N-1, then backward N-1→0, repeat).

Requirements: 4.3, 4.4, 4.5
"""

from __future__ import annotations

import time
from typing import List, Tuple

from PyQt6.QtCore import QObject, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap


# ---------------------------------------------------------------------------
# Pure ping-pong logic (testable without Qt)
# ---------------------------------------------------------------------------


def advance_pingpong(
    current_index: int,
    num_frames: int,
    forward: bool,
) -> Tuple[int, bool]:
    """Advance the ping-pong frame index by one step.

    Returns ``(new_index, new_forward)`` where *new_forward* indicates the
    updated direction.

    For a single frame (num_frames == 1), the index always stays at 0.
    For two or more frames the index bounces: 0→1→…→N-1→N-2→…→0→1→…
    """
    if num_frames <= 1:
        return (0, True)

    if forward:
        next_index = current_index + 1
        if next_index >= num_frames:
            # Hit the end — reverse direction
            return (num_frames - 2, False)
        return (next_index, True)
    else:
        next_index = current_index - 1
        if next_index < 0:
            # Hit the start — reverse direction
            return (1, True)
        return (next_index, False)


# ---------------------------------------------------------------------------
# Frame extraction helpers
# ---------------------------------------------------------------------------

_DEFAULT_FPS = 24


def elapsed_frame_steps(
    last_advance_at: float | None,
    now: float,
    interval_ms: int,
) -> tuple[int, float]:
    """Return frames due and a clock base that preserves late-tick remainder."""
    if last_advance_at is None:
        return 1, now
    interval_s = max(0.001, interval_ms / 1000.0)
    elapsed_s = max(0.0, now - last_advance_at)
    steps = max(1, int(elapsed_s / interval_s))
    return steps, min(now, last_advance_at + (steps * interval_s))


def _sample_background_color(qimg: QImage) -> tuple:
    """Sample the background color from the corners of a QImage.

    Returns the average RGB of the four corner pixels.
    """
    img = qimg.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    corners = [img.pixelColor(0, 0), img.pixelColor(w - 1, 0),
               img.pixelColor(0, h - 1), img.pixelColor(w - 1, h - 1)]
    r = sum(c.red() for c in corners) // 4
    g = sum(c.green() for c in corners) // 4
    b = sum(c.blue() for c in corners) // 4
    return (r, g, b)


def _apply_chroma_key_to_image(qimg: QImage, key_rgb: tuple, threshold: int = 35) -> QImage:
    """Apply chroma-key removal to a QImage, returning an ARGB32 image with transparency."""
    import numpy as np
    img = qimg.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(h * w * 4)
    pixels = np.frombuffer(memoryview(ptr), dtype=np.uint8).reshape((h, w, 4)).copy()

    # ARGB32 little-endian: B=0, G=1, R=2, A=3
    kr, kg, kb = key_rgb
    dr = np.abs(pixels[:, :, 2].astype(np.float32) - kr)
    dg = np.abs(pixels[:, :, 1].astype(np.float32) - kg)
    db = np.abs(pixels[:, :, 0].astype(np.float32) - kb)
    dist = np.sqrt(dr * dr + dg * dg + db * db)

    # Hard transparent
    pixels[:, :, 3][dist <= threshold] = 0

    # Soft edge falloff
    soft = threshold * 0.6
    soft_mask = (dist > threshold) & (dist <= threshold + soft)
    if np.any(soft_mask):
        fade = ((dist[soft_mask] - threshold) / soft).clip(0, 1)
        pixels[:, :, 3][soft_mask] = (255 * fade).astype(np.uint8)

    return QImage(pixels.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()


def _extract_frames_imageio(path: str) -> Tuple[List[QImage], float]:
    """Extract frames from a WebM file using *imageio*.

    Returns ``(list_of_QImage, fps)``.
    """
    import imageio.v3 as iio
    import numpy as np

    frames: List[QImage] = []
    # Read metadata for fps
    props = iio.improps(path, plugin="pyav")
    fps = _DEFAULT_FPS
    if hasattr(props, "fps") and props.fps:
        fps = float(props.fps)

    for frame_np in iio.imread(path, plugin="pyav"):
        frame_np = np.ascontiguousarray(frame_np)
        h, w = frame_np.shape[:2]
        channels = frame_np.shape[2] if frame_np.ndim == 3 else 1

        if channels == 4:
            fmt = QImage.Format.Format_RGBA8888
        elif channels == 3:
            fmt = QImage.Format.Format_RGB888
        else:
            fmt = QImage.Format.Format_Grayscale8

        qimg = QImage(frame_np.data, w, h, frame_np.strides[0], fmt).copy()
        frames.append(qimg)

    return frames, fps


def _extract_frames_cv2(path: str) -> Tuple[List[QImage], float]:
    """Fallback frame extraction using *cv2.VideoCapture*."""
    import cv2

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or _DEFAULT_FPS
    frames: List[QImage] = []

    while True:
        ret, frame_bgr = cap.read()
        if not ret:
            break
        # BGR → RGB
        import numpy as np

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_rgb = np.ascontiguousarray(frame_rgb)
        h, w, ch = frame_rgb.shape
        qimg = QImage(
            frame_rgb.data, w, h, frame_rgb.strides[0], QImage.Format.Format_RGB888
        ).copy()
        frames.append(qimg)

    cap.release()
    return frames, fps


# ---------------------------------------------------------------------------
# WebMPlayer
# ---------------------------------------------------------------------------

_DEFAULT_INTERVAL_MS = 66  # ~15 fps fallback and maximum render rate


class WebMPlayer(QObject):
    """Plays WebM animations with ping-pong looping via frame extraction."""

    frame_ready = pyqtSignal(QPixmap)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._frames: List[QPixmap] = []
        self._source_images: List[QImage] = []
        self._current_index: int = 0
        self._forward: bool = True
        self._pingpong: bool = True
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)
        self._interval_ms: int = _DEFAULT_INTERVAL_MS
        self._last_advance_at: float | None = None
        self._size: QSize | None = None
        self._device_pixel_ratio: float = 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, webm_path: str, playback: str = "pingpong",
             chroma_key: tuple | None = None, chroma_threshold: int = 35) -> None:
        """Extract all frames from *webm_path* and prepare for playback.

        If *chroma_key* is provided (RGB tuple), applies chroma-key removal
        during frame extraction so the resulting pixmaps have transparency.
        """
        self.stop()
        self._frames = []
        self._source_images = []
        self._current_index = 0
        self._forward = True
        self._pingpong = (playback == "pingpong")

        qimages: List[QImage] = []
        fps = _DEFAULT_FPS
        try:
            qimages, fps = _extract_frames_imageio(webm_path)
        except Exception:
            try:
                qimages, fps = _extract_frames_cv2(webm_path)
            except Exception:
                return

        if not qimages:
            return

        # Auto-detect background color from first frame if chroma_key is provided
        # This handles files where the background color differs from the skin config value
        effective_key = chroma_key
        if chroma_key is not None:
            effective_key = _sample_background_color(qimages[0])

        # Apply chroma-key removal if configured (done once at load time)
        if effective_key is not None:
            qimages = [_apply_chroma_key_to_image(img, effective_key, chroma_threshold)
                       for img in qimages]

        self._source_images = qimages
        self._rebuild_frames()

        if fps > 0:
            self._interval_ms = max(_DEFAULT_INTERVAL_MS, int(1000.0 / fps))
        else:
            self._interval_ms = _DEFAULT_INTERVAL_MS

    def play(self) -> None:
        """Start the ping-pong playback loop."""
        if not self._frames:
            return
        # Emit the first frame immediately
        self.frame_ready.emit(self._frames[self._current_index])
        self._last_advance_at = time.monotonic()
        self._timer.start(self._interval_ms)

    def stop(self) -> None:
        """Stop playback."""
        self._timer.stop()
        self._last_advance_at = None

    def set_size(self, width: int, height: int) -> None:
        """Set the display size. Rescales existing frames if loaded."""
        self._size = QSize(width, height)
        if self._source_images or self._frames:
            self._rebuild_frames()

    def set_device_pixel_ratio(self, dpr: float) -> None:
        """Set DPR so frames are rendered at physical pixel density."""
        safe_dpr = max(1.0, float(dpr or 1.0))
        if abs(safe_dpr - self._device_pixel_ratio) < 0.001:
            return
        self._device_pixel_ratio = safe_dpr
        if self._source_images or self._frames:
            self._rebuild_frames()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _advance(self) -> None:
        """Advance to the next frame using pingpong or loop logic."""
        if not self._frames:
            return
        now = time.monotonic()
        steps, self._last_advance_at = elapsed_frame_steps(
            self._last_advance_at, now, self._interval_ms
        )
        if self._pingpong:
            steps %= max(1, 2 * (len(self._frames) - 1))
            for _ in range(steps):
                self._current_index, self._forward = advance_pingpong(
                    self._current_index, len(self._frames), self._forward
                )
        else:
            self._current_index = (self._current_index + steps) % len(self._frames)
        self.frame_ready.emit(self._frames[self._current_index])

    def _rebuild_frames(self) -> None:
        """Rebuild display frames from original decoded images."""
        rebuilt: List[QPixmap] = []
        target_size = self._size
        dpr = max(1.0, float(self._device_pixel_ratio or 1.0))
        # The decoded source video can be hundreds of megabytes even for a
        # tiny 80px oracle.  Use existing pixmaps as resize sources after the
        # initial build, then release decoded QImages instead of retaining two
        # full frame sets for the lifetime of the app.
        source_images = self._source_images or [px.toImage() for px in self._frames]
        for qimg in source_images:
            out = qimg
            if target_size is not None:
                physical_size = QSize(
                    max(1, int(target_size.width() * dpr)),
                    max(1, int(target_size.height() * dpr)),
                )
                out = out.scaled(
                    physical_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            px = QPixmap.fromImage(out)
            px.setDevicePixelRatio(dpr)
            rebuilt.append(px)
        self._frames = rebuilt
        self._source_images = []
        if self._frames:
            self._current_index = max(0, min(self._current_index, len(self._frames) - 1))
