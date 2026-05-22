"""Registry and adapters for project coding CLI backends."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from .base import BackendStatus, BackendTaskResult, EventCallback, ProjectCliBackend, ProjectTask

DEFAULT_BACKEND_ID = "pi"


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
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=folder,
            capture_output=True,
            text=True,
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

    async def send_task(self, task: ProjectTask, on_event: Optional[EventCallback] = None) -> BackendTaskResult:
        status = self.check_availability()
        if not status.ready or not status.path:
            return BackendTaskResult(False, self.id, self.id, error=status.message, session_id=task.audit_id)

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
                env={**os.environ, "TERM": "dumb"},
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
    command_args = ["-p"]
    setup_instructions = (
        "Install Cursor CLI support from Cursor, then make sure the cursor-agent command is on PATH."
    )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        if task.model and task.model != "auto":
            cmd += ["--model", task.model]
        return cmd + [task.instruction]


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

    def _callback_instruction(self, task: ProjectTask) -> str:
        if not task.workflow_id or not task.run_id:
            return ""
        api_base = (os.environ.get("DECISIONS_API_BASE") or "http://127.0.0.1:8765").rstrip("/")
        callback_url = f"{api_base}/api/workflows/{int(task.workflow_id)}/runs/{int(task.run_id)}/codex-events"
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
            "When this work is steered, paused, interrupted, blocked, completed, or materially updated, "
            "report the event back to DecisionsAI. Prefer the reporter script when available:\n"
            f"python3 {json.dumps(reporter)} --callback-url {json.dumps(callback_url)} "
            "--event-type user_steer --status observed --message \"<what changed>\"\n"
            "Use event_type values: codex_started, user_steer, codex_waiting, codex_interrupted, "
            "codex_progress, codex_completed, codex_failed, codex_needs_input.\n"
            "Do not wait until the final answer if the human changes direction mid-run.\n"
            "[/DECISIONS CODEX CALLBACK]\n\n"
        )

    def _build_command(self, executable: str, task: ProjectTask) -> list[str]:
        cmd = [executable] + self.command_args
        if task.model and task.model not in ("auto", "default"):
            cmd += ["--model", task.model]
        effort = (task.codex_reasoning_effort or "").strip()
        if effort:
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
        tier = (task.codex_service_tier or "").strip()
        if tier:
            cmd += ["-c", f'service_tier="{tier}"']
        return cmd + [self._callback_instruction(task) + task.instruction]


class EditorTicketBackend(ProjectCliBackend):
    """Bridge workflow/project tasks into an editor through the DecisionsAI extension."""

    editor_command = ""
    editor_name = ""
    extension_id = "decisionsai.decisionsai"

    def _editor_path(self) -> Optional[str]:
        return shutil.which(self.editor_command)

    def _extension_installed(self, executable: str) -> bool:
        try:
            result = subprocess.run(
                [executable, "--list-extensions"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            extensions = {
                line.strip().lower()
                for line in (result.stdout or "").splitlines()
                if line.strip()
            }
            return self.extension_id.lower() in extensions
        except Exception:
            return False

    def check_availability(self) -> BackendStatus:
        path = self._editor_path()
        if not path:
            return BackendStatus(
                id=self.id,
                name=self.name,
                installed=False,
                ready=False,
                state="missing",
                message=f"{self.editor_name} command `{self.editor_command}` was not found.",
                setup_required=True,
                setup_instructions=self.setup_instructions,
            )
        extension_ready = self._extension_installed(path)
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=True,
            ready=extension_ready,
            state="ready" if extension_ready else "extension_missing",
            message=(
                f"{self.editor_name} and the DecisionsAI extension are ready."
                if extension_ready
                else f"{self.editor_name} is installed, but the DecisionsAI extension is not installed."
            ),
            path=path,
            version=_version_for(path),
            setup_required=not extension_ready,
            setup_instructions=self.setup_instructions,
            supports_rpc=False,
            supports_install=False,
        )

    def _ticket_path(self, task: ProjectTask) -> Path:
        tickets_dir = Path(task.folder).expanduser() / ".tickets"
        tickets_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        suffix = task.step_id or task.audit_id or int(time.time())
        return tickets_dir / f"decisionsai_{self.id}_{stamp}_{suffix}.md"

    def _ticket_body(self, task: ProjectTask) -> str:
        meta = {
            "project_id": task.project_id,
            "project_name": task.project_name,
            "backend": self.id,
            "origin": task.origin,
            "run_id": task.run_id,
            "workflow_id": task.workflow_id,
            "step_id": task.step_id or task.audit_id,
        }
        return (
            "<!-- decisions-ide-meta: "
            + json.dumps({k: v for k, v in meta.items() if v is not None}, separators=(",", ":"))
            + " -->\n"
            "---\n"
            "mode: append\n"
            "---\n\n"
            f"# DecisionsAI Work Packet\n\n"
            f"Project: {task.project_name} ({task.project_id})\n"
            f"Backend: {self.name}\n\n"
            "## Instruction\n\n"
            f"{task.instruction.strip()}\n\n"
            "## Return Contract\n\n"
            "When finished, report back to DecisionsAI with:\n"
            "- Status: completed | failed | needs_input\n"
            "- Summary\n"
            "- Files changed\n"
            "- Tests run\n"
            "- Blockers or next step\n"
        )

    async def send_task(self, task: ProjectTask, on_event: Optional[EventCallback] = None) -> BackendTaskResult:
        status = self.check_availability()
        if not status.ready or not status.path:
            return BackendTaskResult(False, self.id, "ide_ticket", error=status.message, session_id=task.audit_id)
        if not task.folder or not os.path.isdir(task.folder):
            return BackendTaskResult(False, self.id, "ide_ticket", error="Project folder is missing.", session_id=task.audit_id)

        try:
            ticket_path = self._ticket_path(task)
            ticket_path.write_text(self._ticket_body(task), encoding="utf-8")
            subprocess.Popen([status.path, task.folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            msg = f"Failed to create {self.name} work packet: {exc}"
            _emit(on_event, {"type": "error", "message": msg})
            return BackendTaskResult(False, self.id, "ide_ticket", error=msg, session_id=task.audit_id)

        message = (
            f"Created IDE work packet for {self.editor_name}: {ticket_path}\n"
            "The DecisionsAI editor extension should pick it up from `.tickets`. "
            "Keep the workflow step waiting until the editor work is reviewed or reported back."
        )
        _emit(on_event, {"type": "message_end", "message": {"role": "assistant", "content": message}})
        return BackendTaskResult(True, self.id, "ide_ticket", output=message, session_id=task.audit_id)


class CursorIdeBackend(EditorTicketBackend):
    id = "cursor_ide"
    name = "Cursor IDE"
    description = "Cursor editor bridge through the DecisionsAI extension and .tickets work packets."
    editor_command = "cursor"
    editor_name = "Cursor"
    setup_instructions = (
        "Install Cursor, then install the DecisionsAI extension with "
        "`vscode_extension/install_vscode_extension.sh` while Cursor is available on PATH."
    )


class VSCodeIdeBackend(EditorTicketBackend):
    id = "vscode_ide"
    name = "VS Code IDE"
    description = "Visual Studio Code bridge through the DecisionsAI extension and .tickets work packets."
    editor_command = "code"
    editor_name = "Visual Studio Code"
    setup_instructions = (
        "Install VS Code, then install the DecisionsAI extension with "
        "`vscode_extension/install_vscode_extension.sh` while code is available on PATH."
    )


_BACKENDS: dict[str, ProjectCliBackend] = {
    "pi": PiBackend(),
    "cursor": CursorBackend(),
    "claude_code": ClaudeCodeBackend(),
    "codex": CodexBackend(),
    "cursor_ide": CursorIdeBackend(),
    "vscode_ide": VSCodeIdeBackend(),
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
        "cursor_editor": "cursor_ide",
        "cursor_extension": "cursor_ide",
        "vscode": "vscode_ide",
        "vs_code": "vscode_ide",
        "visual_studio_code": "vscode_ide",
        "vscode_extension": "vscode_ide",
        "codex_cli": "codex",
        "openai_codex": "codex",
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

    backend_id = normalize_backend_id(backend_id_override) if backend_id_override else get_project_backend_id(project)
    backend = get_backend(backend_id)
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

    execution_session_id = create_execution_session(
        project_id=task.project_id,
        ticket_id=ticket_id,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        audit_id=audit_id,
        route_type="project_cli",
        route_backend=backend_id,
        selected_model=selected_model,
        selection_reason=selection_reason,
        complexity=ticket_complexity,
        origin=origin,
        input_packet={
            "project_id": task.project_id,
            "project_name": task.project_name,
            "folder": task.folder,
            "ticket_id": ticket_id,
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
    if runtime_snapshot:
        try:
            from distr.core.hermes import emit_event

            active_count = int(runtime_snapshot.get("active_terminal_count") or 0)
            urls = runtime_snapshot.get("urls") or []
            url_text = ", ".join(str(item.get("url")) for item in urls if isinstance(item, dict) and item.get("url"))
            emit_event(
                source="project_runtime",
                event_type="project_runtime_snapshot",
                status="observed",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=ticket_id,
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
        payload={"backend_id": backend_id, "model": selected_model, "runtime_snapshot": runtime_snapshot},
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
