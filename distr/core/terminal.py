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
import subprocess
from typing import Optional, Dict, Any
import uuid
import json

logger = logging.getLogger(__name__)


def _pty_child_setup(slave_fd: int) -> None:
    """Attach the child process to the PTY as its controlling terminal."""
    os.setsid()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
    except OSError:
        pass
    for fd in (0, 1, 2):
        if slave_fd != fd:
            os.dup2(slave_fd, fd)
    if slave_fd > 2:
        os.close(slave_fd)


async def _spawn_pty_process(
    cmd_args: list[str],
    cwd: str,
    env: dict[str, str],
) -> tuple[int, int, subprocess.Popen]:
    """Start cmd_args in a PTY without fork() from the asyncio process."""
    master_fd, slave_fd = os.openpty()
    winsize = struct.pack("HHHH", 24, 80, 0, 0)
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

    def _launch() -> subprocess.Popen:
        return subprocess.Popen(
            cmd_args,
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=lambda: _pty_child_setup(slave_fd),
            close_fds=True,
        )

    loop = asyncio.get_running_loop()
    try:
        proc = await loop.run_in_executor(None, _launch)
    except Exception:
        os.close(master_fd)
        os.close(slave_fd)
        raise

    os.close(slave_fd)
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    return master_fd, proc.pid, proc


def _native_pty_command(shell_bin: str, shell_command: str) -> list[str]:
    """Build shell argv for startup PTYs: source rc, run command, stay on arm64."""
    shell_name = os.path.basename(shell_bin or "")
    # ponytail: -l runs .zshrc before -c and can exec away the command on Rosetta Macs
    if "zsh" in shell_name:
        init = "source ~/.zshrc 2>/dev/null; "
        cmd_args = [shell_bin, "-i", "-c", init + shell_command]
    else:
        init = "source ~/.bash_profile 2>/dev/null; source ~/.bashrc 2>/dev/null; "
        cmd_args = [shell_bin, "-i", "-c", init + shell_command]
    if sys.platform == "darwin":
        try:
            import platform

            if platform.machine() == "arm64":
                cmd_args = ["arch", "-arm64", *cmd_args]
        except Exception:
            pass
    return cmd_args

# ── Global terminal registry ──────────────────────────────────────────────
# Maps project_id -> TerminalSession
_sessions: Dict[int, "TerminalSession"] = {}

# ── Startup sessions for project terminals ────────────────────────────────
_startup_sessions: Dict[str, "TerminalSession"] = {}
_startup_queue_lock = asyncio.Lock()

# ── ANSI escape regex (compiled once) ─────────────────────────────────────
import re
import sys
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[a-zA-Z]')
_LOCAL_URL_RE = re.compile(r'https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::(\d{2,5}))?(?:/[^\s]*)?', re.IGNORECASE)
_PORT_HINT_RE = re.compile(
    r'(?:localhost|127\.0\.0\.1|0\.0\.0\.0|port|listening|server|vite|next).*?(?::|\s)(\d{2,5})',
    re.IGNORECASE,
)


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


def discard_queued_startup_terminals_for_project(project_id: int) -> int:
    """Drop queued-but-unmaterialized startup commands for one project."""
    path = _startup_queue_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            queued = json.load(f) or []
            if not isinstance(queued, list):
                queued = []
    except Exception:
        return 0
    remaining = [item for item in queued if int(item.get("project_id") or 0) != int(project_id)]
    removed = len(queued) - len(remaining)
    if removed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(remaining, f)
    return removed


async def spawn_startup_shell_sessions(
    project_id: int,
    cwd: str,
    commands: list[str],
) -> tuple[int, int]:
    """Spawn startup PTYs on the current asyncio loop (web API path)."""
    started = 0
    failed = 0
    for cmd in commands:
        command = (cmd or "").strip()
        if not command:
            continue
        try:
            await create_startup_shell_session(int(project_id), cwd, command)
            started += 1
        except Exception as e:
            logger.warning("Failed to spawn startup command '%s': %s", command, e)
            failed += 1
    return started, failed


