"""
Action Player Process

Launches playback as a standalone subprocess (player_worker.py) to avoid
fork/spawn crashes on macOS. Public API is unchanged so playback_service.py
needs no modifications.
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_WORKER_SCRIPT = str(Path(__file__).with_name("player_worker.py"))


class ActionPlayerProcess:
    """Subprocess wrapper for action playback — drop-in replacement."""

    def __init__(self, file_path, play_sticky=False):
        self.file_path = file_path
        self.play_sticky = play_sticky
        self._proc: subprocess.Popen | None = None
        self._buf = ""

    # ── helpers ──

    def _send(self, msg: dict):
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                logger.error(f"Error writing to player subprocess: {e}")

    def _recv(self, timeout: float = 5.0) -> dict | None:
        import select
        if not self._proc or not self._proc.stdout:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0)
            ready, _, _ = select.select([self._proc.stdout], [], [], min(remaining, 0.1))
            if ready:
                chunk = os.read(self._proc.stdout.fileno(), 4096)
                if not chunk:
                    return None
                self._buf += chunk.decode("utf-8", errors="replace")
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            return json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            pass
            if self._proc.poll() is not None:
                try:
                    rest = self._proc.stdout.read()
                    if rest:
                        self._buf += rest.decode("utf-8", errors="replace")
                except Exception:
                    pass
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            return json.loads(line)
                        except Exception:
                            pass
                return None
        return None

    def _recv_nowait(self) -> dict | None:
        import select
        if not self._proc or not self._proc.stdout:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], 0)
        if ready:
            chunk = os.read(self._proc.stdout.fileno(), 4096)
            if not chunk:
                return None
            self._buf += chunk.decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                try:
                    return json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    pass
        return None

    # ── public API (same as before) ──

    def start(self):
        """Start the playback subprocess. Returns (success, error_or_None)."""
        try:
            self._proc = subprocess.Popen(
                [sys.executable, _WORKER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
            )
            import io
            self._proc.stdin = io.TextIOWrapper(self._proc.stdin, encoding="utf-8", line_buffering=True)

            self._send({
                "file_path": str(self.file_path),
                "play_sticky": self.play_sticky,
            })

            result = self._recv(timeout=5)
            if result is None:
                return False, "No response from player subprocess"
            if result.get("status") == "started":
                return True, None
            elif result.get("status") == "error":
                return False, result.get("error", "Unknown error")
            else:
                return False, f"Unexpected status: {result}"
        except Exception as e:
            logger.error(f"Failed to launch player subprocess: {e}", exc_info=True)
            return False, str(e)

    def wait_for_completion(self, timeout=None):
        """Wait for playback to finish. Returns (success, error_or_None)."""
        if not self._proc:
            return False, "No process running"
        deadline = time.monotonic() + timeout if timeout else None
        try:
            while True:
                if deadline and time.monotonic() > deadline:
                    return False, "Playback timed out"
                msg = self._recv(timeout=1.0)
                if msg:
                    st = msg.get("status")
                    if st == "completed":
                        return True, None
                    elif st == "stopped":
                        return True, None
                    elif st == "error":
                        return False, msg.get("error")
                if self._proc.poll() is not None:
                    return True, None
        except Exception as e:
            return False, str(e)

    def stop(self):
        """Stop the playback subprocess."""
        if self._proc and self._proc.poll() is None:
            try:
                self._send({"command": "stop"})
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=1)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._buf = ""

    def is_alive(self):
        """Check if the subprocess is still running."""
        return self._proc is not None and self._proc.poll() is None

    def pause(self):
        """Send pause command to the playback subprocess."""
        if self.is_alive():
            try:
                self._send({"command": "pause"})
            except Exception as e:
                logger.error(f"Error sending pause: {e}")

    def resume(self):
        """Send resume command to the playback subprocess."""
        if self.is_alive():
            try:
                self._send({"command": "resume"})
            except Exception as e:
                logger.error(f"Error sending resume: {e}")
