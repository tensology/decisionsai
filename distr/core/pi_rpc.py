"""
Pi RPC Session Manager — spawns `pi --mode rpc` and communicates via JSONL.

This replaces the old PTY-based terminal approach that rendered pi's interactive
TUI (ANSI escape codes) in xterm.js, which produced garbage output.

Instead, we use pi's RPC mode which speaks structured JSONL over stdin/stdout,
giving us:
  - Clean structured events (text deltas, tool calls, thinking, etc.)
  - Steering (mid-run course correction)
  - Abort capability
  - Session management
  - Proper audit trails for the ticket board/project integration
"""

import os
import sys
import json
import logging
import asyncio
import subprocess
import threading
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PiEvent:
    """A single event from pi RPC mode."""
    type: str
    data: dict = field(default_factory=dict)


@dataclass
class PiMessage:
    """A conversational message extracted from pi RPC events."""
    role: str  # "user", "assistant", "tool_result"
    content: str = ""
    tool_calls: list = field(default_factory=list)
    tool_name: str = ""
    tool_result: str = ""
    is_error: bool = False
    timestamp: float = 0


class PiRpcSession:
    """Manages a `pi --mode rpc` subprocess for a project.

    Spawns pi in RPC mode, communicates via JSONL on stdin/stdout,
    and provides methods to send prompts, steer, abort, and collect events.
    """

    def __init__(self, project_id: int, cwd: str, append_system_prompt: str = ""):
        self.project_id = project_id
        self.cwd = cwd
        self.append_system_prompt = append_system_prompt
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

        # Origin tracking (for auto-overview on completion)
        self._origin: str = "cli"  # "cli", "desktop", "telegram"
        self._telegram_chat_id: Optional[str] = None

        # Event callbacks (async or sync)
        self._on_event_callbacks: List[Callable] = []

        # Conversation transcript (clean text, no ANSI)
        self.messages: List[PiMessage] = []
        self._current_assistant_text = ""
        self._current_tool_name = ""
        self._current_tool_args: dict = {}

        # Buffer for "Read Out Overview"
        self._output_lines: List[str] = []
        self._max_buffer_lines = 2000

        # Status tracking
        self.status = "idle"  # idle, running, completed, failed, aborted
        self._pi_bin: Optional[str] = None

    @staticmethod
    def find_pi() -> Optional[str]:
        """Find the pi binary in PATH or common locations."""
        import shutil
        pi_path = shutil.which("pi")
        if pi_path:
            return pi_path
        for common in [
            "/usr/local/bin/pi",
            "/opt/homebrew/bin/pi",
            os.path.expanduser("~/.local/bin/pi"),
            os.path.expanduser("~/bin/pi"),
            os.path.expanduser("~/.npm-global/bin/pi"),
            "/usr/local/lib/node_modules/@mariozechner/pi-coding-agent/bin/pi",
        ]:
            if os.path.isfile(common) and os.access(common, os.X_OK):
                return common
        return None

    def start(self) -> bool:
        """Start the pi RPC subprocess. Returns True on success."""
        if self._running and self._process and self._process.poll() is None:
            return True  # Already running

        self._pi_bin = self.find_pi()
        if not self._pi_bin:
            logger.error("PiRpcSession: pi binary not found in PATH")
            return False

        # Read the current model from settings so pi uses the selected model
        try:
            from distr.core.db import get_session as db_session
            from sqlalchemy import text
            with db_session() as session:
                row = session.execute(text(
                    "SELECT coding_llm_provider, coding_llm_model FROM settings LIMIT 1"
                )).first()
                self._provider = (row[0] or "ollama") if row else "ollama"
                self._model = (row[1] or "") if row else ""
        except Exception:
            self._provider = "ollama"
            self._model = ""

        try:
            cmd = [self._pi_bin, "--mode", "rpc", "--no-session"]
            if self._provider:
                cmd += ["--provider", self._provider]
            if self._model:
                cmd += ["--model", self._model]
            if self.append_system_prompt:
                cmd += ["--append-system-prompt", self.append_system_prompt]
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env={**os.environ, "TERM": "dumb"},  # Prevent any TUI behavior
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as e:
            logger.error(f"PiRpcSession: failed to start pi: {e}")
            return False

        self._running = True
        self.status = "idle"

        # Start reader thread (reads stdout line by line — JSONL protocol)
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        logger.info(f"PiRpcSession started: project={self.project_id}, cwd={self.cwd}, pid={self._process.pid}, provider={self._provider}, model={self._model}")
        return True

    def _read_loop(self):
        """Read JSONL events from pi's stdout (runs in a background thread)."""
        try:
            for line in self._process.stdout:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Process the event into clean transcript
                self._process_event(event)

                # Forward to callbacks
                for cb in self._on_event_callbacks:
                    try:
                        cb(event)
                    except Exception as e:
                        logger.debug(f"PiRpcSession callback error: {e}")

        except Exception as e:
            if self._running:
                logger.error(f"PiRpcSession reader error: {e}")
        finally:
            self._running = False
            self.status = "disconnected"
            logger.info(f"PiRpcSession reader ended: project={self.project_id}")

    def _process_event(self, event: dict):
        """Convert structured RPC events into a clean transcript."""
        event_type = event.get("type", "")

        if event_type == "session":
            # Session header — capture startup info
            self._add_output(f"[pi v{event.get('version', '?')}] Session started in {event.get('cwd', self.cwd)}")

        elif event_type == "message_update":
            delta_type = event.get("assistantMessageEvent", {}).get("type", "")
            if delta_type == "text_delta":
                text = event.get("assistantMessageEvent", {}).get("delta", "")
                self._current_assistant_text += text
                self._add_output(text, newline=False)

            elif delta_type == "thinking_delta":
                thinking = event.get("assistantMessageEvent", {}).get("delta", "")
                # Store thinking but don't spam the output buffer
                self._add_output(f"[thinking] {thinking}", newline=False)

            elif delta_type == "toolcall_start":
                self._current_tool_name = event.get("assistantMessageEvent", {}).get("toolCall", {}).get("name", "")
                self._current_tool_args = event.get("assistantMessageEvent", {}).get("toolCall", {}).get("arguments", {})
                self._add_output(f"\n🔧 {self._current_tool_name}({json.dumps(self._current_tool_args)[:200]})")

            elif delta_type == "toolcall_end":
                self._current_tool_name = ""
                self._current_tool_args = {}

        elif event_type == "message_start":
            msg = event.get("message", {})
            role = msg.get("role", "")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content if b.get("type") == "text"
                    )
                self._add_output(f"\n>>> {content}")
                self.messages.append(PiMessage(role="user", content=content))

        elif event_type == "message_end":
            msg = event.get("message", {})
            role = msg.get("role", "")
            if role == "assistant" and self._current_assistant_text:
                self.messages.append(PiMessage(role="assistant", content=self._current_assistant_text))
                self._current_assistant_text = ""
            elif role == "toolResult":
                content_parts = msg.get("content", [])
                text = ""
                if isinstance(content_parts, list):
                    text = "\n".join(
                        b.get("text", "") for b in content_parts if isinstance(b, dict) and b.get("type") == "text"
                    )
                elif isinstance(content_parts, str):
                    text = content_parts
                self._add_output(f"  → {text[:500]}")
                self.messages.append(PiMessage(
                    role="tool_result",
                    tool_name=msg.get("toolName", ""),
                    tool_result=text,
                    is_error=msg.get("isError", False),
                ))

        elif event_type == "tool_execution_start":
            tool_name = event.get("toolName", "")
            args = event.get("args", {})
            self._add_output(f"\n🔧 {tool_name}({json.dumps(args)[:200]})")

        elif event_type == "tool_execution_end":
            result = event.get("result", {})
            content_parts = result.get("content", []) if isinstance(result, dict) else []
            text = ""
            if isinstance(content_parts, list):
                text = "\n".join(
                    b.get("text", "") for b in content_parts if isinstance(b, dict) and b.get("type") == "text"
                )
            is_error = event.get("isError", False)
            prefix = "❌" if is_error else "✓"
            self._add_output(f"  {prefix} {text[:300]}")
            self.messages.append(PiMessage(
                role="tool_result",
                tool_name=event.get("toolName", ""),
                tool_result=text[:3000],
                is_error=is_error,
            ))

        elif event_type == "agent_start":
            self.status = "running"
            self._add_output("[Agent started]")

        elif event_type == "agent_end":
            self.status = "completed"
            all_msgs = event.get("messages", [])
            self._add_output("[Agent finished]")
            # Auto-read out overview when agent finishes
            self._auto_overview()

        elif event_type == "compaction_start":
            self._add_output("[Compacting context...]")

        elif event_type == "compaction_end":
            self._add_output("[Context compacted]")

        elif event_type == "extension_ui_request":
            # Extension UI requests from RPC — we can auto-respond or surface
            method = event.get("method", "")
            event_id = event.get("id", "")
            if method == "notify":
                msg = event.get("message", "")
                self._add_output(f"ℹ️ {msg}")
            elif method == "setStatus":
                status_text = event.get("statusText", "")
                if status_text:
                    self._add_output(f"⚙️ {status_text}")

    def _auto_overview(self):
        """When pi finishes, auto-read out the overview to the originating channel."""
        try:
            msgs = self.get_messages()
            if not msgs:
                return

            # Build a short summary from last user cmd + last assistant response + last tool
            user_cmds = [m["content"] for m in msgs if m.get("role") == "user" and m.get("content")]
            assistant_resps = [m["content"] for m in msgs if m.get("role") == "assistant" and m.get("content")]
            tool_results = [m for m in msgs if m.get("role") == "tool_result" and m.get("tool_result")]

            last_cmd = user_cmds[-1][:100] if user_cmds else ""
            last_resp = assistant_resps[-1][:300] if assistant_resps else ""
            last_tool = ""
            if tool_results:
                t = tool_results[-1]
                name = t.get("tool_name", "tool")
                result = (t.get("tool_result") or "")[:100]
                is_err = t.get("is_error", False)
                last_tool = f"{'Error in' if is_err else ''} {name}: {result}"

            parts = []
            if last_cmd:
                parts.append(f"Ran: {last_cmd}")
            if last_resp:
                parts.append(f"Result: {last_resp}")
            if last_tool:
                parts.append(last_tool)

            summary = ". ".join(parts) if parts else "Agent finished."

            # Send to the originating channel
            if self._origin == "telegram" and self._telegram_chat_id:
                self._send_telegram_overview(summary)
            else:
                # Desktop / CLI — speak via TTS
                self._speak_overview(summary)
        except Exception as e:
            logger.debug(f"Auto-overview failed: {e}")

    def _speak_overview(self, text: str):
        """Speak the overview via TTS signal."""
        try:
            from distr.core.signals import signal_manager
            signal_manager.send("speak_text", text[:500])
        except Exception:
            pass

    def _send_telegram_overview(self, text: str):
        """Send the overview back to the originating Telegram chat."""
        try:
            from distr.integrations.telegram.client import get_telegram_client
            client = get_telegram_client()
            if client and self._telegram_chat_id:
                client.send_message(self._telegram_chat_id, text[:1000])
        except Exception:
            pass

    def _add_output(self, text: str, newline: bool = True):
        """Append text to the output buffer (clean, no ANSI)."""
        if newline:
            self._output_lines.append(text)
        else:
            # Append to last line or create new
            if self._output_lines:
                self._output_lines[-1] += text
            else:
                self._output_lines.append(text)
        # Trim buffer
        if len(self._output_lines) > self._max_buffer_lines:
            self._output_lines = self._output_lines[-self._max_buffer_lines:]

    def send_prompt(self, instruction: str, origin: str = "cli", telegram_chat_id: Optional[str] = None) -> bool:
        """Send a prompt to pi via RPC. Returns True if sent successfully."""
        if not self._process or not self._running:
            if not self.start():
                return False

        # Track origin for auto-overview on completion
        if origin:
            self._origin = origin
        if telegram_chat_id:
            self._telegram_chat_id = telegram_chat_id

        cmd = {"type": "prompt", "message": instruction}

        # Apply streaming behavior if currently running
        if self.status == "running":
            cmd["streamingBehavior"] = "steer"

        try:
            self._process.stdin.write(json.dumps(cmd) + "\n")
            self._process.stdin.flush()
            self._add_output(f"\n>>> {instruction}")
            self.status = "running"
            return True
        except Exception as e:
            logger.error(f"PiRpcSession: failed to send prompt: {e}")
            self._running = False
            return False

    def send_and_wait(self, instruction: str, timeout: int = 120, poll_interval: float = 2.0) -> Optional[str]:
        """Send a prompt to pi and wait for completion. Returns the last assistant message, or None on failure."""
        import time

        if not self._process or not self._running:
            if not self.start():
                return None

        # If pi is already running, steer instead
        if self.status == "running":
            success = self.steer(instruction)
            if not success:
                return None
        else:
            success = self.send_prompt(instruction)
            if not success:
                return None

        # Poll until status changes from "running" to something else
        start = time.time()
        while time.time() - start < timeout:
            if self.status not in ("running", "idle"):
                break
            time.sleep(poll_interval)

        if self.status == "running":
            # Timed out
            logger.warning(f"PiRpcSession: timed out after {timeout}s waiting for response")
            return f"Timed out after {timeout} seconds. Check the terminal tab for progress."

        # Return the last assistant message
        result = self.get_last_assistant_message()
        if not result:
            # Fallback: return buffer tail
            buf = self.get_buffer(50)
            if buf:
                return buf[-2000:] if len(buf) > 2000 else buf
            return "Pi completed but produced no output."
        return result

    def steer(self, instruction: str) -> bool:
        """Send a steering message while the agent is running."""
        if not self._process or not self._running:
            return False
        try:
            cmd = {"type": "steer", "message": instruction}
            self._process.stdin.write(json.dumps(cmd) + "\n")
            self._process.stdin.flush()
            self._add_output(f"\n>>> [steer] {instruction}")
            return True
        except Exception as e:
            logger.error(f"PiRpcSession: failed to steer: {e}")
            return False

    def abort(self) -> bool:
        """Abort the current agent operation."""
        if not self._process or not self._running:
            return False
        try:
            cmd = {"type": "abort"}
            self._process.stdin.write(json.dumps(cmd) + "\n")
            self._process.stdin.flush()
            self.status = "aborted"
            self._add_output("\n[Aborted]")
            return True
        except Exception as e:
            logger.error(f"PiRpcSession: failed to abort: {e}")
            return False

    def get_state(self) -> Optional[dict]:
        """Query pi for current state (model, session info, etc.)."""
        if not self._process or not self._running:
            return None
        try:
            cmd = {"type": "get_state"}
            self._process.stdin.write(json.dumps(cmd) + "\n")
            self._process.stdin.flush()
            # Note: response comes asynchronously via stdout events
            return {"status": self.status}
        except Exception as e:
            logger.error(f"PiRpcSession: failed to get state: {e}")
            return None

    def get_buffer(self, lines: int = 500) -> str:
        """Return the last N lines of clean output (no ANSI codes)."""
        return "\n".join(self._output_lines[-lines:])

    def get_messages(self) -> List[dict]:
        """Return the conversation transcript as serializable dicts."""
        return [
            {
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_result": m.tool_result[:2000],
                "is_error": m.is_error,
            }
            for m in self.messages
        ]

    def get_last_assistant_message(self) -> str:
        """Get the content of the last assistant message."""
        for m in reversed(self.messages):
            if m.role == "assistant" and m.content:
                return m.content
        return ""

    def on_event(self, callback: Callable):
        """Register a callback for real-time RPC events."""
        self._on_event_callbacks.append(callback)

    def add_event_callback(self, callback: Callable):
        """Register a callback for real-time RPC events (alias for on_event)."""
        self._on_event_callbacks.append(callback)

    def remove_event_callback(self, callback: Callable):
        """Remove a previously registered event callback."""
        try:
            self._on_event_callbacks.remove(callback)
        except ValueError:
            pass

    @property
    def is_alive(self) -> bool:
        if not self._process:
            return False
        return self._process.poll() is None

    async def kill(self):
        """Kill the pi RPC process."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                await asyncio.sleep(1)
                if self._process.poll() is None:
                    self._process.kill()
                self._process.wait(timeout=3)
            except Exception:
                pass
            self._process = None
        self.status = "disconnected"


# ── Global session registry ──────────────────────────────────────────────
_rpc_sessions: Dict[int, PiRpcSession] = {}


def get_rpc_session(project_id: int) -> Optional[PiRpcSession]:
    """Get existing RPC session for a project."""
    return _rpc_sessions.get(project_id)


async def get_or_create_rpc_session(project_id: int, cwd: str, append_system_prompt: str = "") -> PiRpcSession:
    """Get or create an RPC session for a project."""
    session = _rpc_sessions.get(project_id)
    if session and session.is_alive:
        return session
    # Clean up dead session
    if session:
        await session.kill()
    session = PiRpcSession(project_id, cwd, append_system_prompt=append_system_prompt)
    session.start()
    _rpc_sessions[project_id] = session
    return session


async def kill_rpc_session(project_id: int):
    """Kill and remove an RPC session."""
    session = _rpc_sessions.pop(project_id, None)
    if session:
        await session.kill()


# ── Cleanup on exit ────────────────────────────────────────────────────────
import atexit

def _kill_all_rpc_on_exit():
    """atexit handler: kill all pi RPC subprocesses on app exit."""
    killed = 0
    for pid_key, rpc in list(_rpc_sessions.items()):
        try:
            if rpc._process and rpc._process.poll() is None:
                rpc._process.kill()
                rpc._process.wait(timeout=1)
                killed += 1
        except Exception:
            pass
    _rpc_sessions.clear()
    if killed:
        logger.info(f"Cleanup on exit: killed {killed} pi RPC subprocesses")

atexit.register(_kill_all_rpc_on_exit)