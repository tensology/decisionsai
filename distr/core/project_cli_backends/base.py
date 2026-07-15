"""Shared adapter interface for project coding CLI backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from distr.core.project_cli_backends.contracts import BackendCapabilities


EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class BackendStatus:
    id: str
    name: str
    installed: bool
    ready: bool
    state: str
    message: str
    path: Optional[str] = None
    version: Optional[str] = None
    setup_required: bool = False
    setup_instructions: str = ""
    supports_rpc: bool = False
    supports_install: bool = False
    can_receive_remote_handoff: bool = False
    handoff_method: str = ""
    reporter_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackendTaskResult:
    success: bool
    backend_id: str
    engine: str
    output: str = ""
    error: str = ""
    session_id: Optional[int] = None
    execution_session_id: Optional[int] = None
    waits_for_human: bool = False
    work_packet_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectTask:
    project_id: int
    project_name: str
    folder: str
    instruction: str
    chat_id: Optional[int] = None
    audit_id: Optional[int] = None
    run_id: Optional[int] = None
    workflow_id: Optional[int] = None
    step_id: Optional[int] = None
    origin: str = "cli"
    model: str = ""
    ticket_id: Optional[int] = None
    board_id: Optional[int] = None
    ticket_complexity: str = "medium"
    codex_reasoning_effort: str = ""
    codex_service_tier: str = ""
    execution_session_id: Optional[int] = None
    required_capabilities: list[str] = field(default_factory=list)
    adapter_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.required_capabilities = list(self.required_capabilities or [])
        self.adapter_options = dict(self.adapter_options or {})
        # Compatibility bridge: provider-specific values live in the opaque
        # adapter options while older callers migrate off shared fields.
        if self.codex_reasoning_effort:
            self.adapter_options.setdefault("reasoning_effort", self.codex_reasoning_effort)
        if self.codex_service_tier:
            self.adapter_options.setdefault("service_tier", self.codex_service_tier)


class ProjectCliBackend:
    """Base class for coding CLI backends.

    Adapters expose a common surface even when their concrete transport differs
    (Pi RPC, one-shot CLI, future long-lived daemon, etc.).
    """

    id = ""
    name = ""
    description = ""
    supports_rpc = False
    supports_install = False
    setup_instructions = ""
    capabilities = BackendCapabilities()

    def get_capabilities(self) -> BackendCapabilities:
        return self.capabilities

    def supports(self, required: set[str] | list[str] | tuple[str, ...]) -> bool:
        return self.get_capabilities().supports(required)

    def steer(self, message: str, **context: Any) -> dict[str, Any]:
        return {
            "success": False,
            "delivered": False,
            "backend_id": self.id,
            "error": f"{self.name or self.id} does not support steering",
        }

    def check_availability(self) -> BackendStatus:
        raise NotImplementedError

    def setup_status(self) -> BackendStatus:
        return self.check_availability()

    async def install_or_setup(self) -> BackendStatus:
        return self.setup_status()

    async def send_task(
        self,
        task: ProjectTask,
        on_event: Optional[EventCallback] = None,
    ) -> BackendTaskResult:
        raise NotImplementedError

    def get_messages(self, project_id: int) -> list[dict[str, Any]]:
        return []

    def get_buffer(self, project_id: int, lines: int = 100) -> str:
        return ""

    async def restart(self, project_id: int, folder: str) -> BackendTaskResult:
        return BackendTaskResult(
            success=False,
            backend_id=self.id,
            engine=self.id,
            error=f"{self.name} does not support a persistent terminal session.",
        )

    async def disconnect_session(self, project_id: int, folder: str) -> BackendTaskResult:
        return BackendTaskResult(
            success=True,
            backend_id=self.id,
            engine=self.id,
            output="Session disconnected.",
        )
