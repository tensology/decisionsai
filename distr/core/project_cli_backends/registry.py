"""Registry and adapters for project coding CLI backends."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .base import BackendStatus, BackendTaskResult, EventCallback, ProjectCliBackend, ProjectTask
from .contracts import BackendCapabilities
from .timing import ollama_model_loaded, resolve_worker_timing

logger = logging.getLogger(__name__)

# Pi JSON mode writes one complete event per line. Web/search and browser tool
# results can legitimately exceed asyncio's 64 KiB StreamReader default. Keep
# the per-line ceiling bounded, but large enough that transport does not turn a
# valid tool result into a false model failure.
PI_JSONL_STREAM_LIMIT = 4 * 1024 * 1024

DEFAULT_BACKEND_ID = "pi"

_ONE_SHOT_PROCESS_LOCK = threading.RLock()
_ONE_SHOT_PROCESSES: dict[tuple[int, str, int | None], asyncio.subprocess.Process] = {}
_KIRO_SESSION_CONNECTED: dict[int, bool] = {}


def _normalize_board_id(board_id: int | None) -> int | None:
    try:
        if board_id in (None, "", False):
            return None
        return int(board_id)
    except Exception:
        return None


def _oneshot_key(project_id: int, backend_id: str, board_id: int | None = None) -> tuple[int, str, int | None]:
    return int(project_id), str(backend_id or "").strip(), _normalize_board_id(board_id)


def _register_oneshot_process(
    project_id: int,
    backend_id: str,
    process: asyncio.subprocess.Process,
    *,
    board_id: int | None = None,
) -> None:
    with _ONE_SHOT_PROCESS_LOCK:
        _ONE_SHOT_PROCESSES[_oneshot_key(project_id, backend_id, board_id)] = process


def _clear_oneshot_process(
    project_id: int,
    backend_id: str,
    process: asyncio.subprocess.Process | None = None,
    *,
    board_id: int | None = None,
) -> None:
    key = _oneshot_key(project_id, backend_id, board_id)
    with _ONE_SHOT_PROCESS_LOCK:
        current = _ONE_SHOT_PROCESSES.get(key)
        if process is not None and current is not process:
            return
        _ONE_SHOT_PROCESSES.pop(key, None)


async def abort_backend_process(project_id: int, backend_id: str, *, board_id: int | None = None) -> bool:
    key = _oneshot_key(project_id, backend_id, board_id)
    with _ONE_SHOT_PROCESS_LOCK:
        process = _ONE_SHOT_PROCESSES.get(key)
    if not process:
        return False
    try:
        process.terminate()
    except ProcessLookupError:
        _clear_oneshot_process(project_id, backend_id, process, board_id=board_id)
        return False
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    _clear_oneshot_process(project_id, backend_id, process, board_id=board_id)
    return True


def terminate_backend_process(project_id: int, backend_id: str, *, board_id: int | None = None) -> bool:
    """Thread-safe best-effort termination used by synchronous run cancellation."""
    key = _oneshot_key(project_id, backend_id, board_id)
    with _ONE_SHOT_PROCESS_LOCK:
        process = _ONE_SHOT_PROCESSES.pop(key, None)
    if not process:
        return False
    try:
        process.terminate()
    except ProcessLookupError:
        return False
    except Exception:
        try:
            process.kill()
        except Exception:
            return False
    return True


def _decisions_api_base() -> str:
    from distr.core.web_runtime import get_local_web_base_url

    return (os.environ.get("DECISIONS_API_BASE") or get_local_web_base_url()).rstrip("/")


def _internal_api_token() -> str:
    try:
        from distr.gui.web.security import get_internal_api_token

        return get_internal_api_token()
    except Exception:
        return (os.environ.get("DECISIONSAI_INTERNAL_API_TOKEN") or "").strip()


def _with_internal_token(url: str) -> str:
    token = _internal_api_token()
    if not token:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("internal_token", token)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _first_executable(candidates: list[str]) -> Optional[str]:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _version_for(path: Optional[str], args: list[str] | None = None) -> Optional[str]:
    if not path:
        return None
    try:
        result = subprocess.run(
            [path] + (args or ["--version"]),
            capture_output=True,
            text=True,
            timeout=5,
        )
        text = (result.stdout or result.stderr or "").strip()
        return text.splitlines()[0] if text else None
    except Exception:
        return None


def _cursor_api_key() -> str:
    """Resolve Cursor API key from env or Decisions settings."""
    env_key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if env_key:
        return env_key
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db() or {}
        return (settings.get("cursor_key") or "").strip()
    except Exception:
        return ""


def _cursor_auth_ready(path: Optional[str]) -> bool:
    if _cursor_api_key():
        return True
    if not path:
        return False
    try:
        result = subprocess.run(
            [path, "status"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        if "not logged in" in text or "authentication required" in text:
            return False
        if "logged in" in text or "authenticated" in text:
            return True
        return result.returncode == 0 and "login" not in text
    except Exception:
        return False


def _emit(on_event: Optional[EventCallback], event: dict[str, Any]) -> None:
    if not on_event:
        return
    try:
        on_event(event)
    except Exception:
        pass


def _compact_cli_output(output: str, limit: int = 6000) -> str:
    """Keep useful CLI output while avoiding huge warning preambles."""
    text = (output or "").strip()
    if len(text) <= limit:
        return text
    marker_budget = 80
    head_len = min(1200, max(200, limit // 4))
    tail_len = max(200, limit - head_len - marker_budget)
    head = text[:head_len].rstrip()
    tail = text[-tail_len:].lstrip()
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n[... omitted {omitted} chars of CLI output ...]\n\n{tail}"


def _preferred_model_error(errors: list[str]) -> str:
    """Prefer the provider failure a human can act on over downstream parser noise."""
    clean = [str(value or "").strip() for value in errors if str(value or "").strip()]
    if not clean:
        return ""
    actionable_markers = (
        "429",
        "rate limit",
        "quota",
        "credit",
        "insufficient",
        "unauthorized",
        "authentication",
        "forbidden",
        "model unavailable",
        "provider unavailable",
    )
    for value in reversed(clean):
        if any(marker in value.lower() for marker in actionable_markers):
            return value
    return clean[-1]


class _BoundedCliOutput:
    """Retain useful CLI evidence without allowing chatty workers to exhaust RAM."""

    def __init__(self, *, head_limit: int = 12_000, tail_limit: int = 120_000) -> None:
        self.head_limit = max(0, int(head_limit))
        self.tail_limit = max(0, int(tail_limit))
        self._head = ""
        self._tail = ""
        self._seen = 0

    def append(self, value: str) -> None:
        text = str(value or "")
        if not text:
            return
        self._seen += len(text)
        if len(self._head) < self.head_limit:
            missing = self.head_limit - len(self._head)
            self._head += text[:missing]
            text = text[missing:]
        if text and self.tail_limit:
            self._tail = (self._tail + text)[-self.tail_limit :]

    def render(self) -> str:
        retained = len(self._head) + len(self._tail)
        omitted = max(0, self._seen - retained)
        if not omitted:
            return f"{self._head}{self._tail}"
        return (
            f"{self._head}\n\n"
            f"[... omitted {omitted} chars while bounding live CLI output ...]\n\n"
            f"{self._tail}"
        )


def _pi_print_command(pi_path: str, task: ProjectTask) -> list[str]:
    """Build a terminal Pi invocation that honours the resolved workflow route."""
    # JSON mode exposes model text, tool calls and errors as they happen. Text
    # mode buffers the whole run, leaving the workflow UI with a heartbeat but
    # no evidence of whether the worker is thinking, using tools or stuck.
    command = [pi_path, "-p", "--mode", "json"]
    if task.adapter_options.get("disable_tools"):
        command.append("--no-tools")
    elif task.adapter_options.get("read_only_expected"):
        # Pi has no filesystem sandbox, but its explicit tool allowlist gives
        # planning/review workers inspection capability without bash/edit/write.
        # This prevents an overeager model from installing dependencies or
        # modifying the project while it is only meant to produce evidence.
        # The installed Pi web-search extension exposes read-only research
        # tools. Planning/review steps need these for source URLs without
        # granting bash, edit, or write access.
        read_only_tools = "read,grep,find,ls"
        if str(task.adapter_options.get("step_role") or "").strip().lower() in {"review", "validation"}:
            read_only_tools += ",web_search,web_fetch"
        command.extend(["--tools", read_only_tools])
    model = str(task.model or "").strip()
    provider = str(task.adapter_options.get("model_provider") or "").strip()
    if model and model != "auto":
        # Pi's catalog has nested model ids (for example provider=kilocode,
        # model=openrouter/free). A slash therefore does not mean the provider
        # is encoded for the CLI; honour the resolved provider explicitly.
        if provider:
            command.extend(["--provider", provider])
        command.extend(["--model", model])
    system_prompt = f"You are working on project: {task.project_name}"
    if task.origin == "workflow":
        expected_outputs = [
            str(item).strip()
            for item in (task.adapter_options.get("expected_outputs") or [])
            if str(item).strip()
        ]
        if expected_outputs:
            system_prompt += (
                "\nBegin your final response with every exact workflow field label below. "
                "Keep each value concise; use N/A plus a ticket-specific reason when needed:\n"
                + "\n".join(f"{item}: <value>" for item in expected_outputs)
            )
        system_prompt += (
            "\nComplete only the current workflow step. Your final response must end with this "
            "plain-text contract on separate lines:\n"
            "Status: <choose exactly one: completed, failed, or needs_input>\n"
            "Summary: <specific outcome>\n"
            "Files changed: <paths or none>\n"
            "Commands run: <commands or none>\n"
            "Blockers: <details or none>\n"
            "Do not copy the alternatives into Status; choose one exact value. "
            "Do not omit Status, even when no files changed."
        )
    command.extend([
        "--append-system-prompt",
        system_prompt,
        task.instruction,
    ])
    return command


def _pi_message_text(message: Any) -> str:
    """Extract assistant text from a Pi JSON event message without raw logs."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    )


