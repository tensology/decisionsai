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
    active_project: dict = field(default_factory=dict)
    available_tools: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    recent_audit: list = field(default_factory=list)
    initiative_settings: dict = field(default_factory=dict)
    current_datetime: str = ""
    # R8 memory files — trimmed for LLM (not full AGENT/USER/MEMORY bodies)
    memory_agent: str = ""
    memory_user: str = ""
    memory_long_term: str = ""


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
            logger.warning("ContextAssembler: failed to fetch ticket board summary", exc_info=True)

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

        # --- active_project ---
        active_project = {}
        try:
            active_project = self._fetch_active_project()
        except Exception:
            logger.warning("ContextAssembler: failed to fetch active project", exc_info=True)

        # --- available_tools ---
        available_tools = []
        try:
            available_tools = self._fetch_available_tools()
        except Exception:
            logger.warning("ContextAssembler: failed to fetch available tools", exc_info=True)

        # --- skills ---
        skills = []
        try:
            skills = self._fetch_skills()
        except Exception:
            logger.warning("ContextAssembler: failed to fetch skills", exc_info=True)

        # --- recent_audit ---
        recent_audit = []
        try:
            recent_audit = self._fetch_recent_audit(settings)
        except Exception:
            logger.warning("ContextAssembler: failed to fetch recent audit", exc_info=True)

        memory_agent, memory_user, memory_long_term = "", "", ""
        try:
            memory_agent, memory_user, memory_long_term = self._fetch_memory_snippets()
        except Exception:
            logger.warning("ContextAssembler: failed to load memory file snippets", exc_info=True)

        return ContextBundle(
            chat_history=chat_history,
            scheduled_sessions=scheduled_sessions,
            kanban_summary=kanban_summary,
            stuck_tasks=stuck_tasks,
            unfinished_workflows=unfinished_workflows,
            active_project=active_project,
            available_tools=available_tools,
            skills=skills,
            recent_audit=recent_audit,
            initiative_settings=settings,
            current_datetime=now.isoformat(),
            memory_agent=memory_agent,
            memory_user=memory_user,
            memory_long_term=memory_long_term,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_memory_snippets(self) -> tuple[str, str, str]:
        """Trimmed AGENT.md / USER.md / MEMORY.md for initiative context (R8)."""
        from distr.core.memory.files import (
            ensure_memory_files,
            load_context_snippets_for_llm,
            try_load_system_prompt_template,
        )

        tpl = try_load_system_prompt_template()
        ensure_memory_files(system_prompt_template=tpl)
        snippets = load_context_snippets_for_llm()
        return (
            snippets.get("agent") or "",
            snippets.get("user") or "",
            snippets.get("memory") or "",
        )

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
        """Fetch scheduled workflows."""
        from distr.core.workflow.service import list_workflows
        workflows = list_workflows(workflow_type="scheduled")
        return [w for w in workflows if w.get("schedule_enabled")]

    def _fetch_kanban_summary(self, now: datetime) -> list:
        from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
        from distr.core.db import get_session

        overdue_cutoff = now - timedelta(days=7)
        summary = []

        # Local boards
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
                    "source": "local",
                    "total_tickets": total_tickets,
                    "overdue_tickets": overdue_tickets,
                    "lanes": lanes_data,
                })

        # External boards (Trello/Jira) — just names, no ticket counts (too slow)
        try:
            import json as _json
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            accounts = _json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
            for acct in accounts:
                provider = (acct.get("provider") or "").lower()
                if provider == "trello" and acct.get("api_key") and acct.get("api_token"):
                    try:
                        import requests
                        resp = requests.get(
                            "https://api.trello.com/1/members/me/boards",
                            params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,closed"},
                            timeout=5,
                        )
                        if resp.status_code == 200:
                            for b in resp.json():
                                if not b.get("closed", False):
                                    summary.append({"board_name": b["name"], "source": "trello", "board_id": b["id"]})
                    except Exception:
                        pass
                elif provider == "jira" and acct.get("email") and acct.get("api_token"):
                    try:
                        import requests
                        from requests.auth import HTTPBasicAuth
                        domain = acct.get("domain") or ""
                        if not domain:
                            server_url = (acct.get("server_url") or "").strip().rstrip("/")
                            if server_url:
                                domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                        if domain:
                            resp = requests.get(
                                f"https://{domain}/rest/agile/1.0/board",
                                auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                                headers={"Accept": "application/json"}, timeout=5,
                            )
                            if resp.status_code == 200:
                                for b in resp.json().get("values", []):
                                    summary.append({"board_name": b["name"], "source": "jira", "board_id": str(b["id"])})
                    except Exception:
                        pass
        except Exception:
            pass

        return summary

    def _fetch_stuck_tasks(self, now: datetime) -> list:
        from distr.core.db.workflow import AutoWorkflow
        from distr.core.db import get_session

        stuck_cutoff = now - timedelta(minutes=30)
        result = []

        with get_session() as session:
            rows = (
                session.query(AutoWorkflow)
                .filter(
                    AutoWorkflow.status == "in_progress",
                    AutoWorkflow.modified_date < stuck_cutoff,
                )
                .all()
            )
            for r in rows:
                duration_minutes = int(
                    (now - r.modified_date).total_seconds() / 60
                )
                result.append({
                    "session_id": r.id,
                    "instruction": (r.name or "")[:200],
                    "duration_minutes": duration_minutes,
                })

        return result

    def _fetch_unfinished_workflows(self, now: datetime) -> list:
        from distr.core.db.workflow import AutoWorkflow
        from distr.core.db import get_session

        unfinished_cutoff = now - timedelta(hours=24)
        terminal_statuses = ("completed", "failed", "cancelled")
        result = []

        with get_session() as session:
            rows = (
                session.query(AutoWorkflow)
                .filter(
                    AutoWorkflow.status.notin_(terminal_statuses),
                    AutoWorkflow.created_date < unfinished_cutoff,
                )
                .all()
            )
            for r in rows:
                elapsed_hours = round(
                    (now - r.created_date).total_seconds() / 3600, 1
                )
                result.append({
                    "session_id": r.id,
                    "instruction": (r.name or "")[:200],
                    "elapsed_hours": elapsed_hours,
                })

        return result

    def _fetch_active_project(self) -> dict:
        """Fetch the currently active project with its context items and board info."""
        from distr.core.db.projects import Project, ProjectContextItem
        from distr.core.db import get_session

        with get_session() as session:
            project = session.query(Project).filter(Project.in_use == True).first()
            if not project:
                return {}

            context_items = (
                session.query(ProjectContextItem)
                .filter(ProjectContextItem.project_id == project.id)
                .all()
            )

            return {
                "id": project.id,
                "name": project.name or "",
                "description": project.description or "",
                "folder_location": project.folder_location or "",
                "provider": project.provider or "",
                "board_name": project.board_name or "",
                "trigger_words": project.additional_trigger_words or "[]",
                "startup_instructions": project.startup_instructions or "",
                "context_items": [
                    {"title": c.title, "content": (c.content or "")[:500]}
                    for c in context_items[:10]
                ],
            }

    def _fetch_available_tools(self) -> list:
        """Fetch a summary of available agent tools."""
        try:
            from distr.core.agent.tools import load_tools
            tools = load_tools(
                chat_manager=None, use_navigation_tools=False,
                llm_service=None, tts_service=None,
                llm_model=None, event_queue=None,
                command_queue=None, confirmation_results_dict=None,
            )
            return [
                {"name": t.name, "description": (t.description or "")[:100]}
                for t in tools[:30]
            ]
        except Exception as e:
            logger.debug("_fetch_available_tools failed: %s", e)
            return []

    def _fetch_skills(self) -> list:
        """Fetch available skills from the skills directory."""
        import json
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        registry_file = project_root / "skills" / "skills_registry.json"
        if not registry_file.exists():
            return []
        try:
            registry = json.loads(registry_file.read_text(encoding="utf-8"))
            return [{"id": s.get("id", ""), "name": s.get("name", ""), "description": (s.get("description") or "")[:120]} for s in registry[:20]]
        except Exception:
            return []
        except Exception:
            pass
        return result

    def _fetch_recent_audit(self, settings: dict) -> list:
        """Fetch recent tool audit entries from the current chat."""
        from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
        from distr.core.db import get_session

        chat_id = settings.get("agent_current_chat_id") or settings.get("last_chat_id")
        if not chat_id:
            return []

        with get_session() as session:
            # Find audit workflows for this chat
            audit_workflows = (
                session.query(AutoWorkflow)
                .filter(
                    AutoWorkflow.chat_id == int(chat_id),
                    AutoWorkflow.workflow_type == "audit",
                )
                .order_by(AutoWorkflow.modified_date.desc())
                .limit(3)
                .all()
            )
            result = []
            for w in audit_workflows:
                steps = (
                    session.query(AutoWorkflowStep)
                    .filter(AutoWorkflowStep.workflow_id == w.id)
                    .order_by(AutoWorkflowStep.position.desc())
                    .limit(10)
                    .all()
                )
                for st in steps:
                    result.append({
                        "tool": st.tool_used or st.name or "",
                        "status": st.status,
                        "result": (st.result or "")[:200],
                    })
            return result[:15]
