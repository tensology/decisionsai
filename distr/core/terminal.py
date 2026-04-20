"""
Terminal session manager — creates PTY sessions for web terminal.
Each project gets its own terminal running `pi` in the project's directory.
"""

import os
import fcntl
import struct
import signal
import logging
import asyncio
import termios
import time
import atexit
from typing import Optional, Dict, Any
import uuid
import json

logger = logging.getLogger(__name__)

# ── Global terminal registry ──────────────────────────────────────────────
# Maps project_id -> TerminalSession
_sessions: Dict[int, "TerminalSession"] = {}

# ── Startup sessions for project terminals ────────────────────────────────
_startup_sessions: Dict[str, "TerminalSession"] = {}
_startup_queue_lock = asyncio.Lock()

# ── ANSI escape regex (compiled once) ─────────────────────────────────────
import re
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[a-zA-Z]')


def _startup_queue_path() -> str:
    from distr.core.paths import DB_DIR
    return os.path.join(DB_DIR, "startup_terminal_queue.json")


def queue_startup_terminal_launch(project_id: int, cwd: str, commands: list[str]) -> int:
    """Persist startup commands so the web runtime can materialize terminals."""
    if not commands:
        return 0
    path = _startup_queue_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "project_id": int(project_id),
        "cwd": cwd,
        "commands": commands,
        "created_at": time.time(),
    }
    try:
        existing: list[dict[str, Any]] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f) or []
                if not isinstance(existing, list):
                    existing = []
        existing.append(payload)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        return len(commands)
    except Exception as e:
        logger.error("Failed to queue startup terminal launch: %s", e, exc_info=True)
        return 0


async def materialize_queued_startup_terminals(project_id: int) -> tuple[int, int]:
    """Create startup PTY sessions for queued requests for this project."""
    path = _startup_queue_path()
    if not os.path.exists(path):
        return 0, 0

    async with _startup_queue_lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                queued = json.load(f) or []
                if not isinstance(queued, list):
                    queued = []
        except Exception:
            queued = []

        to_process = [item for item in queued if int(item.get("project_id") or 0) == int(project_id)]
        remaining = [item for item in queued if int(item.get("project_id") or 0) != int(project_id)]

        if to_process:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(remaining, f)

    started = 0
    failed = 0
    for item in to_process:
        cwd = (item.get("cwd") or "").strip()
        if not cwd or not os.path.isdir(cwd):
            failed += len(item.get("commands") or [])
            continue
        for cmd in item.get("commands") or []:
            command = (cmd or "").strip()
            if not command:
                continue
            try:
                await create_startup_shell_session(int(project_id), cwd, command)
                started += 1
            except Exception as e:
                logger.warning("Failed to materialize queued startup command '%s': %s", command, e)
                failed += 1

    return started, failed