def _workflow_report_error(
    output: str,
    worker_name: str,
    *,
    expected_outputs: Optional[list[str]] = None,
) -> str:
    """Require a terminal report, while accepting a complete named handoff.

    Small local models occasionally return every field requested by a workflow
    step but omit the redundant ``Status`` line.  The workflow's deterministic
    validator still checks these fields and all downstream evidence; the CLI
    adapter should not discard that useful result before validation can run.
    """
    text = str(output or "").strip()
    if not text:
        return (
            f"{worker_name} exited successfully but returned no completion report; "
            "the workflow treats this as a no-op instead of completed work."
        )
    required = [str(item).strip() for item in (expected_outputs or []) if str(item).strip()]

    def has_named_output(name: str) -> bool:
        expected = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        # Small/local models frequently compress a valid handoff onto one line
        # using semicolons (``rerun_results: ...; next_action: ...``). Treat a
        # semicolon as a field boundary just like a newline, while retaining the
        # exact-label requirement so prose that merely mentions a field does not
        # satisfy the contract.
        for line in re.split(r"[;\n]", text):
            # Models commonly return ``**field:** value``, headings, list
            # items, or backticked labels. Those are presentation choices,
            # not missing workflow data.
            cleaned = re.sub(r"^[\s#>*+-]+", "", line).strip()
            cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
            label = cleaned.split(":", 1)[0]
            normalized = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
            if normalized == expected and ":" in cleaned:
                return True
        return False

    match = re.search(
        r"(?im)^\s*(?:\*\*)?Status(?:\*\*)?\s*:\s*(?:\*\*)?\s*(completed|failed|needs_input)\b",
        text,
    )
    if not match:
        match = re.search(
            r"(?im)^\s*\|\s*(?:\*\*)?Status(?:\*\*)?\s*\|\s*`?(completed|failed|needs_input)\b`?\s*\|",
            text,
        )
    if not match:
        lowered = text.lower()
        unresolved = re.search(
            r"(?im)^\s*(?:status|blockers?)\s*:\s*(?:failed|needs[_ ]?input|.+(?:missing|blocked|cannot))\b",
            text,
        )
        has_every_named_output = bool(required) and all(has_named_output(name) for name in required)
        if has_every_named_output and not unresolved and not any(
            marker in lowered
            for marker in ("insufficient credits", "rate limit", "quota", "llm call failed")
        ):
            return ""
        return (
            f"{worker_name} returned text without the required 'Status: completed' workflow report; "
            "the result remains unverified."
        )
    status = match.group(1).lower()
    if status != "completed":
        return f"{worker_name} reported workflow status {status}; the step was not completed."
    missing = [name for name in required if not has_named_output(name)]
    if missing:
        return (
            f"{worker_name} reported Status: completed but omitted required workflow fields: "
            + ", ".join(missing)
            + "."
        )
    return ""


def _pi_workflow_report_error(
    output: str,
    *,
    expected_outputs: Optional[list[str]] = None,
) -> str:
    return _workflow_report_error(output, "Pi", expected_outputs=expected_outputs)


def _git_status_short(folder: str) -> list[str]:
    """Return a compact dirty-worktree snapshot for audit context."""
    if not folder or not os.path.isdir(folder):
        return []
    try:
        from distr.core.rtk_support import run_argv_command

        result = run_argv_command(
            ["git", "status", "--short"],
            cwd=folder,
            timeout=8,
        )
        if result.returncode != 0:
            return []
        return [line for line in (result.stdout or "").splitlines() if line.strip()][:80]
    except Exception:
        return []


