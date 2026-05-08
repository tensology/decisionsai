"""
Ticket Board Agent Check-In Engine.

Processes tickets from a board's source lane by running the board's default
workflow against each ticket sequentially, then moving completed tickets to
the done lane.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
import threading

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflowRun
from distr.core.llm_override import LLMOverride, set_llm_override, clear_llm_override
from distr.core.workflow.service import start_workflow_run, cancel_run

logger = logging.getLogger(__name__)

# Terminal statuses for a workflow run
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

# Poll interval (seconds) when waiting for a run to finish
_POLL_INTERVAL = 2.0


def _risk_profile_for_ticket(title: str, description: str) -> Dict[str, Any]:
    """Cheap Stage-0 risk profile used to route audit depth."""
    haystack = f"{title}\n{description}".lower()
    high_risk_terms = [
        "auth", "authentication", "payment", "secret", "token", "credential",
        "production", "prod", "migration", "database migration", "infra",
    ]
    matched = [term for term in high_risk_terms if term in haystack]
    level = "high" if matched else "low"
    required_audits = ["A", "B", "C"] if level == "high" else ["A", "B"]
    require_cli_validation = level == "high"
    return {
        "risk_level": level,
        "risk_flags": matched,
        "required_audit_gates": required_audits,
        "require_cli_validation": require_cli_validation,
    }


@dataclass
class AgentStatus:
    """Tracks the current state of an Agent Check-In."""
    state: str = "idle"  # "idle" or "running"
    current_ticket_id: Optional[int] = None
    current_ticket_title: str = ""
    total_tickets: int = 0
    processed_count: int = 0
    current_run_id: Optional[int] = None
    current_phase: Optional[str] = None


# Module-level registry of active agents keyed by board_id
_active_agents: Dict[int, "KanbanAgentCheckIn"] = {}
_active_agents_lock = threading.Lock()


def load_settings_from_db():
    """Lazy settings loader for runtime and test patchability."""
    from distr.core.settings import load_settings_from_db as _loader
    return _loader()


def _emit_board_update(board_id: int, event_type: str, payload: Optional[dict] = None) -> None:
    """Push realtime board update notifications to WebUI clients."""
    try:
        from distr.gui.web.kanban_events import increment_kanban_updated
        increment_kanban_updated(board_id=board_id, event_type=event_type, payload=payload or {})
    except Exception:
        logger.debug("Could not emit kanban board update", exc_info=True)


def _resolve_lane(board_id: int, lane_name: str) -> Optional[KanbanLane]:
    """Find a lane by exact name match on a board. Returns None if not found."""
    if not lane_name:
        return None
    with get_session() as db:
        lane = (
            db.query(KanbanLane)
            .filter(KanbanLane.board_id == board_id, KanbanLane.name == lane_name)
            .first()
        )
        if lane:
            # Detach from session by capturing attributes
            lane_id = lane.id
            lane_board_id = lane.board_id
            lane_name_val = lane.name
            lane_position = lane.position
    if not lane:
        return None
    # Return a detached-friendly object by re-querying or building a simple namespace
    # For simplicity, return the lane object from a fresh query
    # Actually, let's just return a simple object with the needed attrs
    return _LaneInfo(id=lane_id, board_id=lane_board_id, name=lane_name_val, position=lane_position)


@dataclass
class _LaneInfo:
    """Lightweight detached lane info."""
    id: int
    board_id: int
    name: str
    position: int


@dataclass
class _BoardInfo:
    """Lightweight detached board info for use outside session scope."""
    id: int
    name: str
    agent_enabled: bool
    agent_source_lane: str
    agent_done_lane: str
    default_workflow_id: Optional[int]
    default_project_id: Optional[int] = None
    agent_orchestrator_provider: str = ""
    agent_orchestrator_model: str = ""
    agent_coder_provider: str = ""
    agent_coder_model: str = ""
    agent_sub_provider: str = ""
    agent_sub_model: str = ""


class KanbanAgentCheckIn:
    """Processes tickets from a board's source lane using the board's default workflow."""

    def __init__(self, board_id: int):
        self.board_id = board_id
        self._cancelled = False
        self._current_run_id: Optional[int] = None
        self._status = AgentStatus(state="idle")

    @property
    def status(self) -> AgentStatus:
        return self._status

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def _load_board(self) -> Optional[_BoardInfo]:
        """Fetch the board and validate required agent fields.

        Global ``kanban_agent_enabled`` must be true (master switch). Lane strings and
        Ticket Board LLM rows come from settings when those keys are present; otherwise
        the board row supplies defaults. Orchestrator/coder rows fall back to chat
        conversational/coding LLM settings when kanban-specific strings are empty.

        Returns a ``_BoardInfo`` or ``None`` if validation fails.
        """
        settings = load_settings_from_db()

        if not settings.get("kanban_agent_enabled", False):
            logger.info("Agent check-in: globally disabled (kanban_agent_enabled=False)")
            return None

        with get_session() as db:
            board = db.query(KanbanBoard).filter(KanbanBoard.id == self.board_id).first()
            if not board:
                logger.error("Agent check-in: board %s not found", self.board_id)
                return None

            if board.agent_enabled is False:
                logger.info("Agent check-in: board %s has agent_enabled=False", self.board_id)
                return None

            if "kanban_agent_source_lane" in settings:
                agent_source_lane = settings.get("kanban_agent_source_lane") or ""
            else:
                agent_source_lane = board.agent_source_lane or ""

            if "kanban_agent_done_lane" in settings:
                agent_done_lane = settings.get("kanban_agent_done_lane") or ""
            else:
                agent_done_lane = board.agent_done_lane or ""

            default_workflow_id = board.default_workflow_id
            default_project_id = board.default_project_id
            board_id = board.id
            board_name = board.name

        if not agent_source_lane:
            logger.error("Agent check-in: no source lane configured for board %s", self.board_id)
            return None

        if not agent_done_lane:
            logger.error("Agent check-in: no done lane configured for board %s", self.board_id)
            return None

        # Workflow is the only automation path.
        if not default_workflow_id:
            logger.error("Agent check-in: board %s has no default workflow configured", self.board_id)
            return None

        orch_p = settings.get("kanban_agent_orchestrator_provider", "") or settings.get(
            "conversational_llm_provider", ""
        )
        orch_m = settings.get("kanban_agent_orchestrator_model", "") or settings.get(
            "conversational_llm_model", ""
        )
        coder_p = settings.get("kanban_agent_coder_provider", "") or settings.get("coding_llm_provider", "")
        coder_m = settings.get("kanban_agent_coder_model", "") or settings.get("coding_llm_model", "")
        sub_p = settings.get("kanban_agent_sub_provider", "") or ""
        sub_m = settings.get("kanban_agent_sub_model", "") or ""

        info = _BoardInfo(
            id=board_id,
            name=board_name,
            agent_enabled=True,
            agent_source_lane=agent_source_lane,
            agent_done_lane=agent_done_lane,
            default_workflow_id=default_workflow_id,
            default_project_id=default_project_id,
            agent_orchestrator_provider=orch_p,
            agent_orchestrator_model=orch_m,
            agent_coder_provider=coder_p,
            agent_coder_model=coder_m,
            agent_sub_provider=sub_p,
            agent_sub_model=sub_m,
        )
        return info

    def _collect_tickets(self, board) -> List[dict]:
        """Get tickets from the source lane ordered by position ascending.

        Returns a list of dicts with ticket id, title, lane_id, position.
        """
        source_lane = _resolve_lane(board.id, board.agent_source_lane)
        if source_lane is None:
            logger.error(
                "Agent check-in: source lane '%s' not found on board %s",
                board.agent_source_lane, board.id,
            )
            return []

        with get_session() as db:
            tickets = (
                db.query(KanbanTicket)
                .filter(KanbanTicket.lane_id == source_lane.id)
                .order_by(KanbanTicket.position.asc())
                .all()
            )
            result = [
                {"id": t.id, "title": t.title, "lane_id": t.lane_id, "position": t.position}
                for t in tickets
            ]
        return result

    def _resolve_project_context(self, board, ticket_id: int) -> Dict[str, Optional[str]]:
        """Resolve project context from ticket link first, then board default."""
        project_id: Optional[int] = None
        with get_session() as db:
            tk = db.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
            if tk:
                project_id = tk.linked_project_id or board.default_project_id

        if not project_id:
            return {"project_id": None, "project_name": None, "project_folder": None}

        try:
            from distr.core.db.projects import Project
            with get_session() as db:
                project = db.query(Project).filter(Project.id == project_id).first()
                if not project:
                    return {"project_id": str(project_id), "project_name": None, "project_folder": None}
                return {
                    "project_id": str(project_id),
                    "project_name": project.name or None,
                    "project_folder": project.folder_location or None,
                }
        except Exception:
            return {"project_id": str(project_id), "project_name": None, "project_folder": None}

    def _process_ticket(self, board, ticket: dict) -> str:
        """Process a ticket via workflow execution.

        On completion, moves the ticket to the NEXT lane (e.g. Current → QA/Assess).
        Returns the terminal status ('completed', 'failed', 'cancelled').
        """
        self._status.current_ticket_id = ticket["id"]
        self._status.current_ticket_title = ticket.get("title", "")
        _emit_board_update(board.id, "ticket_started", {
            "ticket_id": ticket["id"],
            "ticket_title": ticket.get("title", ""),
        })

        # Workflow path: prefer ticket-linked workflow, fall back to board default.
        workflow_id = None
        # Build context from ticket title and description
        context = f"Ticket: {ticket['title']}"
        ticket_description = ""
        try:
            with get_session() as db:
                tk = db.query(KanbanTicket).filter(KanbanTicket.id == ticket["id"]).first()
                if tk:
                    workflow_id = tk.linked_workflow_id or board.default_workflow_id
                    if tk.description:
                        ticket_description = tk.description
                        context += f"\n\nDescription: {tk.description}"
        except Exception:
            pass

        if not workflow_id:
            logger.error(
                "Agent check-in: no linked workflow on ticket %s and no board default workflow",
                ticket["id"],
            )
            return "failed"

        project_ctx = self._resolve_project_context(board, ticket["id"])
        if project_ctx.get("project_name"):
            context += f"\n\nProject: {project_ctx['project_name']}"
        if project_ctx.get("project_folder"):
            context += f"\nProject folder: {project_ctx['project_folder']}"

        risk_profile = _risk_profile_for_ticket(ticket.get("title", ""), ticket_description)
        run_metadata = {
            "source_type": "board_checkin",
            "board_id": board.id,
            "board_name": board.name,
            "ticket_id": ticket["id"],
            "ticket_title": ticket.get("title", ""),
            "project_id": project_ctx.get("project_id"),
            "project_name": project_ctx.get("project_name"),
            "project_folder": project_ctx.get("project_folder"),
            "phase": "planning",
            "risk_profile": risk_profile,
        }
        run_result = start_workflow_run(
            workflow_id,
            context=context,
            board_id=board.id,
            ticket_id=ticket["id"],
            run_metadata=run_metadata,
        )
        if "error" in run_result:
            logger.error("Agent check-in: failed to start workflow for ticket %s: %s", ticket["id"], run_result["error"])
            return "failed"

        run_id = run_result.get("run_id")
        self._current_run_id = run_id
        self._status.current_run_id = run_id
        self._status.current_phase = "planning"

        terminal_status = self._wait_for_run(run_id)

        if terminal_status == "completed":
            self._move_ticket_to_next_lane(board, ticket)
        else:
            logger.info("Agent check-in: ticket %s run ended with '%s', leaving in current lane", ticket["id"], terminal_status)

        self._current_run_id = None
        self._status.current_run_id = None
        self._status.current_phase = None
        _emit_board_update(board.id, "ticket_finished", {
            "ticket_id": ticket["id"],
            "ticket_title": ticket.get("title", ""),
            "status": terminal_status,
        })
        return terminal_status

    def _try_pi_agent(self, ticket: dict, board) -> str:
        """Process a ticket via pi coding agent (RPC mode). Returns status string."""
        from distr.core.pi_rpc import get_rpc_session, get_or_create_rpc_session, PiRpcSession

        # Resolve project folder
        folder = None
        project_name = None
        project_id = None
        try:
            from distr.core.db.projects import Project
            project_id = board.default_project_id
            if not project_id:
                # Check ticket-level link
                with get_session() as db:
                    tk = db.query(KanbanTicket).filter(KanbanTicket.id == ticket["id"]).first()
                    if tk:
                        project_id = tk.linked_project_id
            if project_id:
                with get_session() as db:
                    project = db.query(Project).filter(Project.id == project_id).first()
                    if project and project.folder_location:
                        folder = project.folder_location
                        project_name = project.name
        except Exception as e:
            logger.debug("Could not resolve project for ticket %s: %s", ticket["id"], e)

        if not folder:
            logger.error("Agent check-in: send_to_cli but no project folder for ticket %s", ticket["id"])
            return "failed"

        # Check pi is available
        pi_path = PiRpcSession.find_pi()
        if not pi_path:
            logger.error("Agent check-in: pi coding agent not found")
            return "failed"

        title = ticket.get("title", "")
        instruction = title
        try:
            from distr.core.kanban.ticket_cli_context import build_kanban_ticket_cli_instruction

            with get_session() as db:
                instruction = build_kanban_ticket_cli_instruction(
                    db,
                    ticket["id"],
                    project_name=project_name or "",
                    project_folder=folder or "",
                    project_id=project_id,
                )
                tk = db.query(KanbanTicket).filter(KanbanTicket.id == ticket["id"]).first()
                if tk and tk.title:
                    title = tk.title
        except Exception:
            description = ""
            try:
                with get_session() as db:
                    tk = db.query(KanbanTicket).filter(KanbanTicket.id == ticket["id"]).first()
                    if tk:
                        description = tk.description or ""
            except Exception:
                pass
            instruction = f"{title}\n\n{description}".strip() if description else title

        logger.info("Agent check-in: sending ticket #%s to pi in %s", ticket["id"], folder)

        output = None
        status = "failed"
        try:
            # Try RPC session first (async, persistent)
            if project_id:
                rpc = get_rpc_session(project_id)
                if rpc and rpc.is_alive:
                    success = rpc.send_prompt(instruction, ticket_id_for_writeback=ticket["id"])
                    if success:
                        status = "running"
                        logger.info("Agent check-in: sent ticket #%s to pi via RPC", ticket["id"])
                    else:
                        status = "failed"
                        output = "Failed to send prompt to Pi RPC"
                        logger.warning("Agent check-in: failed to send ticket #%s via RPC", ticket["id"])
                else:
                    # No active session — use pi -p (print mode, one-shot)
                    import subprocess
                    result = subprocess.run(
                        ["pi", "-p", "--append-system-prompt",
                         f"You are working on project: {project_name}. This is ticket #{ticket['id']}.",
                         instruction],
                        capture_output=True, text=True, timeout=600,
                        cwd=folder,
                    )
                    output = (result.stdout + result.stderr).strip()[:3000]
                    status = "completed" if result.returncode == 0 else "failed"
                    logger.info("Agent check-in: pi %s for ticket #%s (%d chars output)", status, ticket["id"], len(output))
            else:
                # Fallback to pi -p
                import subprocess
                result = subprocess.run(
                    ["pi", "-p", instruction],
                    capture_output=True, text=True, timeout=600,
                    cwd=folder,
                )
                output = (result.stdout + result.stderr).strip()[:3000]
                status = "completed" if result.returncode == 0 else "failed"

        except subprocess.TimeoutExpired:
            logger.warning("Agent check-in: pi timed out for ticket #%s", ticket["id"])
            status = "failed"
            output = "Pi timed out after 10 minutes"
        except Exception as e:
            logger.error("Agent check-in: pi error for ticket #%s: %s", ticket["id"], e)
            status = "failed"
            output = f"Pi error: {e}"

        # Pi one-shot / failure paths: write result onto ticket (RPC async path uses agent_end hook)
        if status != "running" and output is not None:
            try:
                from distr.core.kanban.ticket_writeback import append_pi_cli_summary_to_ticket

                append_pi_cli_summary_to_ticket(
                    ticket["id"],
                    output or "(no output)",
                    outcome_status="failed" if status == "failed" else "completed",
                )
            except Exception:
                logger.debug("Agent check-in: ticket Pi write-back failed", exc_info=True)

        # Log to audit trail
        try:
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            with get_session() as db:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] Ticket #{ticket['id']}: {title}",
                    status=status,
                    workflow_type="pi_agent",
                )
                db.add(audit)
                db.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id, position=0,
                    name=f"Ticket #{ticket['id']}", instruction=instruction[:500],
                    status=status, result=output[:2000] if output else None, tool_used="pi",
                )
                db.add(step)
                db.commit()
        except Exception as e:
            logger.debug("Could not create audit for pi agent ticket: %s", e)

        return status

    def _move_ticket_to_next_lane(self, board, ticket: dict):
        """Move a ticket to the next lane in sequence (e.g. Current → QA/Assess).
        Falls back to the configured done lane if there's no next lane."""
        with get_session() as db:
            tk = db.query(KanbanTicket).filter(KanbanTicket.id == ticket["id"]).first()
            if not tk:
                return

            current_lane = db.query(KanbanLane).filter(KanbanLane.id == tk.lane_id).first()
            if not current_lane:
                return

            # Find the next lane by position
            next_lane = (
                db.query(KanbanLane)
                .filter(
                    KanbanLane.board_id == board.id,
                    KanbanLane.position > current_lane.position,
                )
                .order_by(KanbanLane.position.asc())
                .first()
            )

            if not next_lane:
                # No next lane — try the configured done lane
                next_lane = (
                    db.query(KanbanLane)
                    .filter(KanbanLane.board_id == board.id, KanbanLane.name == board.agent_done_lane)
                    .first()
                )

            if not next_lane:
                logger.warning("Agent check-in: no next lane found for ticket %s, leaving in place", ticket["id"])
                return

            from sqlalchemy import func
            max_pos = (
                db.query(func.max(KanbanTicket.position))
                .filter(KanbanTicket.lane_id == next_lane.id)
                .scalar()
            )
            tk.lane_id = next_lane.id
            tk.position = 0 if max_pos is None else max_pos + 1
            next_lane_name = next_lane.name
            db.commit()

        logger.info("Agent check-in: moved ticket %s to lane '%s'", ticket["id"], next_lane_name)
        _emit_board_update(board.id, "ticket_moved", {
            "ticket_id": ticket["id"],
            "to_lane": next_lane_name,
        })
        try:
            from distr.core.kanban.ticket_chat_notify import notify_source_chat_ticket_moved

            notify_source_chat_ticket_moved(
                ticket["id"],
                board_name=board.name,
                to_lane_name=next_lane_name,
                reason="workflow_completed",
            )
        except Exception:
            logger.debug("Agent check-in: ticket chat notify failed", exc_info=True)

    def _wait_for_run(self, run_id: int, timeout_seconds: float = 3600.0) -> str:
        """Poll until the workflow run reaches a terminal status.

        Hard-stops after *timeout_seconds* (default 1 hour) so a stuck run can
        never hang the entire check-in thread forever.
        """
        import json as _json
        deadline = time.monotonic() + timeout_seconds
        while True:
            if self._cancelled:
                return "cancelled"
            if time.monotonic() >= deadline:
                logger.error(
                    "_wait_for_run: run %d timed out after %.0fs — forcing 'failed'",
                    run_id,
                    timeout_seconds,
                )
                return "failed"
            try:
                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                    if run:
                        try:
                            run_data = _json.loads(run.run_data or "{}")
                            new_phase = run_data.get("phase") or self._status.current_phase
                            if new_phase != self._status.current_phase:
                                self._status.current_phase = new_phase
                                _emit_board_update(self.board_id, "phase_changed", {
                                    "run_id": run_id,
                                    "phase": new_phase,
                                    "ticket_id": self._status.current_ticket_id,
                                })
                            else:
                                self._status.current_phase = new_phase
                        except Exception:
                            pass
                        if run.status in _TERMINAL_STATUSES:
                            return run.status
            except Exception:
                logger.debug("_wait_for_run: DB query failed, will retry", exc_info=True)
            time.sleep(_POLL_INTERVAL)

    def _move_ticket_to_done(self, board, ticket: dict):
        """Move a ticket to the done lane with position = max(existing) + 1."""
        done_lane = _resolve_lane(board.id, board.agent_done_lane)
        if done_lane is None:
            logger.error(
                "Agent check-in: done lane '%s' not found on board %s, leaving ticket in source",
                board.agent_done_lane, board.id,
            )
            return

        with get_session() as db:
            # Compute next position in done lane
            from sqlalchemy import func
            max_pos = (
                db.query(func.max(KanbanTicket.position))
                .filter(KanbanTicket.lane_id == done_lane.id)
                .scalar()
            )
            new_position = 0 if max_pos is None else max_pos + 1

            tk = db.query(KanbanTicket).filter(KanbanTicket.id == ticket["id"]).first()
            if tk:
                tk.lane_id = done_lane.id
                tk.position = new_position
            db.commit()

        logger.info(
            "Agent check-in: moved ticket %s to done lane '%s' at position %s",
            ticket["id"], board.agent_done_lane, new_position,
        )

    def run(self):
        """Main entry point — process all tickets from the source lane."""
        self._cancelled = False
        self._status = AgentStatus(state="running")
        with _active_agents_lock:
            _active_agents[self.board_id] = self
        _emit_board_update(self.board_id, "agent_started", {})

        board = self._load_board()
        if board is None:
            self._status.state = "idle"
            with _active_agents_lock:
                _active_agents.pop(self.board_id, None)
            return

        tickets = self._collect_tickets(board)
        if not tickets:
            logger.info("Agent check-in: no tickets in source lane for board %s", self.board_id)
            self._status.state = "idle"
            with _active_agents_lock:
                _active_agents.pop(self.board_id, None)
            return

        self._status.total_tickets = len(tickets)
        _emit_board_update(self.board_id, "queue_loaded", {"total_tickets": len(tickets)})

        # Set LLM override from board settings
        token = set_llm_override(LLMOverride(
            orchestrator_provider=board.agent_orchestrator_provider,
            orchestrator_model=board.agent_orchestrator_model,
            coder_provider=board.agent_coder_provider,
            coder_model=board.agent_coder_model,
            sub_provider=board.agent_sub_provider,
            sub_model=board.agent_sub_model,
        ))
        try:
            for ticket in tickets:
                if self._cancelled:
                    break
                self._process_ticket(board, ticket)
                self._status.processed_count += 1
        finally:
            clear_llm_override(token)
            self._status.state = "idle"
            with _active_agents_lock:
                _active_agents.pop(self.board_id, None)
            _emit_board_update(self.board_id, "agent_idle", {
                "processed_count": self._status.processed_count,
                "total_tickets": self._status.total_tickets,
            })

    def cancel(self):
        """Cancel the current agent check-in."""
        self._cancelled = True
        if self._current_run_id:
            cancel_run(self._current_run_id)

    def restart(self):
        """Cancel the current run and restart from the first ticket."""
        self.cancel()
        self._cancelled = False
        self.run()


