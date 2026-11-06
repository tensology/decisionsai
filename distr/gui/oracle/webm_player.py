"""WebMPlayer — WebM playback with ping-pong looping via frame extraction.

Extracts frames from WebM files using imageio (or cv2 as fallback), stores
them as QPixmap objects, and uses a QTimer to drive a ping-pong loop
(forward 0→N-1, then backward N-1→0, repeat).

Requirements: 4.3, 4.4, 4.5
"""

from __future__ import annotations

import time
from threading import Event
from typing import List, Tuple

from PyQt6.QtCore import QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, pyqtSignal
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

    # Chroma-key compression blends the key color into antialiased subject
    # edges.  Removing alpha alone leaves a bright halo, especially around
    # pale hair.  Despill only a narrow band beside transparent pixels so
    # legitimate interior colors, such as green eyes or status icons, remain
    # unchanged.
    key_values = np.array((kr, kg, kb), dtype=np.int16)
    dominant_channel = int(np.argmax(key_values))
    other_channels = [index for index in range(3) if index != dominant_channel]
    if key_values[dominant_channel] - max(key_values[other_channels]) >= 40:
        transparent_nearby = pixels[:, :, 3] == 0
        radius = max(2, min(16, round(min(w, h) * 0.022)))
        for _ in range(radius):
            padded = np.pad(transparent_nearby, 1, constant_values=False)
            transparent_nearby = (
                transparent_nearby
                | padded[:-2, 1:-1]
                | padded[2:, 1:-1]
                | padded[1:-1, :-2]
                | padded[1:-1, 2:]
                | padded[:-2, :-2]
                | padded[:-2, 2:]
                | padded[2:, :-2]
                | padded[2:, 2:]
            )

        # Pixel storage is BGRA, while key_rgb is RGB.
        storage_channel = {0: 2, 1: 1, 2: 0}[dominant_channel]
        storage_others = [index for index in (0, 1, 2) if index != storage_channel]
        dominant_values = pixels[:, :, storage_channel]
        other_max = np.maximum(
            pixels[:, :, storage_others[0]],
            pixels[:, :, storage_others[1]],
        )
        spill = (
            (pixels[:, :, 3] > 0)
            & transparent_nearby
            & (dominant_values.astype(np.int16) - other_max.astype(np.int16) > 4)
        )
        dominant_values[spill] = other_max[spill]

    return QImage(pixels.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()


def _extract_frames_imageio(path: str, prepare_frame=None) -> Tuple[List[QImage], float]:
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

    # Iterate instead of materialising the whole full-resolution video.
    for frame_np in iio.imiter(path, plugin="pyav"):
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
        frames.append(prepare_frame(qimg) if prepare_frame else qimg)

    return frames, fps


def _extract_frames_cv2(path: str, prepare_frame=None) -> Tuple[List[QImage], float]:
    """Fallback frame extraction using *cv2.VideoCapture*."""
    import cv2

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or _DEFAULT_FPS
    frames: List[QImage] = []

    try:
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
            frames.append(prepare_frame(qimg) if prepare_frame else qimg)
    finally:
        cap.release()
    return frames, fps


# ---------------------------------------------------------------------------
# WebMPlayer
# ---------------------------------------------------------------------------

_DEFAULT_INTERVAL_MS = 66  # ~15 fps fallback and maximum render rate


class _DecodeSignals(QObject):
    first_frame = pyqtSignal(int, QImage)
    next_frame = pyqtSignal(int, QImage)
    finished = pyqtSignal(int, object, float)


class _DecodeCancelled(Exception):
    pass


class _DecodeTask(QRunnable):
    """Decode QImages only. QPixmaps and playback stay on the GUI thread."""

    def __init__(self, generation, path, size, dpr, key, threshold):
        super().__init__()
        self.signals = _DecodeSignals()
        self.cancelled = Event()
        self.generation, self.path = generation, path
        self.size, self.dpr, self.key, self.threshold = size, dpr, key, threshold

    def run(self):
        first = True
        effective_key = self.key

        def prepare(image):
            nonlocal first, effective_key
            if self.cancelled.is_set():
                raise _DecodeCancelled()
            if first and self.key is not None:
                effective_key = _sample_background_color(image)
            if effective_key is not None:
                image = _apply_chroma_key_to_image(image, effective_key, self.threshold)
            if self.size is not None:
                image = image.scaled(
                    QSize(max(1, int(self.size.width() * self.dpr)),
                          max(1, int(self.size.height() * self.dpr))),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            if first:
                self.signals.first_frame.emit(self.generation, image)
                first = False
            else:
                self.signals.next_frame.emit(self.generation, image)
            return image

        try:
            if self.cancelled.is_set():
                return
            try:
                images, fps = _extract_frames_imageio(self.path, prepare_frame=prepare)
            except _DecodeCancelled:
                return
            except Exception:
                first, effective_key = True, self.key
                images, fps = _extract_frames_cv2(self.path, prepare_frame=prepare)
            if not self.cancelled.is_set():
                self.signals.finished.emit(self.generation, images, fps)
        except _DecodeCancelled:
            pass
        except Exception:
            if not self.cancelled.is_set():
                self.signals.finished.emit(self.generation, [], _DEFAULT_FPS)


# Serial decoding bounds transient video memory. Foreground loads take priority
# over queued preloads without ever blocking the Qt event loop.
_decode_pool = None


class WebMPlayer(QObject):
    """Plays WebM animations with ping-pong looping via frame extraction."""

    frame_ready = pyqtSignal(QPixmap)
    loaded = pyqtSignal()

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
        self._decode_generation = 0
        self._decode_task = None
        self._play_requested = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def memory_bytes(self) -> int:
        """Decoded pixel storage, including physical pixels on Retina screens."""
        return sum(frame.width() * frame.height() * frame.depth() // 8
                   for frame in self._frames)

    def release_frames(self) -> None:
        """Release evicted pixels now rather than waiting for Qt's event loop."""
        self.stop()
        self._cancel_decode()
        self._frames = []
        self._source_images = []

    def _cancel_decode(self):
        self._decode_generation += 1
        if self._decode_task is not None:
            self._decode_task.cancelled.set()
            self._decode_task = None

    def load_async(self, webm_path, playback="pingpong", chroma_key=None,
                   chroma_threshold=35, *, priority=1):
        """Show the first decoded frame without waiting for the whole clip."""
        global _decode_pool
        self.release_frames()
        self._current_index, self._forward = 0, True
        self._pingpong = playback == "pingpong"
        task = _DecodeTask(self._decode_generation, webm_path, self._size,
                           self._device_pixel_ratio, chroma_key, chroma_threshold)
        task.signals.first_frame.connect(self._on_decoded_first_frame)
        task.signals.next_frame.connect(self._on_decoded_next_frame)
        task.signals.finished.connect(self._on_decoded_frames)
        self._decode_task = task
        if _decode_pool is None:
            _decode_pool = QThreadPool()
            _decode_pool.setMaxThreadCount(1)
        _decode_pool.start(task, priority)

    def _on_decoded_first_frame(self, generation, image):
        if generation != self._decode_generation:
            return
        self._source_images = [image]
        self._rebuild_frames()
        if self._play_requested:
            self.play()

    def _on_decoded_next_frame(self, generation, image):
        if generation == self._decode_generation:
            self._frames.append(self._display_frame(image))

    def _on_decoded_frames(self, generation, images, fps):
        if generation != self._decode_generation:
            return
        self._decode_task = None
        # Frames have already been delivered in order on the GUI thread.
        # Do not rebuild the entire clip here and stall its playback again.
        self._interval_ms = max(_DEFAULT_INTERVAL_MS, int(1000.0 / fps)) if fps > 0 else _DEFAULT_INTERVAL_MS
        if self._play_requested:
            self.play()
        self.loaded.emit()

    def load(self, webm_path: str, playback: str = "pingpong",
             chroma_key: tuple | None = None, chroma_threshold: int = 35) -> None:
        """Extract all frames from *webm_path* and prepare for playback.

        If *chroma_key* is provided (RGB tuple), applies chroma-key removal
        during frame extraction so the resulting pixmaps have transparency.
        """
        self.stop()
        self._cancel_decode()
        self._frames = []
        self._source_images = []
        self._current_index = 0
        self._forward = True
        self._pingpong = (playback == "pingpong")

        qimages: List[QImage] = []
        fps = _DEFAULT_FPS
        effective_key = chroma_key
        first_frame = True

        def prepare_frame(image):
            nonlocal effective_key, first_frame
            if first_frame and chroma_key is not None:
                effective_key = _sample_background_color(image)
            first_frame = False
            if effective_key is not None:
                image = _apply_chroma_key_to_image(image, effective_key, chroma_threshold)
            if self._size is not None:
                image = image.scaled(
                    QSize(max(1, int(self._size.width() * self._device_pixel_ratio)),
                          max(1, int(self._size.height() * self._device_pixel_ratio))),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            return image

        try:
            qimages, fps = _extract_frames_imageio(webm_path, prepare_frame=prepare_frame)
        except Exception:
            try:
                first_frame = True
                effective_key = chroma_key
                qimages, fps = _extract_frames_cv2(webm_path, prepare_frame=prepare_frame)
            except Exception:
                return

        if not qimages:
            return

        self._source_images = qimages
        self._rebuild_frames()

        if fps > 0:
            self._interval_ms = max(_DEFAULT_INTERVAL_MS, int(1000.0 / fps))
        else:
            self._interval_ms = _DEFAULT_INTERVAL_MS

    def play(self) -> None:
        """Start the ping-pong playback loop."""
        self._play_requested = True
        if not self._frames:
            return
        # Emit the first frame immediately
        self.frame_ready.emit(self._frames[self._current_index])
        self._last_advance_at = time.monotonic()
        self._timer.start(self._interval_ms)

    def stop(self) -> None:
        """Stop playback."""
        self._play_requested = False
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
        if self._decode_task is not None:
            # The end of the available buffer is not the end of the clip.
            # Wait there for more frames instead of reversing or looping early.
            self._current_index = min(self._current_index + steps, len(self._frames) - 1)
        elif self._pingpong:
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
        # The decoded source video can be hundreds of megabytes even for a
        # tiny 80px oracle.  Use existing pixmaps as resize sources after the
        # initial build, then release decoded QImages instead of retaining two
        # full frame sets for the lifetime of the app.
        source_images = self._source_images or [px.toImage() for px in self._frames]
        self._frames = [self._display_frame(image) for image in source_images]
        self._source_images = []
        if self._frames:
            self._current_index = max(0, min(self._current_index, len(self._frames) - 1))

    def _display_frame(self, image):
        dpr = max(1.0, float(self._device_pixel_ratio or 1.0))
        if self._size is not None:
            image = image.scaled(
                QSize(max(1, int(self._size.width() * dpr)),
                      max(1, int(self._size.height() * dpr))),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(dpr)
        return pixmap