_READ_ONLY_SNAPSHOT_IGNORES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def _workspace_state_snapshot(folder: str) -> dict[str, tuple[int, int]]:
    """Capture a cheap file-state map for read-only workflow enforcement.

    Git porcelain collapses a whole new directory to ``?? directory/``. That
    misses writes inside already-untracked copied trees, so read-only planning
    and review steps also compare path, size and mtime while excluding dependency
    and build directories. The map stays in-process and only its compact delta
    is persisted.
    """
    snapshot: dict[str, tuple[int, int]] = {}
    if not folder or not os.path.isdir(folder):
        return snapshot
    root = os.path.abspath(folder)
    try:
        for current, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name not in _READ_ONLY_SNAPSHOT_IGNORES]
            for name in files:
                path = os.path.join(current, name)
                try:
                    stat = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                snapshot[os.path.relpath(path, root)] = (int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        return snapshot
    return snapshot


def _workspace_state_delta(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
    *,
    limit: int = 80,
) -> dict[str, Any]:
    added = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(path for path in set(before) & set(after) if before[path] != after[path])
    return {
        "changed": bool(added or deleted or modified),
        "added": added[:limit],
        "modified": modified[:limit],
        "deleted": deleted[:limit],
        "total_changed": len(added) + len(modified) + len(deleted),
        "truncated": len(added) + len(modified) + len(deleted) > limit,
    }


class PiBackend(ProjectCliBackend):
    id = "pi"
    name = "Pi"
    description = "Pi coding agent via RPC. This remains the default project backend."
    supports_rpc = True
    supports_install = False
    setup_instructions = "Install Pi with: npm install -g @mariozechner/pi-coding-agent"
    capabilities = BackendCapabilities(
        persistent_session=True,
        steering=True,
        resume=True,
        tools=True,
        files=True,
        structured_output=True,
        local_execution=True,
    )

    def steer(self, message: str, **context: Any) -> dict[str, Any]:
        from distr.core.pi_rpc import get_rpc_session

        project_id = context.get("project_id")
        rpc = get_rpc_session(int(project_id)) if project_id else None
        if rpc and rpc.is_alive:
            delivered = bool(rpc.steer(message))
            return {
                "success": delivered,
                "delivered": delivered,
                "method": "pi_rpc",
                "backend_id": self.id,
                "error": "" if delivered else "Pi RPC steer was not accepted",
            }
        return {
            "success": True,
            "delivered": False,
            "method": "queued",
            "backend_id": self.id,
            "error": "No live session; steering remains queued on the workflow run",
        }

    def check_availability(self) -> BackendStatus:
        from distr.core.pi_rpc import PiRpcSession

        path = PiRpcSession.find_pi()
        installed = bool(path)
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=installed,
            ready=installed,
            state="ready" if installed else "missing",
            message="Pi is installed and ready." if installed else "Pi is not installed.",
            path=path,
            version=_version_for(path),
            setup_required=not installed,
            setup_instructions=self.setup_instructions,
            supports_rpc=True,
            supports_install=False,
        )

    async def send_task(self, task: ProjectTask, on_event: Optional[EventCallback] = None) -> BackendTaskResult:
        from distr.core.pi_rpc import PiRpcSession, get_or_create_rpc_session

        pi_path = PiRpcSession.find_pi()
        if not pi_path:
            return BackendTaskResult(False, self.id, "pi", error=self.setup_instructions, session_id=task.audit_id)

        last_error = ""
        # Workflow steps require a terminal result before validation/routing.
        # Persistent Pi RPC is fire-and-forget here and has no workflow completion
        # callback, so reserve it for interactive project sessions.
        if task.origin != "workflow":
            try:
                rpc = await get_or_create_rpc_session(task.project_id, task.folder, board_id=task.board_id)
                if rpc.send_prompt(task.instruction, origin=task.origin):
                    return BackendTaskResult(True, self.id, "pi_rpc", session_id=task.audit_id)
            except Exception as exc:
                # Fall through to one-shot print mode. The caller still gets a clear
                # engine value so later routing/fallback can distinguish the path.
                last_error = str(exc)
            else:
                last_error = "Pi RPC did not accept the prompt."

        provider = str(task.adapter_options.get("model_provider") or "")
        loaded = None
        if task.model and (provider.lower() in {"", "ollama", "local"}):
            loaded = ollama_model_loaded(task.model)
        timing = resolve_worker_timing(
            backend_id=self.id,
            model=task.model or "auto",
            provider=provider,
            complexity=task.ticket_complexity,
            configured_timeout_seconds=task.adapter_options.get("timeout_seconds"),
            model_loaded=loaded,
        )

        started_at = time.monotonic()
        _emit(on_event, {
            "type": "backend_started",
            "backend": self.id,
            "model": task.model or "auto",
            "timeout_seconds": timing.timeout_seconds,
            "model_loaded": timing.model_loaded,
            "timing_rationale": timing.rationale,
            "message": f"Pi worker started; safety ceiling {timing.timeout_seconds}s ({timing.rationale})",
        })
        output_capture = _BoundedCliOutput(head_limit=4_000, tail_limit=40_000)
        assistant_deltas = _BoundedCliOutput(head_limit=8_000, tail_limit=120_000)
        assistant_messages = _BoundedCliOutput(head_limit=12_000, tail_limit=120_000)
        final_assistant = ""
        model_errors: list[str] = []
        progress_events = 0
        last_progress_at = started_at
        warned_no_progress = False
        timed_out = False
        saw_agent_end = False
        inspection_budget_exceeded = False
        inspection_tool_calls = 0
        raw_inspection_budget = task.adapter_options.get("inspection_budget")
        inspection_budget = raw_inspection_budget if isinstance(raw_inspection_budget, dict) else {}
        try:
            max_inspection_tool_calls = max(0, int(inspection_budget.get("max_tool_calls") or 0))
        except (TypeError, ValueError):
            max_inspection_tool_calls = 0
        inspection_enforcement = str(inspection_budget.get("enforcement") or "hard").strip().lower()
        try:
            hard_max_inspection_tool_calls = max(
                max_inspection_tool_calls,
                int(inspection_budget.get("hard_max_tool_calls") or max_inspection_tool_calls or 0),
            )
        except (TypeError, ValueError):
            hard_max_inspection_tool_calls = max_inspection_tool_calls
        inspection_budget_warned = False
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *_pi_print_command(pi_path, task),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=task.folder,
                limit=PI_JSONL_STREAM_LIMIT,
            )
            _register_oneshot_process(
                task.project_id,
                self.id,
                process,
                board_id=task.board_id,
            )
            assert process.stdout is not None
            while True:
                elapsed = time.monotonic() - started_at
                if elapsed >= timing.timeout_seconds:
                    timed_out = True
                    process.terminate()
                    break
                try:
                    raw = await asyncio.wait_for(process.stdout.readline(), timeout=10.0)
                except asyncio.TimeoutError:
                    raw = b""
                    if process.returncode is not None:
                        break
                    idle_seconds = round(time.monotonic() - last_progress_at, 1)
                    logger.info(
                        "Pi workflow heartbeat audit_id=%s elapsed=%.1fs idle=%.1fs model=%s",
                        task.audit_id, elapsed, idle_seconds, task.model or "auto",
                    )
                    progress_state = "working" if progress_events else (
                        "loading_model" if timing.model_loaded is False else "awaiting_first_output"
                    )
                    _emit(on_event, {
                        "type": "heartbeat",
                        "backend": self.id,
                        "model": task.model or "auto",
                        "elapsed_seconds": round(elapsed, 1),
                        "seconds_since_progress": idle_seconds,
                        "progress_events": progress_events,
                        "progress_state": progress_state,
                        "timeout_seconds": timing.timeout_seconds,
                        "model_loaded": timing.model_loaded,
                        "timing_rationale": timing.rationale,
                        "message": (
                            f"Pi worker: {progress_state.replace('_', ' ')} "
                            f"({int(elapsed)}s; last evidence {int(idle_seconds)}s ago)"
                        ),
                    })
                    warning_after = 300 if timing.model_loaded is not True else 120
                    if idle_seconds >= warning_after and not warned_no_progress:
                        warned_no_progress = True
                        _emit(on_event, {
                            "type": "progress_warning",
                            "backend": self.id,
                            "model": task.model or "auto",
                            "elapsed_seconds": round(elapsed, 1),
                            "seconds_since_progress": idle_seconds,
                            "message": (
                                "No model text or tool event has arrived yet; the run is still "
                                "cancelable and should be rerouted if this continues."
                            ),
                        })
                    continue
                if not raw:
                    if process.returncode is not None:
                        break
                    continue
                decoded = raw.decode(errors="replace").strip()
                if not decoded:
                    continue
                output_capture.append(decoded + "\n")
                try:
                    event = json.loads(decoded)
                except json.JSONDecodeError:
                    event = {
                        "type": "executor_output",
                        "message": decoded[:2000],
                    }
                progress_events += 1
                last_progress_at = time.monotonic()
                if isinstance(event, dict):
                    event_type = str(event.get("type") or "")
                    if event_type == "tool_execution_start" and max_inspection_tool_calls:
                        inspection_tool_calls += 1
                        if (
                            inspection_enforcement == "soft"
                            and inspection_tool_calls > max_inspection_tool_calls
                            and inspection_tool_calls <= hard_max_inspection_tool_calls
                            and not inspection_budget_warned
                        ):
                            inspection_budget_warned = True
                            _emit(on_event, {
                                "type": "inspection_budget_warning",
                                "backend": self.id,
                                "model": task.model or "auto",
                                "observed_tool_calls": inspection_tool_calls,
                                "max_tool_calls": max_inspection_tool_calls,
                                "hard_max_tool_calls": hard_max_inspection_tool_calls,
                                "message": (
                                    f"Inspection passed its {max_inspection_tool_calls}-call target; "
                                    f"allowing a bounded finish up to {hard_max_inspection_tool_calls} calls."
                                ),
                            })
                        hard_limit = (
                            hard_max_inspection_tool_calls
                            if inspection_enforcement == "soft"
                            else max_inspection_tool_calls
                        )
                        if inspection_tool_calls > hard_limit:
                            inspection_budget_exceeded = True
                            _emit(on_event, {
                                "type": "inspection_budget_exceeded",
                                "backend": self.id,
                                "model": task.model or "auto",
                                "observed_tool_calls": inspection_tool_calls,
                                "max_tool_calls": max_inspection_tool_calls,
                                "hard_max_tool_calls": hard_limit,
                                "message": (
                                    f"Inspection stopped after {inspection_tool_calls} tool calls; "
                                    f"this step's hard ceiling is {hard_limit}."
                                ),
                            })
                            process.terminate()
                            break
                    assistant_event = event.get("assistantMessageEvent")
                    if isinstance(assistant_event, dict) and assistant_event.get("type") == "text_delta":
                        assistant_deltas.append(str(assistant_event.get("delta") or ""))
                    if event_type == "message_end":
                        message = event.get("message")
                        if isinstance(message, dict) and message.get("role") == "assistant":
                            message_text = _pi_message_text(message).strip()
                            if message_text:
                                final_assistant = message_text
                                assistant_messages.append(message_text + "\n\n")
                    error_message = str(event.get("errorMessage") or "").strip()
                    if not error_message and isinstance(event.get("message"), dict):
                        error_message = str(event["message"].get("errorMessage") or "").strip()
                    if error_message:
                        model_errors.append(error_message)
                    _emit(on_event, event)
                    # In one-shot JSON mode Pi's ``agent_end`` event is the
                    # terminal protocol signal. Some provider/extension
                    # combinations leave the wrapper process alive after that
                    # signal, which previously made a completed workflow sit
                    # silent until the outer 15-minute timeout cancelled it.
                    # Stop consuming after the terminal event; the bounded
                    # assistant report above is already authoritative and is
                    # still checked against the workflow return contract.
                    if event_type == "agent_end":
                        saw_agent_end = True
                        break
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0 if saw_agent_end else 5.0)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.terminate()
            raise
        except Exception as exc:
            model_errors.append(str(exc))
        finally:
            if process is not None:
                _clear_oneshot_process(
                    task.project_id,
                    self.id,
                    process,
                    board_id=task.board_id,
                )

        # Pi can emit a complete workflow report and then add a short epilogue
        # in a second assistant message. Selecting only the final message loses
        # the named handoff fields and Status contract, causing a false failure
        # and unnecessary provider escalation. Preserve the bounded sequence of
        # assistant messages; raw JSON/tool logs remain in execution events.
        output = (
            assistant_messages.render()
            or final_assistant
            or assistant_deltas.render()
        ).strip()
        returncode = process.returncode if process is not None else 1
        report_error = (
            _pi_workflow_report_error(
                output,
                expected_outputs=task.adapter_options.get("expected_outputs"),
            )
            if task.origin == "workflow"
            else ""
        )
        transient_after_report = bool(model_errors) and all(
            any(marker in str(item or "").lower() for marker in (
                "connection error",
                "connection reset",
                "stream closed",
                "unexpected eof",
            ))
            for item in model_errors
        )
        ignored_transient_errors = transient_after_report and not report_error
        terminal_event_ok = (
            saw_agent_end
            and not report_error
            and (not model_errors or ignored_transient_errors)
        )
        if inspection_budget_exceeded:
            error = (
                f"Inspection budget exceeded: model used {inspection_tool_calls} tool calls; "
                f"this step's hard ceiling is {hard_max_inspection_tool_calls if inspection_enforcement == 'soft' else max_inspection_tool_calls}."
            )
        elif timed_out:
            error = (
                f"Pi worker reached its {timing.timeout_seconds}s safety ceiling "
                f"({timing.rationale}); the workflow was stopped cleanly."
            )
        elif model_errors and not ignored_transient_errors:
            error = _preferred_model_error(model_errors)
        elif returncode != 0 and not terminal_event_ok:
            error = f"Pi exited with code {returncode}."
        else:
            error = report_error
        ok = (
            (returncode == 0 or terminal_event_ok)
            and not timed_out
            and (not model_errors or ignored_transient_errors)
            and not error
        )
        if not output and output_capture.render().strip() and not error:
            error = _pi_workflow_report_error("") if task.origin == "workflow" else "Pi returned no assistant text."
            ok = False
        _emit(on_event, {
            "type": "backend_finished",
            "backend": self.id,
            "model": task.model or "auto",
            "elapsed_seconds": round(time.monotonic() - started_at, 1),
            "success": ok,
        })
        return BackendTaskResult(
            ok,
            self.id,
            "pi_cli",
            # Preserve both the opening handoff fields and terminal contract.
            # A hard prefix slice silently removed fields placed at the end of
            # otherwise successful local-model responses and caused false
            # validation failures plus pointless retries.
            output=_compact_cli_output(output, limit=12_000),
            error=error or ("" if ok else last_error),
            session_id=task.audit_id,
        )

    def get_messages(self, project_id: int) -> list[dict[str, Any]]:
        from distr.core.pi_rpc import get_rpc_session

        rpc = get_rpc_session(project_id)
        if not rpc:
            return []
        return rpc.get_messages()

    def get_buffer(self, project_id: int, lines: int = 100) -> str:
        from distr.core.pi_rpc import get_rpc_session

        rpc = get_rpc_session(project_id)
        return rpc.get_buffer(lines) if rpc else ""

    async def restart(self, project_id: int, folder: str) -> BackendTaskResult:
        from distr.core.pi_rpc import get_or_create_rpc_session, kill_rpc_session

        await kill_rpc_session(project_id)
        rpc = await get_or_create_rpc_session(project_id, folder)
        return BackendTaskResult(True, self.id, "pi_rpc", output="", error="" if rpc.is_alive else "Pi session did not start.")


