"""Classify channel requests into chat, tickets, or workflow actions."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, replace
from typing import Any

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow
from distr.core.kanban.ticket_policy import resolve_ticket_complexity

from .contracts import WorkIntake, WorkIntakeAction, WorkIntakeDecision

logger = logging.getLogger(__name__)

_EXECUTE_RE = re.compile(
    r"\b(run|execute|start|push|send)\b.{0,120}\b(workflow|loop)\b"
    r"|\b(push|send)\s+(it|this|ticket)\s+(into|to|through)\s+"
    r"(?:the\s+|a\s+|configured\s+)?(workflow|loop)\b",
    re.I | re.S,
)
_TICKET_RE = re.compile(
    r"\b(?:create|make|add|open|log|raise)\s+"
    r"(?:an?\s+)?(?:new\s+)?(?:(?:urgent|high|medium|low|priority)\s+){0,2}"
    r"(?:ticket|task|work item)\b|\b(?:ticket|task)\s*:\s*",
    re.I,
)
_UPDATE_RE = re.compile(r"\b(update|edit|change|append|add to)\b.{0,20}\b(ticket|task)\s*#?(\d+)\b", re.I | re.S)
_STEER_RE = re.compile(r"\b(continue|resume|stop|cancel|steer|change)\b.{0,30}\b(run|workflow)\s*#?(\d+)\b", re.I | re.S)
_PROJECT_WORK_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:make|change|fix|repair|patch|update|add|remove|rename|replace|run|execute|build|implement|"
    r"perform|assess|analyse|analyze|refactor|test|validate|verify|audit|review|inspect|"
    r"investigate|redesign|copy|migrate|integrate)\b",
    re.I,
)
_BATCH_TICKETS_RE = re.compile(
    r"\b(?:create|make|open|add)?\s*(?:separate|individual|multiple)\s+"
    r"(?:tickets|tasks|work items)\s+(?:for|:)\s+(?P<items>.+?)"
    r"(?=(?:\.\s+|\n+)(?:run|execute|start|push|send|put|use|prefer|ask|report|update|then)\b|$)",
    re.I | re.S,
)
_LABELED_BATCH_INTENT_RE = re.compile(
    r"\b(?:create|make|open|add)\s+"
    r"(?:(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+)?"
    r"(?:separate|individual|multiple)\s+(?:tickets|tasks|work items)\b",
    re.I,
)
_LABELED_BATCH_ITEM_RE = re.compile(
    r"\b(?:ticket|task|work item)\s+"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s*:\s*",
    re.I,
)
_BATCH_CONTROL_SENTENCE_RE = re.compile(
    r"\.\s+(?=(?:"
    r"keep\s+(?:them|these|the\s+(?:tickets|tasks|work items))"
    r"|(?:run|execute|start|push|send|put|use|prefer|report|update)\s+"
    r"(?:them|these|the\s+(?:tickets|tasks|work items|group))"
    r"|do\s+not\s+start\s+(?:ticket|task|work item)"
    r"|return\s+(?:a\s+)?final\s+report\s+for\s+(?:both|all)"
    r")\b)",
    re.I,
)
_SCOPE_STOPWORDS = {"board", "delivery", "house", "project", "ticket", "workflow"}

_UNRESOLVED_REFERENCE_RE = re.compile(
    r"\b(?:"
    r"(?:the|that)\s+(?:thing|stuff|issue|change|work)"
    r"|(?:make|change|fix|repair|patch|update|add\s+to|remove|rename|replace|"
    r"inspect|review|test|validate)\s+(?:it|this|that)\b"
    r"|(?:it|this|that)\s+(?:we\s+)?(?:discussed|mentioned|talked\s+about|spoke\s+about)"
    r"|what\s+we\s+(?:discussed|mentioned|talked\s+about|spoke\s+about)"
    r")\b",
    re.I,
)


def _has_unresolved_reference(intake: WorkIntake) -> bool:
    """Detect work commands whose omitted object depends on absent context."""
    if intake.attachments or str(intake.conversation_context or "").strip():
        return False
    return bool(_UNRESOLVED_REFERENCE_RE.search(intake.text))


def _is_lightweight_project_change(value: str) -> bool:
    """Conservatively identify an atomic change that does not need a full loop."""
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    # Atomic requests often include the target, desired value, and acceptance
    # hint in one sentence. A 14-word ceiling routed ordinary one-line edits
    # into the full seven-step Development workflow. Multi-step/risky intent is
    # screened below, so keep a modest single-clause allowance instead.
    if not clean or len(clean.split()) > 24 or "\n" in str(value or ""):
        return False
    # A literal/named "test command" is documentation or configuration data,
    # not a request to design and execute a test campaign. Keep actual testing,
    # test coverage, and validation requests on the full development workflow.
    risk_text = re.sub(r"\btest\s+command\b", "command", clean, flags=re.I)
    risk_text = re.sub(r"`[^`]*`", "", risk_text)
    if re.search(
        r"\b(?:and then|after that|multiple|across|audit|investigate|test|validate|"
        r"refactor|redesign|build|implement|migrate|integrate|bug|error|failing)\b",
        risk_text,
        re.I,
    ):
        return False
    return bool(re.match(
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
        r"(?:make|change|update|add|remove|rename|replace)\b",
        clean,
        re.I,
    ))


def _clean_title(value: str) -> str:
    value = re.sub(
        r"^\s*(?:please\s+)?(?:run|execute|start|push|send)\s+"
        r"(?:(?:this|it|the\s+request|the\s+ticket)\s+)"
        r"(?:through|into|to)\s+(?:the\s+)?(?:workflow|loop)\s*"
        r"(?:as\s+(?:a\s+)?)?[:\-]?\s*",
        "",
        str(value or ""),
        flags=re.I,
    )
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
    text = str(value or "")
    # Natural channel requests often spell out substantial tickets as
    # ``Ticket one: ... Ticket two: ...``.  This is stronger evidence than a
    # comma-separated noun list and must not be collapsed into one umbrella
    # ticket merely because a count appears before "separate tickets".
    if _LABELED_BATCH_INTENT_RE.search(text):
        labels = list(_LABELED_BATCH_ITEM_RE.finditer(text))
        if 2 <= len(labels) <= 20:
            labeled_items: list[str] = []
            for index, label in enumerate(labels):
                end = labels[index + 1].start() if index + 1 < len(labels) else len(text)
                raw_item = text[label.end():end].strip()
                control = _BATCH_CONTROL_SENTENCE_RE.search(raw_item)
                if control:
                    raw_item = raw_item[:control.start() + 1]
                clean = raw_item.strip(" .:'\"“”")
                if not clean or len(clean.split()) > 80:
                    return []
                labeled_items.append(clean)
            return labeled_items

    match = _BATCH_TICKETS_RE.search(text)
    if not match:
        return []
    raw = match.group("items").strip().strip('"“”')
    # Prefer explicit punctuation as the list boundary. Splitting every
    # occurrence of "and" turns one compound task ("run tests and report")
    # into two bogus tickets. Bare "A and B" remains supported only when the
    # request did not provide commas or semicolons.
    if ";" in raw:
        parts = re.split(r"\s*;\s*", raw)
    elif "," in raw:
        parts = re.split(r"\s*,\s*", raw)
    else:
        parts = re.split(r"\s+\band\b\s+", raw, flags=re.I)
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
        project_scope = bool(
            str(intake.project_hint or "").strip()
            or (intake.metadata or {}).get("project_id")
            or (intake.metadata or {}).get("active_project_id")
            or (intake.metadata or {}).get("project_name")
        )
        # A project association supplies scope, not intent. Never turn a lone
        # verb such as "fix" into permission to create a ticket and execute
        # against that project; ask for the missing object/outcome first.
        if len(value.split()) < 2 and not intake.attachments:
            return WorkIntakeDecision(
                WorkIntakeAction.ASK_MISSING_INFO,
                "Request is too short to route safely",
                confidence=0.8,
                response_text="What specifically should I change, and what result should I verify?",
            )
        if project_scope and _PROJECT_WORK_RE.search(value) and _has_unresolved_reference(intake):
            return WorkIntakeDecision(
                WorkIntakeAction.ASK_MISSING_INFO,
                "The request refers to prior context that was not supplied",
                confidence=0.9,
                response_text=(
                    "What specifically should I change in this project, and what result should I verify?"
                ),
            )
        if project_scope and _PROJECT_WORK_RE.search(value):
            if _is_lightweight_project_change(value):
                return WorkIntakeDecision(
                    WorkIntakeAction.CREATE_TICKET,
                    "Atomic project change; create a trackable ticket and execute it with the lightweight project worker",
                    diagnostics={
                        "routing_shape": "lightweight_project_work",
                        "project_scoped": True,
                        "execute_lightweight": True,
                    },
                )
            return WorkIntakeDecision(
                WorkIntakeAction.RUN_WORKFLOW,
                "Project-scoped work request; create a trackable ticket and execute it through the linked workflow",
                diagnostics={"routing_shape": "project_work", "project_scoped": True},
            )
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
                if (
                    decision.status != "duplicate"
                    and decision.diagnostics.get("execute_lightweight")
                ):
                    self._start_lightweight_execution(intake, decision)
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

    def _start_lightweight_execution(
        self,
        intake: WorkIntake,
        decision: WorkIntakeDecision,
    ) -> None:
        """Execute one atomic ticket through the normal tracked CLI lifecycle."""
        if not decision.ticket_id:
            raise ValueError("Lightweight execution requires a created ticket")

        def execute() -> dict[str, Any]:
            from distr.core.initiative.action_handlers import run_project_cli_tasks

            return run_project_cli_tasks({
                "ticket_ids": [int(decision.ticket_id)],
                "project_id": decision.project_id,
            })

        synchronous = bool(
            (intake.metadata or {}).get("qualification_sync_execution", False)
        )
        if synchronous:
            outcome = execute()
            rows = outcome.get("results") if isinstance(outcome.get("results"), list) else []
            first = rows[0] if rows and isinstance(rows[0], dict) else {}
            ticket_lane = ""
            try:
                with get_session() as session:
                    ticket = session.query(KanbanTicket).filter(
                        KanbanTicket.id == int(decision.ticket_id)
                    ).first()
                    ticket_lane = str(
                        getattr(getattr(ticket, "lane", None), "name", "") or ""
                    )
            except Exception:
                logger.debug("Could not inspect lightweight ticket lifecycle", exc_info=True)
            decision.diagnostics.update({
                "execution_completed": bool(outcome.get("success")),
                "execution_session_id": first.get("execution_session_id"),
                "execution_backend": first.get("backend_id") or first.get("engine"),
                "terminal_report_observed": bool(
                    str(first.get("output") or first.get("error") or "").strip()
                ),
                "ticket_lane": ticket_lane,
                "lifecycle_correct": bool(
                    outcome.get("success") and ticket_lane.strip().lower() in {"qa", "quality assurance"}
                ),
            })
            decision.status = "completed" if outcome.get("success") else "failed"
            decision.response_text = str(outcome.get("message") or "").strip()
            if not outcome.get("success"):
                raise RuntimeError(decision.response_text or "Lightweight project execution failed")
            return

        threading.Thread(
            target=self._run_lightweight_execution_background,
            args=(int(decision.ticket_id), decision.project_id),
            daemon=True,
            name=f"DecisionsLightweightTicket-{int(decision.ticket_id)}",
        ).start()
        decision.status = "execution_started"
        decision.response_text = (
            f"Created ticket #{decision.ticket_id} and started the project change. "
            "I’ll move it to QA when the checks pass."
        )

    @staticmethod
    def _run_lightweight_execution_background(
        ticket_id: int,
        project_id: int | None,
    ) -> None:
        try:
            from distr.core.initiative.action_handlers import run_project_cli_tasks

            run_project_cli_tasks({
                "ticket_ids": [int(ticket_id)],
                "project_id": project_id,
            })
        except Exception:
            logger.exception("Lightweight ticket execution failed ticket_id=%s", ticket_id)

    def _ingest_ticket_batch(
        self,
        intake: WorkIntake,
        decision: WorkIntakeDecision,
        items: list[str],
    ) -> None:
        """Create an explicit ticket plan and run it as one ordered group."""
        ticket_ids: list[int] = []
        workflow_run_ids: list[int] = []
        duplicate_ticket_ids: list[int] = []
        group_ticket_items: list[dict[str, int | None]] = []
        first: WorkIntakeDecision | None = None
        for index, item in enumerate(items, start=1):
            child_metadata = dict(intake.metadata or {})
            child_metadata.update({
                "batch_index": index,
                "batch_count": len(items),
                "ticket_title": item,
                # The inbound request and the executable ticket are separate
                # concepts.  The channel/message identifiers below preserve the
                # source trace; repeating the whole request in every ticket makes
                # each worker re-plan the batch and bloats every model handoff.
                "ticket_description": item,
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
            if (
                decision.action == WorkIntakeAction.RUN_WORKFLOW
                and item_decision.ticket_id is not None
            ):
                group_ticket_items.append({
                    "ticket_id": int(item_decision.ticket_id),
                    "board_id": (
                        int(item_decision.board_id)
                        if item_decision.board_id is not None
                        else None
                    ),
                })

        if first is None:
            raise ValueError("No ticket items were supplied")
        decision.ticket_id = first.ticket_id
        decision.board_id = first.board_id
        decision.project_id = first.project_id
        decision.workflow_id = first.workflow_id
        group_result: dict[str, Any] = {}
        if decision.action == WorkIntakeAction.RUN_WORKFLOW and group_ticket_items:
            if not decision.workflow_id:
                raise ValueError("The selected board has no default workflow")
            from distr.core.workflow.dispatcher import start_workflow_ticket_group

            group_result = start_workflow_ticket_group(
                int(decision.workflow_id),
                group_ticket_items,
                run_metadata=self._workflow_intake_metadata(intake, decision),
                dispatch_async=True,
            )
            if group_result.get("error") or not group_result.get("success"):
                raise RuntimeError(
                    str(
                        group_result.get("error")
                        or group_result.get("errors")
                        or "Ticket group did not start"
                    )
                )
            workflow_run_ids = [
                int(row["run_id"])
                for row in group_result.get("started") or []
                if isinstance(row, dict) and row.get("run_id") is not None
            ]
        decision.workflow_run_id = workflow_run_ids[0] if workflow_run_ids else None
        decision.handled = True
        decision.diagnostics.update({
            "batch_count": len(items),
            "ticket_ids": ticket_ids,
            "workflow_run_ids": workflow_run_ids,
            "duplicate_ticket_ids": duplicate_ticket_ids,
            "ticket_group_id": group_result.get("group_id"),
            "ticket_group_mode": group_result.get("mode"),
            "ticket_group_queued_count": int(group_result.get("queued_count") or 0),
        })
        ticket_refs = ", ".join(f"#{ticket_id}" for ticket_id in ticket_ids)
        if workflow_run_ids:
            decision.status = "workflow_started"
            decision.response_text = (
                f"Created an ordered group of {len(ticket_ids)} tickets and started the first workflow run. "
                f"The remaining {int(group_result.get('queued_count') or 0)} will follow in sequence."
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
            project_hint = str(
                intake.project_hint
                or (intake.metadata or {}).get("project_id")
                or (intake.metadata or {}).get("active_project_id")
                or (intake.metadata or {}).get("project_name")
                or ""
            ).strip()
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
            if board is None and project is not None:
                # A project may outlive a deleted/recreated board. Never place
                # its work on whichever unrelated board happens to be active.
                # Repair the project control surface with the standard delivery
                # lanes and the canonical Development workflow when available.
                from sqlalchemy import func
                from distr.core.kanban.lifecycle import ensure_delivery_lanes

                default_workflow = hinted_workflow or (
                    session.query(AutoWorkflow)
                    .filter(AutoWorkflow.name.ilike("Development%"))
                    .order_by(AutoWorkflow.id)
                    .first()
                )
                max_position = session.query(func.max(KanbanBoard.position)).scalar()
                board = KanbanBoard(
                    name=f"{project.name} Delivery",
                    default_project_id=int(project.id),
                    default_workflow_id=(
                        int(default_workflow.id) if default_workflow is not None else None
                    ),
                    position=int(max_position or 0) + 1,
                    in_use=False,
                    archived=False,
                )
                session.add(board)
                session.flush()
                ensure_delivery_lanes(session, int(board.id))
                project.kanban_board_id = int(board.id)
                session.flush()
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
        # Board/project links can change after their companion workspace was
        # first created. Refresh the small router files at the intake boundary
        # so a worker never follows stale "no linked board/project" context.
        try:
            from distr.core.workspace_memory.lifecycle import hook_ensure_workspace

            if decision.board_id:
                hook_ensure_workspace("boards", int(decision.board_id), force=True, reason="work_intake")
            if decision.project_id:
                hook_ensure_workspace("projects", int(decision.project_id), force=True, reason="work_intake")
        except Exception:
            logger.debug("Could not refresh intake workspace routers", exc_info=True)
        from distr.core.workflow.service import start_workflow_run

        from distr.core.workflow.ticket_dispatch import build_ticket_run_item

        item = build_ticket_run_item(decision.ticket_id, decision.workflow_id)
        metadata = self._workflow_intake_metadata(
            intake,
            decision,
            base=dict(item.get("run_metadata") or {}),
        )
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

    @staticmethod
    def _workflow_intake_metadata(
        intake: WorkIntake,
        decision: WorkIntakeDecision,
        *,
        base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return channel-neutral metadata shared by single and grouped work."""
        metadata = dict(base or {})
        from .execution_policy import compile_requested_execution_policy

        requested_execution_policy = compile_requested_execution_policy(intake.text)
        metadata.update({
            "source_type": intake.source.value,
            "request_uid": intake.intake_uid,
            "phase": "planning",
            "intake_action": decision.action.value,
            "intake_reason": decision.reason,
            "intake_confidence": decision.confidence,
            "source_thread_id": intake.source_thread_id or None,
            "source_user_id": intake.source_user_id or None,
            "attachments": [asdict(attachment) for attachment in intake.attachments],
        })
        # Preserve only the narrow qualification correlation fields. Copying
        # arbitrary channel metadata into the durable run would pollute worker
        # context and make benchmark evidence user-controlled.
        qualification_scenario_id = str(
            (intake.metadata or {}).get("qualification_scenario_id") or ""
        ).strip()
        if qualification_scenario_id:
            metadata["qualification_scenario_id"] = qualification_scenario_id
            metadata["qualification_auto_record"] = bool(
                (intake.metadata or {}).get("qualification_auto_record", False)
            )
            injected_failure = str(
                (intake.metadata or {}).get("qualification_injected_failure") or ""
            ).strip()
            if injected_failure:
                metadata["qualification_injected_failure"] = injected_failure
            if qualification_scenario_id == "telegram_control_round_trip":
                metadata["qualification_remote_control_probe"] = bool(
                    (intake.metadata or {}).get("qualification_remote_control_probe", False)
                )
        if requested_execution_policy:
            metadata["requested_execution_policy"] = requested_execution_policy
        return metadata

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
        try:
            from distr.core.orchestrator import emit_event

            needs_attention = decision.action in {
                WorkIntakeAction.ASK_MISSING_INFO,
                WorkIntakeAction.REQUEST_APPROVAL,
            } or decision.status in {"needs_info", "failed"}
            emit_event(
                source=str(intake.source.value or "unknown"),
                event_type="work_intake_decision",
                status=("needs_attention" if needs_attention else decision.status or "triaged"),
                ticket_id=decision.ticket_id,
                board_id=decision.board_id,
                project_id=decision.project_id,
                workflow_id=decision.workflow_id,
                run_id=decision.workflow_run_id,
                summary=(
                    decision.response_text
                    or f"{decision.action.value}: {(intake.text or '')[:180]}"
                ),
                payload={
                    "intake": intake.to_dict(),
                    "decision": decision.to_dict(),
                    "needs_attention": needs_attention,
                },
            )
        except Exception:
            logger.debug("Could not persist work intake decision event", exc_info=True)

    def list_inbox(self, *, limit: int = 40) -> list[dict[str, Any]]:
        """Return recent intake items that still need Mission Control attention."""
        try:
            from distr.core.db import get_session
            from distr.core.db.orchestrator import OrchestratorEvent
            from distr.core.orchestrator import ensure_orchestrator_tables, serialize_event

            ensure_orchestrator_tables()
            with get_session() as session:
                rows = (
                    session.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.event_type == "work_intake_decision")
                    .order_by(OrchestratorEvent.created_at.desc(), OrchestratorEvent.id.desc())
                    .limit(max(1, min(int(limit or 40), 100)))
                    .all()
                )
                events = [serialize_event(row) for row in rows]
        except Exception:
            logger.debug("list_inbox failed", exc_info=True)
            events = []
        inbox: list[dict[str, Any]] = []
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload or "{}") or {}
                except Exception:
                    payload = {}
            decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
            intake = payload.get("intake") if isinstance(payload.get("intake"), dict) else {}
            status = str(event.get("status") or decision.get("status") or "").strip().lower()
            action = str(decision.get("action") or "").strip().lower()
            needs = bool(payload.get("needs_attention")) or status in {
                "needs_attention",
                "needs_info",
                "failed",
            } or action in {"ask_missing_info", "request_approval"}
            if status in {"resolved", "dismissed", "handled"}:
                continue
            if not needs and action not in {
                "create_ticket",
                "run_workflow",
                "steer_run",
                "update_ticket",
            }:
                continue
            inbox.append({
                "event_id": event.get("id"),
                "created_at": event.get("created_at"),
                "source": intake.get("source") or event.get("source"),
                "text": intake.get("user_text") or intake.get("transcript") or event.get("summary") or "",
                "action": action,
                "status": status or "triaged",
                "response_text": decision.get("response_text") or event.get("summary") or "",
                "ticket_id": decision.get("ticket_id") or event.get("ticket_id"),
                "workflow_run_id": decision.get("workflow_run_id") or event.get("run_id"),
                "workflow_id": decision.get("workflow_id") or event.get("workflow_id"),
                "board_id": decision.get("board_id") or event.get("board_id"),
                "project_id": decision.get("project_id") or event.get("project_id"),
                "intake_uid": intake.get("intake_uid") or "",
                "needs_attention": needs,
            })
        return inbox

    def record_direct_response(
        self,
        *,
        source: str,
        source_thread_id: str,
        response_text: str,
        intake_uid: str = "",
    ) -> bool:
        """Correlate a final conversational answer with its intake decision.

        ``answer_directly`` intentionally leaves execution to the normal chat
        agent.  Persisting this separate event closes that asynchronous loop
        without mutating the append-only decision event or coupling intake to a
        particular model/provider implementation.
        """
        response = str(response_text or "").strip()
        thread_id = str(source_thread_id or "").strip()
        if not response or not thread_id:
            return False
        try:
            from distr.core.db.orchestrator import OrchestratorEvent
            from distr.core.orchestrator import emit_event, ensure_orchestrator_tables

            ensure_orchestrator_tables()
            with get_session() as session:
                rows = (
                    session.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.event_type == "work_intake_decision")
                    .order_by(OrchestratorEvent.id.desc())
                    .limit(100)
                    .all()
                )
                matched: tuple[OrchestratorEvent, dict[str, Any], dict[str, Any]] | None = None
                for row in rows:
                    try:
                        payload = json.loads(row.payload or "{}") or {}
                    except (TypeError, json.JSONDecodeError):
                        continue
                    intake = payload.get("intake") if isinstance(payload.get("intake"), dict) else {}
                    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
                    if str(intake.get("source") or row.source or "").strip() != str(source or "").strip():
                        continue
                    if str(intake.get("source_thread_id") or "").strip() != thread_id:
                        continue
                    if intake_uid and str(intake.get("intake_uid") or "") != str(intake_uid):
                        continue
                    if str(decision.get("action") or "") != WorkIntakeAction.ANSWER_DIRECTLY.value:
                        continue
                    if str(decision.get("response_text") or "").strip():
                        continue
                    matched = (row, intake, decision)
                    break
                if matched is None:
                    return False
                row, intake, decision = matched
                uid = str(intake.get("intake_uid") or "")
                existing = (
                    session.query(OrchestratorEvent.id)
                    .filter(OrchestratorEvent.event_type == "work_intake_response")
                    .filter(OrchestratorEvent.parent_event_id == int(row.id))
                    .first()
                )
                if existing:
                    return False
                event_context = {
                    "ticket_id": row.ticket_id,
                    "board_id": row.board_id,
                    "project_id": row.project_id,
                    "workflow_id": row.workflow_id,
                    "run_id": row.run_id,
                }
                parent_event_id = int(row.id)
            emit_event(
                source=str(source or "chat"),
                event_type="work_intake_response",
                status="completed",
                parent_event_id=parent_event_id,
                summary=response,
                payload={
                    "intake_uid": uid,
                    "source_message_id": str(intake.get("source_message_id") or ""),
                    "source_thread_id": thread_id,
                    "response_text": response,
                    "decision_action": str(decision.get("action") or ""),
                    "phase": "final",
                },
                **event_context,
            )
            return True
        except Exception:
            logger.debug("Could not correlate direct intake response", exc_info=True)
            return False

    def resolve_inbox_item(
        self,
        event_id: int,
        *,
        action: str,
        message: str = "",
    ) -> dict[str, Any]:
        """Mirror Telegram continue/stop/steer/push actions from Mission Control."""
        from distr.core.db import get_session
        from distr.core.db.orchestrator import OrchestratorEvent
        from distr.core.orchestrator import ensure_orchestrator_tables

        ensure_orchestrator_tables()
        clean_action = str(action or "").strip().lower()
        clean_message = " ".join(str(message or "").split()).strip()
        with get_session() as session:
            row = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == int(event_id)).first()
            if not row:
                return {"success": False, "error": "Inbox item not found"}
            try:
                payload = json.loads(row.payload or "{}") or {}
            except Exception:
                payload = {}
            decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
            intake = payload.get("intake") if isinstance(payload.get("intake"), dict) else {}
            ticket_id = decision.get("ticket_id") or row.ticket_id
            run_id = decision.get("workflow_run_id") or row.run_id
            result: dict[str, Any] = {"success": True, "action": clean_action}

            if clean_action in {"dismiss", "resolve"}:
                row.status = "resolved"
                payload["needs_attention"] = False
                payload["resolved_action"] = clean_action
                row.payload = json.dumps(payload)
                session.commit()
                return result

            if clean_action == "create_ticket":
                child = WorkIntake.from_payload({
                    **intake,
                    "user_text": clean_message or intake.get("user_text") or intake.get("transcript") or "",
                    "source": intake.get("source") or row.source or "web",
                })
                created = self.ingest(child)
                result["decision"] = created.to_dict()
                row.status = "resolved"
                payload["needs_attention"] = False
                payload["resolved_action"] = clean_action
                row.payload = json.dumps(payload)
                session.commit()
                return result

            if clean_action in {"push", "run", "push_to_loop"}:
                if not ticket_id:
                    return {"success": False, "error": "No ticket available to push into a loop"}
                workflow_id = None
                board_id = None
                project_id = None
                with get_session() as ticket_session:
                    ticket = ticket_session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
                    if not ticket:
                        return {"success": False, "error": f"Ticket #{ticket_id} was not found"}
                    workflow_id = ticket.linked_workflow_id
                    project_id = ticket.linked_project_id
                    if ticket.lane_id:
                        lane = ticket_session.query(KanbanLane).filter(KanbanLane.id == int(ticket.lane_id)).first()
                        board_id = getattr(lane, "board_id", None) if lane else None
                if not workflow_id:
                    return {"success": False, "error": "Ticket has no linked workflow"}
                push_decision = WorkIntakeDecision(
                    WorkIntakeAction.RUN_WORKFLOW,
                    "Mission Control push to Loop",
                    ticket_id=int(ticket_id),
                    board_id=int(board_id) if board_id else None,
                    workflow_id=int(workflow_id),
                    project_id=int(project_id) if project_id else None,
                )
                child = WorkIntake.from_payload({
                    **intake,
                    "user_text": clean_message or intake.get("user_text") or f"Push ticket #{ticket_id} into the workflow",
                    "source": intake.get("source") or row.source or "web",
                })
                self._start_workflow(child, push_decision)
                result["decision"] = push_decision.to_dict()
                row.status = "resolved"
                payload["needs_attention"] = False
                payload["resolved_action"] = clean_action
                row.payload = json.dumps(payload)
                session.commit()
                return result

            if clean_action in {"stop", "cancel"} and run_id:
                from distr.core.workflow.dispatcher import cancel_run

                result["cancelled"] = bool(cancel_run(int(run_id)))
                row.status = "resolved"
                payload["needs_attention"] = False
                payload["resolved_action"] = clean_action
                row.payload = json.dumps(payload)
                session.commit()
                return result

            if clean_action in {"continue", "resume"} and run_id:
                from distr.core.workflow.dispatcher import continue_waiting_step

                result["continue"] = continue_waiting_step(int(run_id), clean_message)
                row.status = "resolved"
                payload["needs_attention"] = False
                payload["resolved_action"] = clean_action
                row.payload = json.dumps(payload)
                session.commit()
                return result

            if clean_action == "steer" and run_id:
                from distr.core.workflow.service import apply_run_harness_steer

                if not clean_message:
                    return {"success": False, "error": "Steer requires a message"}
                result["steer"] = apply_run_harness_steer(int(run_id), clean_message, source="mission_control")
                row.status = "resolved"
                payload["needs_attention"] = False
                payload["resolved_action"] = clean_action
                row.payload = json.dumps(payload)
                session.commit()
                return result

            return {"success": False, "error": f"Unsupported inbox action: {clean_action}"}


_service: OrchestratorIntakeService | None = None


def get_work_intake_service() -> OrchestratorIntakeService:
    global _service
    if _service is None:
        _service = OrchestratorIntakeService()
    return _service
