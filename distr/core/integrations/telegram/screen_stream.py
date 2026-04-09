"""
VP9/WebM screen streaming for remote control.

Captures screenshots at a configurable FPS, encodes them as a VP9 WebM
stream via ffmpeg, and sends WebM chunks over the WebSocket.

Binary frame format sent over WS:
  [4 bytes "STRM"] [2 bytes screen_number big-endian] [WebM chunk bytes]

The frontend uses MediaSource API to append these chunks to a SourceBuffer.
"""

import logging
import struct
import subprocess
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_FPS = 3
MIN_FPS = 0.3
MAX_FPS = 10


class ScreenStreamer:
    """Captures screen at configurable FPS → VP9/WebM stream → WS binary."""

    def __init__(self, screen_number: int, capture_fn: Callable,
                 send_binary_fn: Callable, fps: float = DEFAULT_FPS):
        self.screen_number = screen_number
        self._capture_fn = capture_fn
        self._send_binary = send_binary_fn
        self._fps = max(MIN_FPS, min(MAX_FPS, fps))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ffmpeg: Optional[subprocess.Popen] = None
        self._width: Optional[int] = None
        self._height: Optional[int] = None

    @property
    def fps(self):
        return self._fps

    @fps.setter
    def fps(self, value: float):
        self._fps = max(MIN_FPS, min(MAX_FPS, value))

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"ScreenStream-{self.screen_number}")
        self._thread.start()
        logger.info("Screen stream started: screen=%d fps=%.1f", self.screen_number, self._fps)

    def stop(self):
        self._running = False
        self._kill_ffmpeg()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None
        self._width = None
        self._height = None
        logger.info("Screen stream stopped: screen=%d", self.screen_number)

    def _kill_ffmpeg(self):
        proc = self._ffmpeg
        self._ffmpeg = None
        if not proc:
            return
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _start_ffmpeg(self, width: int, height: int):
        """Start ffmpeg VP9 WebM encoder."""
        self._width = width
        self._height = height
        fps_int = max(1, round(self._fps))

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            # Input: raw RGB frames from pipe
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}",
            "-r", str(fps_int),
            "-i", "pipe:0",
            # VP9 realtime encoding tuned for screen content
            "-c:v", "libvpx-vp9",
            "-quality", "realtime",
            "-speed", "8",
            "-tile-columns", "2",
            "-frame-parallel", "0",
            "-tune-content", "screen",
            "-b:v", "500k",
            "-maxrate", "1M",
            "-bufsize", "500k",
            "-g", str(fps_int * 5),  # keyframe every 5 seconds
            "-an",
            # WebM output to pipe — live-friendly settings
            "-f", "webm",
            "-live", "1",
            "pipe:1",
        ]

        try:
            self._ffmpeg = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0,
            )
            logger.info("ffmpeg VP9 started: %dx%d @ %d fps", width, height, fps_int)
        except FileNotFoundError:
            logger.error("ffmpeg not found — screen streaming unavailable")
            self._ffmpeg = None
            self._running = False

    def _reader_thread(self):
        """Read WebM data from ffmpeg stdout and send as STRM binary frames."""
        header = b"STRM" + struct.pack(">H", self.screen_number)
        try:
            while self._running:
                proc = self._ffmpeg
                if not proc or not proc.stdout:
                    break
                try:
                    chunk = proc.stdout.read(16384)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                try:
                    self._send_binary(header + chunk)
                except Exception:
                    break
        except Exception as e:
            if self._running:
                logger.error("Stream reader error: %s", e)

    def _run(self):
        """Main loop: capture → feed to ffmpeg. A separate thread reads ffmpeg output."""
        reader = None
        try:
            while self._running:
                t0 = time.monotonic()

                # Capture
                try:
                    img = self._capture_fn(self.screen_number)
                except Exception as e:
                    logger.error("Capture error: %s", e)
                    time.sleep(0.5)
                    continue
                if img is None:
                    time.sleep(0.5)
                    continue

                w, h = img.size

                # (Re)start ffmpeg if needed
                if self._ffmpeg is None or w != self._width or h != self._height:
                    self._kill_ffmpeg()
                    self._start_ffmpeg(w, h)
                    if not self._ffmpeg:
                        break
                    reader = threading.Thread(target=self._reader_thread, daemon=True,
                                              name="StreamReader")
                    reader.start()

                # Feed raw RGB to ffmpeg stdin
                try:
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    self._ffmpeg.stdin.write(img.tobytes())
                    self._ffmpeg.stdin.flush()
                except (BrokenPipeError, OSError):
                    logger.warning("ffmpeg pipe broken — restarting")
                    self._kill_ffmpeg()
                    continue

                # Pace to target FPS
                elapsed = time.monotonic() - t0
                target = 1.0 / self._fps
                if elapsed < target:
                    time.sleep(target - elapsed)

        except Exception as e:
            logger.error("Stream loop error: %s", e, exc_info=True)
        finally:
            self._running = False
            self._kill_ffmpeg()