class OneShotCliBackend(ProjectCliBackend):
    executable_candidates: list[str] = []
    command_args: list[str] = []
    capabilities = BackendCapabilities(
        steering=True,
        tools=True,
        files=True,
        structured_output=True,
    )

    def steer(self, message: str, **context: Any) -> dict[str, Any]:
        return {
            "success": True,
            "delivered": False,
            "method": "queued",
            "backend_id": self.id,
        }

    def check_availability(self) -> BackendStatus:
        path = _first_executable(self.executable_candidates)
        installed = bool(path)
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=installed,
            ready=installed,
            state="ready" if installed else "missing",
            message=f"{self.name} is installed and ready." if installed else f"{self.name} command was not found.",
            path=path,
            version=_version_for(path),
            setup_required=not installed,
            setup_instructions=self.setup_instructions,
            supports_rpc=False,
            supports_install=False,
        )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        return [executable] + self.command_args + [task.instruction]

    def _subprocess_env(self) -> dict[str, str]:
        return {**os.environ, "TERM": "dumb"}

    def _task_subprocess_env(self, task: ProjectTask) -> dict[str, str]:
        return self._subprocess_env()

    def _result_output(self, output: str) -> str:
        """Return the compact result handed to validation and the next step."""
        return _compact_cli_output(output)

    async def send_task(self, task: ProjectTask, on_event: Optional[EventCallback] = None) -> BackendTaskResult:
        status = self.setup_status()
        if not status.ready or not status.path:
            msg = (status.message or status.setup_instructions or f"{self.name} is not ready.").strip()
            return BackendTaskResult(False, self.id, self.id, error=msg, session_id=task.audit_id)

        _emit(on_event, {"type": "agent_start", "backend": self.id})
        _emit(on_event, {"type": "message_start", "message": {"role": "user", "content": task.instruction}})
        _emit(on_event, {"type": "message_end", "message": {"role": "user", "content": task.instruction}})
        _emit(on_event, {"type": "message_update", "assistantMessageEvent": {"type": "start"}})
        _emit(on_event, {"type": "message_update", "assistantMessageEvent": {"type": "text_start"}})

        cmd = self._build_command(status.path, task)
        _emit(on_event, {
            "type": "command_start",
            "backend": self.id,
            "command": cmd[:-1] + ["<instruction>"],
            "cwd": task.folder,
        })
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=task.folder,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._task_subprocess_env(task),
            )
            _register_oneshot_process(task.project_id, self.id, process, board_id=task.board_id)
        except Exception as exc:
            msg = f"Failed to start {self.name}: {exc}"
            _emit(on_event, {"type": "error", "message": msg})
            _emit(on_event, {"type": "agent_end", "backend": self.id})
            return BackendTaskResult(False, self.id, self.id, error=msg, session_id=task.audit_id)

        output_buffer = _BoundedCliOutput()
        started_at = time.monotonic()
        last_heartbeat_at = started_at
        try:
            safety_ceiling = max(
                1,
                int((task.adapter_options or {}).get("timeout_seconds") or 900),
            )
        except (TypeError, ValueError):
            safety_ceiling = 900
        timed_out = False

        def _emit_liveness_heartbeat() -> None:
            nonlocal last_heartbeat_at
            now = time.monotonic()
            elapsed = round(now - started_at, 1)
            _emit(on_event, {
                "type": "heartbeat",
                "backend": self.id,
                "model": task.model or "auto",
                "elapsed_seconds": elapsed,
                "message": f"{self.name} is still running ({int(elapsed)}s)",
            })
            last_heartbeat_at = now

        try:
            assert process.stdout is not None
            while True:
                remaining = safety_ceiling - (time.monotonic() - started_at)
                if remaining <= 0:
                    timed_out = True
                    process.terminate()
                    break
                try:
                    chunk = await asyncio.wait_for(
                        process.stdout.read(4096),
                        timeout=min(10.0, max(0.1, remaining)),
                    )
                except asyncio.TimeoutError:
                    if time.monotonic() - started_at >= safety_ceiling:
                        timed_out = True
                        process.terminate()
                        break
                    _emit_liveness_heartbeat()
                    continue
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                output_buffer.append(text)
                _emit(on_event, {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": text}})
                # A chatty CLI can continuously produce output, so the read
                # timeout above never fires. Emit a real heartbeat on elapsed
                # time as well; otherwise Mission Control falsely reports a
                # dead worker while it is actively streaming commands.
                if time.monotonic() - last_heartbeat_at >= 10.0:
                    _emit_liveness_heartbeat()
            try:
                rc = await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                rc = await process.wait()
        except asyncio.CancelledError:
            # Workflow timeouts and user cancellation must stop the underlying
            # provider process too. Without this, the run can be marked failed
            # while Codex/Pi/Claude continues consuming resources in the background.
            await abort_backend_process(task.project_id, self.id, board_id=task.board_id)
            _emit(on_event, {"type": "error", "message": f"{self.name} execution cancelled."})
            _emit(on_event, {"type": "agent_end", "backend": self.id})
            raise
        except Exception as exc:
            try:
                process.kill()
            except Exception:
                pass
            rc = -1
            output_buffer.append(f"\n{exc}")
        finally:
            _clear_oneshot_process(task.project_id, self.id, process, board_id=task.board_id)

        output = output_buffer.render().strip()
        _emit(on_event, {"type": "message_update", "assistantMessageEvent": {"type": "done"}})
        _emit(on_event, {"type": "message_end", "message": {"role": "assistant", "content": output}})
        _emit(on_event, {"type": "agent_end", "backend": self.id})
        compact_output = self._result_output(output)
        if timed_out:
            error = f"{self.name} reached its {safety_ceiling}s safety ceiling and was stopped."
            return BackendTaskResult(
                False,
                self.id,
                self.id,
                output=compact_output,
                error=error,
                session_id=task.audit_id,
            )
        if rc == 0:
            return BackendTaskResult(True, self.id, self.id, output=compact_output, session_id=task.audit_id)
        return BackendTaskResult(False, self.id, self.id, output=compact_output, error=compact_output or f"{self.name} exited with code {rc}", session_id=task.audit_id)


class CursorBackend(OneShotCliBackend):
    id = "cursor"
    name = "Cursor CLI"
    description = "Cursor's project coding CLI backend."
    executable_candidates = ["cursor-agent"]
    command_args = ["--trust", "-p"]
    setup_instructions = (
        "Install Cursor CLI support from Cursor, then make sure the cursor-agent command is on PATH."
    )

    def _subprocess_env(self) -> dict[str, str]:
        env = super()._subprocess_env()
        key = _cursor_api_key()
        if key:
            env["CURSOR_API_KEY"] = key
        return env

    def _task_subprocess_env(self, task: ProjectTask) -> dict[str, str]:
        env = super()._task_subprocess_env(task)
        if task.workflow_id and task.run_id:
            env["DECISIONS_CALLBACK_URL"] = _with_internal_token(
                f"{_decisions_api_base()}/api/workflows/{int(task.workflow_id)}/runs/"
                f"{int(task.run_id)}/codex-events"
            )
        return env

    def setup_status(self) -> BackendStatus:
        status = super().setup_status()
        status.can_receive_remote_handoff = bool(status.ready)
        status.handoff_method = "one_shot_cli_with_callback"
        status.reporter_path = os.environ.get(
            "DECISIONS_CURSOR_REPORTER",
            os.path.expanduser("~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py"),
        )
        if status.ready and not _cursor_auth_ready(status.path):
            status.ready = False
            status.state = "auth_required"
            status.message = (
                "Cursor CLI authentication required. Add a Cursor API key in Third Party API Keys, "
                "set CURSOR_API_KEY, or run cursor-agent login."
            )
            status.setup_required = True
            status.can_receive_remote_handoff = False
        return status

    def _callback_instruction(self, task: ProjectTask) -> str:
        if not task.workflow_id or not task.run_id:
            return ""
        api_base = _decisions_api_base()
        reporter = os.environ.get(
            "DECISIONS_CURSOR_REPORTER",
            os.path.expanduser("~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py"),
        )
        meta = {
            "api_base": api_base,
            "callback_url_env": "DECISIONS_CALLBACK_URL",
            "workflow_id": task.workflow_id,
            "run_id": task.run_id,
            "step_id": task.step_id,
            "ticket_id": task.ticket_id,
            "project_id": task.project_id,
            "execution_session_id": task.execution_session_id,
            "reporter": reporter,
        }
        return (
            "[DECISIONS CURSOR CALLBACK]\n"
            f"{json.dumps(meta, ensure_ascii=False, separators=(',', ':'))}\n"
            "When this work is opened, prompted, steered, paused, interrupted, blocked, completed, "
            "or materially updated, report the event back to DecisionsAI if DecisionsAI is reachable. "
            "Prefer the reporter script when available:\n"
            f"python3 {json.dumps(reporter)} --callback-url \"$DECISIONS_CALLBACK_URL\" "
            "--event-type cursor_prompt_submitted --status observed --message \"<what the human asked or changed>\"\n"
            "Use event_type values: cursor_started, cursor_prompt_submitted, user_steer, cursor_waiting, "
            "cursor_interrupted, cursor_progress, cursor_completed, cursor_failed, cursor_needs_input.\n"
            "Do not wait until the final answer if the human submits another prompt, changes direction, "
            "or adds constraints mid-run.\n"
            "[/DECISIONS CURSOR CALLBACK]\n\n"
        )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        if task.model and task.model != "auto":
            cmd += ["--model", task.model]
        return cmd + [self._callback_instruction(task) + task.instruction]



class IdeHandoffBackend(ProjectCliBackend):
    """Hand work to the Cursor/Codex IDE via a visible work packet and plugin bridge."""

    id = ""
    name = ""
    description = ""
    plugin_label = ""
    setup_instructions = (
        "Install Cursor or VS Code with the DecisionsAI plugin, then make sure the cursor or code command is on PATH."
    )
    capabilities = BackendCapabilities(
        persistent_session=True,
        steering=True,
        resume=True,
        tools=True,
        files=True,
        images=True,
    )

    def steer(self, message: str, **context: Any) -> dict[str, Any]:
        return {
            "success": True,
            "delivered": False,
            "method": "queued",
            "backend_id": self.id,
        }

    def check_availability(self) -> BackendStatus:
        from .ide_handoff import _ide_open_command

        path = _ide_open_command()
        installed = bool(path)
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=installed,
            ready=installed,
            state="ready" if installed else "missing",
            message=f"{self.name} is ready for IDE handoff." if installed else f"{self.name} requires Cursor or VS Code on PATH.",
            path=path,
            setup_required=not installed,
            setup_instructions=self.setup_instructions,
            supports_rpc=False,
            supports_install=False,
        )

    def setup_status(self) -> BackendStatus:
        status = self.check_availability()
        status.can_receive_remote_handoff = bool(status.ready)
        status.handoff_method = "ide_work_packet"
        status.reporter_path = os.environ.get(
            "DECISIONS_CODEX_REPORTER" if self.id == "codex_ide" else "DECISIONS_CURSOR_REPORTER",
            os.path.expanduser(
                "~/plugins/decisions-codex/scripts/report_decisions_event.py"
                if self.id == "codex_ide"
                else "~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py"
            ),
        )
        return status

    async def send_task(self, task: ProjectTask, on_event: Optional[EventCallback] = None) -> BackendTaskResult:
        from .ide_handoff import (
            build_ide_callback_meta,
            open_ide_project,
            start_cursor_harness_agent,
            write_ide_work_packet,
            _reporter_path,
        )

        status = self.check_availability()
        if not status.ready:
            return BackendTaskResult(
                False,
                self.id,
                self.id,
                error=status.message,
                session_id=task.audit_id,
            )

        handoff_event_id = getattr(task, "handoff_event_id", None)
        meta = build_ide_callback_meta(task, backend_id=self.id, handoff_event_id=handoff_event_id)
        loop_summary = ""
        extra = getattr(task, "loop_context_summary", "") or ""
        if extra:
            loop_summary = str(extra)
        try:
            packet_path = write_ide_work_packet(
                task,
                backend_id=self.id,
                meta=meta,
                loop_context_summary=loop_summary,
            )
        except Exception as exc:
            return BackendTaskResult(
                False,
                self.id,
                self.id,
                error=f"Could not write IDE work packet: {exc}",
                session_id=task.audit_id,
            )

        harness: dict[str, Any] = {}
        if self.id == "cursor_ide":
            harness = start_cursor_harness_agent(task, packet_path)

        opened = open_ide_project(task.folder, packet_path)
        _emit(
            on_event,
            {
                "type": "ide_handoff_dispatched",
                "backend": self.id,
                "work_packet": packet_path,
                "opened": opened,
                "harness_started": bool(harness.get("started")),
            },
        )
        output_lines: list[str] = []
        if self.id == "cursor_ide" and harness.get("started"):
            output_lines.append("Started the Cursor harness on your work packet.")
        elif self.id == "cursor_ide":
            reason = str(harness.get("reason") or "not ready").strip()
            output_lines.append(f"Opened Cursor; harness did not auto-start ({reason}).")
        else:
            output_lines.append(f"Opened {self.name} with your work packet.")
        if opened:
            output_lines.append("Project folder is open in the editor.")
        output_lines.extend(
            [
                f"Packet: {packet_path}",
                "",
                "The harness reports back when this step is done.",
                f"Manual reporter: python3 {json.dumps(_reporter_path(self.id))} "
                '--turn-output "Status: completed\\nSummary: ..."',
            ]
        )
        return BackendTaskResult(
            True,
            self.id,
            self.id,
            output="\n".join(output_lines),
            session_id=task.audit_id,
            waits_for_human=True,
            work_packet_path=packet_path,
        )


class CursorIdeBackend(IdeHandoffBackend):
    id = "cursor_ide"
    name = "Cursor IDE"
    description = "Visible Cursor IDE handoff via DecisionsAI work packet and plugin bridge."
    plugin_label = "Cursor"


class CodexIdeBackend(IdeHandoffBackend):
    id = "codex_ide"
    name = "Codex IDE"
    description = "Visible Codex IDE handoff via DecisionsAI work packet and plugin bridge."
    plugin_label = "Codex"


class ClaudeCodeBackend(OneShotCliBackend):
    id = "claude_code"
    name = "Claude Code"
    description = "Anthropic Claude Code CLI backend."
    executable_candidates = ["claude"]
    command_args = ["-p"]
    setup_instructions = "Install Claude Code and authenticate it, then make sure the claude command is on PATH."

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        if task.model and task.model != "default":
            cmd += ["--model", task.model]
        return cmd + [task.instruction]


class CodexBackend(OneShotCliBackend):
    id = "codex"
    name = "Codex CLI"
    description = "OpenAI Codex CLI backend for project implementation tasks."
    executable_candidates = ["codex"]
    command_args = ["exec"]
    setup_instructions = (
        "Install and authenticate Codex CLI, then make sure the codex command is on PATH."
    )

    def setup_status(self) -> BackendStatus:
        status = super().setup_status()
        status.can_receive_remote_handoff = bool(status.ready)
        status.handoff_method = "one_shot_cli_with_callback"
        status.reporter_path = os.environ.get(
            "DECISIONS_CODEX_REPORTER",
            os.path.expanduser("~/plugins/decisions-codex/scripts/report_decisions_event.py"),
        )
        return status

    def _callback_instruction(self, task: ProjectTask) -> str:
        if not task.workflow_id or not task.run_id:
            return ""
        api_base = _decisions_api_base()
        reporter = os.environ.get(
            "DECISIONS_CODEX_REPORTER",
            os.path.expanduser("~/plugins/decisions-codex/scripts/report_decisions_event.py"),
        )
        meta = {
            "api_base": api_base,
            "callback_url_env": "DECISIONS_CALLBACK_URL",
            "workflow_id": task.workflow_id,
            "run_id": task.run_id,
            "step_id": task.step_id,
            "ticket_id": task.ticket_id,
            "project_id": task.project_id,
            "execution_session_id": task.execution_session_id,
            "reporter": reporter,
        }
        return (
            "[DECISIONS CODEX CALLBACK]\n"
            f"{json.dumps(meta, ensure_ascii=False, separators=(',', ':'))}\n"
            "When this work is opened, prompted, steered, paused, interrupted, blocked, completed, "
            "or materially updated, report the event back to DecisionsAI if DecisionsAI is reachable. "
            "Prefer the reporter script when available:\n"
            f"python3 {json.dumps(reporter)} --callback-url \"$DECISIONS_CALLBACK_URL\" "
            "--event-type codex_prompt_submitted --status observed --message \"<what the human asked or changed>\"\n"
            "Use event_type values: codex_started, codex_prompt_submitted, user_steer, codex_waiting, codex_interrupted, "
            "codex_progress, codex_completed, codex_failed, codex_needs_input.\n"
            "Do not wait until the final answer if the human submits another prompt, changes direction, "
            "or adds constraints mid-run.\n"
            "[/DECISIONS CODEX CALLBACK]\n\n"
        )

    def _task_subprocess_env(self, task: ProjectTask) -> dict[str, str]:
        env = super()._task_subprocess_env(task)
        if task.workflow_id and task.run_id:
            env["DECISIONS_CALLBACK_URL"] = _with_internal_token(
                f"{_decisions_api_base()}/api/workflows/{int(task.workflow_id)}/runs/"
                f"{int(task.run_id)}/codex-events"
            )
        return env

    def _result_output(self, output: str) -> str:
        """Keep Codex's final contract, not its CLI banner/MCP transcript.

        The raw stream is already durable in ProjectExecutionEvent for Mission
        Control. Passing it into validation and the next model wastes context
        and can bury the actual result under startup warnings.
        """
        text = str(output or "").strip()
        matches = list(re.finditer(r"(?im)^\s*(?:\*\*)?Status(?:\*\*)?\s*:\s*", text))
        if matches:
            return _compact_cli_output(text[matches[-1].start():], limit=12_000)
        return super()._result_output(text)

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        # Planning and independent-review steps are contracts for observation,
        # not implementation. Enforce that boundary in Codex itself instead of
        # merely detecting writes after the worker has polluted the project.
        # An explicit environment override still controls mutable steps.
        sandbox = (
            "read-only"
            if task.adapter_options.get("read_only_expected")
            else (os.environ.get("DECISIONSAI_CODEX_SANDBOX") or "workspace-write").strip()
        )
        if sandbox:
            cmd += ["--sandbox", sandbox]
        if task.model and task.model not in ("auto", "default"):
            cmd += ["--model", task.model]
        effort = (task.codex_reasoning_effort or "").strip()
        if effort:
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
        tier = (task.codex_service_tier or "").strip()
        if tier:
            cmd += ["-c", f'service_tier="{tier}"']
        return cmd + [self._callback_instruction(task) + task.instruction]


class HermesAgentBackend(OneShotCliBackend):
    """Optional Nous Hermes Agent CLI (not the Ollama hermes3 model)."""

    id = "hermes_agent"
    name = "Hermes Agent"
    description = "Nous Hermes Agent operator CLI (~/.hermes). Optional; not required for the Orchestrator."
    executable_candidates = ["hermes"]
    command_args = ["chat"]
    setup_instructions = (
        "Install Nous Hermes Agent: NONINTERACTIVE=1 bash scripts/setup_project_clis.sh hermes-agent "
        "then run hermes setup. See docs/nous-hermes-agent.md."
    )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        if task.model and task.model not in ("auto", "default"):
            cmd += ["--model", task.model]
        return cmd + [task.instruction]


class OpenCodeBackend(OneShotCliBackend):
    """OpenCode CLI — provider/model via build.nvidia.com, OpenRouter, Kilo, etc."""

    id = "opencode"
    name = "OpenCode"
    description = "OpenCode agent CLI with multi-provider model routing."
    executable_candidates = ["opencode"]
    command_args = ["run"]
    setup_instructions = (
        "Install OpenCode and configure providers with opencode providers login "
        "or NVIDIA_API_KEY / KILO_API_KEY in your shell."
    )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        if task.model and task.model not in ("auto", "default", ""):
            model = task.model
            provider = str(task.adapter_options.get("model_provider") or "").strip().lower()
            if provider == "kilocode" and not model.startswith("kilo/"):
                model = f"kilo/{model}"
            cmd += ["--dir", task.folder, "-m", model]
        return cmd + [task.instruction]

    async def send_task(self, task: ProjectTask, on_event: Optional[EventCallback] = None) -> BackendTaskResult:
        result = await super().send_task(task, on_event=on_event)
        if result.success and task.origin == "workflow":
            report_error = _workflow_report_error(result.output, self.name)
            if report_error:
                result.success = False
                result.error = report_error
        return result


class KiroBackend(OneShotCliBackend):
    """Kiro headless CLI backend with per-project session resume."""

    id = "kiro"
    name = "Kiro CLI"
    description = "Kiro CLI headless agent backend with resumable per-project sessions."
    executable_candidates = ["kiro-cli"]
    setup_instructions = (
        "Install Kiro CLI and authenticate it, or provide KIRO_API_KEY for headless runs."
    )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable, "chat", "--no-interactive", "--trust-all-tools", "--mode", "agent", "--wrap", "never"]
        if _KIRO_SESSION_CONNECTED.get(int(task.project_id)):
            cmd.append("--resume")
        if task.model and task.model not in ("auto", "default", ""):
            cmd += ["--model", task.model]
        return cmd + [task.instruction]

    async def send_task(self, task: ProjectTask, on_event: Optional[EventCallback] = None) -> BackendTaskResult:
        result = await super().send_task(task, on_event=on_event)
        if result.success:
            _KIRO_SESSION_CONNECTED[int(task.project_id)] = True
        return result

    async def disconnect_session(self, project_id: int, folder: str) -> BackendTaskResult:
        _KIRO_SESSION_CONNECTED.pop(int(project_id), None)
        return BackendTaskResult(
            success=True,
            backend_id=self.id,
            engine=self.id,
            output="Kiro session disconnected.",
        )