def materialize_queued_startup_terminals_sync(
    project_id: int,
    *,
    timeout: float = 120.0,
) -> tuple[int, int]:
    """Materialize queued startup terminals from a sync caller (tray menu, tools)."""
    from distr.core.web_runtime import run_on_unified_server_loop

    result = run_on_unified_server_loop(
        materialize_queued_startup_terminals(project_id),
        timeout=timeout,
    )
    return result if result is not None else (0, 0)


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
        self._subprocess: Optional[subprocess.Popen] = None
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
            explicit_shell = shell_spec_match.group(1)
            actual_cmd = shell_spec_match.group(2)
            shell_bin = shutil.which(explicit_shell) or f"/bin/{explicit_shell}"
            cmd_args = _native_pty_command(shell_bin, actual_cmd)
        else:
            if "zsh" in shell_name:
                shell_bin = user_shell or shutil.which("zsh") or "/bin/zsh"
            else:
                shell_bin = user_shell or shutil.which("bash") or "/bin/bash"
            cmd_args = _native_pty_command(shell_bin, self.shell_command or "")

        # Build env with TERM set for proper color/cursor support
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["DECISIONS_STARTUP_TERMINAL"] = "1"
        env.pop("DISPLAY", None)

        self.master_fd, self.pid, self._subprocess = await _spawn_pty_process(
            cmd_args, self.cwd, env
        )
        self._running = True
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(
            "Startup terminal: project=%s, pid=%s, cmd=%r, cwd=%s",
            self.project_id,
            self.pid,
            self.shell_command,
            self.cwd,
        )

    # ── Pi agent terminal startup ─────────────────────────────────────────

    async def start(self):
        """Fork a PTY and start the command."""
        if self._running:
            return

        # Find pi binary or fallback to shell
        cmd_args = self._find_pi()
        if not cmd_args:
            cmd_args = [os.environ.get("SHELL", "/bin/bash"), "-l"]

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.pop("DISPLAY", None)  # No X11 for headless

        self.master_fd, self.pid, self._subprocess = await _spawn_pty_process(
            cmd_args, self.cwd, env
        )
        self._running = True
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(
            "Terminal session started: project=%s, pid=%s, cmd=%s, cwd=%s",
            self.project_id,
            self.pid,
            " ".join(cmd_args),
            self.cwd,
        )

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
            if self.purpose in ("startup", "cli_shell"):
                try:
                    from distr.core.orchestrator import mark_project_runtime_session_stopped

                    mark_project_runtime_session_stopped(self.session_key, status="stopped")
                except Exception:
                    pass
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
    _sync_runtime_session_to_orchestrator(session)
    logger.info(f"Startup session created: key={terminal_id} cmd={shell_command!r} cwd={cwd}")
    return terminal_id, session


async def kill_startup_session(terminal_id: str) -> bool:
    session = _startup_sessions.pop(terminal_id, None)
    if session:
        await session.kill()
        try:
            from distr.core.orchestrator import mark_project_runtime_session_stopped

            mark_project_runtime_session_stopped(terminal_id, status="stopped")
        except Exception:
            pass
        return True
    return False


def cleanup_dead_startup_sessions() -> int:
    """Remove dead sessions from the registry. Returns count removed."""
    dead_keys = [k for k, s in _startup_sessions.items() if not s.is_alive]
    for k in dead_keys:
        _startup_sessions.pop(k, None)
        try:
            from distr.core.orchestrator import mark_project_runtime_session_stopped

            mark_project_runtime_session_stopped(k, status="dead")
        except Exception:
            pass
    return len(dead_keys)


def get_startup_sessions_for_project(project_id: int, purpose: Optional[str] = "startup") -> list[dict]:
    """Return all alive startup sessions for a project (and clean up dead ones)."""
    cleanup_dead_startup_sessions()
    results = []
    for session_key, sess in list(_startup_sessions.items()):
        if sess.project_id == project_id and sess.is_alive:
            if purpose and sess.purpose != purpose:
                continue
            _sync_runtime_session_to_orchestrator(sess)
            results.append({
                "process_id": session_key,
                "pid": sess.pid,
                "command": sess.shell_command or "",
                "alive": True,
                "purpose": sess.purpose,
            })
    return results


