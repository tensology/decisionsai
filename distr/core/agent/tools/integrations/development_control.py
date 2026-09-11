"""Explicit orchestrator access to the Development workspace.

This tool never reads an ambient current Chat id. Every mutating thread action
requires a Development thread id, and each list response declares its surface.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


_DELETE_CONFIRMATIONS: dict[str, tuple[float, str, str, bool]] = {}
_DELETE_CONFIRMATION_TTL_SECONDS = 120.0


class DevelopmentControlInput(BaseModel):
    action: str = Field(
        default="list_threads",
        description=(
            "Action: list_threads, get_thread, create_thread, fork_thread, "
            "archive_thread, delete_thread, list_workflows, list_automations, "
            "get_automation, create_automation, update_automation, pause_automation, resume_automation, "
            "run_automation, delete_automation, import_automations, check_incoming, or list_reports."
        ),
    )
    thread_id: Optional[int] = None
    project_id: Optional[int] = None
    title: str = ""
    instruction: str = ""
    automation_id: Optional[str] = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    fallback_provider: str = ""
    fallback_model: str = ""
    sources: list[str] = Field(default_factory=list)
    archived: bool = True
    delete_linked_ticket: bool = True
    confirm: bool = False
    confirmation_token: str = ""
    limit: int = Field(default=50, ge=1, le=200)


class DevelopmentControlTool(BaseTool):
    name: str = "development_control"
    description: str = (
        "Inspect and manage the DecisionsAI Development workspace with explicit target ids. "
        "Use this for Development threads, workflows, automations, Incoming, and Reports. "
        "For scheduled or recurring agent tasks, use this tool to create, inspect, edit, pause, resume, run, or delete the automation. "
        "Automation deletion is completed from the three-dot UI or the automation-owned thread after verified later-turn user confirmation. "
        "Use kanban_ticket for boards/tickets and terminal_overview for project terminals. "
        "Never use Chat load/send endpoints for a Development thread."
    )
    args_schema: type[BaseModel] = DevelopmentControlInput

    def _json(self, payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _require_thread_id(self, thread_id: Optional[int]) -> int:
        if thread_id is None or int(thread_id) <= 0:
            raise ValueError("An explicit Development thread_id is required.")
        return int(thread_id)

    def _deletion_challenge(self, action: str, target: str, delete_linked_ticket: bool) -> str:
        now = time.monotonic()
        for token, record in list(_DELETE_CONFIRMATIONS.items()):
            if record[0] <= now:
                _DELETE_CONFIRMATIONS.pop(token, None)
        token = secrets.token_urlsafe(24)
        _DELETE_CONFIRMATIONS[token] = (
            now + _DELETE_CONFIRMATION_TTL_SECONDS,
            action,
            target,
            bool(delete_linked_ticket),
        )
        return token

    def _consume_deletion_challenge(
        self,
        token: str,
        action: str,
        target: str,
        delete_linked_ticket: bool,
    ) -> bool:
        record = _DELETE_CONFIRMATIONS.pop(str(token or ""), None)
        if record is None:
            return False
        expires_at, expected_action, expected_target, expected_delete_ticket = record
        return (
            expires_at > time.monotonic()
            and expected_action == action
            and expected_target == target
            and expected_delete_ticket == bool(delete_linked_ticket)
        )

    def _list_threads(self, limit: int) -> dict[str, Any]:
        from distr.core.db import Chat, get_session
        from distr.core.db.workflow import DevelopmentWorkItem, StudioArtifact
        from distr.core.workflow.development_threads import development_thread_record

        with get_session() as db:
            items = (
                db.query(DevelopmentWorkItem)
                .order_by(DevelopmentWorkItem.modified_at.desc(), DevelopmentWorkItem.id.desc())
                .limit(limit)
                .all()
            )
            rows = []
            for item in items:
                chat = db.get(Chat, int(item.chat_id))
                if chat is None or chat.parent_id is not None:
                    continue
                metadata = development_thread_record(db, chat)
                artifact_count = db.query(StudioArtifact.id).filter_by(chat_id=int(chat.id)).count()
                rows.append({
                    "thread_id": int(chat.id),
                    "title": chat.title or "Development",
                    "project_id": chat.project_id,
                    "archived": bool(chat.is_archived),
                    "ticket_id": item.local_ticket_id,
                    "workflow_id": item.workflow_id,
                    "execution": metadata.get("execution") or {},
                    "artifact_count": int(artifact_count),
                    "modified_at": chat.modified_date.isoformat() if chat.modified_date else None,
                })
        return {"surface": "development", "threads": rows}

    def _list_reports(self, limit: int) -> dict[str, Any]:
        from distr.core.chat_turns import redact_text
        from distr.core.db import Chat, get_session
        from distr.core.db.kanban import ProjectExecutionSession
        from distr.core.db.workflow import DevelopmentWorkItem, StudioArtifact

        with get_session() as db:
            owned_ids = {
                int(row.chat_id)
                for row in db.query(DevelopmentWorkItem.chat_id).all()
            }
            reports = []
            for chat_id in list(owned_ids):
                chat = db.get(Chat, chat_id)
                if chat is None:
                    continue
                execution = (
                    db.query(ProjectExecutionSession)
                    .filter(ProjectExecutionSession.input_packet.contains(f'"chat_id": {chat_id}'))
                    .order_by(ProjectExecutionSession.updated_at.desc(), ProjectExecutionSession.id.desc())
                    .first()
                )
                artifacts = (
                    db.query(StudioArtifact)
                    .filter(StudioArtifact.chat_id == chat_id)
                    .order_by(StudioArtifact.sort_order, StudioArtifact.id)
                    .all()
                )
                reports.append({
                    "thread_id": chat_id,
                    "title": chat.title or "Development",
                    "status": getattr(execution, "status", None) or "idle",
                    "summary": (
                        redact_text(
                            str(getattr(execution, "output_packet", None) or ""),
                            limit=2000,
                            preserve_paths=False,
                        )
                        if execution is not None
                        else ""
                    ),
                    "artifacts": [
                        {"id": int(row.id), "type": row.artifact_type, "title": row.title, "status": row.status}
                        for row in artifacts
                    ],
                    "modified_at": chat.modified_date.isoformat() if chat.modified_date else None,
                })
            reports.sort(key=lambda item: item.get("modified_at") or "", reverse=True)
        return {"surface": "development", "reports": reports[:limit]}

    def _run(
        self,
        action: str = "list_threads",
        thread_id: Optional[int] = None,
        project_id: Optional[int] = None,
        title: str = "",
        instruction: str = "",
        automation_id: Optional[str] = None,
        schedule: Optional[dict[str, Any]] = None,
        status: str = "",
        provider: str = "",
        model: str = "",
        reasoning_effort: str = "",
        fallback_provider: str = "",
        fallback_model: str = "",
        sources: Optional[list[str]] = None,
        archived: bool = True,
        delete_linked_ticket: bool = True,
        confirm: bool = False,
        confirmation_token: str = "",
        limit: int = 50,
        **kwargs,
    ) -> str:
        action = str(action or "list_threads").strip().lower()
        limit = max(1, min(int(limit), 200))
        try:
            if action == "list_threads":
                return self._json(self._list_threads(limit))
            if action == "get_thread":
                from distr.core.workflow.development_control import thread_export

                return self._json(thread_export(self._require_thread_id(thread_id), redacted=True))
            if action == "create_thread":
                from distr.core.workflow.development_threads import ensure_development_thread

                created = ensure_development_thread(
                    title=str(title or "Development work").strip(),
                    project_id=project_id,
                    starting_question=str(instruction or "").strip() or None,
                    source_type="orchestrator",
                )
                return self._json({"surface": "development", "created": True, "thread_id": int(created)})
            if action == "fork_thread":
                from distr.core.workflow.development_control import fork_thread

                return self._json(fork_thread(self._require_thread_id(thread_id), title=title or None))
            if action == "archive_thread":
                from distr.core.workflow.development_control import archive_thread

                return self._json(archive_thread(self._require_thread_id(thread_id), archived=archived))
            if action == "delete_thread":
                target_thread_id = self._require_thread_id(thread_id)
                if not confirm or not self._consume_deletion_challenge(
                    confirmation_token,
                    action,
                    str(target_thread_id),
                    delete_linked_ticket,
                ):
                    return self._json({
                        "surface": "development",
                        "confirmation_required": True,
                        "confirmation_token": self._deletion_challenge(
                            action,
                            str(target_thread_id),
                            delete_linked_ticket,
                        ),
                        "thread_id": target_thread_id,
                        "delete_linked_ticket": bool(delete_linked_ticket),
                    })
                from distr.core.workflow.development_control import delete_thread

                return self._json(delete_thread(
                    target_thread_id,
                    delete_linked_ticket=delete_linked_ticket,
                ))
            if action == "list_workflows":
                from distr.core.workflow.service import list_workflows

                return self._json({"surface": "development", "workflows": list_workflows(limit=limit)})
            if action == "list_automations":
                from distr.core.automation.store import list_automations

                return self._json({"surface": "development", "automations": list_automations()[:limit]})
            if action == "get_automation":
                from distr.core.automation.store import get_automation

                if not automation_id:
                    raise ValueError("An explicit automation_id is required.")
                automation = get_automation(automation_id)
                if not automation:
                    raise ValueError("Automation not found.")
                return self._json({"surface": "development", "automation": automation})
            if action == "create_automation":
                from distr.core.automation.store import create_automation, get_automation, normalize_schedule

                clean_schedule = normalize_schedule(schedule or {"kind": "daily", "time": "09:00"}, strict=True)
                created = create_automation(
                    name=str(title or "New Automation").strip(),
                    automation_type="scheduled_instruction",
                    status=str(status or "active").strip().lower(),
                    instruction=str(instruction or "").strip(),
                    preset_id="",
                    schedule=clean_schedule,
                    action_config={
                        "run_in_new_thread": True,
                        "linked_project_id": int(project_id) if project_id is not None else None,
                        "model_provider": str(provider or "").strip().lower(),
                        "model": str(model or "").strip(),
                        "reasoning_effort": str(reasoning_effort or "medium").strip().lower(),
                        "fallback_model_provider": str(fallback_provider or "").strip().lower(),
                        "fallback_model": str(fallback_model or "").strip(),
                    },
                )
                from distr.core.automation_orchestrator import ensure_automation_thread

                try:
                    thread_id = ensure_automation_thread(created)
                except Exception:
                    from distr.core.automation.store import delete_automation

                    delete_automation(created["id"])
                    raise
                created = get_automation(created["id"]) or created
                return self._json({
                    "surface": "development",
                    "created": True,
                    "thread_id": int(thread_id),
                    "automation": created,
                })
            if action == "update_automation":
                from distr.core.automation.store import get_automation, update_automation

                if not automation_id:
                    raise ValueError("An explicit automation_id is required.")
                existing = get_automation(automation_id)
                if not existing:
                    raise ValueError("Automation not found.")
                fields: dict[str, Any] = {}
                if title:
                    fields["name"] = title.strip()
                if instruction:
                    fields["instruction"] = instruction.strip()
                if schedule:
                    fields["schedule"] = schedule
                if status:
                    fields["status"] = status.strip().lower()
                config = dict(existing.get("action_config") or {})
                for key, value in {
                    "model_provider": provider,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "fallback_model_provider": fallback_provider,
                    "fallback_model": fallback_model,
                }.items():
                    if str(value or "").strip():
                        config[key] = str(value).strip()
                config["run_in_new_thread"] = True
                fields["action_config"] = config
                updated = update_automation(automation_id, **fields)
                from distr.core.automation_orchestrator import ensure_automation_thread

                thread_id = ensure_automation_thread(updated)
                updated = get_automation(automation_id) or updated
                return self._json({
                    "surface": "development",
                    "updated": True,
                    "thread_id": int(thread_id),
                    "automation": updated,
                })
            if action in {"pause_automation", "resume_automation"}:
                from distr.core.automation.store import update_automation

                if not automation_id:
                    raise ValueError("An explicit automation_id is required.")
                next_status = "paused" if action == "pause_automation" else "active"
                return self._json({"surface": "development", "automation": update_automation(automation_id, status=next_status)})
            if action == "run_automation":
                from distr.core.automation.store import get_automation
                from distr.core.automation_orchestrator import dispatch_automation_to_current_chat

                if not automation_id:
                    raise ValueError("An explicit automation_id is required.")
                automation = get_automation(automation_id)
                if not automation:
                    raise ValueError("Automation not found.")
                return self._json({"surface": "development", "run": dispatch_automation_to_current_chat(automation, manual=True)})
            if action == "delete_automation":
                if not automation_id:
                    raise ValueError("An explicit automation_id is required.")
                return self._json({
                    "surface": "development",
                    "confirmation_required": True,
                    "ui_confirmation_required": True,
                    "automation_id": automation_id,
                    "message": "Delete this automation from its three-dot menu or its owned thread after a later user confirmation.",
                })
            if action == "import_automations":
                from distr.core.automation.imports import import_automations

                return self._json({"surface": "development", **import_automations(sources or None)})
            if action == "check_incoming":
                from distr.core.incoming.service import list_development_incoming

                return self._json({"surface": "development", **list_development_incoming(limit=limit)})
            if action == "list_reports":
                return self._json(self._list_reports(limit))
            return self._json({"error": f"Unknown Development action: {action}"})
        except (LookupError, ValueError) as error:
            return self._json({"surface": "development", "error": str(error)})

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
