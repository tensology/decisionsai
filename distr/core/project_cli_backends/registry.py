"""Registry and adapters for project coding CLI backends."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .base import BackendStatus, BackendTaskResult, EventCallback, ProjectCliBackend, ProjectTask

DEFAULT_BACKEND_ID = "pi"


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


class PiBackend(ProjectCliBackend):
    id = "pi"
    name = "Pi"
    description = "Pi coding agent via RPC. This remains the default project backend."
    supports_rpc = True
    supports_install = False
    setup_instructions = "Install Pi with: npm install -g @mariozechner/pi-coding-agent"

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

        try:
            rpc = await get_or_create_rpc_session(task.project_id, task.folder)
            if rpc.send_prompt(task.instruction, origin=task.origin):
                return BackendTaskResult(True, self.id, "pi_rpc", session_id=task.audit_id)
        except Exception as exc:
            # Fall through to one-shot print mode. The caller still gets a clear
            # engine value so later routing/fallback can distinguish the path.
            last_error = str(exc)
        else:
            last_error = "Pi RPC did not accept the prompt."

        def _run_pi_print() -> tuple[bool, str, str]:
            try:
                result = subprocess.run(
                    [
                        pi_path,
                        "-p",
                        "--append-system-prompt",
                        f"You are working on project: {task.project_name}",
                        task.instruction,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=task.folder,
                )
                output = ((result.stdout or "") + (result.stderr or "")).strip()
                return result.returncode == 0, output, ""
            except Exception as exc:
                return False, "", str(exc)

        ok, output, error = await asyncio.to_thread(_run_pi_print)
        return BackendTaskResult(
            ok,
            self.id,
            "pi_cli",
            output=output[:3000],
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
                env=self._subprocess_env(),
            )
        except Exception as exc:
            msg = f"Failed to start {self.name}: {exc}"
            _emit(on_event, {"type": "error", "message": msg})
            _emit(on_event, {"type": "agent_end", "backend": self.id})
            return BackendTaskResult(False, self.id, self.id, error=msg, session_id=task.audit_id)

        chunks: list[str] = []
        try:
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                chunks.append(text)
                _emit(on_event, {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": text}})
            rc = await process.wait()
        except Exception as exc:
            try:
                process.kill()
            except Exception:
                pass
            rc = -1
            chunks.append(f"\n{exc}")

        output = "".join(chunks).strip()
        _emit(on_event, {"type": "message_update", "assistantMessageEvent": {"type": "done"}})
        _emit(on_event, {"type": "message_end", "message": {"role": "assistant", "content": output}})
        _emit(on_event, {"type": "agent_end", "backend": self.id})
        compact_output = _compact_cli_output(output)
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
        callback_url = _with_internal_token(f"{api_base}/api/workflows/{int(task.workflow_id)}/runs/{int(task.run_id)}/codex-events")
        reporter = os.environ.get(
            "DECISIONS_CURSOR_REPORTER",
            os.path.expanduser("~/.cursor/plugins/local/decisions-cursor/scripts/report_decisions_event.py"),
        )
        meta = {
            "api_base": api_base,
            "callback_url": callback_url,
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
            f"python3 {json.dumps(reporter)} --callback-url {json.dumps(callback_url)} "
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
        callback_url = _with_internal_token(f"{api_base}/api/workflows/{int(task.workflow_id)}/runs/{int(task.run_id)}/codex-events")
        reporter = os.environ.get(
            "DECISIONS_CODEX_REPORTER",
            os.path.expanduser("~/plugins/decisions-codex/scripts/report_decisions_event.py"),
        )
        meta = {
            "api_base": api_base,
            "callback_url": callback_url,
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
            f"python3 {json.dumps(reporter)} --callback-url {json.dumps(callback_url)} "
            "--event-type codex_prompt_submitted --status observed --message \"<what the human asked or changed>\"\n"
            "Use event_type values: codex_started, codex_prompt_submitted, user_steer, codex_waiting, codex_interrupted, "
            "codex_progress, codex_completed, codex_failed, codex_needs_input.\n"
            "Do not wait until the final answer if the human submits another prompt, changes direction, "
            "or adds constraints mid-run.\n"
            "[/DECISIONS CODEX CALLBACK]\n\n"
        )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        sandbox = (os.environ.get("DECISIONSAI_CODEX_SANDBOX") or "workspace-write").strip()
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
            cmd += ["-m", task.model]
        return cmd + [task.instruction]


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
    }
    backend_id = aliases.get(backend_id, backend_id)
    return backend_id if backend_id in _BACKENDS else DEFAULT_BACKEND_ID


def get_backend(backend_id: str | None) -> ProjectCliBackend:
    return _BACKENDS[normalize_backend_id(backend_id)]


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
    if event.get("message"):
        return str(event.get("message"))[:1000]
    if event.get("type") == "command_start" and event.get("command"):
        command = event.get("command")
        if isinstance(command, list):
            return "Command: " + " ".join(str(part) for part in command)
        return "Command: " + str(command)
    assistant_event = event.get("assistantMessageEvent")
    if isinstance(assistant_event, dict):
        if assistant_event.get("type") == "text_delta":
            return str(assistant_event.get("delta") or "")[:1000]
        if assistant_event.get("type"):
            return str(assistant_event.get("type"))[:1000]
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if content:
            return str(content)[:1000]
    if event.get("type"):
        return str(event.get("type"))[:1000]
    return ""


def _compact_execution_event(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"value": str(event)[:4000]}
    compact = dict(event)
    assistant_event = compact.get("assistantMessageEvent")
    if isinstance(assistant_event, dict) and "delta" in assistant_event:
        assistant_event = dict(assistant_event)
        assistant_event["delta"] = str(assistant_event.get("delta") or "")[:4000]
        compact["assistantMessageEvent"] = assistant_event
    message = compact.get("message")
    if isinstance(message, dict) and "content" in message:
        message = dict(message)
        message["content"] = str(message.get("content") or "")[:4000]
        compact["message"] = message
    return compact


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
) -> BackendTaskResult:
    from distr.core.kanban.project_execution import (
        append_execution_event,
        complete_execution_session,
        create_execution_session,
    )

    board_id = None
    if ticket_id:
        try:
            from distr.core.orchestrator import resolve_board_id_for_ticket

            board_id = resolve_board_id_for_ticket(int(ticket_id))
        except Exception:
            board_id = None

    backend_id = normalize_backend_id(backend_id_override) if backend_id_override else get_project_backend_id(project)
    backend = get_backend(backend_id)
    setup_status = backend.setup_status()
    if not setup_status.ready:
        msg = (setup_status.message or setup_status.setup_instructions or f"{backend.name or backend_id} is not ready.").strip()
        return BackendTaskResult(False, backend_id, backend_id, error=msg)
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
            "run_id": run_id,
            "step_id": step_id,
            "audit_id": audit_id,
            "backend_id": backend_id,
            "model": selected_model,
            "complexity": ticket_complexity,
            "origin": origin,
            "instruction": instruction,
            "git_status_before": git_status_before,
            "runtime_snapshot": runtime_snapshot,
        },
    )
    task.execution_session_id = execution_session_id
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

    def _tracked_event(event: dict[str, Any]) -> None:
        append_execution_event(
            execution_session_id,
            str((event or {}).get("type") or "event"),
            status="running",
            message=_execution_event_message(event),
            payload=_compact_execution_event(event),
        )
        _emit(on_event, event)

    try:
        result = await backend.send_task(task, on_event=_tracked_event)
    except Exception as exc:
        complete_execution_session(
            execution_session_id,
            success=False,
            output_packet={"backend_id": backend_id, "engine": backend_id},
            error=str(exc),
        )
        raise

    result.execution_session_id = execution_session_id
    git_status_after = _git_status_short(task.folder)
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
        output_packet={
            "backend_id": result.backend_id,
            "engine": result.engine,
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "ticket_id": ticket_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "step_id": step_id,
            "audit_id": audit_id,
            "model": selected_model,
            "complexity": ticket_complexity,
            "git_status_before": git_status_before,
            "git_status_after": git_status_after,
            "runtime_snapshot": runtime_snapshot,
        },
        error=result.error,
    )
    return result
