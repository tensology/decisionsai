"""
Initiative Service — proactive agent behaviour based on initiative level.

Levels:
  observe  — fully passive, no proactive actions
  assist   — suggest next steps in chat when idle (no execution)
  operate  — follow up on stuck work, run approved routines, keep user updated
  own      — manage outcomes end-to-end, only pull user in when boundaries require it

The service runs two timers:
  idle_timer     — fires after 5 min of user inactivity
  schedule_timer — fires every 60 s for periodic checks

On each cycle it assembles context (chat history, ticket boards, workflows, etc.),
asks the LLM to propose ONE action, evaluates it against the policy gate,
and dispatches accordingly.
"""
import json
import hashlib
import logging
import dataclasses
import threading
import time
import uuid
import re
from collections import deque
from typing import Any, Optional
from datetime import datetime, timezone, timedelta

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from distr.core.initiative.context import ContextAssembler
from distr.core.initiative.draft_queue import DraftQueue, DraftEntry
from distr.core.initiative.proposed_action import (
    ProposedAction,
    parse_llm_response,
    serialize,
)
from distr.core.human_engagement import (
    EngagementIntent,
    HumanEngagementService,
    human_project_label,
)

logger = logging.getLogger("distr.core.initiative.service")


def _hash_initiative_payload(action_type: str, payload: dict | None) -> str:
    raw = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{action_type}:{raw}".encode("utf-8")).hexdigest()


def _coerce_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_int_list(value) -> list[int]:
    items: list[int] = []
    if not isinstance(value, (list, tuple, set)):
        return items
    for item in value:
        parsed = _coerce_int(item)
        if parsed is not None:
            items.append(parsed)
    return items


def _derive_initiative_action_context(action: ProposedAction) -> dict[str, Any]:
    payload = action.payload if isinstance(action.payload, dict) else {}
    def _pick_first_nonempty(*values: Any) -> str:
        for value in values:
            candidate = str(value or "").strip()
            if candidate:
                return candidate
        return ""
    raw_goal = _pick_first_nonempty(
        payload.get("goal_hint"),
        payload.get("goal"),
        payload.get("goal_text"),
        payload.get("objective"),
        action.description,
    )
    if raw_goal and len(raw_goal) > 160:
        raw_goal = raw_goal[:157].rstrip() + "..."
    context: dict[str, Any] = {
        "action_type": action.action_type,
        "board_id": _coerce_int(payload.get("board_id")),
        "workflow_id": _coerce_int(payload.get("workflow_id")),
        "project_id": _coerce_int(payload.get("project_id")),
        "ticket_ids": _coerce_int_list(payload.get("ticket_ids")),
        "goal_hint": raw_goal,
        "board_title": str(payload.get("board_title", "") or "").strip(),
        "workflow_title": str(payload.get("workflow_title", "") or "").strip(),
        "project_title": str(payload.get("project_title", "") or "").strip(),
        "ticket_title": str(payload.get("ticket_title", "") or "").strip(),
        "target_lane": str(
            payload.get("target_lane", "") or payload.get("lane", "") or ""
        ).strip(),
    }
    ticket_ids = context.get("ticket_ids") or []
    context["ticket_id"] = ticket_ids[0] if ticket_ids else None

    board_id = context.get("board_id")
    workflow_id = context.get("workflow_id")
    project_id = context.get("project_id")

    if not (context["board_title"] and board_id) and not (context["ticket_title"] and context["ticket_id"]):
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanBoard, KanbanTicket

            with get_session() as session:
                if board_id and not context["board_title"]:
                    board = session.query(KanbanBoard).filter(KanbanBoard.id == board_id).first()
                    if board:
                        context["board_title"] = str(board.name or "").strip()
                if context["ticket_id"] and not context["ticket_title"]:
                    ticket = session.query(KanbanTicket).filter(
                        KanbanTicket.id == context["ticket_id"]
                    ).first()
                    if ticket:
                        context["ticket_title"] = str(ticket.title or "").strip()
        except Exception:
            pass

    if not context["workflow_title"] and workflow_id:
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflow

            with get_session() as session:
                workflow = session.query(AutoWorkflow).filter(
                    AutoWorkflow.id == workflow_id
                ).first()
                if workflow:
                    context["workflow_title"] = str(workflow.name or "").strip()
        except Exception:
            pass

    if not context["project_title"] and project_id:
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project

            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if project:
                    context["project_title"] = str(project.name or "").strip()
        except Exception:
            pass

    return context


def _initiative_context_line(context: dict[str, Any]) -> str:
    action_type = str(context.get("action_type") or "").strip()
    board = context.get("board_title") or (
        f"Board {context['board_id']}" if context.get("board_id") else "a board"
    )
    ticket_id = context.get("ticket_id")
    ticket_title = context.get("ticket_title") or ""
    ticket_count = len(context.get("ticket_ids") or [])

    if action_type == "ticket_lane_move":
        lane = context.get("target_lane") or "Current"
        if ticket_count:
            noun = "ticket" if ticket_count == 1 else "tickets"
            return f"{ticket_count} {noun} on {board}: move to {lane}"
        return f"Board change on {board}: move to {lane}"

    if action_type == "workflow_start":
        workflow = context.get("workflow_title") or f"Workflow {context.get('workflow_id')}"
        subject = f"{ticket_title}" if ticket_title else f"Ticket {ticket_id}"
        return f"Run {workflow} for {subject} on {board}"

    if action_type == "project_cli_task":
        project = context.get("project_title") or f"Project {context.get('project_id')}"
        subject = f"{ticket_title}" if ticket_title else f"Ticket {ticket_id}"
        return f"Run project task on {subject} for {project}"

    return f"Initiative action on {board}"


def _clean_telegram_line(text: str, max_len: int = 260) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    clean = re.sub(r"\bPayload:\s*\{.*$", "", clean).strip()
    clean = clean.replace("[Initiative]", "").replace("[APPROVE]", "").replace("[ESCALATE]", "").strip()
    if len(clean) > max_len:
        clean = clean[: max_len - 3].rstrip() + "..."
    return clean


def _initiative_approval_text(
    action: ProposedAction,
    entry: DraftEntry,
    tier_name: str,
    context: dict[str, Any] | None = None,
) -> str:
    description = _clean_telegram_line(entry.description, 220)
    context = context or _derive_initiative_action_context(action)
    scope = _initiative_context_line(context)
    if entry.action_type == "ticket_lane_move":
        return (
            "I found a board update that needs your approval:\n"
            f"- {scope}\n\n"
            f"Goal: {description}\n\n"
            "Reply approve or reject, or handle it in the app."
        )
    if entry.action_type == "workflow_start":
        return (
            "A workflow is ready to run, but I need your approval first:\n"
            f"- {scope}\n"
            f"- {description}\n\n"
            "Reply approve or reject, or handle it in the app."
        )
    if entry.action_type == "project_cli_task":
        return (
            "A project execution is ready, but I need your approval first:\n"
            f"- {scope}\n"
            f"- {description}\n\n"
            "Reply approve or reject, or handle it in the app."
        )
    return (
        f"I need your approval before I do this ({tier_name}):\n"
        f"- {scope}\n"
        f"- {description}\n\n"
        "Reply approve or reject, or handle it in the app."
    )


def _initiative_update_text(text: str) -> str:
    clean = _clean_telegram_line(text, 360)
    if not clean:
        return ""
    clean = re.sub(r"(?i)^#+\s*quick check-?in\s*[-:]*\s*", "Quick check-in: ", clean).strip()
    if clean.lower().startswith(("i ", "i'", "i’ve", "i'll", "workflow", "ticket", "created", "started", "approved", "rejected")):
        return clean
    if clean.lower().startswith("quick check-in"):
        return clean
    return clean


def _planner_telegram_excerpt(markdown: str, max_len: int = 320) -> str:
    """Keep scheduled planner notifications readable in chat previews."""
    from distr.core.initiative.planners import tts_excerpt_from_markdown

    clean = tts_excerpt_from_markdown(markdown, max_len=max_len)
    clean = re.sub(r"\bHermes\b", "I", clean)
    return clean


def _planner_ready_text(scope_label: str, excerpt: str) -> str:
    if excerpt:
        return f"Your {scope_label} is ready in the app.\n\n{excerpt}"
    return f"Your {scope_label} is ready in the app."


def build_initiative_boundaries(settings: dict) -> dict:
    """Extract policy boundary flags from persisted Initiative settings."""
    return {
        "initiative_allow_telegram": settings.get("initiative_allow_telegram", False),
        "initiative_allow_routine_tasks": settings.get("initiative_allow_routine_tasks", False),
        "initiative_allow_ticket_lane_moves": settings.get("initiative_allow_ticket_lane_moves", False),
        "initiative_allow_workflow_start": settings.get("initiative_allow_workflow_start", False),
        "initiative_allow_project_cli": settings.get("initiative_allow_project_cli", False),
        "initiative_ask_external_comms": settings.get("initiative_ask_external_comms", True),
        "initiative_ask_file_changes": settings.get("initiative_ask_file_changes", True),
        "initiative_ask_sensitive": settings.get("initiative_ask_sensitive", True),
    }


class _InitiativeQtBridge(QObject):
    """Bridge signals so timer mutations always execute on Qt thread."""
    reset_idle_timer_requested = pyqtSignal()


# ---------------------------------------------------------------------------
# LLM model resolution (shared with workflow/planning.py)
# ---------------------------------------------------------------------------