def analyze_board_checkin(board_id: int) -> Dict[str, Any]:
    """Inspect check-in readiness for one board (no side effects).

    Used by the web UI to explain what check-in will do: source lane, ticket counts,
    workflow name / first step, and why a board cannot run.
    """
    from distr.core.db.workflow import AutoWorkflow

    settings = load_settings_from_db()
    out: Dict[str, Any] = {
        "board_id": board_id,
        "board_name": None,
        "source_type": "database",
        "agent_enabled_resolved": False,
        "blockers": [],
        "runnable": False,
        "already_running": False,
        "agent_source_lane": None,
        "source_lane_exists": False,
        "agent_done_lane": None,
        "default_workflow_id": None,
        "workflow_name": None,
        "workflow_step_count": 0,
        "first_step_name": None,
        "tickets_in_source": 0,
        "tickets_runnable": 0,
        "tickets": [],
    }

    with _active_agents_lock:
        existing = _active_agents.get(board_id)
    if existing and existing.status.state == "running":
        out["already_running"] = True
        out["blockers"].append({
            "code": "already_running",
            "detail": "A check-in is already in progress on this board — wait or cancel it before starting again.",
        })

    with get_session() as db:
        board = db.query(KanbanBoard).filter(KanbanBoard.id == board_id).first()
        if not board:
            out["blockers"].append({"code": "board_missing", "detail": "Board not found."})
            return out

        out["board_name"] = board.name
        out["source_type"] = (board.source or "database").strip() or "database"

        agent_enabled = board.agent_enabled
        if agent_enabled is None:
            agent_enabled = settings.get("kanban_agent_enabled", False)
        out["agent_enabled_resolved"] = bool(agent_enabled)
        if not agent_enabled:
            out["blockers"].append({
                "code": "agent_disabled",
                "detail": "Agent check-in is disabled for this board (enable “Agent check-in” in board settings).",
            })

        agent_source_lane = board.agent_source_lane or settings.get("kanban_agent_source_lane", "") or ""
        agent_done_lane = board.agent_done_lane or settings.get("kanban_agent_done_lane", "") or ""
        out["agent_source_lane"] = agent_source_lane or None
        out["agent_done_lane"] = agent_done_lane or None

        if not agent_source_lane:
            out["blockers"].append({
                "code": "no_source_lane",
                "detail": "No source lane is set — choose the column tickets are picked from (e.g. “Current”) in board or global kanban settings.",
            })

        dwf = board.default_workflow_id
        out["default_workflow_id"] = dwf
        if not dwf:
            out["blockers"].append({
                "code": "no_workflow",
                "detail": "No default workflow — select a workflow on this board so each ticket can run step 1 → … until complete.",
            })

        if dwf:
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == dwf).first()
            if wf:
                out["workflow_name"] = wf.name
                steps = sorted(wf.steps, key=lambda s: s.position) if wf.steps else []
                out["workflow_step_count"] = len(steps)
                out["first_step_name"] = steps[0].name if steps else None
            else:
                out["blockers"].append({
                    "code": "workflow_missing",
                    "detail": f"Default workflow (id {dwf}) no longer exists — pick another workflow on the board.",
                })

        if agent_source_lane:
            lane = (
                db.query(KanbanLane)
                .filter(KanbanLane.board_id == board_id, KanbanLane.name == agent_source_lane)
                .first()
            )
            out["source_lane_exists"] = lane is not None
            if not lane:
                out["blockers"].append({
                    "code": "lane_not_found",
                    "detail": f"No lane named “{agent_source_lane}” on this board — names must match a column exactly.",
                })
            else:
                tickets = (
                    db.query(KanbanTicket)
                    .filter(KanbanTicket.lane_id == lane.id)
                    .order_by(KanbanTicket.position.asc())
                    .all()
                )
                out["tickets_in_source"] = len(tickets)
                for t in tickets:
                    wf_for_ticket = t.linked_workflow_id or dwf
                    origin = "local"
                    if t.external_source in ("trello", "jira"):
                        origin = t.external_source
                    can_run = bool(wf_for_ticket)
                    if can_run:
                        out["tickets_runnable"] += 1
                    out["tickets"].append({
                        "id": t.id,
                        "title": (t.title or "")[:200],
                        "origin": origin,
                        "workflow_id": wf_for_ticket,
                        "workflow_resolvable": can_run,
                    })

    agent = KanbanAgentCheckIn(board_id)
    board_info = agent._load_board()
    out["runnable"] = board_info is not None and not out["already_running"]
    return out


