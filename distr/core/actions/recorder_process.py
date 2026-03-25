"""
Action Recorder Process

Launches the recorder as a standalone subprocess (recorder_worker.py) to avoid:
  - fork() crash on macOS (CoreFoundation is not fork-safe with Qt threads)
  - spawn() SemLock pickle crash on Python 3.12+

Communicates via stdin/stdout JSON lines. Public API is unchanged so
recorder_host.py needs no modifications.
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the worker script (same directory as this file)
_WORKER_SCRIPT = str(Path(__file__).with_name("recorder_worker.py"))


class ActionRecorderProcess:
    """Subprocess wrapper for action recording — drop-in replacement."""

    def __init__(self, action_id, action_title, recordings_dir):
        self.action_id = action_id
        self.action_title = action_title
        self.recordings_dir = recordings_dir
        self._proc: subprocess.Popen | None = None
        self._buf = ""  # leftover bytes from stdout reads

    # ── helpers ──

    def _send(self, msg: dict):
        """Write a JSON line to the child's stdin."""
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                logger.error(f"Error writing to recorder subprocess: {e}")

    def _recv(self, timeout: float = 5.0) -> dict | None:
        """Read one JSON line from the child's stdout (blocking up to *timeout* seconds)."""
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
                    return None  # EOF
                self._buf += chunk.decode("utf-8", errors="replace")
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            return json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            logger.warning(f"Non-JSON line from recorder worker: {line!r}")
            # Check if process died
            if self._proc.poll() is not None:
                # Drain remaining
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
        """Non-blocking read — returns None immediately if nothing available."""
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
        """Start the recording subprocess. Returns (success, filename_or_error)."""
        try:
            self._proc = subprocess.Popen(
                [sys.executable, _WORKER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # worker logs go to stderr
                text=False,  # binary mode — we handle encoding ourselves
                bufsize=0,
            )
            # Wrap stdin for text writes
            import io
            self._proc.stdin = io.TextIOWrapper(self._proc.stdin, encoding="utf-8", line_buffering=True)

            # Send init config
            self._send({
                "action_id": self.action_id,
                "action_title": self.action_title,
                "recordings_dir": str(self.recordings_dir),
            })

            # Wait for ready / error
            result = self._recv(timeout=5)
            if result is None:
                return False, "No response from recorder subprocess"
            if result.get("success"):
                return True, result.get("filename")
            else:
                return False, result.get("error", "Unknown error")
        except Exception as e:
            logger.error(f"Failed to launch recorder subprocess: {e}", exc_info=True)
            return False, str(e)

    def stop(self):
        """Stop recording, wait for save confirmation. Returns (filename, error)."""
        saved_filename = None
        save_error = None

        if not self._proc:
            return None, "Process not running"

        try:
            # Send stop command if process is still alive
            if self._proc.poll() is None:
                self._send({"command": "stop"})

            # Wait for saved / save_error message (process may have already exited)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                msg = self._recv_nowait()
                if msg:
                    cmd = msg.get("command")
                    if cmd == "saved":
                        saved_filename = msg.get("filename")
                        break
                    elif cmd == "save_error":
                        save_error = msg.get("error")
                        break
                if self._proc.poll() is not None:
                    # Process exited — drain any remaining output
                    msg = self._recv(timeout=0.5)
                    if msg:
                        cmd = msg.get("command")
                        if cmd == "saved":
                            saved_filename = msg.get("filename")
                        elif cmd == "save_error":
                            save_error = msg.get("error")
                    break
                time.sleep(0.1)

            # Give it a moment to exit
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning("Recorder subprocess didn't exit, killing")
                self._proc.kill()
                self._proc.wait(timeout=1)
        except Exception as e:
            logger.error(f"Error stopping recorder subprocess: {e}", exc_info=True)
            if self._proc:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        finally:
            self._proc = None
            self._buf = ""

        if save_error:
            return None, save_error
        return saved_filename, None

    def is_alive(self):
        """Check if the subprocess is still running."""
        return self._proc is not None and self._proc.poll() is None

    def pause(self):
        """Toggle pause/resume. Returns the new paused state or None on failure."""
        if not self.is_alive():
            return None
        try:
            self._send({"command": "pause"})
            # Wait briefly for ack
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                msg = self._recv_nowait()
                if msg and msg.get("command") == "pause_state":
                    return msg.get("paused", False)
                time.sleep(0.05)
        except Exception as e:
            logger.error(f"Error toggling pause: {e}")
        return None
