"""
VP9/WebM screen streaming for remote control.

Captures screenshots at a configurable FPS, encodes them as a VP9 WebM
stream via ffmpeg, and sends WebM chunks over the WebSocket.

Binary frame format sent over WS:
  [4 bytes "STRM"] [2 bytes screen_number big-endian] [WebM chunk bytes]

The frontend uses MediaSource API to append these chunks to a SourceBuffer
for smooth, low-bandwidth video playback.
"""

import io
import logging
import struct
import subprocess
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Default and limits
DEFAULT_FPS = 3
MIN_FPS = 0.3
MAX_FPS = 10


class ScreenStreamer:
    """Captures screen at configurable FPS → VP9/WebM stream → WS binary."""

    def __init__(
        self,
        screen_number: int,
        capture_fn: Callable,
        send_binary_fn: Callable,
        fps: float = DEFAULT_FPS,
    ):
        self.screen_number = screen_number
        self._capture_fn = capture_fn  # (screen_number) -> PIL Image or None
        self._send_binary = send_binary_fn
        self._fps = max(MIN_FPS, min(MAX_FPS, fps))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ffmpeg: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._width: Optional[int] = None
        self._height: Optional[int] = None

    @property
    def fps(self):
        return self._fps

    @fps.setter
    def fps(self, value: float):
        self._fps = max(MIN_FPS, min(MAX_FPS, value))

    def start(self):
        """Start the streaming loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._stream_loop, daemon=True, name=f"ScreenStream-{self.screen_number}"
        )
        self._thread.start()
        logger.info("Screen stream started: screen=%d fps=%.1f", self.screen_number, self._fps)

    def stop(self):
        """Stop the streaming loop and clean up ffmpeg."""
        self._running = False
        if self._ffmpeg:
            try:
                self._ffmpeg.stdin.close()
            except Exception:
                pass
            try:
                self._ffmpeg.terminate()
                self._ffmpeg.wait(timeout=3)
            except Exception:
                try:
                    self._ffmpeg.kill()
                except Exception:
                    pass
            self._ffmpeg = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._width = None
        self._height = None
        logger.info("Screen stream stopped: screen=%d", self.screen_number)

    def _start_ffmpeg(self, width: int, height: int):
        """Start ffmpeg VP9 WebM encoder subprocess."""
        self._width = width
        self._height = height

        cmd = [
            "ffmpeg",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}",
            "-r", str(max(1, int(self._fps))),
            "-i", "pipe:0",
            # VP9 encoding with screen content mode
            "-c:v", "libvpx-vp9",
            "-quality", "realtime",
            "-speed", "8",           # fastest encoding
            "-tile-columns", "2",
            "-frame-parallel", "1",
            "-tune-content", "screen",
            "-b:v", "0",             # constant quality mode
            "-crf", "32",            # quality (lower = better, 32 is good for screen)
            "-g", "60",              # keyframe every 60 frames
            "-an",                   # no audio
            "-f", "webm",
            "-cluster_size_limit", "65536",
            "-cluster_time_limit", "1000",
            "-dash", "0",
            "pipe:1",
        ]

        try:
            self._ffmpeg = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            logger.info("ffmpeg VP9 encoder started: %dx%d", width, height)
        except FileNotFoundError:
            logger.error("ffmpeg not found — screen streaming requires ffmpeg")
            self._ffmpeg = None
            self._running = False

    def _reader_loop(self):
        """Read WebM chunks from ffmpeg stdout and send over WS."""
        header = b"STRM" + struct.pack(">H", self.screen_number)
        try:
            while self._running and self._ffmpeg and self._ffmpeg.stdout:
                chunk = self._ffmpeg.stdout.read(32768)  # 32KB chunks
                if not chunk:
                    break
                self._send_binary(header + chunk)
        except Exception as e:
            if self._running:
                logger.error("Stream reader error: %s", e)

    def _stream_loop(self):
        """Main capture → encode loop."""
        reader_thread = None

        try:
            while self._running:
                frame_start = time.monotonic()

                # Capture screenshot as PIL Image
                try:
                    pil_img = self._capture_fn(self.screen_number)
                except Exception as e:
                    logger.error("Screen capture error: %s", e)
                    time.sleep(0.5)
                    continue

                if pil_img is None:
                    time.sleep(0.5)
                    continue

                # Get dimensions
                w, h = pil_img.size

                # Start or restart ffmpeg if dimensions changed
                if self._ffmpeg is None or w != self._width or h != self._height:
                    if self._ffmpeg:
                        try:
                            self._ffmpeg.stdin.close()
                            self._ffmpeg.terminate()
                        except Exception:
                            pass
                    self._start_ffmpeg(w, h)
                    if not self._ffmpeg:
                        break
                    # Start reader thread for this ffmpeg instance
                    reader_thread = threading.Thread(
                        target=self._reader_loop, daemon=True, name="StreamReader"
                    )
                    reader_thread.start()

                # Convert to RGB bytes and feed to ffmpeg
                try:
                    if pil_img.mode != "RGB":
                        pil_img = pil_img.convert("RGB")
                    raw = pil_img.tobytes()
                    self._ffmpeg.stdin.write(raw)
                    self._ffmpeg.stdin.flush()
                except (BrokenPipeError, OSError):
                    logger.warning("ffmpeg pipe broken, restarting encoder")
                    self._ffmpeg = None
                    continue

                # Sleep to maintain target FPS
                elapsed = time.monotonic() - frame_start
                target = 1.0 / self._fps
                if elapsed < target:
                    time.sleep(target - elapsed)

        except Exception as e:
            logger.error("Stream loop error: %s", e, exc_info=True)
        finally:
            self.stop()
