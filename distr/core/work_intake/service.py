"""Classify channel requests into chat, tickets, or workflow actions."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, replace

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow
from distr.core.kanban.ticket_policy import resolve_ticket_complexity

from .contracts import WorkIntake, WorkIntakeAction, WorkIntakeDecision

logger = logging.getLogger(__name__)

_EXECUTE_RE = re.compile(r"\b(run|execute|start|push|send)\b.{0,30}\b(workflow|loop)\b|\b(push|send)\s+(it|this|ticket)\s+(into|to|through)\s+(the\s+)?(workflow|loop)\b", re.I | re.S)
_TICKET_RE = re.compile(r"\b(create|make|add|open|log|raise)\b.{0,24}\b(ticket|task|work item)\b|\b(ticket|task)\s*:\s*", re.I | re.S)
_UPDATE_RE = re.compile(r"\b(update|edit|change|append|add to)\b.{0,20}\b(ticket|task)\s*#?(\d+)\b", re.I | re.S)
_STEER_RE = re.compile(r"\b(continue|resume|stop|cancel|steer|change)\b.{0,30}\b(run|workflow)\s*#?(\d+)\b", re.I | re.S)
_BATCH_TICKETS_RE = re.compile(
    r"\b(?:create|make|open|add)?\s*(?:separate|individual|multiple)\s+"
    r"(?:tickets|tasks|work items)\s+(?:for|:)\s+(?P<items>.+?)"
    r"(?=(?:\.\s+|\n+)(?:run|execute|start|push|send|put|use|prefer|ask|report|update|then)\b|$)",
    re.I | re.S,
)
_SCOPE_STOPWORDS = {"board", "delivery", "house", "project", "ticket", "workflow"}


def _clean_title(value: str) -> str:
    clean = re.sub(r"^\s*(please\s+)?(create|make|add|open|log|raise|run|execute|start|push|send)\s+(a\s+)?(new\s+)?(ticket|task|work item)?\s*[:\-]?\s*", "", value, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip(" .:-")
    first = re.split(r"(?<=[.!?])\s+|\n", clean, maxsplit=1)[0].strip()
    return (first or "New work request")[:160]


def _scope_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) >= 4 and token not in _SCOPE_STOPWORDS
    }


def _explicit_batch_ticket_items(value: str) -> list[str]:
    """Extract a short, explicitly requested ticket list without guessing."""
    match = _BATCH_TICKETS_RE.search(str(value or ""))
    if not match:
        return []
    raw = match.group("items").strip().strip('"“”')
    parts = re.split(r"\s*(?:,|;)\s*|\s+\band\b\s+", raw, flags=re.I)
    items: list[str] = []
    for part in parts:
        clean = re.sub(r"^and\s+", "", part.strip(), flags=re.I)
        clean = re.sub(r"^(?:the|a|an)\s+", "", clean, flags=re.I)
        clean = clean.strip(" .:'\"“”")
        if not clean or len(clean.split()) > 18:
            return []
        items.append(clean)
    return items if 2 <= len(items) <= 20 else []


class OrchestratorIntakeService:
    def classify(self, intake: WorkIntake) -> WorkIntakeDecision:
        value = intake.text.strip()
        if not value and not intake.attachments:
            return WorkIntakeDecision(WorkIntakeAction.ASK_MISSING_INFO, "No request text or attachment was supplied", response_text="What would you like DecisionsAI to do?")
        steer = _STEER_RE.search(value)
        if steer:
            return WorkIntakeDecision(WorkIntakeAction.STEER_RUN, "Explicit workflow-run control command", diagnostics={"run_id": int(steer.group(3)), "command": steer.group(1).lower()})
        update = _UPDATE_RE.search(value)
        if update:
            return WorkIntakeDecision(WorkIntakeAction.UPDATE_TICKET, "Explicit ticket update command", diagnostics={"ticket_id": int(update.group(3))})
        if _EXECUTE_RE.search(value):
            return WorkIntakeDecision(WorkIntakeAction.RUN_WORKFLOW, "Explicit request to execute work through a workflow")
        if _TICKET_RE.search(value):
            return WorkIntakeDecision(WorkIntakeAction.CREATE_TICKET, "Explicit request to create a durable work item")
        if len(value.split()) < 2 and not intake.attachments:
            return WorkIntakeDecision(WorkIntakeAction.ASK_MISSING_INFO, "Request is too short to route safely", confidence=0.8, response_text="Could you add the outcome you want?")
        return WorkIntakeDecision(WorkIntakeAction.ANSWER_DIRECTLY, "Conversational or non-explicit request; preserve normal agent behaviour")

    def ingest(self, intake: WorkIntake, *, execute: bool = True) -> WorkIntakeDecision:
        decision = self.classify(intake)
        if not execute or decision.action == WorkIntakeAction.ANSWER_DIRECTLY:
            self._log_decision(intake, decision)
            return decision
        started = time.monotonic()
        try:
            batch_items = (
                _explicit_batch_ticket_items(intake.text)
                if decision.action in {WorkIntakeAction.CREATE_TICKET, WorkIntakeAction.RUN_WORKFLOW}
                else []
            )
            if batch_items:
                self._ingest_ticket_batch(intake, decision, batch_items)
            elif decision.action == WorkIntakeAction.CREATE_TICKET:
                self._create_ticket(intake, decision)
            elif decision.action == WorkIntakeAction.RUN_WORKFLOW:
                self._create_ticket(intake, decision)
                if decision.status != "duplicate":
                    self._start_workflow(intake, decision)
            elif decision.action == WorkIntakeAction.UPDATE_TICKET:
                self._update_ticket(intake, decision)
            elif decision.action == WorkIntakeAction.STEER_RUN:
                self._steer_run(intake, decision)
            else:
                decision.handled = True
                decision.status = "needs_info"
            decision.diagnostics["triage_elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        except Exception as exc:
            logger.exception("Orchestrator request failed uid=%s action=%s", intake.intake_uid, decision.action.value)
            decision.status = "failed"
            decision.handled = True
            decision.response_text = f"I could not route the request: {exc}"
            decision.diagnostics["error"] = str(exc)
        self._log_decision(intake, decision)
        return decision

    def _ingest_ticket_batch(
        self,
        intake: WorkIntake,
        decision: WorkIntakeDecision,
        items: list[str],
    ) -> None:
        """Create and optionally run each explicitly named work item independently."""
        ticket_ids: list[int] = []
        workflow_run_ids: list[int] = []
        duplicate_ticket_ids: list[int] = []
        first: WorkIntakeDecision | None = None
        original_text = intake.text

        for index, item in enumerate(items, start=1):
            child_metadata = dict(intake.metadata or {})
            child_metadata.update({
                "batch_index": index,
                "batch_count": len(items),
                "ticket_title": item,
                "ticket_description": f"{item}\n\nOriginal request:\n{original_text}",
            })
            child = replace(
                intake,
                source_message_id=(
                    f"{intake.source_message_id}::item:{index}"
                    if intake.source_message_id
                    else ""
                ),
                metadata=child_metadata,
                intake_uid=f"{intake.intake_uid}:{index}",
            )
            item_decision = WorkIntakeDecision(decision.action, decision.reason)
            self._create_ticket(child, item_decision)
            if first is None:
                first = item_decision
            if item_decision.ticket_id is not None:
                ticket_ids.append(int(item_decision.ticket_id))
            if item_decision.status == "duplicate":
                if item_decision.ticket_id is not None:
                    duplicate_ticket_ids.append(int(item_decision.ticket_id))
                continue
            if decision.action == WorkIntakeAction.RUN_WORKFLOW:
                self._start_workflow(child, item_decision)
                if item_decision.workflow_run_id is not None:
                    workflow_run_ids.append(int(item_decision.workflow_run_id))

        if first is None:
            raise ValueError("No ticket items were supplied")
        decision.ticket_id = first.ticket_id
        decision.board_id = first.board_id
        decision.project_id = first.project_id
        decision.workflow_id = first.workflow_id
        decision.workflow_run_id = workflow_run_ids[0] if workflow_run_ids else None
        decision.handled = True
        decision.diagnostics.update({
            "batch_count": len(items),
            "ticket_ids": ticket_ids,
            "workflow_run_ids": workflow_run_ids,
            "duplicate_ticket_ids": duplicate_ticket_ids,
        })
        ticket_refs = ", ".join(f"#{ticket_id}" for ticket_id in ticket_ids)
        if workflow_run_ids:
            run_refs = ", ".join(f"#{run_id}" for run_id in workflow_run_ids)
            decision.status = "workflow_started"
            decision.response_text = (
                f"Created tickets {ticket_refs} and started workflow runs {run_refs}."
            )
        elif len(duplicate_ticket_ids) == len(items):
            decision.status = "duplicate"
            decision.response_text = f"Tickets {ticket_refs} were already created from this message."
        else:
            decision.status = "ticket_created"
            decision.response_text = f"Created tickets {ticket_refs}."

    def _resolve_scope(
        self,
        intake: WorkIntake,
        *,
        require_workflow: bool = False,
    ) -> tuple[KanbanBoard, KanbanLane, Project | None, AutoWorkflow | None]:
        with get_session() as session:
            request_text = intake.text.lower()
            projects = session.query(Project).all()
            project = None
            project_hint = str(intake.project_hint or "").strip()
            if project_hint.isdigit():
                project = session.query(Project).filter(Project.id == int(project_hint)).first()
            elif project_hint:
                project = session.query(Project).filter(Project.name.ilike(f"%{project_hint}%")).first()
            if project is None and request_text:
                exact_projects = [row for row in projects if row.name and row.name.lower() in request_text]
                if len(exact_projects) == 1:
                    project = exact_projects[0]
                else:
                    request_tokens = _scope_tokens(request_text)
                    scored = [
                        (len(request_tokens & _scope_tokens(row.name)), row)
                        for row in projects
                    ]
                    scored = [item for item in scored if item[0] > 0]
                    scored.sort(key=lambda item: item[0], reverse=True)
                    if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                        project = scored[0][1]

            board = None
            hint = str(intake.board_hint or "").strip()
            if hint.isdigit():
                board = session.query(KanbanBoard).filter(KanbanBoard.id == int(hint)).first()
            elif hint:
                board = session.query(KanbanBoard).filter(KanbanBoard.name.ilike(f"%{hint}%"), KanbanBoard.archived.is_(False)).first()
            if board is None and project is not None:
                if getattr(project, "kanban_board_id", None):
                    board = session.query(KanbanBoard).filter(
                        KanbanBoard.id == int(project.kanban_board_id),
                        KanbanBoard.archived.is_(False),
                    ).first()
                if board is None:
                    board = session.query(KanbanBoard).filter(
                        KanbanBoard.default_project_id == project.id,
                        KanbanBoard.archived.is_(False),
                    ).first()
            workflow_hint = str(intake.workflow_hint or "").strip()
            hinted_workflow = None
            if workflow_hint.isdigit():
                hinted_workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_hint)).first()
            elif workflow_hint:
                hinted_workflow = session.query(AutoWorkflow).filter(AutoWorkflow.name.ilike(f"%{workflow_hint}%")).first()
            if board is None and hinted_workflow is not None:
                matching_boards = session.query(KanbanBoard).filter(
                    KanbanBoard.default_workflow_id == hinted_workflow.id,
                    KanbanBoard.archived.is_(False),
                ).all()
                if len(matching_boards) == 1:
                    board = matching_boards[0]
            if board is None:
                query = session.query(KanbanBoard).filter(KanbanBoard.archived.is_(False))
                if require_workflow:
                    query = query.filter(KanbanBoard.default_workflow_id.is_not(None))
                candidates = query.order_by(KanbanBoard.position, KanbanBoard.id).all()
                active = [row for row in candidates if bool(row.in_use)]
                if len(active) == 1:
                    board = active[0]
                elif len(candidates) == 1:
                    board = candidates[0]
                elif require_workflow:
                    raise ValueError("More than one workflow-ready board is available; name the project or board")
            if board is None:
                board = session.query(KanbanBoard).filter(KanbanBoard.archived.is_(False)).order_by(KanbanBoard.position, KanbanBoard.id).first()
            if board is None:
                raise ValueError("No active ticket board is available")
            lanes = session.query(KanbanLane).filter(KanbanLane.board_id == board.id).order_by(KanbanLane.position, KanbanLane.id).all()
            if not lanes:
                raise ValueError(f"Board '{board.name}' has no lanes")
            preferred = str(getattr(board, "agent_source_lane", "") or "").lower()
            lane = next((row for row in lanes if preferred and row.name.lower() == preferred), lanes[0])
            if project is None and board.default_project_id:
                project = session.query(Project).filter(Project.id == int(board.default_project_id)).first()
            workflow = hinted_workflow
            if workflow is None and board.default_workflow_id:
                workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(board.default_workflow_id)).first()
            if require_workflow and workflow is None:
                raise ValueError(f"Board '{board.name}' has no default workflow")
            session.expunge(board)
            session.expunge(lane)
            if project:
                session.expunge(project)
            if workflow:
                session.expunge(workflow)
            return board, lane, project, workflow

    def _create_ticket(self, intake: WorkIntake, decision: WorkIntakeDecision) -> None:
        board, lane, project, workflow = self._resolve_scope(
            intake,
            require_workflow=decision.action == WorkIntakeAction.RUN_WORKFLOW,
        )
        with get_session() as session:
            if intake.source_message_id:
                existing = session.query(KanbanTicket).filter(
                    KanbanTicket.source_provider == intake.source.value,
                    KanbanTicket.source_external_id == intake.source_message_id,
                ).first()
                if existing:
                    existing_lane = session.query(KanbanLane).filter(
                        KanbanLane.id == existing.lane_id,
                    ).first()
                    decision.ticket_id = existing.id
                    decision.board_id = existing_lane.board_id if existing_lane else board.id
                    decision.project_id = existing.linked_project_id
                    decision.workflow_id = existing.linked_workflow_id
                    decision.handled = True
                    decision.status = "duplicate"
                    decision.response_text = f"Ticket #{existing.id} was already created from this message."
                    return
            maximum = session.query(KanbanTicket).filter(KanbanTicket.lane_id == lane.id).order_by(KanbanTicket.position.desc()).first()
            value = str(
                intake.metadata.get("ticket_title")
                or intake.text
                or intake.requested_outcome
            )
            attachment_lines = []
            for attachment in intake.attachments:
                location = attachment.path or attachment.url or attachment.name
                if location:
                    label = attachment.kind or "file"
                    mime = f" ({attachment.mime_type})" if attachment.mime_type else ""
                    attachment_lines.append(f"- {label}: {location}{mime}")
            description = str(intake.metadata.get("ticket_description") or value)
            if attachment_lines:
                description = f"{description}\n\nAttachments:\n" + "\n".join(attachment_lines)
            ticket = KanbanTicket(
                lane_id=lane.id,
                title=_clean_title(value),
                description=description,
                priority="high" if intake.urgency in {"urgent", "high", "critical"} else "medium",
                complexity=resolve_ticket_complexity(_clean_title(value), value),
                position=(int(maximum.position) + 1) if maximum else 0,
                linked_workflow_id=workflow.id if workflow else board.default_workflow_id,
                linked_project_id=project.id if project else board.default_project_id,
                source_provider=intake.source.value,
                source_external_id=intake.source_message_id or None,
                source_thread_id=intake.source_thread_id or None,
                source_contact=intake.source_user_id or None,
                source_label=intake.source.value.replace("_", " ").title(),
            )
            session.add(ticket)
            session.flush()
            decision.ticket_id = ticket.id
            decision.board_id = board.id
            decision.project_id = ticket.linked_project_id
            decision.workflow_id = ticket.linked_workflow_id
        try:
            from distr.core.orchestrator import emit_channel_intake_event

            emit_channel_intake_event(
                channel=intake.source.value,
                ticket_id=int(decision.ticket_id),
                board_id=decision.board_id,
                workflow_id=decision.workflow_id,
                project_id=decision.project_id,
                summary=f"{intake.source.value.title()} created ticket #{decision.ticket_id}: {_clean_title(value)}",
                payload={"request_uid": intake.intake_uid, "attachment_count": len(intake.attachments)},
            )
        except Exception:
            logger.debug("Could not record channel intake event", exc_info=True)
        decision.handled = True
        decision.status = "ticket_created"
        decision.response_text = f"Created ticket #{decision.ticket_id}: {_clean_title(value)}"

    def _start_workflow(self, intake: WorkIntake, decision: WorkIntakeDecision) -> None:
        if not decision.ticket_id or not decision.workflow_id:
            raise ValueError("The selected board has no default workflow")
        from distr.core.workflow.service import start_workflow_run

        from distr.core.workflow.ticket_dispatch import build_ticket_run_item

        item = build_ticket_run_item(decision.ticket_id, decision.workflow_id)
        metadata = dict(item.get("run_metadata") or {})
        from .execution_policy import compile_requested_execution_policy

        requested_execution_policy = compile_requested_execution_policy(intake.text)
        metadata.update({
            "source_type": intake.source.value,
            "request_uid": intake.intake_uid,
            "phase": "planning",
            "source_thread_id": intake.source_thread_id or None,
            "source_user_id": intake.source_user_id or None,
            "attachments": [asdict(attachment) for attachment in intake.attachments],
        })
        if requested_execution_policy:
            metadata["requested_execution_policy"] = requested_execution_policy
        result = start_workflow_run(
            decision.workflow_id,
            context=str(item.get("context") or f"Ticket: {_clean_title(intake.text)}"),
            board_id=item.get("board_id") or decision.board_id,
            ticket_id=decision.ticket_id,
            run_metadata=metadata,
            dispatch_async=True,
        )
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        decision.workflow_run_id = int(result.get("run_id")) if result.get("run_id") else None
        decision.status = "workflow_started"
        decision.response_text = f"Created ticket #{decision.ticket_id} and started workflow run #{decision.workflow_run_id}."

    def _update_ticket(self, intake: WorkIntake, decision: WorkIntakeDecision) -> None:
        ticket_id = int(decision.diagnostics["ticket_id"])
        with get_session() as session:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
            if not ticket:
                raise ValueError(f"Ticket #{ticket_id} was not found")
            note = intake.text.strip()
            ticket.context_notes = "\n\n".join(part for part in (ticket.context_notes or "", note) if part).strip()
            decision.ticket_id = ticket.id
        decision.handled = True
        decision.status = "ticket_updated"
        decision.response_text = f"Updated ticket #{ticket_id}."

    def _steer_run(self, intake: WorkIntake, decision: WorkIntakeDecision) -> None:
        run_id = int(decision.diagnostics["run_id"])
        command = str(decision.diagnostics["command"])
        if command in {"stop", "cancel"}:
            from distr.core.workflow.dispatcher import cancel_run
            ok = bool(cancel_run(run_id))
        else:
            from distr.core.workflow.service import apply_run_harness_steer
            result = apply_run_harness_steer(run_id, intake.text, source=intake.source.value)
            ok = bool(result.get("success")) if isinstance(result, dict) else bool(result)
        if not ok:
            raise RuntimeError(f"Workflow run #{run_id} did not accept {command}")
        decision.workflow_run_id = run_id
        decision.handled = True
        decision.status = "workflow_interaction"
        decision.response_text = f"Workflow run #{run_id} accepted: {command}."

    @staticmethod
    def _log_decision(intake: WorkIntake, decision: WorkIntakeDecision) -> None:
        logger.info(
            "Orchestrator request uid=%s source=%s action=%s status=%s ticket=%s run=%s",
            intake.intake_uid,
            intake.source.value,
            decision.action.value,
            decision.status,
            decision.ticket_id,
            decision.workflow_run_id,
        )


_service: OrchestratorIntakeService | None = None


def get_work_intake_service() -> OrchestratorIntakeService:
    global _service
    if _service is None:
        _service = OrchestratorIntakeService()
    return _service