class TerminalSession:
    """A single PTY terminal session bound to a project."""

    def __init__(self, project_id: int, cwd: str, command: str = "pi",
                 shell_command: Optional[str] = None, session_key: Optional[str] = None,
                 purpose: str = "startup"):
        self.project_id = project_id
        self.cwd = cwd
        self.command = command
        self.shell_command = shell_command
        self.session_key = session_key or str(uuid.uuid4())
        self.purpose = purpose
        self._raw_buffer = bytearray()  # raw PTY output for replay on reconnect (256 KB cap)
        self._max_raw_buffer = 256 * 1024
        self.master_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self.websockets = set()  # websocket clients subscribed to this session
        self.buffer_lines: list = []  # scrollback buffer (max 5000 lines)
        self.max_buffer_lines = 5000
        self._reader_task: Optional[asyncio.Task] = None
        self._running = False
        self.created_at = time.time()

    # ── Shell command startup (for project terminals) ─────────────────────

    async def start_with_shell_command(self):
        """Spawn PTY with shell_command support — always sources shell config
        for virtualenvwrapper/nvm/pyenv so commands like `workon` are available."""
        import shutil

        if self._running:
            return

        user_shell = os.environ.get("SHELL", shutil.which("zsh") or shutil.which("bash") or "/bin/bash")
        shell_name = os.path.basename(user_shell)

        # Detect [zsh] or [bash] explicit shell prefix
        shell_spec_match = re.match(r'^\s*\[(zsh|bash)\]\s+(.+)$', self.shell_command or '', re.IGNORECASE)

        if shell_spec_match:
            # Explicit shell — source config for virtualenvwrapper/nvm/pyenv
            explicit_shell = shell_spec_match.group(1)
            actual_cmd = shell_spec_match.group(2)
            if explicit_shell == "zsh":
                init = "source ~/.zshrc 2>/dev/null; "
                cmd_args = [shutil.which(explicit_shell) or "/bin/" + explicit_shell, "-i", "-l", "-c", init + actual_cmd]
            else:
                init = "source ~/.bash_profile 2>/dev/null; source ~/.bashrc 2>/dev/null; "
                cmd_args = [shutil.which(explicit_shell) or "/bin/" + explicit_shell, "-i", "-l", "-c", init + actual_cmd]
        else:
            # No prefix — source shell config for virtualenvwrapper/nvm/pyenv
            # Use -i (interactive) so aliases from .zshrc/.bashrc are expanded
            if "zsh" in shell_name:
                init = "source ~/.zshrc 2>/dev/null; "
                cmd_args = [user_shell, "-i", "-l", "-c", init + self.shell_command]
            else:
                init = "source ~/.bash_profile 2>/dev/null; source ~/.bashrc 2>/dev/null; "
                cmd_args = [user_shell, "-i", "-l", "-c", init + self.shell_command]

        # Build env with TERM set for proper color/cursor support
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.pop("DISPLAY", None)

        # Create PTY pair
        self.master_fd, slave_fd = os.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        # Fork and exec
        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(self.master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.chdir(self.cwd)
            os.execve(cmd_args[0], cmd_args, env)
            os._exit(1)  # shouldn't reach here

        # Parent process
        os.close(slave_fd)
        self.pid = pid
        self._running = True

        # Set master_fd non-blocking
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Start async reader
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(f"Startup terminal: project={self.project_id}, pid={pid}, cmd={self.shell_command!r}, cwd={self.cwd}")

    # ── Pi agent terminal startup ─────────────────────────────────────────

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

    # ── Async PTY reader using add_reader ─────────────────────────────────

    async def _read_loop(self):
        """Read output from PTY using asyncio add_reader for true async I/O."""
        loop = asyncio.get_event_loop()
        read_future = loop.create_future()
        closed = False

        def _on_readable():
            """Called by the event loop when master_fd has data to read."""
            nonlocal closed
            if closed or self.master_fd is None:
                if not read_future.done():
                    read_future.set_result(None)
                return
            try:
                data = os.read(self.master_fd, 65536)
            except OSError:
                # EOF or fd closed
                closed = True
                if not read_future.done():
                    read_future.set_result(None)
                return
            if not data:
                closed = True
                if not read_future.done():
                    read_future.set_result(None)
                return
            # Store data and wake up the loop
            if not read_future.done():
                read_future.set_result(data)

        try:
            loop.add_reader(self.master_fd, _on_readable)
        except OSError:
            self._running = False
            return

        try:
            while self._running and not closed and self.master_fd is not None:
                # Wait for data
                data = await read_future

                # Reset future for next read
                read_future = loop.create_future()
                if closed:
                    break
                if self.master_fd is None:
                    break

                # Re-register reader on new future
                try:
                    loop.add_reader(self.master_fd, _on_readable)
                except OSError:
                    break

                if data is None:
                    break
                if data:
                    await self._broadcast(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._running:
                logger.debug(f"Terminal read error: {e}")
        finally:
            self._running = False
            try:
                loop.remove_reader(self.master_fd)
            except Exception:
                pass
            # Notify websockets that the process ended
            await self._broadcast(b"\r\n\x1b[33m[Terminal session ended]\x1b[0m\r\n")
            await self._broadcast_to_websockets({
                "type": "exit",
                "project_id": self.project_id,
            })

    # ── WebSocket broadcast ───────────────────────────────────────────────

    async def _broadcast(self, data: bytes):
        """Send raw PTY output to all websockets and store in buffer."""
        # Store raw output for replay on reconnect (truncated to max size)
        try:
            self._raw_buffer.extend(data)
            if len(self._raw_buffer) > self._max_raw_buffer:
                self._raw_buffer = self._raw_buffer[-self._max_raw_buffer:]
        except Exception as e:
            logger.debug(f"Raw buffer store error: {e}")

        # Store in cleaned line buffer
        try:
            text = data.decode("utf-8", errors="replace")
            lines = text.split("\n")
            for i, line in enumerate(lines):
                clean = _ANSI_RE.sub('', line).replace('\r', '').strip()
                if i == len(lines) - 1 and not text.endswith("\n"):
                    if self.buffer_lines:
                        self.buffer_lines[-1] += clean
                    elif clean:
                        self.buffer_lines.append(clean)
                else:
                    if clean:
                        self.buffer_lines.append(clean)
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

    # ── Public API ────────────────────────────────────────────────────────

    def add_websocket(self, ws: Any):
        """Add a websocket client."""
        self.websockets.add(ws)

    def remove_websocket(self, ws: Any):
        """Remove a websocket client."""
        self.websockets.discard(ws)

    def resize(self, rows: int, cols: int):
        """Resize the PTY."""
        if self.master_fd is not None and self.pid:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    def write(self, data: str):
        """Write input to the PTY."""
        if self.master_fd is not None and self._running:
            try:
                os.write(self.master_fd, data.encode("utf-8"))
            except OSError:
                pass

    @property
    def is_alive(self) -> bool:
        """Check if the child process is still running.
        Attempts non-blocking waitpid to reap zombies, then checks with kill(0)."""
        if not self.pid:
            return False
        # Try to reap zombie
        try:
            wpid, _ = os.waitpid(self.pid, os.WNOHANG)
            if wpid != 0:
                # Child was reaped — it's dead
                self._running = False
                self.pid = None
                return False
        except ChildProcessError:
            self._running = False
            return False
        except OSError:
            pass
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            self._running = False
            return False
        except PermissionError:
            return True
        except OSError:
            self._running = False
            return False

    async def kill(self):
        """Kill the terminal process and all its descendants."""
        self._running = False

        # Cancel reader
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # Remove event loop reader to prevent callbacks on closed fd
        try:
            asyncio.get_event_loop().remove_reader(self.master_fd)
        except Exception:
            pass

        if self.pid:
            # Kill main process's descendants FIRST (while parent still alive,
            # children are in same process group and easy to find)
            for pid in reversed(self._collect_descendants(self.pid)):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

            # Kill main process
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            # Brief wait for graceful shutdown
            await asyncio.sleep(0.2)

            # SIGKILL any surviving descendants
            for pid in reversed(self._collect_descendants(self.pid)):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

            # SIGKILL main process if still alive
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

            # Reap zombie — use WNOHANG to avoid blocking
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                pass

        # Close master fd
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        self.pid = None

    @staticmethod
    def _collect_descendants(pid: int) -> list[int]:
        """Collect all descendant PIDs of the given process (non-blocking, sync)."""
        import subprocess as sp
        all_pids = []
        to_check = [pid]
        checked = set()
        while to_check:
            current = to_check.pop(0)
            if current in checked:
                continue
            checked.add(current)
            try:
                result = sp.run(["pgrep", "-P", str(current)],
                                capture_output=True, text=True, timeout=2)
                if result.returncode == 0 and result.stdout.strip():
                    for pid_str in result.stdout.strip().split("\n"):
                        if pid_str:
                            try:
                                child = int(pid_str.strip())
                                all_pids.append(child)
                                to_check.append(child)
                            except ValueError:
                                pass
            except Exception:
                pass
        return all_pids

    def get_buffer(self, lines: int = 100) -> str:
        """Return the last N lines of the terminal buffer (clean text)."""
        return "\n".join(self.buffer_lines[-lines:])


# ── Global functions for pi terminal sessions ────────────────────────────

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


# ── Startup (project) terminal sessions ──────────────────────────────────

def get_startup_session(terminal_id: str) -> Optional[TerminalSession]:
    return _startup_sessions.get(terminal_id)


async def create_startup_shell_session(project_id: int, cwd: str, shell_command: str, purpose: str = "startup") -> tuple[str, TerminalSession]:
    """Spawn a PTY running the shell command in cwd. Returns (terminal_id, session)."""
    terminal_id = str(uuid.uuid4())
    session = TerminalSession(project_id=project_id, cwd=cwd, command="shell",
                               shell_command=shell_command, session_key=terminal_id, purpose=purpose)
    await session.start_with_shell_command()
    _startup_sessions[terminal_id] = session
    logger.info(f"Startup session created: key={terminal_id} cmd={shell_command!r} cwd={cwd}")
    return terminal_id, session


async def kill_startup_session(terminal_id: str) -> bool:
    session = _startup_sessions.pop(terminal_id, None)
    if session:
        await session.kill()
        return True
    return False


def cleanup_dead_startup_sessions() -> int:
    """Remove dead sessions from the registry. Returns count removed."""
    dead_keys = [k for k, s in _startup_sessions.items() if not s.is_alive]
    for k in dead_keys:
        _startup_sessions.pop(k, None)
    return len(dead_keys)


def get_startup_sessions_for_project(project_id: int, purpose: Optional[str] = "startup") -> list[dict]:
    """Return all alive startup sessions for a project (and clean up dead ones)."""
    cleanup_dead_startup_sessions()
    results = []
    for session_key, sess in list(_startup_sessions.items()):
        if sess.project_id == project_id and sess.is_alive:
            if purpose and sess.purpose != purpose:
                continue
            results.append({
                "process_id": session_key,
                "pid": sess.pid,
                "command": sess.shell_command or "",
                "alive": True,
                "purpose": sess.purpose,
            })
    return results


# ── Cleanup on exit ────────────────────────────────────────────────────────

def _kill_all_on_exit():
    """atexit handler: synchronously kill all PTY and pi RPC subprocesses."""
    killed = 0

    # Kill startup PTY sessions
    for key, sess in list(_startup_sessions.items()):
        try:
            if sess.pid:
                for cpid in sess._collect_descendants(sess.pid):
                    try: os.kill(cpid, signal.SIGKILL)
                    except: pass
                try: os.kill(sess.pid, signal.SIGKILL)
                except: pass
                try: os.waitpid(sess.pid, os.WNOHANG)
                except: pass
                killed += 1
        except Exception:
            pass
    _startup_sessions.clear()

    # Kill pi RPC sessions
    try:
        from distr.core.pi_rpc import _rpc_sessions
        for pid_key, rpc in list(_rpc_sessions.items()):
            try:
                if rpc._process and rpc._process.poll() is None:
                    rpc._process.kill()
                    rpc._process.wait(timeout=1)
                    killed += 1
            except Exception:
                pass
        _rpc_sessions.clear()
    except Exception:
        pass

    if killed:
        logger.info(f"Cleanup on exit: killed {killed} terminal subprocesses")


atexit.register(_kill_all_on_exit)