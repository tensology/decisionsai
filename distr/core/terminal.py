"""
Terminal session manager — creates PTY sessions for web terminal.
Each project gets its own terminal running `pi` in the project's directory.
"""

import os
import sys
import fcntl
import struct
import signal
import logging
import asyncio
import subprocess
import termios
import select
import time
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# ── Global terminal registry ──────────────────────────────────────────────
# Maps project_id -> TerminalSession
_sessions: Dict[int, "TerminalSession"] = {}


class TerminalSession:
    """A single PTY terminal session bound to a project."""

    def __init__(self, project_id: int, cwd: str, command: str = "pi"):
        self.project_id = project_id
        self.cwd = cwd
        self.command = command
        self.master_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self.websockets = set()  # websocket clients subscribed to this session
        self.buffer_lines: list = []  # scrollback buffer (max 5000 lines)
        self.max_buffer_lines = 5000
        self._reader_task: Optional[asyncio.Task] = None
        self._running = False
        self.created_at = time.time()

    async def start(self):
        """Fork a PTY and start the command."""
        if self._running:
            return

        # Find pi binary or fallback to shell
        cmd_args = self._find_pi()
        if not cmd_args:
            cmd_args = [os.environ.get("SHELL", "/bin/bash"), "-l"]

        # Create PTY
        master_fd, slave_fd = os.openpty()

        # Set terminal size (24 rows x 80 cols is a default; will be resized)
        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.pop("DISPLAY", None)  # No X11 for headless

        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()

            # Make slave_fd the controlling terminal
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)

            if slave_fd > 2:
                os.close(slave_fd)

            os.chdir(self.cwd)
            os.execvp(cmd_args[0], cmd_args)
            # Should never reach here
            os._exit(1)
        else:
            # Parent
            os.close(slave_fd)
            self.master_fd = master_fd
            self.pid = pid
            self._running = True

            # Set master_fd non-blocking
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Start reading loop
            self._reader_task = asyncio.create_task(self._read_loop())
            logger.info(f"Terminal session started: project={self.project_id}, pid={pid}, cmd={' '.join(cmd_args)}, cwd={self.cwd}")

    def _find_pi(self) -> list:
        """Find the command to run — returns [command, *args]."""
        # Try to find 'pi' in PATH
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            pi_path = os.path.join(path_dir, "pi")
            if os.path.isfile(pi_path) and os.access(pi_path, os.X_OK):
                return [pi_path]
        # Check common locations
        for common in ["/usr/local/bin/pi", "/opt/homebrew/bin/pi",
                       os.path.expanduser("~/.local/bin/pi"),
                       os.path.expanduser("~/bin/pi"),
                       "/opt/homebrew/lib/node_modules/@anthropic-ai/pi-coding-agent/bin/pi"]:
            if os.path.isfile(common) and os.access(common, os.X_OK):
                return [common]
        # Fallback to the user's shell
        shell = os.environ.get("SHELL", "/bin/bash")
        return [shell, "-l"]  # -l for login shell (loads profile)

    async def _read_loop(self):
        """Read output from PTY and broadcast to websockets."""
        loop = asyncio.get_event_loop()
        buf = b""
        try:
            while self._running and self.master_fd is not None:
                try:
                    # Use run_in_executor to avoid blocking
                    data = await loop.run_in_executor(None, self._read_available)
                    if data is None:
                        # EOF or error
                        break
                    if data:
                        # Broadcast to all connected websockets
                        await self._broadcast(data)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if self._running:
                        logger.debug(f"Terminal read error: {e}")
                    break
        finally:
            self._running = False
            # Send exit message
            await self._broadcast(b"\r\n\x1b[33m[Terminal session ended]\x1b[0m\r\n")
            await self._broadcast_to_websockets({
                "type": "exit",
                "project_id": self.project_id,
            })

    def _read_available(self) -> Optional[bytes]:
        """Read all available data from the PTY master fd (blocking call)."""
        if self.master_fd is None:
            return None
        result = b""
        idle_count = 0
        try:
            while self._running:
                try:
                    r, _, _ = select.select([self.master_fd], [], [], 0.1)
                except (ValueError, OSError):
                    # master_fd was closed
                    return result if result else None
                if not r:
                    if result:
                        return result
                    idle_count += 1
                    if idle_count > 50:  # 5 seconds idle with no data — check if alive
                        if not self.is_alive:
                            return result if result else None
                        idle_count = 0
                    continue
                idle_count = 0
                try:
                    data = os.read(self.master_fd, 65536)
                except OSError:
                    return result if result else None
                if not data:
                    return result if result else None
                result += data
        except Exception:
            return result if result else None

    async def _broadcast(self, data: bytes):
        """Send raw PTY output to all websockets and store in buffer."""
        # Store in buffer (decode and split by lines)
        try:
            text = data.decode("utf-8", errors="replace")
            lines = text.split("\n")
            for i, line in enumerate(lines):
                # Strip ANSI escape sequences for buffer storage
                import re
                clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[a-zA-Z]', '', line)
                clean = clean.replace('\r', '').strip()
                if i == len(lines) - 1 and not text.endswith("\n"):
                    # Partial line — append to last buffer line or track
                    if self.buffer_lines:
                        self.buffer_lines[-1] += clean
                    elif clean:
                        self.buffer_lines.append(clean)
                else:
                    if clean:
                        self.buffer_lines.append(clean)
            # Trim buffer
            if len(self.buffer_lines) > self.max_buffer_lines:
                self.buffer_lines = self.buffer_lines[-self.max_buffer_lines:]
        except Exception as e:
            logger.debug(f"Buffer store error: {e}")

        # Broadcast to websockets
        message = {"type": "output", "data": data.decode("utf-8", errors="replace")}
        await self._broadcast_to_websockets(message)

    async def _broadcast_to_websockets(self, message: dict):
        """Send a JSON message to all connected websockets."""
        import json
        msg_str = json.dumps(message)
        dead = set()
        for ws in self.websockets:
            try:
                await ws.send_text(msg_str)
            except Exception:
                dead.add(ws)
        self.websockets -= dead

    def write(self, data: str):
        """Write input to the PTY."""
        if self.master_fd is not None and self._running:
            try:
                os.write(self.master_fd, data.encode("utf-8"))
            except OSError:
                pass

    def resize(self, rows: int, cols: int):
        """Resize the PTY."""
        if self.master_fd is not None and self.pid:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    async def kill(self):
        """Kill the terminal process."""
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            # Wait briefly for graceful exit
            await asyncio.sleep(0.5)
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Reap the zombie
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        self.pid = None

    def get_buffer(self, lines: int = 100) -> str:
        """Return the last N lines of the terminal buffer (clean text)."""
        return "\n".join(self.buffer_lines[-lines:])

    @property
    def is_alive(self) -> bool:
        """Check if the child process is still running.
        Uses os.kill(pid, 0) to avoid consuming the wait status."""
        if not self.pid:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            self._running = False
            return False
        except PermissionError:
            # Process exists but we can't signal it
            return True
        except OSError:
            self._running = False
            return False


def get_session(project_id: int) -> Optional[TerminalSession]:
    """Get existing terminal session for a project."""
    return _sessions.get(project_id)


async def get_or_create_session(project_id: int, cwd: str, command: str = "pi") -> TerminalSession:
    """Get or create a terminal session for a project."""
    session = _sessions.get(project_id)
    if session and session.is_alive:
        return session
    # Clean up dead session
    if session:
        await session.kill()
    session = TerminalSession(project_id, cwd, command)
    await session.start()
    _sessions[project_id] = session
    return session


async def kill_session(project_id: int):
    """Kill and remove a terminal session."""
    session = _sessions.pop(project_id, None)
    if session:
        await session.kill()