class ClineBackend(OneShotCliBackend):
    """Cline terminal CLI (shared agent core with the VS Code extension)."""

    id = "cline"
    name = "Cline"
    description = "Cline CLI backend (~/.cline). Uses act mode with yolo for workflow tasks."
    executable_candidates = ["cline"]
    command_args = ["--yolo", "--act"]
    setup_instructions = (
        "Install Cline CLI: NONINTERACTIVE=1 bash scripts/setup_project_clis.sh cline "
        "then run cline auth to configure your model provider."
    )

    def check_availability(self) -> BackendStatus:
        path = _first_executable(self.executable_candidates)
        installed = bool(path)
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=installed,
            ready=installed,
            state="ready" if installed else "missing",
            message=f"{self.name} is installed and ready." if installed else f"{self.name} command was not found.",
            path=path,
            version=_version_for(path, ["version"]),
            setup_required=not installed,
            setup_instructions=self.setup_instructions,
            supports_rpc=False,
            supports_install=False,
        )

    def _subprocess_env(self) -> dict[str, str]:
        env = super()._subprocess_env()
        env["FALLOW_AGENT_SOURCE"] = "cline"
        return env

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        if task.model and task.model not in ("auto", "default", ""):
            cmd += ["--model", task.model]
        return cmd + [task.instruction]


