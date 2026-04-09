"""
VP9/WebM screen streaming for remote control.

Captures screenshots at a configurable FPS, encodes as VP9 WebM via ffmpeg,
sends chunks over WebSocket with STRM prefix.

Binary frame format: [4 bytes "STRM"] [2 bytes screen_number BE] [WebM chunk]
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
        self._width = width
        self._height = height
        fps_int = max(1, round(self._fps))

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}",
            "-r", str(fps_int),
            "-i", "pipe:0",
            "-c:v", "libvpx-vp9",
            "-quality", "realtime",
            "-speed", "8",
            "-tile-columns", "2",
            "-frame-parallel", "0",
            "-row-mt", "1",
            "-tune-content", "screen",
            "-b:v", "500k",
            "-maxrate", "1M",
            "-bufsize", "500k",
            "-g", str(fps_int * 5),
            "-an",
            "-f", "webm",
            "pipe:1",
        ]

        try:
            self._ffmpeg = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # CRITICAL: don't use PIPE — causes deadlock
            )
            logger.info("ffmpeg VP9 started: %dx%d @ %d fps, cmd: %s", width, height, fps_int, ' '.join(cmd))
        except FileNotFoundError:
            logger.error("ffmpeg not found")
            self._ffmpeg = None
            self._running = False
        except Exception as e:
            logger.error("ffmpeg start failed: %s", e)
            self._ffmpeg = None
            self._running = False

    def _reader_thread(self):
        """Read WebM data from ffmpeg stdout, send as STRM binary frames."""
        header = b"STRM" + struct.pack(">H", self.screen_number)
        bytes_sent = 0
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
                    bytes_sent += len(chunk)
                except Exception as e:
                    logger.error("Send binary failed: %s", e)
                    break
        except Exception as e:
            if self._running:
                logger.error("Stream reader error: %s", e)
        logger.info("Stream reader done, sent %d bytes total", bytes_sent)

    def _run(self):
        """Capture loop: grab screen → feed raw RGB to ffmpeg stdin."""
        reader = None
        frames_fed = 0
        try:
            while self._running:
                t0 = time.monotonic()

                try:
                    img = self._capture_fn(self.screen_number)
                except Exception as e:
                    logger.error("Capture error: %s", e)
                    time.sleep(0.5)
                    continue

                if img is None:
                    logger.warning("Capture returned None, retrying...")
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

                # Feed raw RGB to ffmpeg
                try:
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    raw = img.tobytes()
                    self._ffmpeg.stdin.write(raw)
                    self._ffmpeg.stdin.flush()
                    frames_fed += 1
                    if frames_fed <= 3 or frames_fed % 30 == 0:
                        logger.info("Fed frame %d to ffmpeg (%dx%d, %d bytes)", frames_fed, w, h, len(raw))
                except (BrokenPipeError, OSError) as e:
                    logger.warning("ffmpeg pipe broken: %s — restarting", e)
                    self._kill_ffmpeg()
                    continue

                elapsed = time.monotonic() - t0
                target = 1.0 / self._fps
                if elapsed < target:
                    time.sleep(target - elapsed)

        except Exception as e:
            logger.error("Stream loop error: %s", e, exc_info=True)
        finally:
            logger.info("Stream loop ended after %d frames", frames_fed)
            self._running = False
            self._kill_ffmpeg()
