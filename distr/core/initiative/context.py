import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger("distr.core.initiative.service")


@dataclass
class ContextBundle:
    chat_history: list = field(default_factory=list)
    scheduled_sessions: list = field(default_factory=list)
    kanban_summary: list = field(default_factory=list)
    stuck_tasks: list = field(default_factory=list)
    unfinished_workflows: list = field(default_factory=list)
    initiative_settings: dict = field(default_factory=dict)
    current_datetime: str = ""


class ContextAssembler:
    def build(self, settings: dict) -> ContextBundle:
        now = datetime.utcnow()

        # --- chat_history ---
        chat_history = []
        try:
            chat_history = self._fetch_chat_history(settings)
        except Exception:
            logger.warning("ContextAssembler: failed to fetch chat history", exc_info=True)

        # --- scheduled_sessions ---
        scheduled_sessions = []
        try:
            scheduled_sessions = self._fetch_scheduled_sessions()
        except Exception:
            logger.warning("ContextAssembler: failed to fetch scheduled sessions", exc_info=True)

        # --- kanban_summary ---
        kanban_summary = []
        try:
            kanban_summary = self._fetch_kanban_summary(now)
        except Exception:
            logger.warning("ContextAssembler: failed to fetch kanban summary", exc_info=True)

        # --- stuck_tasks ---
        stuck_tasks = []
        try:
            stuck_tasks = self._fetch_stuck_tasks(now)
        except Exception:
            logger.warning("ContextAssembler: failed to fetch stuck tasks", exc_info=True)

        # --- unfinished_workflows ---
        unfinished_workflows = []
        try:
            unfinished_workflows = self._fetch_unfinished_workflows(now)
        except Exception:
            logger.warning("ContextAssembler: failed to fetch unfinished workflows", exc_info=True)

        return ContextBundle(
            chat_history=chat_history,
            scheduled_sessions=scheduled_sessions,
            kanban_summary=kanban_summary,
            stuck_tasks=stuck_tasks,
            unfinished_workflows=unfinished_workflows,
            initiative_settings=settings,
            current_datetime=now.isoformat(),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_chat_history(self, settings: dict) -> list:
        from distr.core.chat import ChatService

        chat_id = settings.get("agent_current_chat_id") or settings.get("last_chat_id")
        if not chat_id:
            return []

        messages = ChatService.get_chat_history(int(chat_id))
        # Keep last 20 messages
        messages = messages[-20:]

        # Truncate to 4000 chars keeping most recent messages
        result = []
        total_chars = 0
        for msg in reversed(messages):
            serialized = json.dumps(msg, ensure_ascii=False)
            if total_chars + len(serialized) > 4000:
                break
            result.append(msg)
            total_chars += len(serialized)

        result.reverse()
        return result

    def _fetch_scheduled_sessions(self) -> list:
        from distr.core.step_runner.service import list_sessions

        sessions = list_sessions(session_type="scheduled")
        return [s for s in sessions if s.get("enabled", True)]

    def _fetch_kanban_summary(self, now: datetime) -> list:
        from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
        from distr.core.db import get_session

        overdue_cutoff = now - timedelta(days=7)
        summary = []

        with get_session() as session:
            boards = session.query(KanbanBoard).all()
            for board in boards:
                lanes_data = []
                total_tickets = 0
                overdue_tickets = 0

                for lane in board.lanes:
                    lane_tickets = (
                        session.query(KanbanTicket)
                        .filter(KanbanTicket.lane_id == lane.id)
                        .all()
                    )
                    lane_count = len(lane_tickets)
                    total_tickets += lane_count

                    for ticket in lane_tickets:
                        if ticket.created_date and ticket.created_date < overdue_cutoff:
                            overdue_tickets += 1

                    lanes_data.append({
                        "name": lane.name,
                        "ticket_count": lane_count,
                    })

                summary.append({
                    "board_id": board.id,
                    "board_name": board.name,
                    "total_tickets": total_tickets,
                    "overdue_tickets": overdue_tickets,
                    "lanes": lanes_data,
                })

        return summary

    def _fetch_stuck_tasks(self, now: datetime) -> list:
        from distr.core.db.step_runner import StepRunnerSession
        from distr.core.db import get_session

        stuck_cutoff = now - timedelta(minutes=30)
        result = []

        with get_session() as session:
            rows = (
                session.query(StepRunnerSession)
                .filter(
                    StepRunnerSession.status == "in_progress",
                    StepRunnerSession.modified_date < stuck_cutoff,
                )
                .all()
            )
            for r in rows:
                duration_minutes = int(
                    (now - r.modified_date).total_seconds() / 60
                )
                result.append({
                    "session_id": r.id,
                    "instruction": (r.instruction or "")[:200],
                    "duration_minutes": duration_minutes,
                })

        return result

    def _fetch_unfinished_workflows(self, now: datetime) -> list:
        from distr.core.db.step_runner import StepRunnerSession
        from distr.core.db import get_session

        unfinished_cutoff = now - timedelta(hours=24)
        terminal_statuses = ("completed", "failed", "cancelled")
        result = []

        with get_session() as session:
            rows = (
                session.query(StepRunnerSession)
                .filter(
                    StepRunnerSession.status.notin_(terminal_statuses),
                    StepRunnerSession.created_date < unfinished_cutoff,
                )
                .all()
            )
            for r in rows:
                elapsed_hours = round(
                    (now - r.created_date).total_seconds() / 3600, 1
                )
                result.append({
                    "session_id": r.id,
                    "instruction": (r.instruction or "")[:200],
                    "elapsed_hours": elapsed_hours,
                })

        return result