_BACKENDS: dict[str, ProjectCliBackend] = {
    "pi": PiBackend(),
    "cursor": CursorBackend(),
    "cursor_ide": CursorIdeBackend(),
    "claude_code": ClaudeCodeBackend(),
    "codex": CodexBackend(),
    "codex_ide": CodexIdeBackend(),
    "hermes_agent": HermesAgentBackend(),
    "kiro": KiroBackend(),
    "cline": ClineBackend(),
    "opencode": OpenCodeBackend(),
}


def normalize_backend_id(value: str | None) -> str:
    backend_id = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": DEFAULT_BACKEND_ID,
        "default": DEFAULT_BACKEND_ID,
        "claude": "claude_code",
        "claude-code": "claude_code",
        "claudecode": "claude_code",
        "cursor_cli": "cursor",
        "cursor_editor": "cursor",
        "cursor_extension": "cursor",
        "vscode_ide": "cursor_ide",
        "vscode": "cursor_ide",
        "vs_code": "cursor_ide",
        "visual_studio_code": "cursor_ide",
        "vscode_extension": "cursor_ide",
        "codex_cli": "codex",
        "openai_codex": "codex",
        "hermes": "hermes_agent",
        "hermes-agent": "hermes_agent",
        "hermes_agent": "hermes_agent",
        "nous_hermes": "hermes_agent",
        "cline_cli": "cline",
        "open_code": "opencode",
        "kiro_cli": "kiro",
    }
    backend_id = aliases.get(backend_id, backend_id)
    return backend_id if backend_id in _BACKENDS else DEFAULT_BACKEND_ID


def get_backend(backend_id: str | None) -> ProjectCliBackend:
    return _BACKENDS[normalize_backend_id(backend_id)]


def resolve_backend_for_capabilities(
    required: set[str] | list[str] | tuple[str, ...],
    *,
    preferred_backend_id: str | None = None,
    require_ready: bool = True,
) -> ProjectCliBackend | None:
    """Select the first capable adapter, preferring the requested backend."""
    preferred = get_backend(preferred_backend_id) if preferred_backend_id else None
    ordered = ([preferred] if preferred else []) + [
        backend for backend in list_backends() if backend is not preferred
    ]
    for backend in ordered:
        if not backend.supports(required):
            continue
        if require_ready and not backend.setup_status().ready:
            continue
        return backend
    return None


def list_backends() -> list[ProjectCliBackend]:
    return list(_BACKENDS.values())


def get_backend_statuses(active_backend_id: str | None = None) -> dict[str, Any]:
    active = normalize_backend_id(active_backend_id)
    statuses = []
    for backend in list_backends():
        item = backend.setup_status().to_dict()
        item["description"] = backend.description
        item["active"] = backend.id == active
        statuses.append(item)
    return {"active_backend": active, "backends": statuses}


def get_project_backend_id(project: Any) -> str:
    return normalize_backend_id(getattr(project, "coding_backend", None))


def _execution_event_message(event: dict[str, Any]) -> str:
    if not isinstance(event, dict):
        return ""

    def _structured_message(value: Any) -> str:
        if not isinstance(value, dict):
            return str(value or "")[:1000]
        content = value.get("content")
        if isinstance(content, str):
            return content[:1000]
        if isinstance(content, list):
            text_parts: list[str] = []
            has_reasoning = False
            has_tool = False
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").lower()
                if part_type in {"thinking", "reasoning", "reasoning_details"}:
                    has_reasoning = True
                    continue
                if part_type in {"toolcall", "tool_call", "tool_use"}:
                    has_tool = True
                    continue
                text_value = part.get("text") or part.get("content")
                if isinstance(text_value, str) and text_value.strip():
                    text_parts.append(text_value.strip())
            if text_parts:
                joined = " ".join(text_parts)
                return joined[:1000] if len(joined) <= 500 else "Worker is processing project context."
            if has_tool:
                return "Worker is using a project tool."
            if has_reasoning:
                return "Worker is reasoning over the ticket and project evidence."
        role = str(value.get("role") or "").lower()
        if role == "assistant":
            return "Worker completed a reasoning turn."
        return "Worker reported a structured execution update."

    message = event.get("message")
    if message:
        return _structured_message(message)
    if event.get("role") or isinstance(event.get("content"), list):
        return _structured_message(event)
    if event.get("type") == "command_start" and event.get("command"):
        command = event.get("command")
        if isinstance(command, list):
            return "Command: " + " ".join(str(part) for part in command)
        return "Command: " + str(command)
    assistant_event = event.get("assistantMessageEvent")
    if isinstance(assistant_event, dict):
        if assistant_event.get("type") == "text_delta":
            delta = str(assistant_event.get("delta") or "").strip()
            lines = delta.splitlines()
            lowered = delta.lower()
            if delta.startswith("exec\n") or (" exited " in lowered and " in " in lowered):
                return "Worker ran a project command."
            if "# decisionsai step handoff" in lowered:
                return "Worker received the ticket and step context."
            has_patch_marker = any(
                line.startswith(("@@", "diff --git", "*** Begin Patch"))
                for line in lines
            )
            changed_lines = sum(
                1 for line in lines
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            )
            if "*** begin patch" in lowered or (has_patch_marker and changed_lines >= 2):
                return "Worker is updating project files."
            if len(delta) > 1000 and ("# " in delta or "---\nname:" in lowered):
                return "Worker loaded project guidance."
            if len(delta) > 500:
                return "Worker is processing project context."
            return delta[:600]
        if assistant_event.get("type"):
            return str(assistant_event.get("type"))[:1000]
    if event.get("type"):
        return str(event.get("type"))[:1000]
    return ""