def format_checkin_dispatch_report(
    board_reports: List[Dict[str, Any]],
    *,
    started: int,
    already_running: int,
    skipped: int,
    focused_single_board: bool = False,
) -> str:
    """Human-readable summary for the Check-in button (multi-line)."""
    lines: List[str] = []
    if len(board_reports) == 1:
        bn = board_reports[0].get("board_name") or f"Board #{board_reports[0].get('board_id')}"
        lines.append(
            f'Check-in for “{bn}”: {started} started, {already_running} already running, '
            f"{skipped} could not start."
        )
        if focused_single_board:
            lines.append(
                "(Only the board open in the sidebar — other boards are not included.)"
            )
    else:
        lines.append(
            f"Check-in dispatch: {started} started, {already_running} already running, "
            f"{skipped} could not start."
        )
        lines.append(
            "(Every board below has “Agent check-in” enabled in its board settings.)"
        )
    lines.append("")
    for rep in board_reports:
        name = rep.get("board_name") or f"Board #{rep.get('board_id')}"
        src = rep.get("source_type") or "database"
        lines.append(f"— {name}  ({src})")

        if rep.get("already_running"):
            lines.append("  · Check-in already running on this board.")
            continue

        if rep.get("blockers"):
            for b in rep["blockers"]:
                if b.get("code") == "already_running":
                    continue
                lines.append(f"  · {b.get('detail', '')}")

        src_lane = rep.get("agent_source_lane") or "?"
        n = rep.get("tickets_in_source", 0)
        nr = rep.get("tickets_runnable", 0)
        lines.append(f"  · Source lane: “{src_lane}” — {n} ticket(s) there ({nr} can run a workflow).")

        if rep.get("tickets"):
            preview = rep["tickets"][:8]
            parts = []
            for t in preview:
                tag = t.get("origin") or "local"
                wf_ok = "+" if t.get("workflow_resolvable") else "-"
                parts.append(f"#{t.get('id')}{wf_ok} [{tag}] {(t.get('title') or '')[:48]}")
            lines.append("  · Tickets: " + "; ".join(parts))
            if len(rep["tickets"]) > 8:
                lines.append(f"  · … and {len(rep['tickets']) - 8} more.")

        wf_name = rep.get("workflow_name")
        steps = rep.get("workflow_step_count", 0)
        first = rep.get("first_step_name")
        if wf_name and steps:
            first_bit = f' First step: “{first}”.' if first else ""
            lines.append(
                f"  · Workflow: “{wf_name}” ({steps} step(s)).{first_bit} "
                "When the run finishes successfully, the ticket moves to the next lane on the board "
                "(or the configured target lane)."
            )
        elif rep.get("runnable") and n == 0:
            lines.append("  · Nothing to process — move tickets into the source lane to pick them up.")

        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def start_agent_checkin(board_id: int) -> dict:
    """Start a board check-in in a background thread using shared runtime checks.

    Returns:
        dict with keys:
        - status: "started" | "already_running" | "not_runnable"
        - board_id: int
        - reason: optional string
        - report: optional analyze_board_checkin snapshot (for UI)
    """
    report = analyze_board_checkin(board_id)
    with _active_agents_lock:
        existing = _active_agents.get(board_id)
    if existing and existing.status.state == "running":
        return {"status": "already_running", "board_id": board_id, "reason": "already_running", "report": report}

    agent = KanbanAgentCheckIn(board_id)
    board = agent._load_board()
    if board is None:
        return {"status": "not_runnable", "board_id": board_id, "reason": "board_not_runnable", "report": report}

    threading.Thread(target=agent.run, daemon=True).start()
    return {"status": "started", "board_id": board_id, "report": report}
