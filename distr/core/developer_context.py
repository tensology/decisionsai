"""Shared developer workflow context for orchestration surfaces.

This module is the read-only spine for agentic developer workflows. It gathers
the active project, active ticket board, active tickets, workflow runs, and
skill recommendations into one compact object so chat, initiative, ticketing,
and workflow execution stop rebuilding different versions of "what is going
on?".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def is_pickup_request(user_request: str) -> bool:
    from distr.core.workspace_memory.pickup_handoff import is_pickup_keyword

    return is_pickup_keyword(user_request or "")


def _handoff_request(user_request: str) -> bool:
    from distr.core.workspace_memory.pickup_handoff import is_handoff_keyword

    return is_handoff_keyword(user_request or "")


@dataclass(frozen=True)
class DeveloperProjectContext:
    id: int
    name: str
    description: str = ""
    folder_location: str = ""
    provider: str = ""
    board_id: str = ""
    board_name: str = ""
    kanban_board_id: int | None = None
    startup_instructions: str = ""
    context_items: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class DeveloperBoardContext:
    id: int
    name: str
    source: str = "database"
    external_board_id: str = ""
    external_url: str = ""
    default_project_id: int | None = None
    default_workflow_id: int | None = None
    default_action_id: int | None = None
    send_to_cli: bool = False
    source_lane: str = ""
    done_lane: str = ""
    lanes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class DeveloperTicketContext:
    id: int
    title: str
    lane: str
    priority: str = "medium"
    workflow_status: str = ""
    linked_project_id: int | None = None
    linked_workflow_id: int | None = None
    linked_action_id: int | None = None
    send_to_cli: bool = False
    source_chat_id: int | None = None
    parent_ticket_id: int | None = None
    description_preview: str = ""


@dataclass(frozen=True)
class DeveloperWorkflowContext:
    id: int
    name: str
    status: str
    workflow_type: str = "manual"
    workflow_id: int | None = None
    current_step_id: int | None = None
    current_step_name: str = ""
    board_id: int | None = None
    ticket_id: int | None = None
    parent_run_id: int | None = None
    started_at: str = ""
    modified_date: str = ""
    waiting_kind: str = ""
    elapsed_seconds: int | None = None
    live_agent_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeveloperExecutionContext:
    id: int
    status: str
    backend: str = ""
    project_id: int | None = None
    project_name: str = ""
    origin: str = ""
    instruction_preview: str = ""
    output_preview: str = ""
    error_preview: str = ""
    started_at: str = ""
    updated_at: str = ""
    completed_at: str = ""


@dataclass(frozen=True)
class DeveloperRuntimeContext:
    cwd: str
    current_chat_id: int | None = None
    debug_mode: bool = False
    captured_at: str = ""


@dataclass(frozen=True)
class DeveloperSkillContext:
    name: str
    reason: str


@dataclass(frozen=True)
class DeveloperWorkContext:
    runtime: DeveloperRuntimeContext
    active_project: DeveloperProjectContext | None = None
    active_board: DeveloperBoardContext | None = None
    active_tickets: list[DeveloperTicketContext] = field(default_factory=list)
    active_workflows: list[DeveloperWorkflowContext] = field(default_factory=list)
    active_executions: list[DeveloperExecutionContext] = field(default_factory=list)
    external_agent_context: dict[str, Any] = field(default_factory=dict)
    user_memory_context: str = ""
    board_notes: list[dict[str, Any]] = field(default_factory=list)
    recommended_skills: list[DeveloperSkillContext] = field(default_factory=list)
    workspace: dict[str, Any] = field(default_factory=dict)
    ecosystem: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self, max_chars: int = 4000) -> str:
        """Render compact, stable context for agent prompts."""
        lines: list[str] = ["Developer workflow context:"]
        if self.runtime.captured_at:
            lines.append(f"- now: {self.runtime.captured_at}")
        lines.append(f"- cwd: {self.runtime.cwd}")
        if self.runtime.current_chat_id:
            lines.append(f"- current_chat_id: {self.runtime.current_chat_id}")
        lines.append(f"- debug_mode: {self.runtime.debug_mode}")

        if self.active_project:
            project = self.active_project
            folder = f" ({project.folder_location})" if project.folder_location else ""
            lines.append(f"- active_project: #{project.id} {project.name}{folder}")
            if project.description:
                lines.append(f"  description: {_one_line(project.description, 180)}")

        if self.active_board:
            board = self.active_board
            lanes = ", ".join(
                f"{lane.get('name')}={lane.get('ticket_count', 0)}"
                for lane in board.lanes[:8]
            )
            lines.append(f"- active_board: #{board.id} {board.name} [{board.source}]")
            if lanes:
                lines.append(f"  lanes: {lanes}")
            if board.default_project_id or board.default_workflow_id or board.send_to_cli:
                lines.append(
                    "  defaults: "
                    f"project={board.default_project_id or 'none'}, "
                    f"workflow={board.default_workflow_id or 'none'}, "
                    f"send_to_cli={board.send_to_cli}"
                )

        if self.active_tickets:
            lines.append("- active_tickets:")
            for ticket in self.active_tickets[:8]:
                status = f", workflow={ticket.workflow_status}" if ticket.workflow_status else ""
                lines.append(
                    f"  - #{ticket.id} {ticket.title} "
                    f"(lane={ticket.lane}, priority={ticket.priority}{status})"
                )

        if self.active_workflows:
            lines.append("- active_workflows:")
            for workflow in self.active_workflows[:8]:
                step = f", step={workflow.current_step_name or workflow.current_step_id}" if workflow.current_step_id else ""
                ticket = f", ticket={workflow.ticket_id}" if workflow.ticket_id else ""
                lines.append(
                    f"  - run/workflow #{workflow.id} {workflow.name} "
                    f"({workflow.status}{step}{ticket})"
                )
                if workflow.waiting_kind:
                    lines.append(f"    waiting_kind={workflow.waiting_kind}")
                if workflow.elapsed_seconds is not None:
                    lines.append(f"    elapsed_s={workflow.elapsed_seconds}")
                live = workflow.live_agent_context or {}
                live_summary = _live_agent_context_line(live)
                if live_summary:
                    lines.append(f"    live_agent_context: {live_summary}")

        if self.active_executions:
            lines.append("- active_project_executions:")
            for execution in self.active_executions[:6]:
                detail = execution.instruction_preview or execution.output_preview or execution.error_preview
                suffix = f": {_one_line(detail, 180)}" if detail else ""
                lines.append(
                    f"  - session #{execution.id} {execution.backend} "
                    f"({execution.status}, origin={execution.origin or 'unknown'}, project={execution.project_name or execution.project_id}){suffix}"
                )

        if self.external_agent_context:
            try:
                from distr.core.external_agent_context import format_external_agent_context_for_prompt

                external_text = format_external_agent_context_for_prompt(self.external_agent_context, max_chars=1400)
                if external_text:
                    lines.append(external_text)
            except Exception:
                logger.warning("Could not render external agent context", exc_info=True)

        if self.user_memory_context:
            lines.append(self.user_memory_context)

        if self.workspace:
            preview = (self.workspace.get("handoff_preview") or "").strip()
            projection = (self.workspace.get("projection_path") or "").strip()
            if projection:
                lines.append(f"- workspace_projection: {projection}")
            if preview:
                lines.append(f"- workspace_handoff: {_one_line(preview, 220)}")
            pickup_brief = (self.workspace.get("pickup_brief") or "").strip()
            if pickup_brief:
                lines.append(pickup_brief[:1200])

        if self.board_notes:
            from distr.core.kanban.board_notes import format_board_notes_for_prompt

            notes_block = format_board_notes_for_prompt(self.board_notes, max_notes=10, max_content_chars=320)
            if notes_block:
                lines.append(notes_block)

        if self.recommended_skills:
            lines.append("- recommended_skills:")
            for skill in self.recommended_skills:
                lines.append(f"  - {skill.name}: {_one_line(skill.reason, 160)}")

        if self.warnings:
            lines.append("- context_warnings: " + "; ".join(self.warnings[:8]))

        eco = self.ecosystem or {}
        name_index = eco.get("name_index") or {}
        if name_index:
            wf_names = name_index.get("workflows") or {}
            board_names = name_index.get("boards") or {}
            if wf_names:
                bits = [f"{n}=#{i}" for n, i in list(wf_names.items())[:12]]
                lines.append("- workflow_name_index: " + ", ".join(bits))
            if board_names:
                bits = [f"{n}=#{i}" for n, i in list(board_names.items())[:12]]
                lines.append("- board_name_index: " + ", ".join(bits))
        for note in (eco.get("board_health") or [])[:6]:
            if isinstance(note, str) and note.strip():
                lines.append(f"  - board: {note.strip()}")
        for note in (eco.get("unscoped_tickets") or [])[:6]:
            if isinstance(note, str) and note.strip():
                lines.append(f"  - unscoped: {note.strip()}")
        for note in (eco.get("projects_missing_folder") or [])[:4]:
            if isinstance(note, str) and note.strip():
                lines.append(f"  - project: {note.strip()}")

        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "…"


class DeveloperContextAssembler:
    """Collect current developer workflow state for agent surfaces."""

    def build(
        self,
        settings: dict[str, Any] | None = None,
        user_request: str = "",
        chat_id: int | None = None,
    ) -> DeveloperWorkContext:
        settings = settings or {}
        warnings: list[str] = []
        current_chat_id = _coerce_int(
            chat_id
            or settings.get("agent_current_chat_id")
            or settings.get("last_chat_id")
        )
        runtime = DeveloperRuntimeContext(
            cwd=os.getcwd(),
            current_chat_id=current_chat_id,
            debug_mode=_debug_mode(),
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

        active_project = _safe_call("active project", warnings, self._fetch_active_project)
        active_board = _safe_call("active board", warnings, self._fetch_active_board, active_project)
        active_tickets = _safe_call("active tickets", warnings, self._fetch_active_tickets, active_board, current_chat_id) or []
        active_workflows = _safe_call("active workflows", warnings, self._fetch_active_workflows, active_board, active_tickets) or []
        active_executions = _safe_call("active project executions", warnings, self._fetch_active_executions, active_project) or []
        external_agent_context = _safe_call("external agent context", warnings, self._fetch_external_agent_context) or {}
        user_memory_context = _safe_call(
            "Orchestrator user memory",
            warnings,
            self._fetch_user_memory_context,
            active_project,
            active_board,
            active_workflows,
        ) or ""
        board_notes = _safe_call("ticket board notes", warnings, self._fetch_board_notes, active_board) or []
        recommended_skills = _safe_call("skill recommendations", warnings, self._recommend_skills, user_request) or []
        workspace = _safe_call(
            "workspace memory",
            warnings,
            self._fetch_workspace,
            active_project,
            active_board,
            active_tickets,
            active_workflows,
            user_request,
        ) or {}
        ecosystem = _safe_call("ecosystem snapshot", warnings, self._fetch_ecosystem_snapshot) or {}

        if is_pickup_request(user_request):
            pickup_brief = _safe_call("pickup brief", warnings, self._build_pickup_brief, workspace) or ""
            if pickup_brief:
                workspace = {**workspace, "pickup_brief": pickup_brief}

        if _handoff_request(user_request):
            handoff_result = _safe_call("handoff", warnings, self._perform_handoff, workspace, user_request) or {}
            if handoff_result:
                workspace = {**workspace, "handoff_result": handoff_result}

        return DeveloperWorkContext(
            runtime=runtime,
            active_project=active_project,
            active_board=active_board,
            active_tickets=active_tickets,
            active_workflows=active_workflows,
            active_executions=active_executions,
            external_agent_context=external_agent_context,
            user_memory_context=user_memory_context,
            board_notes=board_notes,
            recommended_skills=recommended_skills,
            workspace=workspace,
            ecosystem=ecosystem,
            warnings=warnings,
        )

    def _fetch_active_project(self) -> DeveloperProjectContext | None:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as session:
            project = (
                session.query(Project)
                .filter(Project.in_use == True)  # noqa: E712 - SQLAlchemy comparison
                .order_by(Project.modified_date.desc())
                .first()
            )
            if not project:
                return None

            context_items = [
                {
                    "id": item.id,
                    "title": item.title,
                    "content_preview": _one_line(item.content or "", 500),
                }
                for item in list(project.context_items or [])[:8]
            ]
            files = [
                {
                    "id": file.id,
                    "filename": file.filename,
                    "file_path": file.file_path,
                    "description": file.description or "",
                }
                for file in list(project.files or [])[:12]
            ]
            return DeveloperProjectContext(
                id=project.id,
                name=project.name or "",
                description=project.description or "",
                folder_location=project.folder_location or "",
                provider=project.provider or "",
                board_id=project.board_id or "",
                board_name=project.board_name or "",
                kanban_board_id=project.kanban_board_id,
                startup_instructions=project.startup_instructions or "",
                context_items=context_items,
                files=files,
            )

    def _fetch_active_board(
        self,
        active_project: DeveloperProjectContext | None = None,
    ) -> DeveloperBoardContext | None:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanBoard, KanbanTicket

        with get_session() as session:
            board = (
                session.query(KanbanBoard)
                .filter(KanbanBoard.in_use == True, KanbanBoard.archived == False)  # noqa: E712
                .order_by(KanbanBoard.modified_date.desc())
                .first()
            )
            if not board and active_project and active_project.kanban_board_id:
                board = (
                    session.query(KanbanBoard)
                    .filter(
                        KanbanBoard.id == active_project.kanban_board_id,
                        KanbanBoard.archived == False,  # noqa: E712
                    )
                    .first()
                )
            if not board:
                board = (
                    session.query(KanbanBoard)
                    .filter(KanbanBoard.archived == False)  # noqa: E712
                    .order_by(KanbanBoard.position.asc(), KanbanBoard.id.asc())
                    .first()
                )
            if not board:
                return None

            settings = {}
            try:
                from distr.core.utils import load_settings_from_db

                settings = load_settings_from_db()
            except Exception:
                logger.debug("DeveloperContextAssembler: could not load kanban lane settings", exc_info=True)

            source_lane = (
                getattr(board, "agent_source_lane", None)
                or settings.get("kanban_agent_source_lane")
                or ""
            )
            done_lane = (
                getattr(board, "agent_done_lane", None)
                or settings.get("kanban_agent_done_lane")
                or ""
            )

            lanes: list[dict[str, Any]] = []
            for lane in board.lanes or []:
                ticket_count = (
                    session.query(KanbanTicket)
                    .filter(KanbanTicket.lane_id == lane.id)
                    .count()
                )
                lanes.append({
                    "id": lane.id,
                    "name": lane.name,
                    "position": lane.position,
                    "ticket_count": ticket_count,
                })

            return DeveloperBoardContext(
                id=board.id,
                name=board.name or "",
                source=board.source or "database",
                external_board_id=board.external_board_id or "",
                external_url=board.external_url or "",
                default_project_id=board.default_project_id,
                default_workflow_id=board.default_workflow_id,
                default_action_id=board.default_action_id,
                send_to_cli=bool(board.send_to_cli),
                source_lane=source_lane,
                done_lane=done_lane,
                lanes=lanes,
            )

    def _fetch_active_tickets(
        self,
        active_board: DeveloperBoardContext | None,
        chat_id: int | None = None,
    ) -> list[DeveloperTicketContext]:
        if not active_board:
            return []

        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanLane, KanbanTicket

        terminal = {"completed", "cancelled"}
        with get_session() as session:
            rows = (
                session.query(KanbanTicket, KanbanLane)
                .join(KanbanLane, KanbanTicket.lane_id == KanbanLane.id)
                .filter(KanbanLane.board_id == active_board.id)
                .order_by(KanbanLane.position.asc(), KanbanTicket.position.asc(), KanbanTicket.modified_date.desc())
                .limit(50)
                .all()
            )

            selected: list[tuple[Any, Any]] = []
            for ticket, lane in rows:
                workflow_status = (ticket.workflow_status or "").lower()
                lane_name = (lane.name or "").strip().lower()
                source_lane = (active_board.source_lane or "").strip().lower()
                is_relevant = (
                    workflow_status not in terminal
                    and (
                        workflow_status in {"running", "waiting", "failed"}
                        or (source_lane and lane_name == source_lane)
                        or (not source_lane and _looks_active_lane(lane_name))
                        or (chat_id and ticket.source_chat_id == chat_id)
                    )
                )
                if is_relevant:
                    selected.append((ticket, lane))
                if len(selected) >= 10:
                    break

            return [
                DeveloperTicketContext(
                    id=ticket.id,
                    title=ticket.title or "",
                    lane=lane.name or "",
                    priority=ticket.priority or "medium",
                    workflow_status=ticket.workflow_status or "",
                    linked_project_id=ticket.linked_project_id,
                    linked_workflow_id=ticket.linked_workflow_id,
                    linked_action_id=ticket.linked_action_id,
                    send_to_cli=bool(ticket.send_to_cli),
                    source_chat_id=ticket.source_chat_id,
                    parent_ticket_id=ticket.parent_ticket_id,
                    description_preview=_one_line(ticket.description or "", 300),
                )
                for ticket, lane in selected
            ]

    def _fetch_active_workflows(
        self,
        active_board: DeveloperBoardContext | None,
        active_tickets: list[DeveloperTicketContext],
    ) -> list[DeveloperWorkflowContext]:
        from sqlalchemy import or_

        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep

        ticket_ids = [ticket.id for ticket in active_tickets]
        with get_session() as session:
            filters = [AutoWorkflowRun.status.in_(["running", "waiting", "failed"])]
            scope_filters = []
            if active_board:
                scope_filters.append(AutoWorkflowRun.board_id == active_board.id)
            if ticket_ids:
                scope_filters.append(AutoWorkflowRun.ticket_id.in_(ticket_ids))
            if scope_filters:
                filters.append(or_(*scope_filters))

            query = (
                session.query(AutoWorkflowRun, AutoWorkflow)
                .join(AutoWorkflow, AutoWorkflowRun.workflow_id == AutoWorkflow.id)
                .filter(*filters)
            )

            rows = query.order_by(AutoWorkflowRun.started_at.desc()).limit(10).all()
            step_ids = [run.current_step_id for run, _workflow in rows if run.current_step_id]
            step_names: dict[int, str] = {}
            if step_ids:
                for step in session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id.in_(step_ids)).all():
                    step_names[step.id] = step.name or ""

            return [
                DeveloperWorkflowContext(
                    id=run.id,
                    name=workflow.name or "",
                    status=run.status or workflow.status or "",
                    workflow_type=workflow.workflow_type or "manual",
                    workflow_id=workflow.id,
                    current_step_id=run.current_step_id,
                    current_step_name=step_names.get(run.current_step_id or 0, ""),
                    board_id=run.board_id,
                    ticket_id=run.ticket_id,
                    parent_run_id=run.parent_run_id,
                    started_at=run.started_at.isoformat() if run.started_at else "",
                    modified_date=workflow.modified_date.isoformat() if workflow.modified_date else "",
                    waiting_kind=_waiting_kind_from_run(run),
                    elapsed_seconds=_elapsed_seconds(run.started_at),
                    live_agent_context=_live_context_from_run_data(run.run_data),
                )
                for run, workflow in rows
            ]

    def _fetch_active_executions(
        self,
        active_project: DeveloperProjectContext | None = None,
    ) -> list[DeveloperExecutionContext]:
        from distr.core.db import get_session
        from distr.core.db.kanban import ProjectExecutionSession
        from distr.core.db.projects import Project

        with get_session() as session:
            query = (
                session.query(ProjectExecutionSession, Project)
                .outerjoin(Project, Project.id == ProjectExecutionSession.project_id)
            )
            if active_project:
                query = query.filter(ProjectExecutionSession.project_id == int(active_project.id))
            rows = (
                query.order_by(ProjectExecutionSession.updated_at.desc(), ProjectExecutionSession.started_at.desc())
                .limit(8)
                .all()
            )
            contexts: list[DeveloperExecutionContext] = []
            for row, project in rows:
                input_packet = _json_obj(row.input_packet)
                output_packet = _json_obj(row.output_packet)
                instruction = input_packet.get("instruction", "") if isinstance(input_packet, dict) else ""
                output = output_packet.get("output", "") if isinstance(output_packet, dict) else ""
                contexts.append(
                    DeveloperExecutionContext(
                        id=row.id,
                        status=row.status or "",
                        backend=row.route_backend or "",
                        project_id=row.project_id,
                        project_name=getattr(project, "name", "") or "",
                        origin=row.origin or "",
                        instruction_preview=_one_line(instruction, 220),
                        output_preview=_one_line(output, 220),
                        error_preview=_one_line(row.error or "", 220),
                        started_at=row.started_at.isoformat() if row.started_at else "",
                        updated_at=row.updated_at.isoformat() if row.updated_at else "",
                        completed_at=row.completed_at.isoformat() if row.completed_at else "",
                    )
                )
            return contexts

    def _fetch_external_agent_context(self) -> dict[str, Any]:
        from distr.core.external_agent_context import build_external_agent_context

        return build_external_agent_context(limit=8)

    def _fetch_user_memory_context(
        self,
        active_project: DeveloperProjectContext | None = None,
        active_board: DeveloperBoardContext | None = None,
        active_workflows: list[DeveloperWorkflowContext] | None = None,
    ) -> str:
        from distr.core.orchestrator_memory import build_memory_context

        workflow_id = None
        run_id = None
        if active_workflows:
            wf = active_workflows[0]
            workflow_id = wf.workflow_id
            run_id = wf.id
        return build_memory_context(
            limit=30,
            project_id=active_project.id if active_project else None,
            board_id=active_board.id if active_board else None,
            workflow_id=workflow_id,
            run_id=run_id,
        )

    def _fetch_board_notes(self, active_board: DeveloperBoardContext | None = None) -> list[dict[str, Any]]:
        from distr.core.kanban.board_notes import load_board_notes

        board_id = active_board.id if active_board else None
        return load_board_notes(board_id=board_id)

    def _fetch_workspace(
        self,
        active_project: DeveloperProjectContext | None,
        active_board: DeveloperBoardContext | None,
        active_tickets: list[DeveloperTicketContext],
        active_workflows: list[DeveloperWorkflowContext],
        user_request: str,
    ) -> dict[str, Any]:
        from distr.core.workspace_memory.reader import load_workspace_context

        project_id = active_project.id if active_project else None
        board_id = active_board.id if active_board else None
        workflow_id = None
        run_id = None
        ticket_id = None
        if active_workflows:
            wf = active_workflows[0]
            workflow_id = wf.workflow_id
            run_id = wf.id
            ticket_id = wf.ticket_id
        elif active_tickets:
            ticket_id = active_tickets[0].id
        folder = active_project.folder_location if active_project else ""
        ctx = load_workspace_context(
            project_id=project_id,
            board_id=board_id,
            workflow_id=workflow_id,
            run_id=run_id,
            ticket_id=ticket_id,
            folder_location=folder,
            ensure=True,
            include_pickup_brief=is_pickup_request(user_request),
        )
        return {
            "companion_paths": ctx.companion_paths,
            "projection_path": ctx.projection_path,
            "router_chain": ctx.router_chain,
            "handoff_preview": ctx.handoff_preview,
            "pickup_brief": ctx.pickup_brief,
            "org_router": ctx.org_router,
            "references_index": ctx.references_index,
        }

    def _build_pickup_brief(self, workspace: dict[str, Any]) -> str:
        from distr.core.workspace_memory.pickup_handoff import build_pickup_brief, load_decisions_json

        paths = workspace.get("companion_paths") or {}
        for key, entity_type in (
            ("run", "runs"),
            ("project", "projects"),
            ("board", "boards"),
            ("workflow", "workflows"),
        ):
            root = paths.get(key)
            if not root:
                continue
            entity_id = root.rstrip("/").split("/")[-1]
            decisions = load_decisions_json(entity_type, entity_id)
            return build_pickup_brief(
                entity_type=entity_type,
                entity_id=entity_id,
                decisions=decisions,
            )
        return ""

    def _perform_handoff(self, workspace: dict[str, Any], user_request: str) -> dict[str, str]:
        from distr.core.workspace_memory.pickup_handoff import load_decisions_json, perform_handoff

        paths = workspace.get("companion_paths") or {}
        summary = (user_request or "").strip() or "Session handoff requested."
        for key, entity_type in (
            ("run", "runs"),
            ("project", "projects"),
            ("board", "boards"),
            ("workflow", "workflows"),
        ):
            root = paths.get(key)
            if not root:
                continue
            entity_id = root.rstrip("/").split("/")[-1]
            decisions = load_decisions_json(entity_type, entity_id)
            return perform_handoff(
                entity_type,
                entity_id,
                summary=summary,
                source="chat_handoff",
                extra=decisions,
            )
        return {}

    def _recommend_skills(self, user_request: str) -> list[DeveloperSkillContext]:
        if not (user_request or "").strip():
            return []
        from distr.core.agent.ticket_intent import recommend_skills_for_ticket

        return [
            DeveloperSkillContext(name=rec.name, reason=rec.reason)
            for rec in recommend_skills_for_ticket(user_request)
        ]

    def _fetch_ecosystem_snapshot(self) -> dict[str, Any]:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
        from distr.core.db.projects import Project
        from distr.core.db.workflow import AutoWorkflow

        board_health: list[str] = []
        unscoped: list[str] = []
        projects_missing_folder: list[str] = []
        workflow_names: dict[str, int] = {}
        board_names: dict[str, int] = {}

        with get_session() as session:
            boards = (
                session.query(KanbanBoard)
                .filter(KanbanBoard.archived == False)  # noqa: E712
                .order_by(KanbanBoard.position.asc(), KanbanBoard.id.asc())
                .limit(30)
                .all()
            )
            for board in boards:
                name = (board.name or f"Board {board.id}").strip()
                board_names[name] = int(board.id)
                lanes = (
                    session.query(KanbanLane)
                    .filter(KanbanLane.board_id == board.id)
                    .order_by(KanbanLane.position.asc())
                    .all()
                )
                lane_counts = []
                backlog_count = current_count = 0
                for lane in lanes:
                    count = session.query(KanbanTicket).filter(KanbanTicket.lane_id == lane.id).count()
                    lane_counts.append(f"{lane.name}={count}")
                    lname = (lane.name or "").strip().lower()
                    if lname in {"backlog", "todo", "to do"}:
                        backlog_count += count
                    if lname in {"current", "active", "in progress", "in-progress", "doing"}:
                        current_count += count
                if backlog_count and not current_count:
                    board_health.append(
                        f"#{board.id} {name}: backlog has {backlog_count} ticket(s), current lane empty"
                    )
                if not board.default_project_id:
                    board_health.append(f"#{board.id} {name}: no default_project_id — harness dispatch may fail")
                if lane_counts:
                    board_health.append(f"#{board.id} {name} lanes: {', '.join(lane_counts[:8])}")

                rows = (
                    session.query(KanbanTicket, KanbanLane)
                    .join(KanbanLane, KanbanTicket.lane_id == KanbanLane.id)
                    .filter(KanbanLane.board_id == board.id)
                    .limit(80)
                    .all()
                )
                for ticket, lane in rows:
                    if not ticket.linked_project_id and not board.default_project_id:
                        unscoped.append(
                            f"ticket #{ticket.id} on board #{board.id} ({lane.name}): no linked project"
                        )

            projects = session.query(Project).order_by(Project.modified_date.desc()).limit(40).all()
            for project in projects:
                if not (project.folder_location or "").strip():
                    projects_missing_folder.append(f"#{project.id} {project.name or 'Project'}: no folder_location")

            workflows = (
                session.query(AutoWorkflow)
                .order_by(AutoWorkflow.modified_date.desc())
                .limit(40)
                .all()
            )
            for wf in workflows:
                wname = (wf.name or f"Workflow {wf.id}").strip()
                workflow_names[wname] = int(wf.id)

        return {
            "name_index": {
                "workflows": workflow_names,
                "boards": board_names,
            },
            "board_health": board_health[:20],
            "unscoped_tickets": unscoped[:20],
            "projects_missing_folder": projects_missing_folder[:15],
        }


def build_developer_context(
    settings: dict[str, Any] | None = None,
    user_request: str = "",
    chat_id: int | None = None,
) -> DeveloperWorkContext:
    return DeveloperContextAssembler().build(settings=settings, user_request=user_request, chat_id=chat_id)


def format_developer_context_dict_for_prompt(
    context: dict[str, Any] | None,
    max_chars: int = 2200,
) -> str:
    """Render a stored DeveloperWorkContext dict without requiring ORM access."""
    if not isinstance(context, dict):
        return ""

    runtime = context.get("runtime") or {}
    project = context.get("active_project") or {}
    board = context.get("active_board") or {}
    tickets = context.get("active_tickets") or []
    workflows = context.get("active_workflows") or []
    executions = context.get("active_executions") or []
    external_agent_context = context.get("external_agent_context") or {}
    user_memory_context = context.get("user_memory_context") or ""
    board_notes = context.get("board_notes") or []
    skills = context.get("recommended_skills") or []

    lines: list[str] = ["Developer workflow context:"]
    if runtime.get("cwd"):
        lines.append(f"- cwd: {runtime.get('cwd')}")
    if runtime.get("current_chat_id"):
        lines.append(f"- current_chat_id: {runtime.get('current_chat_id')}")
    if "debug_mode" in runtime:
        lines.append(f"- debug_mode: {bool(runtime.get('debug_mode'))}")

    if project:
        folder = f" ({project.get('folder_location')})" if project.get("folder_location") else ""
        lines.append(f"- active_project: #{project.get('id')} {project.get('name', '')}{folder}")

    if board:
        lines.append(f"- active_board: #{board.get('id')} {board.get('name', '')} [{board.get('source') or 'database'}]")
        lane_bits = [
            f"{lane.get('name')}={lane.get('ticket_count', 0)}"
            for lane in (board.get("lanes") or [])[:8]
            if isinstance(lane, dict)
        ]
        if lane_bits:
            lines.append(f"  lanes: {', '.join(lane_bits)}")
        if board.get("default_project_id") or board.get("default_workflow_id") or board.get("send_to_cli"):
            lines.append(
                "  defaults: "
                f"project={board.get('default_project_id') or 'none'}, "
                f"workflow={board.get('default_workflow_id') or 'none'}, "
                f"send_to_cli={bool(board.get('send_to_cli'))}"
            )

    if tickets:
        lines.append("- active_tickets:")
        for ticket in [t for t in tickets if isinstance(t, dict)][:8]:
            status = f", workflow={ticket.get('workflow_status')}" if ticket.get("workflow_status") else ""
            lines.append(
                f"  - #{ticket.get('id')} {ticket.get('title', '')} "
                f"(lane={ticket.get('lane', '')}, priority={ticket.get('priority', 'medium')}{status})"
            )

    if workflows:
        lines.append("- active_workflows:")
        for workflow in [w for w in workflows if isinstance(w, dict)][:8]:
            step = f", step={workflow.get('current_step_name') or workflow.get('current_step_id')}" if workflow.get("current_step_id") else ""
            ticket = f", ticket={workflow.get('ticket_id')}" if workflow.get("ticket_id") else ""
            lines.append(
                f"  - run #{workflow.get('id')} {workflow.get('name', '')} "
                f"({workflow.get('status', '')}{step}{ticket})"
            )
            live_summary = _live_agent_context_line(workflow.get("live_agent_context") or {})
            if live_summary:
                lines.append(f"    live_agent_context: {live_summary}")

    if executions:
        lines.append("- active_project_executions:")
        for execution in [e for e in executions if isinstance(e, dict)][:6]:
            detail = execution.get("instruction_preview") or execution.get("output_preview") or execution.get("error_preview") or ""
            suffix = f": {_one_line(detail, 180)}" if detail else ""
            lines.append(
                f"  - session #{execution.get('id')} {execution.get('backend', '')} "
                f"({execution.get('status', '')}, origin={execution.get('origin') or 'unknown'}, "
                f"project={execution.get('project_name') or execution.get('project_id')}){suffix}"
            )

    if external_agent_context:
        try:
            from distr.core.external_agent_context import format_external_agent_context_for_prompt

            external_text = format_external_agent_context_for_prompt(external_agent_context, max_chars=1400)
            if external_text:
                lines.append(external_text)
        except Exception:
            pass

    if user_memory_context:
        lines.append(str(user_memory_context))

    workspace = context.get("workspace") or {}
    if workspace:
        preview = (workspace.get("handoff_preview") or "").strip()
        projection = (workspace.get("projection_path") or "").strip()
        if projection:
            lines.append(f"- workspace_projection: {projection}")
        if preview:
            lines.append(f"- workspace_handoff: {_one_line(preview, 220)}")
        pickup_brief = (workspace.get("pickup_brief") or "").strip()
        if pickup_brief:
            lines.append(pickup_brief[:1200])

    if board_notes:
        from distr.core.kanban.board_notes import format_board_notes_for_prompt

        notes_block = format_board_notes_for_prompt(board_notes, max_notes=10, max_content_chars=320)
        if notes_block:
            lines.append(notes_block)

    if skills:
        lines.append("- recommended_skills:")
        for skill in [s for s in skills if isinstance(s, dict)][:5]:
            lines.append(f"  - {skill.get('name', '')}: {_one_line(skill.get('reason', ''), 160)}")

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _waiting_kind_from_run(run: Any) -> str:
    try:
        data = json.loads(run.run_data or "{}") or {}
    except Exception:
        return ""
    return str(data.get("waiting_kind") or "").strip()


def _elapsed_seconds(started_at: Any) -> int | None:
    if not started_at:
        return None
    try:
        if getattr(started_at, "tzinfo", None) is None:
            started = started_at.replace(tzinfo=timezone.utc)
        else:
            started = started_at
        delta = datetime.now(timezone.utc) - started.astimezone(timezone.utc)
        return max(0, int(delta.total_seconds()))
    except Exception:
        return None


def _safe_call(label: str, warnings: list[str], func, *args):
    try:
        return func(*args)
    except Exception:
        logger.warning("DeveloperContextAssembler: failed to fetch %s", label, exc_info=True)
        warnings.append(f"{label} unavailable")
        return None


def _live_context_from_run_data(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    live = data.get("live_agent_context")
    if isinstance(live, dict) and live:
        return live
    last = data.get("last_codex_bridge_state")
    if isinstance(last, dict) and last:
        return last
    events = data.get("codex_bridge_events")
    if isinstance(events, list) and events:
        tail = [event for event in events if isinstance(event, dict)][-10:]
        if tail:
            return {"recent_events": tail, **tail[-1]}
    return {}


def _json_obj(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _live_agent_context_line(live: dict[str, Any] | None, max_chars: int = 360) -> str:
    if not isinstance(live, dict) or not live:
        return ""
    parts: list[str] = []
    event_type = live.get("last_event_type") or live.get("event_type")
    status = live.get("last_status") or live.get("status")
    if event_type:
        parts.append(f"event={event_type}")
    if status:
        parts.append(f"status={status}")
    steer = live.get("latest_user_steer")
    if steer:
        parts.append(f"user_steer={_one_line(steer, 140)}")
    terminal = live.get("latest_terminal_summary")
    if terminal:
        parts.append(f"summary={_one_line(terminal, 140)}")
    message = live.get("last_message") or live.get("message")
    if message and not steer and not terminal:
        parts.append(f"message={_one_line(message, 160)}")
    execution_session_id = live.get("execution_session_id")
    if execution_session_id:
        parts.append(f"execution_session={execution_session_id}")
    if not parts:
        recent = live.get("recent_events")
        if isinstance(recent, list) and recent:
            last = next((event for event in reversed(recent) if isinstance(event, dict)), None)
            if last:
                return _live_agent_context_line(last, max_chars=max_chars)
    text = "; ".join(parts)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _debug_mode() -> bool:
    try:
        from distr.core.agent.ticket_intent import is_debug_enabled

        return bool(is_debug_enabled())
    except Exception:
        return False


def _one_line(value: str, max_chars: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _looks_active_lane(name: str) -> bool:
    normalized = (name or "").strip().lower()
    return normalized in {
        "active",
        "current",
        "doing",
        "in progress",
        "in-progress",
        "next",
        "ready",
        "selected",
        "working",
    }
