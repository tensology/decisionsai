"""Provider-neutral contracts for requests entering DecisionsAI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class WorkIntakeSource(str, Enum):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    GMAIL = "gmail"
    SLACK = "slack"
    WEB = "web"
    BOARD = "board"
    API = "api"
    UNKNOWN = "unknown"


class WorkIntakeAction(str, Enum):
    ANSWER_DIRECTLY = "answer_directly"
    CREATE_TICKET = "create_ticket"
    UPDATE_TICKET = "update_ticket"
    RUN_WORKFLOW = "run_workflow"
    ASK_MISSING_INFO = "ask_missing_info"
    REQUEST_APPROVAL = "request_approval"
    WORKFLOW_INTERACTION = "workflow_interaction"
    STEER_RUN = "steer_run"


@dataclass(frozen=True)
class WorkIntakeAttachment:
    kind: str = "file"
    path: str = ""
    url: str = ""
    name: str = ""
    mime_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkIntake:
    source: WorkIntakeSource | str
    user_text: str = ""
    transcript: str = ""
    attachments: list[WorkIntakeAttachment] = field(default_factory=list)
    source_user_id: str = ""
    source_thread_id: str = ""
    source_message_id: str = ""
    project_hint: str = ""
    board_hint: str = ""
    workflow_hint: str = ""
    urgency: str = "normal"
    requested_outcome: str = ""
    conversation_context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    intake_uid: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.source, WorkIntakeSource):
            try:
                self.source = WorkIntakeSource(str(self.source).lower())
            except ValueError:
                self.source = WorkIntakeSource.UNKNOWN
        self.user_text = str(self.user_text or "").strip()
        self.transcript = str(self.transcript or "").strip()
        self.urgency = str(self.urgency or "normal").strip().lower()

    @property
    def text(self) -> str:
        return self.transcript or self.user_text

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source"] = self.source.value
        return value

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WorkIntake":
        attachments = []
        for item in payload.get("attachments") or []:
            if isinstance(item, WorkIntakeAttachment):
                attachments.append(item)
            elif isinstance(item, dict):
                allowed = WorkIntakeAttachment.__dataclass_fields__.keys()
                attachments.append(WorkIntakeAttachment(**{key: item[key] for key in allowed if key in item}))
        allowed = cls.__dataclass_fields__.keys()
        values = {key: payload[key] for key in allowed if key in payload and key != "attachments"}
        return cls(attachments=attachments, **values)


@dataclass
class WorkIntakeDecision:
    action: WorkIntakeAction
    reason: str
    confidence: float = 1.0
    handled: bool = False
    status: str = "triaged"
    ticket_id: int | None = None
    board_id: int | None = None
    project_id: int | None = None
    workflow_id: int | None = None
    workflow_run_id: int | None = None
    response_text: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["action"] = self.action.value
        return value