def _litellm_model(provider: str, model: str, settings: dict) -> str:
    """Map provider + model to a litellm model string."""
    p = provider.strip().lower()
    if p == "ollama":
        base = settings.get("ollama_url", "http://localhost:11434").rstrip("/")
        import os
        os.environ.setdefault("OLLAMA_API_BASE", base)
        return f"ollama/{model}" if model else "ollama/llama3.2"
    if p == "openai":
        return model or "gpt-4o-mini"
    if p == "anthropic":
        return model or "claude-3-5-sonnet-20241022"
    if p == "groq":
        return f"groq/{model}" if model else "groq/llama-3.1-70b-versatile"
    if p == "openrouter":
        return f"openrouter/{model}" if model else "openrouter/openai/gpt-4o-mini"
    if p in ("kilocode", "kilo"):
        return model or "kilocode/kilocode"
    if p == "gemini":
        return f"gemini/{model}" if model else "gemini/gemini-2.5-flash"
    if p == "nvidia":
        return f"nvidia/{model}" if model else "nvidia/meta/llama-3.3-70b-instruct"
    # Fallback
    return f"ollama/{model}" if model else "ollama/llama3.2"


# ---------------------------------------------------------------------------
# Initiative Service
# ---------------------------------------------------------------------------

class InitiativeService:
    IDLE_TIMEOUT_MS = 300_000   # 5 minutes
    SCHEDULE_TICK_MS = 60_000   # 1 minute

    def __init__(self, telegram_manager, chat_manager, event_queue=None):
        self.telegram_manager = telegram_manager
        self.chat_manager = chat_manager
        self.event_queue = event_queue
        self._draft_queue = DraftQueue()
        self._context_assembler = ContextAssembler()
        self._idle_timer = QTimer()
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timer_expired)
        self._schedule_timer = QTimer()
        self._schedule_timer.timeout.connect(self._on_schedule_tick)
        self._qt_bridge = _InitiativeQtBridge()
        self._qt_bridge.reset_idle_timer_requested.connect(self._reset_idle_timer_on_qt)
        self._cycle_lock = threading.Lock()
        self._cycle_running = False
        self._stopped = False
        self._started = False
        self._last_cycle_error: Optional[str] = None
        self._last_cycle_at: Optional[float] = None  # Unix timestamp
        self._cycle_count: int = 0
        self._consecutive_cycle_failures: int = 0
        self._last_cycle_success_at: Optional[float] = None
        self._last_cycle_failure_at: Optional[float] = None
        # Proposal dedup: track (action_type, payload_hash, timestamp) so the
        # same action is not repeatedly proposed within cooldown_seconds.
        self._recent_proposals: deque = deque()
        self._proposal_cooldown_s: float = 7_200.0  # 2 hours; approvals should not nag.
        self._execution_notice_cache: dict[str, float] = {}
        self._execution_stale_after_s: float = 900.0
        self._execution_stale_repeat_s: float = 1800.0
        self._execution_idle_max_notice_age_s: float = 1200.0
        self._execution_terminal_notice_window_s: float = 3600.0
        # Run settings migration on init
        try:
            from distr.core.utils import load_settings_from_db
            from distr.core.initiative.policy import migrate_initiative_level
            settings = load_settings_from_db()
            level = settings.get("initiative_level", "assist")
            migrate_initiative_level(level)
        except Exception:
            logger.error("InitiativeService: failed to run settings migration on init", exc_info=True)

        # Pre-load tools for the context assembler (use_navigation_tools=True
        # so the initiative service has full tool context)
        try:
            from distr.core.agent.tools.loader import load_tools as _load_tools
            self._tools = _load_tools(use_navigation_tools=True)
        except Exception:
            logger.debug("InitiativeService: could not pre-load tools, will use defaults")
            self._tools = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopped = False
        from distr.core.signals import signal_manager
        signal_manager.chat_stream_finished.connect(self._reset_idle_timer)
        signal_manager.chat_stream_started.connect(self._on_chat_started)
        self._idle_timer.start(self.IDLE_TIMEOUT_MS)
        self._schedule_timer.start(self.SCHEDULE_TICK_MS)
        logger.debug("InitiativeService: started")

    def stop(self) -> None:
        if not self._started:
            return
        self._stopped = True
        self._started = False
        from distr.core.signals import signal_manager
        try:
            signal_manager.chat_stream_finished.disconnect(self._reset_idle_timer)
        except Exception:
            pass
        try:
            signal_manager.chat_stream_started.disconnect(self._on_chat_started)
        except Exception:
            pass
        self._idle_timer.stop()
        self._schedule_timer.stop()
        logger.debug("InitiativeService: stopped")

    # ------------------------------------------------------------------
    # Draft queue — public API for routes
    # ------------------------------------------------------------------

    def get_pending_drafts(self) -> list:
        """Return all pending draft entries (for the web UI)."""
        self._draft_queue.expire_old()
        return [dataclasses.asdict(e) for e in self._draft_queue.get_all()]

    def approve_draft(self, draft_id: str) -> bool:
        """Run optional ``execute_payload``, then remove the draft entry."""
        from distr.core.initiative.draft_execute import approve_draft_in_queue

        removed = approve_draft_in_queue(self._draft_queue, draft_id)
        if removed:
            logger.info("InitiativeService: draft %s approved and removed", draft_id)
        return removed

    def reject_draft(self, draft_id: str) -> bool:
        """Reject and remove a draft entry."""
        removed = self._draft_queue.remove(draft_id)
        if removed:
            logger.info("InitiativeService: draft %s rejected and removed", draft_id)
        return removed

    def get_status(self) -> dict:
        """Return observable cycle status for the settings UI."""
        now = time.time()
        if self._consecutive_cycle_failures >= 3:
            status = "failing"
        elif self._consecutive_cycle_failures > 0 or self._cycle_running:
            status = "degraded"
        else:
            status = "healthy"
        return {
            "status": status,
            "running": self._cycle_running if hasattr(self, '_cycle_running') else False,
            "cycle_count": self._cycle_count,
            "last_cycle_at": self._last_cycle_at,
            "last_cycle_ago_s": round(now - self._last_cycle_at, 1) if self._last_cycle_at else None,
            "last_error": self._last_cycle_error,
            "consecutive_failures": self._consecutive_cycle_failures,
            "last_success_at": self._last_cycle_success_at,
            "last_failure_at": self._last_cycle_failure_at,
            "last_success_ago_s": round(now - self._last_cycle_success_at, 1) if self._last_cycle_success_at else None,
            "last_failure_ago_s": round(now - self._last_cycle_failure_at, 1) if self._last_cycle_failure_at else None,
        }

    def _record_cycle_success(self) -> None:
        """Reset failure streak after a completed cycle."""
        previous_failures = self._consecutive_cycle_failures
        self._consecutive_cycle_failures = 0
        self._last_cycle_success_at = time.time()
        if previous_failures > 0:
            logger.info(
                "InitiativeService: recovered after %s consecutive cycle failure(s)",
                previous_failures,
            )

    def _record_cycle_failure(self) -> None:
        """Increment streak and log loudly after repeated failures."""
        self._consecutive_cycle_failures += 1
        self._last_cycle_failure_at = time.time()
        if self._consecutive_cycle_failures >= 3:
            logger.error(
                "InitiativeService: %s consecutive cycle failures — last_error=%s",
                self._consecutive_cycle_failures,
                (self._last_cycle_error or "").split("\n")[0][:500],
            )

    # ------------------------------------------------------------------
    # Timer callbacks
    # ------------------------------------------------------------------

    def _reset_idle_timer(self, chat_id: int = 0) -> None:
        # This method can be called from worker threads (initiative cycle),
        # so marshal the timer mutation onto the Qt thread.
        self._qt_bridge.reset_idle_timer_requested.emit()
        logger.debug("InitiativeService: idle timer reset requested (chat_id=%s)", chat_id)

    def _reset_idle_timer_on_qt(self) -> None:
        self._idle_timer.start(self.IDLE_TIMEOUT_MS)
        logger.debug("InitiativeService: idle timer reset (Qt thread)")

    def _on_idle_timer_expired(self) -> None:
        try:
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
        except Exception:
            logger.error("InitiativeService: failed to load settings on idle timer expiry", exc_info=True)
            return
        level = self._get_level(settings)
        # observe: cycle still runs; policy gates proposals (rubric can surface drafts).
        # assist level: run cycle to generate suggestions (SUGGEST_ONLY)
        # operate/own: run full cycle
        with self._cycle_lock:
            if self._cycle_running:
                logger.debug("InitiativeService: cycle already running, skipping idle trigger")
                return
        QTimer.singleShot(0, lambda: self._dispatch_cycle("idle_timer"))

    def _on_schedule_tick(self) -> None:
        if self._stopped:
            return
        try:
            from distr.core.mcp.runtime import tick_mcp_reconnect

            tick_mcp_reconnect()
        except Exception:
            logger.debug("tick_mcp_reconnect skipped", exc_info=True)
        try:
            from distr.core.agent.tools.sidecar_tool_watch import tick_sidecar_tool_availability

            tick_sidecar_tool_availability()
        except Exception:
            logger.debug("tick_sidecar_tool_availability skipped", exc_info=True)
        # R3: DB-backed proactive tasks (Morning Brief, planners, etc.)
        threading.Thread(target=self._maybe_run_proactive_scheduler, daemon=True).start()
        try:
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
        except Exception:
            logger.error("InitiativeService: failed to load settings on schedule tick", exc_info=True)
            return
        threading.Thread(
            target=self._maybe_send_execution_nudges,
            args=(settings,),
            daemon=True,
        ).start()
        try:
            from distr.core.initiative.daily_plan_prompt import maybe_suggest_daily_plan_automation

            maybe_suggest_daily_plan_automation(self, settings)
        except Exception:
            logger.debug("daily plan automation suggestion skipped", exc_info=True)
        try:
            from distr.core.initiative.automation_recommendations import maybe_suggest_automation_from_initiative

            maybe_suggest_automation_from_initiative(self, settings)
        except Exception:
            logger.debug("automation recommendation suggestion skipped", exc_info=True)
        level = self._get_level(settings)
        if level not in ("operate", "own"):
            return
        with self._cycle_lock:
            if self._cycle_running:
                return
        QTimer.singleShot(0, lambda: self._dispatch_cycle("schedule_tick"))

    def _on_chat_started(self, chat_id: int) -> None:
        """Surface pending drafts when a chat stream starts."""
        self._surface_draft_queue(chat_id)

    # ------------------------------------------------------------------
    # Proposal dedup helpers
    # ------------------------------------------------------------------

    def _proposal_key(self, action) -> tuple:
        """Build a dedup key from the action type and a hash of the payload."""
        payload = getattr(action, "payload", None) or {}
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
        return (action.action_type, payload_hash)

    def _prune_expired_proposals(self) -> None:
        """Remove proposals that have exceeded the cooldown window."""
        now = time.time()
        cutoff = now - self._proposal_cooldown_s
        while self._recent_proposals and self._recent_proposals[0][1] < cutoff:
            self._recent_proposals.popleft()

    def _is_duplicate_proposal(self, action) -> bool:
        """Return True if *action* matches any recent proposal."""
        key = self._proposal_key(action)
        for existing_key, _ in self._recent_proposals:
            if existing_key == key:
                return True
        return False

    def _record_proposal(self, action) -> None:
        """Record *action* in the proposal history."""
        key = self._proposal_key(action)
        self._recent_proposals.append((key, time.time()))

    # ------------------------------------------------------------------
    # Cycle dispatch
    # ------------------------------------------------------------------

    def _dispatch_cycle(self, trigger_source: str) -> None:
        if self._stopped:
            return
        with self._cycle_lock:
            if self._cycle_running:
                return
            self._cycle_running = True
        t = threading.Thread(
            target=self._run_initiative_cycle,
            args=(trigger_source,),
            daemon=True,
        )
        t.start()

    @staticmethod
    def _get_level(settings: dict) -> str:
        from distr.core.initiative.policy import migrate_initiative_level
        return migrate_initiative_level(settings.get("initiative_level", "assist"))

    @staticmethod
    def _initiative_boundaries(settings: dict) -> dict:
        return build_initiative_boundaries(settings)

    @staticmethod
    def _proposal_from_work_scan(bundle, settings: dict, level: str) -> ProposedAction | None:
        if level == "observe":
            return None
        scan = getattr(bundle, "work_scan", {}) or {}
        proposals = scan.get("proposals") if isinstance(scan, dict) else []
        if not proposals:
            return None

        enabled = {
            "ticket_lane_move": (
                settings.get("initiative_scan_boards", True)
                and settings.get("initiative_suggest_backlog_promotion", True)
            ),
            "workflow_start": (
                settings.get("initiative_scan_boards", True)
                and settings.get("initiative_allow_workflow_start", False)
            ),
            "project_cli_task": (
                settings.get("initiative_scan_boards", True)
                and settings.get("initiative_allow_project_cli", False)
            ),
            "message_triage": True,
            "board_triage": True,
            "email_triage": True,
        }
        priority = {
            "workflow_start": 0,
            "project_cli_task": 1,
            "ticket_lane_move": 2,
            "message_triage": 3,
            "board_triage": 4,
            "email_triage": 5,
        }
        candidates = [
            p for p in proposals
            if isinstance(p, dict) and enabled.get(p.get("action_type"), True)
        ]
        if not candidates:
            return None
        chosen = sorted(candidates, key=lambda p: priority.get(p.get("action_type"), 99))[0]
        action_type = chosen.get("action_type") or "suggestion"
        payload = chosen.get("payload") if isinstance(chosen.get("payload"), dict) else {}
        description = chosen.get("description") or "Initiative found actionable work."
        if level == "assist" and action_type not in ("message_triage", "board_triage", "email_triage"):
            action_type = "board_triage"
            payload = {**payload, "proposed_action_type": chosen.get("action_type")}
        return ProposedAction(
            action_type=action_type,
            description=description,
            payload=payload,
            draft=chosen.get("draft") or description,
            telegram_message=chosen.get("telegram_message") or description,
        )

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    def _run_initiative_cycle(self, trigger_source: str) -> None:
        logger.debug("InitiativeService: cycle started (trigger=%s)", trigger_source)
        cycle_ok = False
        try:
            self._last_cycle_at = time.time()
            self._cycle_count += 1
            from distr.core.utils import load_settings_from_db
            from distr.core.initiative.policy import evaluate, migrate_initiative_level

            try:
                settings = load_settings_from_db()
            except Exception:
                logger.error("InitiativeService: load_settings_from_db failed", exc_info=True)
                self._last_cycle_error = "load_settings_from_db failed"
                return

            level = migrate_initiative_level(settings.get("initiative_level", "assist"))

            # Expire old drafts
            self._draft_queue.expire_old()

            # Assemble context
            bundle = self._context_assembler.build(settings)

            action = self._proposal_from_work_scan(bundle, settings, level)
            if action is None:
                # Call LLM
                try:
                    raw = self._call_llm(bundle, settings, level)
                except RuntimeError as e:
                    logger.error("InitiativeService: %s", e)
                    self._last_cycle_error = str(e)
                    self._reset_idle_timer()
                    return
                except Exception as e:
                    logger.error("InitiativeService: LLM call failed — %s: %s", type(e).__name__, e)
                    self._last_cycle_error = f"{type(e).__name__}: {e}"
                    self._reset_idle_timer()
                    return

                # Parse action
                action = parse_llm_response(raw)
            logger.info("InitiativeService: proposed action_type=%s description=%s",
                        action.action_type, action.description)

            if action.action_type == "none":
                self._last_cycle_error = None
                cycle_ok = True
                return

            # Evaluate policy
            boundaries = build_initiative_boundaries(settings)
            # Evaluate policy — inject duplicate_recent flag if the proposal
            # matches a recent one within the cooldown window.
            policy_context: dict[str, Any] = {}
            self._prune_expired_proposals()
            if self._is_duplicate_proposal(action):
                logger.info(
                    "InitiativeService: duplicate proposal detected (type=%s, desc=%s) — marking as duplicate_recent",
                    action.action_type, action.description,
                )
                policy_context["duplicate_recent"] = True
            else:
                self._record_proposal(action)

            decision = evaluate(action, level, boundaries, policy_context=policy_context)
            logger.info("InitiativeService: policy decision=%s for action_type=%s",
                        decision, action.action_type)

            self._dispatch_action(action, settings, decision, boundaries)
            self._last_cycle_error = None  # clear previous error on success
            cycle_ok = True

        except Exception:
            import traceback
            self._last_cycle_error = traceback.format_exc()
            logger.error("InitiativeService: unhandled exception in cycle", exc_info=True)
        finally:
            if cycle_ok:
                self._record_cycle_success()
            else:
                self._record_cycle_failure()
            with self._cycle_lock:
                self._cycle_running = False
            logger.debug("InitiativeService: cycle finished (trigger=%s)", trigger_source)

    # ------------------------------------------------------------------
    # Proactive scheduler (R3)
    # ------------------------------------------------------------------

    def _maybe_run_proactive_scheduler(self) -> None:
        """Run due proactive tasks when no initiative cycle holds the lock."""
        if self._stopped:
            return
        with self._cycle_lock:
            if self._cycle_running:
                return
            self._cycle_running = True
        try:
            self._run_proactive_scheduler_cycle()
        except Exception:
            logger.error("InitiativeService: proactive scheduler failed", exc_info=True)
        finally:
            with self._cycle_lock:
                self._cycle_running = False

    def _run_proactive_scheduler_cycle(self) -> None:
        from distr.core.db import get_session
        from distr.core.initiative.scheduler import (
            default_local_tz,
            iter_due_proactive_tasks,
            mark_proactive_task_run,
        )
        from distr.core.initiative.tiers import PermissionTier
        from distr.core.utils import load_settings_from_db

        try:
            settings = load_settings_from_db()
        except Exception:
            logger.error("InitiativeService: proactive cycle could not load settings", exc_info=True)
            return

        boundaries = build_initiative_boundaries(settings)

        snapshots: list[dict] = []
        try:
            with get_session() as session:
                local_tz = default_local_tz()
                now_utc = datetime.now(timezone.utc)
                for row in iter_due_proactive_tasks(session, now_utc=now_utc, local_tz=local_tz):
                    snapshots.append(
                        {
                            "id": row.id,
                            "name": row.name,
                            "instruction": (row.instruction or "").strip(),
                            "tier": row.tier,
                        }
                    )
        except Exception:
            logger.error("InitiativeService: proactive due query failed", exc_info=True)
            return

        if not snapshots:
            return

        for snap in snapshots:
            tid = snap["id"]
            name = snap["name"]
            instruction = snap["instruction"] or name
            tier_val = max(0, min(3, int(snap["tier"])))
            tier = PermissionTier(tier_val)

            if str(name).strip().lower() == "memory distillation":
                try:
                    from distr.core.memory.distiller import run_memory_distillation

                    outcome = run_memory_distillation()
                    logger.info(
                        "InitiativeService: Memory Distillation task id=%s ok=%s skipped=%s reason=%s",
                        tid,
                        outcome.ok,
                        outcome.skipped,
                        outcome.reason,
                    )
                except Exception:
                    logger.error(
                        "InitiativeService: Memory Distillation task id=%s failed",
                        tid,
                        exc_info=True,
                    )
                try:
                    with get_session() as session:
                        mark_proactive_task_run(session, tid)
                except Exception:
                    logger.error(
                        "InitiativeService: could not mark proactive task id=%s run",
                        tid,
                        exc_info=True,
                    )
                continue

            from distr.core.initiative.planners import planner_scope_for_task_name

            planner_scope = planner_scope_for_task_name(name)
            if planner_scope:
                logger.info(
                    "InitiativeService: skipping legacy planner task %r — use Automations preset instead",
                    name,
                )
                try:
                    with get_session() as session:
                        mark_proactive_task_run(session, tid)
                except Exception:
                    logger.error(
                        "InitiativeService: could not mark proactive task id=%s run",
                        tid,
                        exc_info=True,
                    )
                continue

            action = ProposedAction(
                action_type="suggestion",
                description=f"[{name}] {instruction}",
                payload={
                    "source": "proactive",
                    "task_name": name,
                    "proactive_task_id": tid,
                },
                telegram_message=f"Proactive — {name}",
            )
            try:
                self._dispatch_proactive_instruction(action, settings, boundaries, tier=tier)
            except Exception:
                logger.error(
                    "InitiativeService: proactive task id=%s failed", tid, exc_info=True
                )
            try:
                with get_session() as session:
                    mark_proactive_task_run(session, tid)
            except Exception:
                logger.error(
                    "InitiativeService: could not mark proactive task id=%s run", tid, exc_info=True
                )

    def _run_planner_proactive_task(
        self,
        *,
        scope: str,
        task_name: str,
        task_id: int,
        instruction: str,
        settings: dict,
        tier,
    ) -> None:
        """R4: LLM markdown planner, DB row, chat, Telegram, optional TTS."""
        from distr.core.db import get_session
        from distr.core.db.planner_output import save_planner_row
        from distr.core.initiative.planners import generate_planner_markdown, tts_excerpt_from_markdown
        from distr.core.initiative.tiers import PermissionTier
        from distr.core.signals import signal_manager

        if tier == PermissionTier.SILENT:
            logger.info(
                "InitiativeService: planner suppressed (SILENT tier): %s",
                task_name,
            )
            return

        bundle = self._context_assembler.build(settings)
        triage_summary = ""
        triage_candidate_count = 0
        triage_markdown = ""
        if scope == "morning":
            try:
                from distr.core.orchestrator import emit_event
                from distr.core.orchestrator_daily_triage import enqueue_triage_candidates, format_triage_markdown

                triage = (
                    bundle.work_scan.get("orchestrator_triage")
                    if isinstance(bundle.work_scan, dict)
                    else {}
                )
                candidates = triage.get("candidates") if isinstance(triage, dict) else []
                triage_summary = str(triage.get("summary") or "") if isinstance(triage, dict) else ""
                triage_candidate_count = len(candidates) if isinstance(candidates, list) else 0
                if isinstance(candidates, list):
                    added = enqueue_triage_candidates(self._draft_queue, candidates, limit=6)
                    triage_markdown = format_triage_markdown(triage, max_candidates=6) if isinstance(triage, dict) else ""
                    emit_event(
                        source="orchestrator",
                        event_type="daily_triage_generated",
                        status="ready",
                        summary=triage.get("summary") if isinstance(triage, dict) else "",
                        payload={
                            "candidate_count": len(candidates),
                            "drafts_added": added,
                            "triage": triage,
                        },
                    )
            except Exception:
                logger.warning("InitiativeService: Hermes daily triage enqueue failed", exc_info=True)
        markdown, date_info = generate_planner_markdown(
            scope, settings, bundle, instruction
        )
        with get_session() as session:
            save_planner_row(
                session,
                scope=scope,
                date_info=date_info,
                content=markdown,
                proactive_task_id=task_id,
            )

        scope_label = "morning brief" if scope == "morning" else f"{scope} planner"
        header = f"[Planner — {task_name}]"
        chat_body = f"{header}\n\n{markdown.strip()}"
        self._log_planner_to_chat(chat_body)

        excerpt = tts_excerpt_from_markdown(markdown, max_len=700)
        telegram_excerpt = _planner_telegram_excerpt(markdown)
        if scope == "morning":
            if triage_candidate_count:
                tg_body = (
                    f"{triage_markdown or excerpt or triage_summary}\n\n"
                    "Reply approve 1, reject 1, approve all, or tell me what to turn into tickets."
                )
            else:
                tg_body = telegram_excerpt or triage_summary or "Nothing needs your review right now."
        else:
            tg_body = _planner_ready_text(scope_label, telegram_excerpt)
        self._send_telegram_if_allowed(tg_body, settings)

        if settings.get("chat_voice_enabled", True):
            if excerpt:
                try:
                    signal_manager.speak_text_directly.emit(excerpt)
                except Exception as e:
                    logger.debug("InitiativeService: planner TTS emit failed: %s", e)

    def _log_planner_to_chat(self, message: str) -> None:
        """Append planner markdown without the generic [Initiative] suggestion prefix."""
        if not self.chat_manager:
            return
        try:
            current_chat = self.chat_manager.get_current_chat()
            if current_chat:
                self.chat_manager.add_assistant_message(current_chat, message)
        except Exception as e:
            logger.debug("InitiativeService: _log_planner_to_chat failed: %s", e)

    def _dispatch_proactive_instruction(self, action, settings: dict, boundaries: dict, tier) -> None:
        """
        Deliver a scheduled proactive instruction without LLM policy SKIP on observe/assist.

        Uses the task's permission tier (R2); APPROVE+ queues a draft for confirmation.
        """
        from distr.core.initiative.tiers import PermissionTier

        if tier.value >= PermissionTier.APPROVE.value:
            action.requires_confirmation = True
            self._draft_and_ask(action, settings, tier=tier)
            return
        self._execute_action(action, settings, tier=tier)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, bundle, settings: dict, level: str) -> str:
        from distr.core.litellm_utils import litellm_completion

        try:
            import litellm
        except ImportError:
            raise RuntimeError("InitiativeService: litellm is not installed")

        # Collect candidate providers in priority order (workflow → conversational → agent → ollama).
        from distr.core.llm_factory import resolve_llm_candidates

        candidates = resolve_llm_candidates(settings)

        system_prompt = self._build_system_prompt(settings, bundle, level)
        user_prompt = json.dumps({
            "chat_history": bundle.chat_history,
            "active_project": bundle.active_project,
            "kanban_summary": bundle.kanban_summary,
            "stuck_tasks": bundle.stuck_tasks,
            "unfinished_workflows": bundle.unfinished_workflows,
            "available_tools": bundle.available_tools[:15],
            "skills": bundle.skills[:10],
            "recent_audit": bundle.recent_audit[:10],
            "developer_context": bundle.developer_context,
            "work_scan": bundle.work_scan,
            "memory_files_trimmed": {
                "has_agent": bool(bundle.memory_agent),
                "has_user": bool(bundle.memory_user),
                "has_long_term": bool(bundle.memory_long_term),
            },
        }, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        failure_reasons: list[tuple[str, str, str]] = []  # (provider, model, short_reason)
        for provider, model in candidates:
            litellm_model = _litellm_model(provider, model, settings)
            try:
                response = litellm_completion(
                    model=litellm_model,
                    messages=messages,
                    max_tokens=512,
                    temperature=0.4,
                )
                return response.choices[0].message.content
            except litellm.AuthenticationError as e:
                short = str(e).split("\n")[0][:120]
                failure_reasons.append((provider, model, f"AUTH: {short}"))
                logger.warning(
                    "InitiativeService: auth error for %s/%s, trying next provider",
                    provider, model,
                )
                continue
            except Exception as e:
                short = f"{type(e).__name__}: {str(e).split(chr(10))[0][:120]}"
                failure_reasons.append((provider, model, short))
                logger.warning(
                    "InitiativeService: LLM call failed for %s/%s, trying next provider",
                    provider, model,
                )
                continue

        # All providers exhausted — raise a clean, informative error
        summary_lines = ", ".join(
            f"{p}/{m}: {r}" for p, m, r in failure_reasons
        )
        raise RuntimeError(
            f"InitiativeService: all LLM providers failed. Tried: [{summary_lines}]"
        )

    @staticmethod
    def _build_system_prompt(settings: dict, bundle, level: str) -> str:
        boundary_info = json.dumps({
            k: v for k, v in settings.items() if k.startswith("initiative_")
        })

        if level == "observe":
            role_instruction = (
                "Your initiative level is OBSERVE: do not propose autonomous execution. "
                "Prefer action_type 'none' unless something clearly warrants user review — "
                "then include a rubric so important items can become pending approvals."
            )
        elif level == "assist":
            role_instruction = (
                "Your role is to SUGGEST helpful next steps based on the context. "
                "You should NOT propose executing anything — only surface observations "
                "and recommendations the user might find useful. "
                "Prefer action_type 'suggestion' or 'none'. "
                "Include a rubric (see below) so higher-impact items can be queued for approval."
            )
        elif level == "operate":
            role_instruction = (
                "Your role is to follow up on stuck work, run approved routine tasks, "
                "and keep the user updated. You can propose executing routine tasks "
                "(ticket board creation, workflow runs) and sending suggestions. "
                "For external communications, file changes, or sensitive actions, "
                "check the boundary settings — if 'ask' is enabled, use those action types "
                "so the system can request confirmation."
            )
        else:  # own
            role_instruction = (
                "Your role is to manage outcomes end-to-end. You can propose any action type. "
                "The boundary settings determine which actions need confirmation. "
                "Be proactive but respect the boundaries."
            )

        rubric_help = (
            "Also include an optional \"rubric\" object when proposing any non-none action. "
            "Each dimension is an integer 1–5 (omit keys you are unsure about — they default to 3):\n"
            "  impact — 1 negligible … 5 critical\n"
            "  risk — 1 high risk … 5 no/low risk\n"
            "  cost — 1 very costly … 5 negligible cost\n"
            "  urgency — 1 not urgent … 5 immediate\n"
            "  confidence — 1 unlikely … 5 certain\n"
            "Scores sum to a total; totals ≥13 queue for user approval (draft); "
            "≥18 at operate/own allow autonomous execution only when boundaries permit.\n"
        )

        memory_sections: list[str] = []
        if getattr(bundle, "memory_agent", "").strip():
            memory_sections.append(
                "### Cross-chat memory — AGENT.md (trimmed)\n" + bundle.memory_agent.strip()
            )
        if getattr(bundle, "memory_user", "").strip():
            memory_sections.append(
                "### Cross-chat memory — USER.md (trimmed)\n" + bundle.memory_user.strip()
            )
        if getattr(bundle, "memory_long_term", "").strip():
            memory_sections.append(
                "### Cross-chat memory — MEMORY.md (trimmed)\n" + bundle.memory_long_term.strip()
            )
        memory_block = ""
        if memory_sections:
            memory_block = (
                "\nPersistent memory files (cross-chat, not the chat thread):\n"
                + "\n\n".join(memory_sections)
                + "\n\n"
            )

        return (
            f"You are an autonomous agent assistant. Initiative level: {level}.\n"
            f"Current datetime: {bundle.current_datetime}\n"
            f"Boundary settings: {boundary_info}\n\n"
            f"{role_instruction}\n\n"
            f"{memory_block}"
            "Context available: active project, ticket boards and tickets, "
            "workflows (stuck/unfinished), recent tool audit trail, available tools, "
            "available skills, and trimmed AGENT/USER/MEMORY markdown files when present.\n\n"
            f"{rubric_help}"
            "Use work_scan when present. It contains read-only observations and candidate "
            "proposals from boards, workflows, WhatsApp, Telegram, and future email sources. "
            "Based on the context, propose ONE action. "
            "Respond with a JSON object (no markdown fences) with fields:\n"
            "  action_type: suggestion | routine_task | board_triage | ticket_lane_move | "
            "workflow_start | project_cli_task | message_triage | email_triage | "
            "external_comms | file_change | sensitive | none\n"
            "  description: what the action does (string)\n"
            "  rubric: optional object with impact, risk, cost, urgency, confidence (ints 1–5)\n"
            "  payload: optional dict with details (e.g. board_id, ticket_ids, target_lane, workflow_id, project_id)\n"
            "  draft: optional text draft for the action\n"
            "  telegram_message: optional notification text\n"
            "  suggested_tool: OPTIONAL. Only if action_type is suggestion or none. "
            "If the user could resolve this by asking the main assistant to run one tool, set "
            '{"name": "<tool>", "args": {}} using ONLY these names: '
            "create_ticket, pi_agent, terminal_overview, list_workflows, get_workflow, "
            "run_workflow, continue_workflow, cancel_workflow_run, find_skill, push_skill. "
            "Otherwise omit suggested_tool.\n\n"
            "If nothing useful can be done, return {\"action_type\": \"none\"}."
        )


    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    def _dispatch_action(
        self,
        action: ProposedAction,
        settings: dict,
        decision,
        boundaries: dict,
        *,
        tier_override=None,
    ) -> None:
        from distr.core.initiative.policy import PolicyDecision
        from distr.core.initiative.tiers import PermissionTier, effective_permission_tier

        tier = (
            tier_override
            if tier_override is not None
            else effective_permission_tier(action.action_type, boundaries, settings)
        )

        if decision == PolicyDecision.SKIP:
            logger.info("InitiativeService: action skipped (policy=SKIP) action_type=%s",
                        action.action_type)
            return

        if decision == PolicyDecision.SUGGEST_ONLY:
            logger.info("InitiativeService: suggestion: %s", action.description)
            self._deliver_suggestion(action, settings, tier=tier)
            return

        if decision == PolicyDecision.DRAFT_AND_ASK:
            action.requires_confirmation = True
            self._draft_and_ask(action, settings, tier=tier)
            return

        # EXECUTE — permission tier may still require approval (APPROVE / ESCALATE)
        if tier.value >= PermissionTier.APPROVE.value:
            action.requires_confirmation = True
            self._draft_and_ask(action, settings, tier=tier)
            return

        self._execute_action(action, settings, tier=tier)

    def _execute_action(self, action: ProposedAction, settings: dict, tier=None) -> None:
        """Execute an approved action."""
        from distr.core.initiative.tiers import PermissionTier

        allow_routine = settings.get("initiative_allow_routine_tasks", False)

        if action.action_type == "routine_task":
            if not allow_routine:
                logger.info("InitiativeService: routine task blocked by boundary: %s",
                            action.description)
                self._deliver_suggestion(action, settings, tier=tier)
                return

            runner_type = (action.payload or {}).get("runner_type", "")
            if runner_type == "kanban":
                self._dispatch_kanban(action, settings, tier=tier)
            elif runner_type == "workflow":
                self._dispatch_workflow(action, settings, tier=tier)
            else:
                # Default: try ticket board if board_id present, otherwise log as suggestion
                if (action.payload or {}).get("board_id"):
                    self._dispatch_kanban(action, settings, tier=tier)
                else:
                    self._deliver_suggestion(action, settings, tier=tier)
            return

        if action.action_type == "suggestion":
            self._deliver_suggestion(action, settings, tier=tier)
            return

        if action.action_type in ("board_triage", "message_triage", "email_triage"):
            self._deliver_suggestion(action, settings, tier=tier)
            return

        if action.action_type in ("ticket_lane_move", "workflow_start", "project_cli_task"):
            try:
                from distr.core.initiative.action_handlers import execute_initiative_action

                result = execute_initiative_action(
                    action_type=action.action_type,
                    description=action.description,
                    payload=action.payload,
                    draft=action.draft,
                    settings=settings,
                )
                logger.info("InitiativeService: executed %s result=%s", action.action_type, result)
                if tier != PermissionTier.SILENT:
                    message = result.get("message") if isinstance(result, dict) else action.description
                    self._log_to_chat(f"{action.action_type}: {message}", settings)
                    self._send_telegram_if_allowed(
                        action.telegram_message or f"{action.action_type}: {message}",
                        settings,
                    )
            except Exception as e:
                logger.error("InitiativeService: %s failed: %s", action.action_type, e, exc_info=True)
                failed = ProposedAction(
                    action_type="suggestion",
                    description=f"{action.description} could not run: {e}",
                    payload=action.payload,
                    draft=action.draft,
                    telegram_message=f"Initiative action failed: {e}",
                )
                self._deliver_suggestion(failed, settings, tier=tier)
            return

        # external_comms, file_change, sensitive with EXECUTE decision
        logger.info("InitiativeService: auto-executing %s: %s",
                     action.action_type, action.description)
        if tier == PermissionTier.SILENT:
            return
        self._log_to_chat(f"[Auto] {action.action_type}: {action.description}", settings)
        self._send_telegram_if_allowed(
            action.telegram_message or f"{action.action_type}: {action.description}",
            settings,
        )

    # ------------------------------------------------------------------
    # Delivery helpers
    # ------------------------------------------------------------------

    def _deliver_suggestion(
        self, action: ProposedAction, settings: dict, tier=None
    ) -> None:
        """Deliver a suggestion via chat and optionally Telegram."""
        from distr.core.initiative.tiers import PermissionTier

        if tier == PermissionTier.SILENT:
            logger.info(
                "InitiativeService: suggestion suppressed (SILENT tier): %s",
                action.description,
            )
            return
        msg = action.description
        if action.suggested_tool and isinstance(action.suggested_tool, dict):
            tn = action.suggested_tool.get("name", "")
            if tn:
                msg = f"{msg} (You can ask me to use the {tn} tool for this.)"
        self._log_to_chat(f"Suggestion: {msg}", settings)
        if action.action_type in ("board_triage", "message_triage", "email_triage") and not settings.get("initiative_telegram_notify_suggestions", False):
            logger.info(
                "InitiativeService: suggestion kept in app, not Telegram (%s): %s",
                action.action_type,
                action.description,
            )
            return
        self._send_telegram_if_allowed(
            action.telegram_message or f"Suggestion: {msg}",
            settings,
        )

    def _dispatch_kanban(
        self, action: ProposedAction, settings: dict, tier=None
    ) -> None:
        """Create a ticket from the proposed action."""
        payload = action.payload or {}
        board_id = payload.get("board_id")
        lane_name = payload.get("lane", "Backlog")
        title = payload.get("title") or action.description
        description = payload.get("description") or action.draft or ""

        if not board_id:
            # Try to find the first available board
            try:
                from distr.core.db.kanban import KanbanBoard
                from distr.core.db import get_session
                with get_session() as session:
                    board = session.query(KanbanBoard).first()
                    if board:
                        board_id = board.id
            except Exception:
                pass

        if not board_id:
            logger.warning("InitiativeService: no board_id for ticket board dispatch, falling back to suggestion")
            self._deliver_suggestion(action, settings, tier=tier)
            return

        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanLane, KanbanTicket
            with get_session() as session:
                lane = (
                    session.query(KanbanLane)
                    .filter(KanbanLane.board_id == int(board_id),
                            KanbanLane.name.ilike(f"%{lane_name}%"))
                    .first()
                )
                if not lane:
                    lane = (session.query(KanbanLane)
                            .filter(KanbanLane.board_id == int(board_id))
                            .order_by(KanbanLane.position)
                            .first())
                if lane:
                    max_pos = max((t.position for t in lane.tickets), default=-1)
                    ticket = KanbanTicket(
                        lane_id=lane.id,
                        title=title[:200],
                        description=description[:2000],
                        position=max_pos + 1,
                    )
                    session.add(ticket)
                    session.commit()
                    logger.info("InitiativeService: created ticket '%s' in lane '%s' (board %s)",
                                title[:50], lane.name, board_id)
                    from distr.core.initiative.tiers import PermissionTier

                    if tier != PermissionTier.SILENT:
                        self._log_to_chat(f"Created ticket: {title[:100]}", settings)
                        self._send_telegram_if_allowed(
                            action.telegram_message or f"Created ticket: {title[:100]}",
                            settings,
                        )
                    return
        except Exception:
            logger.error("InitiativeService: ticket creation failed", exc_info=True)

        self._deliver_suggestion(action, settings, tier=tier)

    def _dispatch_workflow(
        self, action: ProposedAction, settings: dict, tier=None
    ) -> None:
        """Trigger a workflow run from the proposed action."""
        payload = action.payload or {}
        workflow_id = payload.get("workflow_id")
        if not workflow_id:
            logger.warning("InitiativeService: no workflow_id for workflow dispatch")
            self._deliver_suggestion(action, settings, tier=tier)
            return

        try:
            from distr.core.initiative.tiers import PermissionTier
            from distr.core.workflow.dispatcher import start_workflow_run

            start_workflow_run(int(workflow_id))
            logger.info("InitiativeService: triggered workflow %s: %s",
                        workflow_id, action.description)
            if tier != PermissionTier.SILENT:
                self._log_to_chat(f"Started workflow #{workflow_id}: {action.description}", settings)
                self._send_telegram_if_allowed(
                    action.telegram_message or f"Started workflow: {action.description}",
                    settings,
                )
        except Exception:
            logger.error("InitiativeService: workflow dispatch failed", exc_info=True)
            self._deliver_suggestion(action, settings, tier=tier)

    # ------------------------------------------------------------------
    # Draft queue
    # ------------------------------------------------------------------

    def _surface_draft_queue(self, chat_id: int) -> None:
        """Surface pending drafts as chat messages when a chat starts."""
        from distr.core.initiative.tiers import PermissionTier

        self._draft_queue.expire_old()
        drafts = self._draft_queue.get_all()
        if not drafts:
            return
        logger.info("InitiativeService: surfacing %d pending draft(s) for chat_id=%s",
                     len(drafts), chat_id)
        if not self.chat_manager:
            return
        current_chat = self.chat_manager.get_current_chat()
        if not current_chat:
            return
        if not self._chat_is_initiative_approval_context(current_chat):
            logger.debug(
                "InitiativeService: not surfacing %d pending draft(s) into unrelated chat_id=%s",
                len(drafts),
                current_chat,
            )
            return
        for draft in drafts:
            tier_label = PermissionTier(draft.permission_tier).name
            msg = (
                f"Pending approval: {_clean_telegram_line(draft.description, 260)}\n\n"
                f"ID: `{draft.id[:8]}`\n"
                "Say approve or reject, or manage it in Settings → Initiative."
            )
            self.chat_manager.add_assistant_message(current_chat, msg)

    def _chat_is_initiative_approval_context(self, chat_id: int) -> bool:
        """Return true only when surfacing Initiative approvals fits the active chat."""
        chat_manager = self.chat_manager
        if not chat_manager:
            return False

        text_parts: list[str] = []
        try:
            getter = getattr(chat_manager, "get_chat_title", None)
            if callable(getter):
                text_parts.append(str(getter(chat_id) or ""))
        except Exception:
            pass
        try:
            history_getter = getattr(chat_manager, "get_chat_history", None)
            if callable(history_getter):
                history = history_getter(chat_id) or []
                for item in list(history)[-6:]:
                    if isinstance(item, dict):
                        role = str(item.get("role") or item.get("sender") or "").lower()
                        if role and role not in {"user", "human"}:
                            continue
                        text_parts.append(str(item.get("content") or item.get("message") or item.get("text") or ""))
                    else:
                        role = str(getattr(item, "role", "") or getattr(item, "sender", "")).lower()
                        if role and role not in {"user", "human"}:
                            continue
                        text_parts.append(str(getattr(item, "content", "") or getattr(item, "message", "") or getattr(item, "text", "") or ""))
        except Exception:
            pass

        combined = " ".join(part for part in text_parts if part).lower()
        if not combined:
            return False
        explicit_phrases = (
            "initiative",
            "pending approval",
            "pending approvals",
            "pending action",
            "pending actions",
            "approval queue",
            "what needs approval",
            "show approvals",
            "show pending",
            "orchestrator triage",
            "orchestrator decisions",
            "hermes triage",
            "hermes decisions",
            "standup decisions",
        )
        return any(phrase in combined for phrase in explicit_phrases)

    def _draft_and_ask(
        self, action: ProposedAction, settings: dict, tier=None
    ) -> None:
        """Queue an action for user approval."""
        from distr.core.initiative.tiers import PermissionTier

        resolved_tier = tier if tier is not None else PermissionTier.APPROVE
        action_hash = _hash_initiative_payload(action.action_type, action.payload)
        for existing in self._draft_queue.get_all():
            existing_payload = existing.execute_payload or {}
            existing_action = (
                existing_payload.get("action")
                if isinstance(existing_payload, dict)
                else None
            )
            existing_hash = ""
            if isinstance(existing_action, dict):
                existing_hash = _hash_initiative_payload(
                    existing_action.get("action_type") or existing.action_type,
                    existing_action.get("payload") or {},
                )
            elif existing.action_type == action.action_type and existing.description == action.description:
                existing_hash = action_hash
            if existing_hash == action_hash:
                logger.info(
                    "InitiativeService: duplicate pending draft suppressed (%s): %s",
                    existing.id,
                    action.description,
                )
                return
        now = datetime.now(tz=timezone.utc)
        reason = f"Confirmation required ({resolved_tier.name}) for {action.action_type}"
        if resolved_tier.value >= PermissionTier.ESCALATE.value:
            reason += " — explicit approval required (ESCALATE)."

        entry = DraftEntry(
            id=str(uuid.uuid4()),
            action_type=action.action_type,
            description=action.description,
            draft=action.draft or action.description,
            reason=reason,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=48)).isoformat(),
            permission_tier=int(resolved_tier),
            execute_payload=(
                {
                    "kind": "initiative_action",
                    "action": serialize(action),
                }
                if action.action_type in (
                    "ticket_lane_move",
                    "workflow_start",
                    "project_cli_task",
                )
                else {
                    "kind": "automation_preset_install",
                    "preset_id": str((action.payload or {}).get("preset_id") or "").strip(),
                    "source": "initiative",
                }
                if action.action_type == "automation_recommendation"
                else None
            ),
        )
        self._draft_queue.add(entry)
        logger.info("InitiativeService: draft queued %s: %s", entry.id, entry.description)

        # Notify via Telegram if possible
        action_context = _derive_initiative_action_context(action)
        allow_telegram = settings.get("initiative_allow_telegram", False)
        uid = getattr(self.telegram_manager, "telegram_user_id", None)
        if allow_telegram and uid and uid > 0:
            msg = _initiative_approval_text(
                action,
                entry,
                resolved_tier.name,
                context=action_context,
            )
            self._send_telegram_if_allowed(
                msg,
                settings,
                kind="approval_request",
                subject_type="initiative_action",
                subject_id=entry.id,
                state_fingerprint=f"{entry.id}:{action_context.get('action_type', '')}",
                requires_response=True,
                priority="high",
                initiative_context=action_context,
            )

        # Also log to chat
        self._log_to_chat(
            f"Pending approval [{resolved_tier.name}] — {action.action_type}: {action.description}",
            settings,
        )

    # ------------------------------------------------------------------
    # Messaging helpers
    # ------------------------------------------------------------------

    def _log_to_chat(self, message: str, settings: dict) -> None:
        if not self.chat_manager:
            return
        try:
            current_chat = self.chat_manager.get_current_chat()
            if current_chat:
                self.chat_manager.add_assistant_message(
                    current_chat, _initiative_update_text(message)
                )
        except Exception as e:
            logger.debug("InitiativeService: _log_to_chat failed: %s", e)

    def _send_telegram_if_allowed(
        self,
        text: str,
        settings: dict,
        *,
        kind: str = "initiative_update",
        subject_type: str = "initiative",
        subject_id: str = "global",
        state_fingerprint: str | None = None,
        requires_response: bool = False,
        priority: str = "normal",
        allow_voice: bool | None = None,
        voice_body: str | None = None,
        initiative_context: dict[str, Any] | None = None,
    ) -> None:
        text = _initiative_update_text(text)
        if not text:
            return
        if allow_voice is None:
            allow_voice = kind not in {"idle_nudge", "workflow_idle_nudge"}
        if kind not in {"initiative_suggestion"} and not requires_response:
            try:
                from distr.core.engagement_gates import proactive_delivery_blocked

                blocked, reason = proactive_delivery_blocked(
                    delivery_kind=kind,
                    body=text,
                    manual=False,
                )
                if blocked:
                    logger.info(
                        "InitiativeService: suppressed %s delivery (%s): %s",
                        kind,
                        reason,
                        text[:120],
                    )
                    return
            except Exception:
                logger.debug("engagement gate check failed", exc_info=True)
        allow_telegram = settings.get("initiative_allow_telegram", False)
        service = HumanEngagementService(
            telegram_manager=self.telegram_manager,
            allow_telegram=allow_telegram,
        )
        decision = service.decide(EngagementIntent(
            source="initiative",
            surface="proactive",
            kind=kind,
            priority=priority,
            subject_type=subject_type,
            subject_id=str(subject_id),
            state_fingerprint=state_fingerprint or text,
            body=text,
            voice_body=(voice_body or text) if allow_voice else None,
            allow_voice=bool(allow_voice),
            requires_response=requires_response,
            workflow_id=(initiative_context or {}).get("workflow_id"),
            run_id=(initiative_context or {}).get("run_id"),
            step_id=(initiative_context or {}).get("step_id"),
            project_id=(initiative_context or {}).get("project_id"),
            execution_session_id=(initiative_context or {}).get("execution_session_id"),
        ))
        if not decision.should_send:
            return
        outbound_text = decision.final_text or decision.final_voice_text or text
        self._record_notification_route(decision.channel, decision.route_reason, outbound_text)
        event_queue = getattr(self, "event_queue", None)
        event_context = initiative_context or {}
        thread_hint = None
        if self.telegram_manager is not None:
            telegram_user_id = getattr(self.telegram_manager, "telegram_user_id", None)
            if telegram_user_id:
                try:
                    thread_hint = str(int(telegram_user_id))
                except (TypeError, ValueError):
                    thread_hint = str(telegram_user_id)
        base_event_data = {
            "provider": "tool",
            "thread_id": thread_hint,
            "skip_screenshot": True,
            "explicit_artifact_intent": False,
            "requires_response": bool(requires_response),
            "input_type": "voice" if decision.format == "voice" else "text",
            "engagement_source": "initiative",
            "engagement_kind": kind,
            "engagement_subject_type": subject_type,
            "engagement_subject_id": str(subject_id),
            "engagement_priority": priority,
            "engagement_ticket_title": str(event_context.get("ticket_title") or ""),
            "engagement_workflow_title": str(event_context.get("workflow_title") or ""),
            "engagement_step_title": str(event_context.get("step_title") or ""),
            "engagement_goal_hint": str(event_context.get("goal_hint") or ""),
            "workflow_id": event_context.get("workflow_id"),
            "run_id": event_context.get("run_id"),
            "step_id": event_context.get("step_id"),
            "ticket_id": event_context.get("ticket_id"),
            "board_id": event_context.get("board_id"),
            "project_id": event_context.get("project_id"),
            "state_fingerprint": str(
                state_fingerprint
                or event_context.get("state_fingerprint")
                or event_context.get("ticket_id")
                or str(subject_id)
                or "initiative"
            ),
            "ticket_title": str(event_context.get("ticket_title") or ""),
            "workflow_title": str(event_context.get("workflow_title") or ""),
            "step_title": str(event_context.get("step_title") or ""),
            "execution_session_id": event_context.get("execution_session_id"),
        }
        try:
            if decision.channel == "desktop":
                from distr.core.signals import signal_manager

                signal_manager.speak_text_directly.emit(outbound_text)
                logger.info("InitiativeService: sent desktop notification (%s): %s", decision.route_reason, outbound_text[:100])
                return
            if decision.channel == "remote":
                if self._send_remote_notification(outbound_text):
                    logger.info("InitiativeService: sent remote notification (%s): %s", decision.route_reason, outbound_text[:100])
                    return
                if not allow_telegram:
                    return
            if decision.channel == "telegram":
                if event_queue is not None:
                    try:
                        payload = {
                            "text": outbound_text,
                            "is_done": False,
                            **base_event_data,
                        }
                        event_queue.put(("send_to_telegram", payload), block=False)
                        logger.info(
                            "InitiativeService: queued Telegram notification (%s): %s",
                            decision.route_reason,
                            outbound_text[:100],
                        )
                        return
                    except Exception:
                        logger.debug("InitiativeService: could not queue Telegram notification", exc_info=True)

                if self.telegram_manager is not None:
                    try:
                        from distr.core.human_engagement import record_remote_reply_context

                        record_remote_reply_context(
                            platform="telegram",
                            channel="telegram",
                            workflow_id=base_event_data.get("workflow_id"),
                            run_id=base_event_data.get("run_id"),
                            step_id=base_event_data.get("step_id"),
                            ticket_id=base_event_data.get("ticket_id"),
                            board_id=base_event_data.get("board_id"),
                            project_id=base_event_data.get("project_id"),
                            execution_session_id=base_event_data.get("execution_session_id"),
                            ticket_title=base_event_data.get("engagement_ticket_title", ""),
                            workflow_title=base_event_data.get("engagement_workflow_title", ""),
                            step_title=base_event_data.get("engagement_step_title", ""),
                            state_fingerprint=state_fingerprint
                            or base_event_data.get("state_fingerprint")
                            or "",
                            outbound_text=outbound_text,
                            metadata={
                                "thread_id": thread_hint,
                                "engagement_source": base_event_data.get("engagement_source"),
                                "engagement_kind": base_event_data.get("engagement_kind"),
                                "requires_response": bool(base_event_data.get("requires_response")),
                                "engagement_goal_hint": base_event_data.get("engagement_goal_hint"),
                                "input_type": base_event_data.get("input_type"),
                                "provider": base_event_data.get("provider"),
                            },
                        )
                    except Exception:
                        logger.debug("InitiativeService: fallback remote context record failed", exc_info=True)

                if self.telegram_manager is not None:
                    self.telegram_manager.send_to_telegram(text=outbound_text)
                    logger.info("InitiativeService: sent Telegram notification (%s): %s", decision.route_reason, outbound_text[:100])
        except Exception as e:
            logger.warning("InitiativeService: notification send failed: %s", e)

    def _record_notification_route(self, surface: str, reason: str, text: str) -> None:
        try:
            from distr.core.orchestration_events import emit_orchestration_event

            emit_orchestration_event(
                source="initiative",
                event_type="initiative_notification_routed",
                status="observed",
                summary=f"Initiative notification routed to {surface}.",
                payload={
                    "surface": surface,
                    "reason": reason,
                    "preview": (text or "")[:240],
                },
            )
        except Exception:
            logger.debug("InitiativeService: could not record notification route", exc_info=True)

    def _send_remote_notification(self, text: str) -> bool:
        from distr.core.integrations.telegram.remote_tts_delivery import enqueue_remote_tts_delivery

        return enqueue_remote_tts_delivery(
            text,
            data={
                "mode": "proactive",
                "source_command": "initiative_notification",
                "engagement_source": "initiative",
                "explicit_notification_intent": True,
            },
        )

    def _notice_allowed(self, key: str, *, repeat_after_s: float | None = None) -> bool:
        now = time.time()
        previous = self._execution_notice_cache.get(key)
        if previous is None:
            self._execution_notice_cache[key] = now
            return True
        if repeat_after_s is not None and now - previous >= repeat_after_s:
            self._execution_notice_cache[key] = now
            return True
        return False

    @staticmethod
    def _dt_age_s(value) -> float | None:
        if not value:
            return None
        try:
            if getattr(value, "tzinfo", None) is not None:
                value = value.replace(tzinfo=None)
            return max(0.0, (datetime.utcnow() - value).total_seconds())
        except Exception:
            return None

    @staticmethod
    def _short_label(value: str, max_len: int = 72) -> str:
        clean = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(clean) <= max_len:
            return clean
        return clean[: max_len - 1].rsplit(" ", 1)[0].rstrip() + "…"

    @staticmethod
    def _packet(value) -> dict:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _clean_summary_fragment(value: str, max_len: int = 150) -> str:
        from distr.core.agent.services.llm.text_utils import clean_text_for_tts

        clean = clean_text_for_tts(str(value or ""), spoken_prose=True)
        clean = re.sub(r"\s+", " ", clean).strip().strip(" -:")
        if len(clean) <= max_len:
            return clean
        return clean[: max_len - 1].rsplit(" ", 1)[0].rstrip() + "..."

    def _execution_backend_label(self, row, input_packet: dict, project) -> tuple[str, str]:
        candidates = [
            input_packet.get("source"),
            input_packet.get("surface"),
            input_packet.get("backend_id"),
            input_packet.get("backend"),
            getattr(row, "route_backend", ""),
            getattr(project, "coding_backend", ""),
        ]
        known = {
            "cursor": "Cursor",
            "codex": "Codex",
            "claude": "Claude",
            "claude_code": "Claude",
            "cline": "Cline",
            "opencode": "OpenCode",
            "claude-code": "Claude",
        }
        for candidate in candidates:
            key = re.sub(r"\s+", "_", str(candidate or "").strip().lower())
            if key in known:
                return known[key], key

        route_type = str(getattr(row, "route_type", "") or "").strip().lower()
        if route_type == "ide_bridge" or any(input_packet.get(k) for k in ("cwd", "folder", "chat_id")):
            return "the IDE session", "ide"
        return "the project session", "project"

    def _execution_project_label(self, row, input_packet: dict, project, surface: str) -> str:
        workspace_path = (
            input_packet.get("folder")
            or input_packet.get("cwd")
            or getattr(project, "folder_location", "")
            or getattr(project, "path", "")
            or ""
        )
        raw_name = (
            input_packet.get("project_name")
            or getattr(project, "name", "")
            or (f"project {getattr(row, 'project_id', '')}" if getattr(row, "project_id", None) else "")
        )
        return self._short_label(
            human_project_label(
                raw_name,
                workspace_path=workspace_path,
                surface=surface,
            )
        )

    def _execution_instruction_summary(self, row, input_packet: dict) -> str:
        from distr.core.agent.services.llm.text_utils import spoken_task_summary

        spoken = spoken_task_summary(
            str(input_packet.get("instruction") or ""),
            ticket_title=str(input_packet.get("ticket_title") or input_packet.get("ticket_name") or ""),
            max_len=150,
        )
        if spoken:
            return spoken
        for key in ("prompt", "user_request", "request", "task", "message"):
            summary = self._clean_summary_fragment(input_packet.get(key, ""))
            if summary:
                return summary
        return ""

    def _execution_result_summary(self, row, output_packet: dict) -> str:
        from distr.core.agent.services.llm.text_utils import spoken_result_summary

        for key in ("summary", "result", "message", "final_response", "output"):
            summary = spoken_result_summary(output_packet.get(key, ""), max_len=170)
            if summary:
                return summary
        return spoken_result_summary(getattr(row, "error", ""), max_len=150)

    def _execution_idle_fingerprint(self, row, status: str, input_packet: dict) -> str:
        # Idle means the user-facing state has not changed. updated_at can churn
        # from bookkeeping, so keep it out of this fingerprint.
        started = getattr(row, "started_at", None) or input_packet.get("started_at") or ""
        instruction = self._execution_instruction_summary(row, input_packet)
        instruction_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:12] if instruction else ""
        return f"idle:{status}:{started}:{instruction_hash}"

    def _execution_idle_text(self, backend_label: str, project_name: str, minutes: int, instruction: str) -> str:
        prefix = f"{backend_label} has been quiet on {project_name} for {minutes} minutes."
        if instruction:
            prefix += f" It was working on: {instruction}."
        return f"{prefix} Ask me to check it when you want me to step in."

    def _execution_waiting_text(self, backend_label: str, project_name: str, instruction: str) -> str:
        prefix = f"{backend_label} for {project_name} is waiting for input."
        if instruction:
            prefix += f" It was working on: {instruction}."
        return f"{prefix} Tell me what you want it to do next."

    def _execution_terminal_text(
        self,
        backend_label: str,
        project_name: str,
        status: str,
        instruction: str,
        result: str,
    ) -> str:
        if status == "completed":
            text = f"{backend_label} finished {project_name}."
        else:
            text = f"{backend_label} ran into an issue on {project_name}."
        if instruction:
            text += f" It was working on: {instruction}."
        if result:
            text += f" Result: {result}."
        return text

    def _looks_like_test_runtime_artifact(self, *values: Any) -> bool:
        raw = " ".join(str(value or "") for value in values).lower()
        return any(
            marker in raw
            for marker in (
                "pytest-of-",
                "/pytest-",
                "\\pytest-",
                "pytest-current",
            )
        )

    def _skip_execution_nudge_row(self, row, input_packet: dict, project) -> bool:
        route_type = str(getattr(row, "route_type", "") or "").strip().lower()
        workspace_path = (
            input_packet.get("folder")
            or input_packet.get("cwd")
            or getattr(project, "folder_location", "")
            or getattr(project, "path", "")
            or ""
        )
        if self._looks_like_test_runtime_artifact(
            workspace_path,
            getattr(row, "input_packet", ""),
            getattr(row, "output_packet", ""),
        ):
            return True
        if route_type == "ide_bridge" and project is None:
            return True
        return False

    def _workflow_is_quiet_surface(self, workflow) -> bool:
        marker = self._packet(getattr(workflow, "context_rules", None))
        surface = str(marker.get("decisions_surface") or "").strip().lower()
        workflow_type = str(getattr(workflow, "workflow_type", "") or "").strip().lower()
        return surface == "automation" or workflow_type == "audit"

    def _maybe_send_execution_nudges(self, settings: dict) -> None:
        if not settings.get("initiative_allow_telegram", False):
            return
        uid = getattr(self.telegram_manager, "telegram_user_id", None)
        if not uid or uid <= 0:
            return
        sent_this_tick = 0
        try:
            max_notices = max(1, int(settings.get("initiative_max_notifications_per_tick", 1) or 1))
        except Exception:
            max_notices = 1
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import ProjectExecutionSession
            from distr.core.db.projects import Project
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

            with get_session() as session:
                project_rows = (
                    session.query(ProjectExecutionSession, Project)
                    .outerjoin(Project, Project.id == ProjectExecutionSession.project_id)
                    .order_by(ProjectExecutionSession.updated_at.desc())
                    .limit(40)
                    .all()
                )
                workflow_rows = (
                    session.query(AutoWorkflowRun, AutoWorkflow)
                    .outerjoin(AutoWorkflow, AutoWorkflow.id == AutoWorkflowRun.workflow_id)
                    .order_by(AutoWorkflowRun.started_at.desc())
                    .limit(40)
                    .all()
                )

                for row, project in project_rows:
                    status = (row.status or "").strip().lower()
                    input_packet = self._packet(getattr(row, "input_packet", None))
                    output_packet = self._packet(getattr(row, "output_packet", None))
                    if self._skip_execution_nudge_row(row, input_packet, project):
                        continue
                    backend_label, backend_surface = self._execution_backend_label(row, input_packet, project)
                    project_name = self._execution_project_label(row, input_packet, project, backend_surface)
                    instruction = self._execution_instruction_summary(row, input_packet)
                    if status in {"completed", "failed"}:
                        age_s = self._dt_age_s(row.completed_at or row.updated_at)
                        if age_s is not None and age_s > self._execution_terminal_notice_window_s:
                            continue
                        key = f"project:{row.id}:{status}"
                        if self._notice_allowed(key):
                            result = self._execution_result_summary(row, output_packet)
                            self._send_telegram_if_allowed(
                                self._execution_terminal_text(
                                    backend_label,
                                    project_name,
                                    status,
                                    instruction,
                                    result,
                                ),
                                settings,
                                kind="execution_terminal",
                                subject_type="ide_session",
                                subject_id=str(row.id),
                                state_fingerprint=status,
                            )
                            sent_this_tick += 1
                            if sent_this_tick >= max_notices:
                                return
                    elif status in {"waiting", "needs_input"}:
                        key = f"project:{row.id}:waiting"
                        if self._notice_allowed(key):
                            self._send_telegram_if_allowed(
                                self._execution_waiting_text(backend_label, project_name, instruction),
                                settings,
                                kind="execution_waiting",
                                subject_type="ide_session",
                                subject_id=str(row.id),
                                state_fingerprint=f"waiting:{status}:{instruction}",
                                requires_response=True,
                            )
                            sent_this_tick += 1
                            if sent_this_tick >= max_notices:
                                return
                    elif status in {"queued", "running", "dispatched", "observed"}:
                        age_s = self._dt_age_s(row.updated_at)
                        if age_s is not None and age_s >= self._execution_stale_after_s:
                            max_age_s = float(getattr(self, "_execution_idle_max_notice_age_s", 1200.0))
                            if max_age_s > 0 and age_s > max_age_s:
                                continue
                            minutes = int(age_s // 60)
                            key = f"project:{row.id}:stale"
                            state_fingerprint = self._execution_idle_fingerprint(row, status, input_packet)
                            if self._notice_allowed(key):
                                self._send_telegram_if_allowed(
                                    self._execution_idle_text(backend_label, project_name, minutes, instruction),
                                    settings,
                                    kind="idle_nudge",
                                    subject_type="ide_session",
                                    subject_id=str(row.id),
                                    state_fingerprint=state_fingerprint,
                                    requires_response=True,
                                    allow_voice=False,
                                )
                                sent_this_tick += 1
                                if sent_this_tick >= max_notices:
                                    return

                for run, workflow in workflow_rows:
                    if workflow is not None and self._workflow_is_quiet_surface(workflow):
                        continue
                    status = (run.status or "").strip().lower()
                    workflow_name = self._short_label(getattr(workflow, "name", "") or f"workflow {run.workflow_id}")
                    if status in {"completed", "failed", "cancelled"}:
                        age_s = self._dt_age_s(run.completed_at or run.started_at)
                        if age_s is not None and age_s > self._execution_terminal_notice_window_s:
                            continue
                        key = f"workflow:{run.id}:{status}"
                        if self._notice_allowed(key):
                            if status == "completed":
                                workflow_text = f"{workflow_name} finished successfully."
                            elif status == "cancelled":
                                workflow_text = f"{workflow_name} was cancelled."
                            else:
                                workflow_text = f"{workflow_name} ran into an issue."
                            self._send_telegram_if_allowed(
                                workflow_text,
                                settings,
                                kind="workflow_terminal",
                                subject_type="workflow_run",
                                subject_id=str(run.id),
                                state_fingerprint=status,
                            )
                            sent_this_tick += 1
                            if sent_this_tick >= max_notices:
                                return
                    elif status == "waiting":
                        key = f"workflow:{run.id}:waiting"
                        if self._notice_allowed(key):
                            run_data = self._packet(run.run_data)
                            waiting_kind = str(run_data.get("waiting_kind") or "")
                            step_name = ""
                            if run.current_step_id:
                                try:
                                    from distr.core.db.workflow import AutoWorkflowStep

                                    with get_session() as s:
                                        step = (
                                            s.query(AutoWorkflowStep)
                                            .filter(AutoWorkflowStep.id == int(run.current_step_id))
                                            .first()
                                        )
                                        if step:
                                            step_name = (step.name or "").strip()
                                except Exception:
                                    pass
                            ticket_title = str(run_data.get("ticket_title") or "").strip()
                            from distr.core.kanban.ticket_workflow_engagement import (
                                build_workflow_waiting_nudge,
                            )

                            nudge_text, nudge_voice = build_workflow_waiting_nudge(
                                workflow_name=workflow_name,
                                ticket_title=ticket_title,
                                step_name=step_name,
                                waiting_kind=waiting_kind,
                            )
                            self._send_telegram_if_allowed(
                                nudge_text,
                                settings,
                                kind="workflow_waiting",
                                subject_type="workflow_run",
                                subject_id=str(run.id),
                                state_fingerprint=f"{waiting_kind}:{run.started_at}",
                                requires_response=True,
                                voice_body=nudge_voice,
                            )
                            sent_this_tick += 1
                            if sent_this_tick >= max_notices:
                                return
                    elif status == "running":
                        age_s = self._dt_age_s(run.started_at)
                        if age_s is not None and age_s >= self._execution_stale_after_s:
                            max_age_s = float(getattr(self, "_execution_idle_max_notice_age_s", 1200.0))
                            if max_age_s > 0 and age_s > max_age_s:
                                continue
                            minutes = int(age_s // 60)
                            key = f"workflow:{run.id}:stale"
                            if self._notice_allowed(key):
                                self._send_telegram_if_allowed(
                                    (
                                        f"{workflow_name} has not shown new movement for {minutes} minutes. "
                                        "I can inspect it if you ask."
                                    ),
                                    settings,
                                    kind="workflow_idle_nudge",
                                    subject_type="workflow_run",
                                    subject_id=str(run.id),
                                    state_fingerprint=str(run.started_at),
                                    requires_response=True,
                                    allow_voice=False,
                                )
                                sent_this_tick += 1
                                if sent_this_tick >= max_notices:
                                    return
        except Exception:
            logger.debug("InitiativeService: execution nudge scan failed", exc_info=True)
