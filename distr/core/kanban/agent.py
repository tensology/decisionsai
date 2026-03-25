"""
Kanban Agent Check-In Engine.

Processes tickets from a board's source lane by running the board's default
workflow against each ticket sequentially, then moving completed tickets to
the done lane.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, List

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


@dataclass
class AgentStatus:
    """Tracks the current state of an Agent Check-In."""
    state: str = "idle"  # "idle" or "running"
    current_ticket_id: Optional[int] = None
    current_ticket_title: str = ""
    total_tickets: int = 0
    processed_count: int = 0
    current_run_id: Optional[int] = None


# Module-level registry of active agents keyed by board_id
_active_agents: Dict[int, "KanbanAgentCheckIn"] = {}


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
    agent_enabled: bool
    agent_source_lane: str
    agent_done_lane: str
    default_workflow_id: int
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

    def _load_board(self) -> Optional[KanbanBoard]:
        """Fetch the board and validate required agent fields.

        Returns the board dict-like info or None if validation fails.
        """
        with get_session() as db:
            board = db.query(KanbanBoard).filter(KanbanBoard.id == self.board_id).first()
            if not board:
                logger.error("Agent check-in: board %s not found", self.board_id)
                return None
            if not board.agent_enabled:
                logger.info("Agent check-in: agent not enabled on board %s", self.board_id)
                return None
            if not board.agent_source_lane:
                logger.error("Agent check-in: no source lane configured on board %s", self.board_id)
                return None
            if not board.agent_done_lane:
                logger.error("Agent check-in: no done lane configured on board %s", self.board_id)
                return None
            if not board.default_workflow_id:
                logger.error("Agent check-in: no default_workflow_id on board %s", self.board_id)
                return None

            # Capture all needed attributes before session closes
            info = _BoardInfo(
                id=board.id,
                agent_enabled=board.agent_enabled,
                agent_source_lane=board.agent_source_lane,
                agent_done_lane=board.agent_done_lane,
                default_workflow_id=board.default_workflow_id,
                agent_orchestrator_provider=board.agent_orchestrator_provider or "",
                agent_orchestrator_model=board.agent_orchestrator_model or "",
                agent_coder_provider=board.agent_coder_provider or "",
                agent_coder_model=board.agent_coder_model or "",
                agent_sub_provider=board.agent_sub_provider or "",
                agent_sub_model=board.agent_sub_model or "",
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

    def _process_ticket(self, board, ticket: dict) -> str:
        """Run the board's default workflow for a ticket and handle completion.

        Returns the terminal run status ('completed', 'failed', 'cancelled').
        """
        self._status.current_ticket_id = ticket["id"]
        self._status.current_ticket_title = ticket.get("title", "")

        # Start workflow run
        run_result = start_workflow_run(board.default_workflow_id)
        if "error" in run_result:
            logger.error(
                "Agent check-in: failed to start workflow for ticket %s: %s",
                ticket["id"], run_result["error"],
            )
            return "failed"

        run_id = run_result.get("run_id")
        self._current_run_id = run_id
        self._status.current_run_id = run_id

        # Wait for terminal status
        terminal_status = self._wait_for_run(run_id)

        if terminal_status == "completed":
            self._move_ticket_to_done(board, ticket)
        else:
            logger.info(
                "Agent check-in: ticket %s run ended with status '%s', leaving in source lane",
                ticket["id"], terminal_status,
            )

        self._current_run_id = None
        self._status.current_run_id = None
        return terminal_status

    def _wait_for_run(self, run_id: int) -> str:
        """Poll until the workflow run reaches a terminal status."""
        while True:
            if self._cancelled:
                return "cancelled"
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                if run and run.status in _TERMINAL_STATUSES:
                    return run.status
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
        _active_agents[self.board_id] = self

        board = self._load_board()
        if board is None:
            self._status.state = "idle"
            _active_agents.pop(self.board_id, None)
            return

        tickets = self._collect_tickets(board)
        if not tickets:
            logger.info("Agent check-in: no tickets in source lane for board %s", self.board_id)
            self._status.state = "idle"
            _active_agents.pop(self.board_id, None)
            return

        self._status.total_tickets = len(tickets)

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
            _active_agents.pop(self.board_id, None)

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