def kill_all_startup_sessions_for_project(project_id: int, purpose: str = "startup") -> int:
    """Synchronously terminate all alive startup sessions for a project."""
    cleanup_dead_startup_sessions()
    killed = 0
    keys_to_kill = [
        key
        for key, sess in list(_startup_sessions.items())
        if sess.project_id == int(project_id)
        and sess.is_alive
        and (not purpose or sess.purpose == purpose)
    ]
    for key in keys_to_kill:
        sess = _startup_sessions.pop(key, None)
        if not sess:
            continue
        sess._running = False
        if sess._reader_task:
            sess._reader_task.cancel()
        if sess.master_fd is not None:
            try:
                asyncio.get_event_loop().remove_reader(sess.master_fd)
            except Exception:
                pass
            try:
                os.close(sess.master_fd)
            except OSError:
                pass
            sess.master_fd = None
        if sess.pid:
            for pid in reversed(sess._collect_descendants(sess.pid)):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                os.kill(sess.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            from distr.core.orchestrator import mark_project_runtime_session_stopped

            mark_project_runtime_session_stopped(key, status="stopped")
        except Exception:
            pass
        killed += 1
    return killed


def _sync_runtime_session_to_orchestrator(sess: TerminalSession) -> None:
    try:
        from distr.core.orchestrator import upsert_project_runtime_session

        buffer = sess.get_buffer(80)
        upsert_project_runtime_session(
            terminal_id=sess.session_key,
            project_id=sess.project_id,
            pid=sess.pid,
            command=sess.shell_command or sess.command or "",
            cwd=sess.cwd,
            purpose=sess.purpose,
            owner="decisions_project_runtime",
            status="running" if sess.is_alive else "dead",
            urls=_infer_urls_from_terminal_buffer(buffer),
            buffer_preview=buffer,
            created_at_epoch=sess.created_at,
        )
    except Exception:
        pass


def _infer_urls_from_terminal_buffer(buffer: str) -> list[dict[str, Any]]:
    """Infer local app URLs from recent terminal output."""
    seen: set[str] = set()
    urls: list[dict[str, Any]] = []
    for match in _LOCAL_URL_RE.finditer(buffer or ""):
        raw_url = match.group(0).rstrip(".,;)")
        port_text = match.group(1)
        normalized = raw_url.replace("localhost", "127.0.0.1").replace("0.0.0.0", "127.0.0.1").rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append({
            "url": normalized,
            "port": int(port_text) if port_text and port_text.isdigit() else None,
            "source": "terminal_output",
        })

    for match in _PORT_HINT_RE.finditer(buffer or ""):
        port_text = match.group(1)
        if not port_text or not port_text.isdigit():
            continue
        port = int(port_text)
        if port < 80 or port > 65535:
            continue
        url = f"http://127.0.0.1:{port}"
        if url in seen:
            continue
        seen.add(url)
        urls.append({"url": url, "port": port, "source": "terminal_output"})
    return urls[:8]


def get_project_runtime_snapshot(project_id: int) -> dict[str, Any]:
    """Return the live runtime context Hermes should know before acting.

    This is intentionally read-only. It reports active project terminals and
    likely local app URLs without restarting, killing, or claiming ownership of
    anything.
    """
    cleanup_dead_startup_sessions()
    sessions: list[dict[str, Any]] = []
    url_by_value: dict[str, dict[str, Any]] = {}

    for session_key, sess in list(_startup_sessions.items()):
        if sess.project_id != project_id or not sess.is_alive:
            continue
        _sync_runtime_session_to_orchestrator(sess)
        buffer = sess.get_buffer(80)
        inferred_urls = _infer_urls_from_terminal_buffer(buffer)
        for item in inferred_urls:
            if item.get("url"):
                url_by_value[str(item["url"])] = item
        sessions.append({
            "process_id": session_key,
            "terminal_id": session_key,
            "pid": sess.pid,
            "command": sess.shell_command or "",
            "cwd": sess.cwd,
            "alive": True,
            "purpose": sess.purpose,
            "created_at": sess.created_at,
            "urls": inferred_urls,
            "owner": "decisions_project_runtime",
        })
    durable_sessions = []
    try:
        from distr.core.orchestrator import list_project_runtime_sessions

        durable_sessions = list_project_runtime_sessions(project_id=int(project_id), active_only=False, limit=20)
    except Exception:
        durable_sessions = []

    return {
        "project_id": int(project_id),
        "active_terminal_count": len(sessions),
        "sessions": sessions,
        "durable_sessions": durable_sessions,
        "urls": list(url_by_value.values()),
        "safe_restart_policy": "Only restart Decisions-owned project runtime terminals; do not kill user-owned terminals without approval.",
    }


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
