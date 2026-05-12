"""Registry and adapters for project coding CLI backends."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
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
        if rc == 0:
            return BackendTaskResult(True, self.id, self.id, output=output[:3000], session_id=task.audit_id)
        return BackendTaskResult(False, self.id, self.id, output=output[:3000], error=output or f"{self.name} exited with code {rc}", session_id=task.audit_id)


class CursorBackend(OneShotCliBackend):
    id = "cursor"
    name = "Cursor CLI"
    description = "Cursor's project coding CLI backend."
    executable_candidates = ["cursor-agent"]
    command_args = ["-p"]
    setup_instructions = (
        "Install Cursor CLI support from Cursor, then make sure the cursor-agent command is on PATH."
    )


class ClaudeCodeBackend(OneShotCliBackend):
    id = "claude_code"
    name = "Claude Code"
    description = "Anthropic Claude Code CLI backend."
    executable_candidates = ["claude"]
    command_args = ["-p"]
    setup_instructions = "Install Claude Code and authenticate it, then make sure the claude command is on PATH."


_BACKENDS: dict[str, ProjectCliBackend] = {
    "pi": PiBackend(),
    "cursor": CursorBackend(),
    "claude_code": ClaudeCodeBackend(),
}


def normalize_backend_id(value: str | None) -> str:
    backend_id = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "": DEFAULT_BACKEND_ID,
        "default": DEFAULT_BACKEND_ID,
        "claude": "claude_code",
        "claude-code": "claude_code",
        "claudecode": "claude_code",
        "cursor_cli": "cursor",
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


async def run_project_task(project: Any, instruction: str, *, chat_id: Optional[int] = None, audit_id: Optional[int] = None, on_event: Optional[EventCallback] = None, origin: str = "cli") -> BackendTaskResult:
    backend_id = get_project_backend_id(project)
    backend = get_backend(backend_id)
    task = ProjectTask(
        project_id=int(project.id),
        project_name=project.name or "",
        folder=project.folder_location or "",
        instruction=instruction,
        chat_id=chat_id,
        audit_id=audit_id,
        origin=origin,
    )
    return await backend.send_task(task, on_event=on_event)