def _compact_execution_value(value: Any, *, depth: int = 0) -> Any:
    """Bound one CLI event before it reaches any live or durable consumer."""
    if depth >= 5:
        return str(value)[:1000]
    if isinstance(value, dict):
        return {
            str(key): _compact_execution_value(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
            if str(key) not in {"partial", "thinkingSignature"}
        }
    if isinstance(value, (list, tuple)):
        return [_compact_execution_value(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value[:4000]
    return value


def _compact_execution_event(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"value": str(event)[:4000]}
    if str(event.get("type") or "").strip().lower() == "message_update":
        # Token deltas can split a credential label and its value across
        # different events, making content-aware redaction impossible. The
        # terminal message is persisted on message_end; streaming updates only
        # need a semantic progress marker for Mission Control.
        assistant_event = event.get("assistantMessageEvent")
        update_type = (
            str(assistant_event.get("type") or "")
            if isinstance(assistant_event, dict)
            else ""
        )
        return {
            "type": "message_update",
            "update_type": update_type,
            "summary": _execution_event_message(event),
        }
    compact = _compact_execution_value(event)
    return compact if isinstance(compact, dict) else {"value": str(compact)[:4000]}


def _is_duplicate_progress_event(
    event: dict[str, Any],
    message: str,
    *,
    previous_message: str,
    previous_at: float,
    now: float,
) -> bool:
    """Coalesce bursty CLI deltas before they hit SQLite, WebSocket, and chat.

    Streaming backends often resend the complete assistant message for every
    token.  Comparing only the rendered message misses those growing deltas and
    can create hundreds of database writes and browser updates per second.  A
    human progress surface does not need token cadence, so persist at most one
    changing message per second and suppress an unchanged message for longer.
    Lifecycle, command and tool events are never throttled here.
    """
    if str((event or {}).get("type") or "") != "message_update":
        return False
    elapsed = now - previous_at
    if elapsed < 1.0:
        return True
    return bool(message and message == previous_message and elapsed < 5.0)


def _handoff_callback_metadata(task: ProjectTask) -> dict[str, Any]:
    api_base = _decisions_api_base()
    callback: dict[str, Any] = {"api_base": api_base}
    if task.workflow_id and task.run_id:
        callback.update({
            "continue_url": _with_internal_token(f"{api_base}/api/workflows/{int(task.workflow_id)}/runs/{int(task.run_id)}/continue"),
            "bridge_url": _with_internal_token(f"{api_base}/api/workflows/{int(task.workflow_id)}/runs/{int(task.run_id)}/codex-events"),
        })
    return callback


def _loop_handoff_extra(workflow_id: int | None, run_id: int | None) -> dict[str, Any]:
    """Attach loop contract context to CLI handoff when available."""
    if not run_id:
        return {}
    try:
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflow
        from distr.core.workflow.planning import build_loop_context_summary

        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return {}
            run_data = json.loads(run.run_data or "{}") or {}
            loop_contract = run_data.get("loop_contract") or {}
            if not loop_contract and workflow_id:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
                if wf and wf.workflow_input:
                    loop_contract = json.loads(wf.workflow_input or "{}") or {}
            if not loop_contract:
                return {}
            iteration = int(run_data.get("loop_iteration") or 0)
            extra: dict[str, Any] = {
                "loop_contract": loop_contract,
                "loop_context_summary": build_loop_context_summary(loop_contract, iteration),
            }
            steering = (run_data.get("run_briefing_steering") or "").strip()
            if steering:
                extra["run_briefing_steering"] = steering
            return extra
    except Exception:
        return {}


def _update_run_handoff_state(
    *,
    run_id: int | None,
    packet: dict[str, Any],
    handoff_event_id: int | None = None,
    state: str = "dispatched",
    result: dict[str, Any] | None = None,
) -> None:
    if not run_id:
        return
    try:
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflowRun

        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return
            try:
                run_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                run_data = {}
            handoff = dict(packet or {})
            if handoff_event_id:
                handoff["handoff_event_id"] = int(handoff_event_id)
            handoff["state"] = state
            if result:
                handoff["result"] = result
            history = run_data.get("backend_handoffs") if isinstance(run_data.get("backend_handoffs"), list) else []
            history.append(handoff)
            run_data["backend_handoffs"] = history[-20:]
            run_data["latest_backend_handoff"] = handoff
            run_data["execution_session_id"] = packet.get("execution_session_id") or run_data.get("execution_session_id")
            if state in {"needs_human_input", "human_took_over", "changes_requested"}:
                run_data["human_intervention_state"] = state
            run.run_data = json.dumps(run_data)
            db.commit()
    except Exception:
        pass


async def run_project_task(
    project: Any,
    instruction: str,
    *,
    chat_id: Optional[int] = None,
    audit_id: Optional[int] = None,
    run_id: Optional[int] = None,
    workflow_id: Optional[int] = None,
    step_id: Optional[int] = None,
    on_event: Optional[EventCallback] = None,
    origin: str = "cli",
    ticket_id: Optional[int] = None,
    ticket_complexity: str = "medium",
    backend_id_override: Optional[str] = None,
    model_override: Optional[str] = None,
    codex_reasoning_effort_override: Optional[str] = None,
    codex_service_tier_override: Optional[str] = None,
    board_id_override: Optional[int] = None,
    adapter_options: Optional[dict[str, Any]] = None,
) -> BackendTaskResult:
    from distr.core.kanban.project_execution import (
        append_execution_event,
        complete_execution_session,
        create_execution_session,
    )
    from distr.core.project_cli_backends.live_sessions import any_live_session_running

    board_id = _normalize_board_id(board_id_override)
    if board_id is None and ticket_id:
        try:
            from distr.core.orchestrator import resolve_board_id_for_ticket

            board_id = resolve_board_id_for_ticket(int(ticket_id))
        except Exception:
            board_id = None

    backend_id = normalize_backend_id(backend_id_override) if backend_id_override else get_project_backend_id(project)
    backend = get_backend(backend_id)
    from .ide_handoff import is_ide_backend, plugin_source_for

    ide_mode = is_ide_backend(backend_id)
    route_type = "ide_bridge" if ide_mode else "project_cli"
    route_backend = plugin_source_for(backend_id) if ide_mode else backend_id
    loop_extra = _loop_handoff_extra(workflow_id, run_id)
    task = ProjectTask(
        project_id=int(project.id),
        project_name=project.name or "",
        folder=project.folder_location or "",
        instruction=instruction,
        chat_id=chat_id,
        audit_id=audit_id,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        origin=origin,
        model=(model_override if model_override is not None else (getattr(project, "coding_backend_model", "") or "")).strip(),
        ticket_id=ticket_id,
        board_id=board_id,
        ticket_complexity=ticket_complexity,
        codex_reasoning_effort=(codex_reasoning_effort_override or "").strip(),
        codex_service_tier=(codex_service_tier_override or "").strip(),
        adapter_options=adapter_options or {},
    )
    selected_model = task.model or "auto"
    selection_reason = "explicit backend override" if backend_id_override else "project backend setting"
    if model_override:
        selection_reason += "; explicit model override"
    elif task.model:
        selection_reason += "; project model setting"
    else:
        selection_reason += "; backend default model"
    git_status_before = _git_status_short(task.folder)
    read_only_expected = bool(task.adapter_options.get("read_only_expected"))
    workspace_state_before = (
        _workspace_state_snapshot(task.folder) if read_only_expected else {}
    )
    runtime_snapshot: dict[str, Any] = {}
    try:
        from distr.core.terminal import get_project_runtime_snapshot

        runtime_snapshot = get_project_runtime_snapshot(task.project_id)
    except Exception:
        runtime_snapshot = {}

    ticket_title = ""
    if ticket_id:
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanTicket

            with get_session() as db:
                ticket_row = (
                    db.query(KanbanTicket)
                    .filter(KanbanTicket.id == int(ticket_id))
                    .first()
                )
                if ticket_row and ticket_row.title:
                    ticket_title = str(ticket_row.title).strip()
        except Exception:
            ticket_title = ""

    execution_session_id = create_execution_session(
        project_id=task.project_id,
        ticket_id=ticket_id,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        audit_id=audit_id,
        route_type=route_type,
        route_backend=route_backend,
        selected_model=selected_model,
        selection_reason=selection_reason,
        complexity=ticket_complexity,
        origin=origin,
        input_packet={
            "project_id": task.project_id,
            "project_name": task.project_name,
            "folder": task.folder,
            "ticket_id": ticket_id,
            "ticket_title": ticket_title,
            "workflow_id": workflow_id,
            "board_id": board_id,
            "run_id": run_id,
            "step_id": step_id,
            "audit_id": audit_id,
            "backend_id": backend_id,
            "model": selected_model,
            "complexity": ticket_complexity,
            "origin": origin,
            "instruction": instruction,
            "git_status_before": git_status_before,
            "read_only_expected": read_only_expected,
            "runtime_snapshot": runtime_snapshot,
        },
    )
    task.execution_session_id = execution_session_id

    def _normalized_output_packet(raw: Any, **metadata: Any) -> dict[str, Any]:
        from distr.core.project_cli_backends.contracts import normalize_execution_result

        return {
            **normalize_execution_result(
                raw,
                backend_id=backend_id,
                attempt_id=execution_session_id,
            ).to_dict(),
            **metadata,
        }

    def _status_payload(status: Any) -> dict[str, Any]:
        if status is None:
            return {}
        try:
            raw = status.to_dict()
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
        return {
            "id": getattr(status, "id", backend_id),
            "name": getattr(status, "name", getattr(backend, "name", "") or backend_id),
            "ready": bool(getattr(status, "ready", False)),
            "state": getattr(status, "state", "") or "",
            "message": getattr(status, "message", "") or "",
            "setup_required": bool(getattr(status, "setup_required", False)),
            "setup_instructions": getattr(status, "setup_instructions", "") or "",
        }

    setup_status = backend.setup_status()
    preflight_payload = {
        "backend_id": backend_id,
        "backend_name": getattr(backend, "name", "") or backend_id,
        "route_type": route_type,
        "route_backend": route_backend,
        "model": selected_model,
        "project_id": task.project_id,
        "project_name": task.project_name,
        "folder": task.folder,
        "preflight": _status_payload(setup_status),
    }
    if any_live_session_running(int(project.id), exclude_backend_id=backend_id, board_id=board_id):
        msg = "Another workflow CLI is already processing work for this board. Wait for it to finish before starting a different backend."
        append_execution_event(
            execution_session_id,
            "preflight",
            status="failed",
            message=msg,
            payload={**preflight_payload, "reason": "live_session_conflict"},
        )
        complete_execution_session(
            execution_session_id,
            success=False,
            output_packet=_normalized_output_packet({
                "backend_id": backend_id,
                "engine": backend_id,
                "success": False,
                "error": msg,
            }, preflight=preflight_payload["preflight"]),
            error=msg,
        )
        return BackendTaskResult(
            False,
            backend_id,
            backend_id,
            error=msg,
            execution_session_id=execution_session_id,
        )
    if not setup_status.ready:
        msg = (setup_status.message or setup_status.setup_instructions or f"{backend.name or backend_id} is not ready.").strip()
        append_execution_event(
            execution_session_id,
            "preflight",
            status="failed",
            message=msg,
            payload=preflight_payload,
        )
        complete_execution_session(
            execution_session_id,
            success=False,
            output_packet=_normalized_output_packet({
                "backend_id": backend_id,
                "engine": backend_id,
                "success": False,
                "error": msg,
            }, preflight=preflight_payload["preflight"]),
            error=msg,
        )
        return BackendTaskResult(
            False,
            backend_id,
            backend_id,
            error=msg,
            execution_session_id=execution_session_id,
        )

    # Backstop for direct prompts and any execution path that did not pass
    # through workflow route selection. This probe never invokes a model or
    # spends tokens. Workflow approvals carry an explicit one-run override.
    provider_financial = None
    if not bool(task.adapter_options.get("provider_preflight_override")):
        try:
            from distr.core.project_cli_backends.provider_preflight import preflight_provider_route
            from distr.core.settings import load_settings_from_db

            provider_financial = preflight_provider_route(
                {
                    "backend": backend_id,
                    "model": selected_model,
                    "model_provider": task.adapter_options.get("model_provider") or "",
                },
                settings=load_settings_from_db(),
                complexity=ticket_complexity,
            )
        except Exception:
            logger.warning("Project CLI provider preflight failed unexpectedly", exc_info=True)
    if provider_financial is not None:
        preflight_payload["provider_financial"] = provider_financial.to_dict()
    if provider_financial is not None and provider_financial.ready is False:
        msg = (
            f"{provider_financial.message} No model work was started. "
            "Would you like to choose another route or proceed anyway?"
        )
        append_execution_event(
            execution_session_id,
            "provider_preflight",
            status="waiting",
            message=msg,
            payload=preflight_payload,
        )
        complete_execution_session(
            execution_session_id,
            success=False,
            output_packet=_normalized_output_packet(
                {
                    "backend_id": backend_id,
                    "engine": backend_id,
                    "success": False,
                    "error": msg,
                },
                preflight=preflight_payload["preflight"],
                provider_preflight=provider_financial.to_dict(),
            ),
            error=msg,
        )
        return BackendTaskResult(
            False,
            backend_id,
            backend_id,
            error=msg,
            execution_session_id=execution_session_id,
            waits_for_human=True,
        )

    append_execution_event(
        execution_session_id,
        "preflight",
        status="completed",
        message=f"{getattr(backend, 'name', '') or backend_id} is ready.",
        payload=preflight_payload,
    )
    setattr(task, "loop_context_summary", str(loop_extra.get("loop_context_summary") or ""))
    setattr(task, "run_briefing_steering", str(loop_extra.get("run_briefing_steering") or ""))
    handoff_packet: dict[str, Any] = {}
    handoff_event_id: int | None = None
    try:
        from distr.core.orchestrator import build_backend_handoff_packet, record_backend_handoff

        handoff_packet = build_backend_handoff_packet(
            backend_id=backend_id,
            model=selected_model,
            instruction=instruction,
            project_id=task.project_id,
            project_name=task.project_name,
            project_folder=task.folder,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            execution_session_id=execution_session_id,
            route_rationale=selection_reason,
            selection_reason=selection_reason,
            origin=origin,
            complexity=ticket_complexity,
            git_status_before=git_status_before,
            runtime_snapshot=runtime_snapshot,
            callback=_handoff_callback_metadata(task),
            extra=loop_extra,
        )
        handoff_event_id = record_backend_handoff(
            packet=handoff_packet,
            status="dispatched",
            summary=f"Sent work to {backend.name or backend_id}.",
        )
        setattr(task, "handoff_event_id", handoff_event_id)
        _update_run_handoff_state(
            run_id=run_id,
            packet=handoff_packet,
            handoff_event_id=handoff_event_id,
            state="dispatched",
        )
    except Exception:
        pass
    if runtime_snapshot:
        try:
            from distr.core.orchestrator import resolve_board_id_for_ticket
            from distr.core.orchestration_events import emit_orchestration_event

            active_count = int(runtime_snapshot.get("active_terminal_count") or 0)
            urls = runtime_snapshot.get("urls") or []
            url_text = ", ".join(str(item.get("url")) for item in urls if isinstance(item, dict) and item.get("url"))
            emit_orchestration_event(
                source="project_runtime",
                event_type="project_runtime_snapshot",
                status="observed",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=ticket_id,
                board_id=board_id or resolve_board_id_for_ticket(ticket_id),
                project_id=task.project_id,
                execution_session_id=execution_session_id,
                summary=(
                    f"Observed {active_count} active project runtime terminal(s)"
                    + (f"; app URL candidates: {url_text}" if url_text else ".")
                ),
                payload=runtime_snapshot,
            )
        except Exception:
            pass
    append_execution_event(
        execution_session_id,
        "executor_start",
        status="running",
        message=f"Starting {backend.name or backend_id}.",
        payload={
            "backend_id": backend_id,
            "model": selected_model,
            "runtime_snapshot": runtime_snapshot,
            "backend_handoff": handoff_packet or {},
            "handoff_event_id": handoff_event_id,
        },
    )
    try:
        from distr.core.project_cli_backends.live_sessions import mark_live_session_presence, set_live_session_running

        mark_live_session_presence(
            task.project_id,
            backend_id,
            workflow_id=task.workflow_id,
            board_id=task.board_id,
            present=True,
        )
        set_live_session_running(task.project_id, backend_id, True, board_id=task.board_id)
    except Exception:
        pass

    progress_state = {"message": "", "at": 0.0}

    def _tracked_event(event: dict[str, Any]) -> None:
        from distr.core.orchestrator import redact_handoff_payload

        safe_event = redact_handoff_payload(event)
        if not isinstance(safe_event, dict):
            safe_event = {}
        message = _execution_event_message(safe_event)
        now = time.monotonic()
        if _is_duplicate_progress_event(
            safe_event,
            message,
            previous_message=str(progress_state["message"]),
            previous_at=float(progress_state["at"]),
            now=now,
        ):
            return
        progress_state["message"] = message
        progress_state["at"] = now
        compact_event = _compact_execution_event(safe_event)
        try:
            from distr.core.project_cli_backends.live_sessions import publish_live_session_event

            publish_live_session_event(task.project_id, backend_id, compact_event, board_id=task.board_id)
        except Exception:
            pass
        append_execution_event(
            execution_session_id,
            str((event or {}).get("type") or "event"),
            status="running",
            message=message,
            payload=compact_event,
        )
        _emit(on_event, compact_event)

    try:
        result = await backend.send_task(task, on_event=_tracked_event)
    except asyncio.CancelledError:
        message = f"{backend.name or backend_id} execution cancelled."
        complete_execution_session(
            execution_session_id,
            success=False,
            output_packet=_normalized_output_packet({
                "success": False,
                "backend_id": backend_id,
                "engine": backend_id,
                "error": message,
            }),
            error=message,
        )
        raise
    except Exception as exc:
        complete_execution_session(
            execution_session_id,
            success=False,
            output_packet=_normalized_output_packet({
                "success": False,
                "backend_id": backend_id,
                "engine": backend_id,
                "error": str(exc),
            }),
            error=str(exc),
        )
        raise
    finally:
        try:
            from distr.core.project_cli_backends.live_sessions import set_live_session_running

            set_live_session_running(task.project_id, backend_id, False, board_id=task.board_id)
        except Exception:
            pass

    # The backend result is returned to the workflow dispatcher and therefore
    # must be redacted as well as the durable execution transcript. A worker
    # may quote a secret it inspected even when it was explicitly told not to.
    from distr.core.orchestrator import redact_handoff_payload

    result.output = str(redact_handoff_payload(result.output or ""))
    result.error = str(redact_handoff_payload(result.error or ""))

    result.execution_session_id = execution_session_id
    git_status_after = _git_status_short(task.folder)
    workspace_state_delta = (
        _workspace_state_delta(
            workspace_state_before,
            _workspace_state_snapshot(task.folder),
        )
        if read_only_expected
        else {"changed": False, "added": [], "modified": [], "deleted": [], "total_changed": 0, "truncated": False}
    )
    if getattr(result, "waits_for_human", False) and result.success:
        append_execution_event(
            execution_session_id,
            "ide_handoff_waiting",
            status="waiting",
            message="Waiting for IDE plugin completion.",
            payload={
                "backend_id": backend_id,
                "work_packet_path": getattr(result, "work_packet_path", "") or "",
                "output": (result.output or "")[:4000],
            },
        )
        try:
            from distr.core.orchestrator import record_backend_handoff

            update_packet = {
                **(handoff_packet or {}),
                "git_status_after": git_status_after,
                "work_packet_path": getattr(result, "work_packet_path", "") or "",
            }
            record_backend_handoff(
                packet=update_packet,
                status="waiting",
                event_type="backend_handoff_updated",
                summary=f"{backend.name or backend_id} IDE handoff is waiting in the IDE.",
                evidence={"output": (result.output or "")[:4000]},
            )
            _update_run_handoff_state(
                run_id=run_id,
                packet=update_packet,
                handoff_event_id=handoff_event_id,
                state="waiting_ide",
                result={
                    "success": True,
                    "engine": result.engine,
                    "output": (result.output or "")[:4000],
                    "work_packet_path": getattr(result, "work_packet_path", "") or "",
                },
            )
        except Exception:
            pass
        if run_id:
            try:
                from distr.core.db import get_session
                from distr.core.db.workflow import AutoWorkflowRun

                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                    if run:
                        try:
                            run_data = json.loads(run.run_data or "{}") or {}
                        except Exception:
                            run_data = {}
                        run_data["ide_handoff_pending"] = True
                        run_data["latest_ide_handoff"] = {
                            "backend_id": backend_id,
                            "work_packet_path": getattr(result, "work_packet_path", "") or "",
                            "execution_session_id": execution_session_id,
                        }
                        run.run_data = json.dumps(run_data)
                        db.commit()
            except Exception:
                pass
        return result
    try:
        from distr.core.orchestrator import record_backend_handoff

        update_packet = {
            **(handoff_packet or {}),
            "git_status_after": git_status_after,
        }
        record_backend_handoff(
            packet=update_packet,
            status="completed" if result.success else "failed",
            event_type="backend_handoff_updated",
            summary=(
                f"{backend.name or backend_id} completed the handoff."
                if result.success
                else f"{backend.name or backend_id} failed the handoff."
            ),
            evidence={
                "output": (result.output or "")[:4000],
                "error": (result.error or "")[:2000],
                "engine": result.engine,
            },
        )
        _update_run_handoff_state(
            run_id=run_id,
            packet=update_packet,
            handoff_event_id=handoff_event_id,
            state="completed" if result.success else "failed",
            result={
                "success": result.success,
                "engine": result.engine,
                "output": (result.output or "")[:4000],
                "error": (result.error or "")[:2000],
            },
        )
    except Exception:
        pass
    complete_execution_session(
        execution_session_id,
        success=result.success,
        output_packet=_normalized_output_packet(
            result,
            ticket_id=ticket_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            audit_id=audit_id,
            model=selected_model,
            complexity=ticket_complexity,
            git_status_before=git_status_before,
            git_status_after=git_status_after,
            read_only_expected=read_only_expected,
            read_only_violation=bool(read_only_expected and workspace_state_delta.get("changed")),
            workspace_state_delta=workspace_state_delta,
            runtime_snapshot=runtime_snapshot,
        ),
        error=result.error,
    )
    return result
