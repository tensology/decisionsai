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

On each cycle it assembles context (chat history, kanban, workflows, etc.),
asks the LLM to propose ONE action, evaluates it against the policy gate,
and dispatches accordingly.
"""
import json
import logging
import dataclasses
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from PyQt6.QtCore import QTimer

from distr.core.initiative.context import ContextAssembler
from distr.core.initiative.draft_queue import DraftQueue, DraftEntry

logger = logging.getLogger("distr.core.initiative.service")

VALID_ACTION_TYPES = {"suggestion", "routine_task", "external_comms", "file_change", "sensitive", "none"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProposedAction:
    action_type: str = "none"
    description: str = "No description provided"
    payload: dict = field(default_factory=dict)
    draft: str = ""
    telegram_message: str = ""
    requires_confirmation: bool = False


def serialize(action: ProposedAction) -> dict:
    return dataclasses.asdict(action)


def deserialize(data: dict) -> ProposedAction:
    description = data.get("description", "No description provided")
    if not description:
        description = "No description provided"
    return ProposedAction(
        action_type=data.get("action_type", "none"),
        description=description,
        payload=data.get("payload") or {},
        draft=data.get("draft") or "",
        telegram_message=data.get("telegram_message") or "",
        requires_confirmation=data.get("requires_confirmation", False),
    )


def parse_llm_response(raw: str) -> ProposedAction:
    """Parse a JSON action proposal from the LLM response."""
    text = raw.strip()
    if text.startswith("```json"):
        text = text[len("```json"):]
    elif text.startswith("```"):
        text = text[len("```"):]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("parse_llm_response: failed to parse JSON from LLM response")
        return ProposedAction(action_type="none")

    action_type = data.get("action_type", "none")
    if action_type not in VALID_ACTION_TYPES:
        logger.warning("parse_llm_response: invalid action_type %r, defaulting to 'none'", action_type)
        data["action_type"] = "none"

    return deserialize(data)


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
    # Fallback
    return f"ollama/{model}" if model else "ollama/llama3.2"


# ---------------------------------------------------------------------------
# Initiative Service
# ---------------------------------------------------------------------------

class InitiativeService:
    IDLE_TIMEOUT_MS = 300_000   # 5 minutes
    SCHEDULE_TICK_MS = 60_000   # 1 minute

    def __init__(self, telegram_manager, chat_manager):
        self.telegram_manager = telegram_manager
        self.chat_manager = chat_manager
        self._draft_queue = DraftQueue()
        self._context_assembler = ContextAssembler()
        self._idle_timer = QTimer()
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timer_expired)
        self._schedule_timer = QTimer()
        self._schedule_timer.timeout.connect(self._on_schedule_tick)
        self._cycle_lock = threading.Lock()
        self._cycle_running = False
        self._stopped = False
        self._started = False
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
        """Approve and remove a draft entry."""
        removed = self._draft_queue.remove(draft_id)
        if removed:
            logger.info("InitiativeService: draft %s approved and removed", draft_id)
        return removed

    def reject_draft(self, draft_id: str) -> bool:
        """Reject and remove a draft entry."""
        removed = self._draft_queue.remove(draft_id)
        if removed:
            logger.info("InitiativeService: draft %s rejected and removed", draft_id)
        return removed

    # ------------------------------------------------------------------
    # Timer callbacks
    # ------------------------------------------------------------------

    def _reset_idle_timer(self, chat_id: int = 0) -> None:
        self._idle_timer.start(self.IDLE_TIMEOUT_MS)
        logger.debug("InitiativeService: idle timer reset (chat_id=%s)", chat_id)

    def _on_idle_timer_expired(self) -> None:
        try:
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
        except Exception:
            logger.error("InitiativeService: failed to load settings on idle timer expiry", exc_info=True)
            return
        level = self._get_level(settings)
        if level == "observe":
            logger.debug("InitiativeService: idle timer expired but level=observe, skipping")
            return
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
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
        except Exception:
            logger.error("InitiativeService: failed to load settings on schedule tick", exc_info=True)
            return
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

    # ------------------------------------------------------------------
    # Main cycle
    # ------------------------------------------------------------------

    def _run_initiative_cycle(self, trigger_source: str) -> None:
        logger.debug("InitiativeService: cycle started (trigger=%s)", trigger_source)
        try:
            from distr.core.utils import load_settings_from_db
            from distr.core.initiative.policy import evaluate, migrate_initiative_level, PolicyDecision

            try:
                settings = load_settings_from_db()
            except Exception:
                logger.error("InitiativeService: load_settings_from_db failed", exc_info=True)
                return

            level = migrate_initiative_level(settings.get("initiative_level", "assist"))
            if level == "observe":
                return

            # Expire old drafts
            self._draft_queue.expire_old()

            # Assemble context
            bundle = self._context_assembler.build(settings)

            # Call LLM
            try:
                raw = self._call_llm(bundle, settings, level)
            except RuntimeError as e:
                logger.error("InitiativeService: %s", e)
                self._reset_idle_timer()
                return
            except Exception as e:
                logger.error("InitiativeService: LLM call failed — %s: %s", type(e).__name__, e)
                self._reset_idle_timer()
                return

            # Parse action
            action = parse_llm_response(raw)
            logger.info("InitiativeService: proposed action_type=%s description=%s",
                        action.action_type, action.description)

            if action.action_type == "none":
                return

            # Evaluate policy
            boundaries = {
                "initiative_allow_telegram": settings.get("initiative_allow_telegram", False),
                "initiative_allow_routine_tasks": settings.get("initiative_allow_routine_tasks", False),
                "initiative_ask_external_comms": settings.get("initiative_ask_external_comms", True),
                "initiative_ask_file_changes": settings.get("initiative_ask_file_changes", True),
                "initiative_ask_sensitive": settings.get("initiative_ask_sensitive", True),
            }
            decision = evaluate(action, level, boundaries)
            logger.info("InitiativeService: policy decision=%s for action_type=%s",
                        decision, action.action_type)

            self._dispatch_action(action, settings, decision)

        except Exception:
            logger.error("InitiativeService: unhandled exception in cycle", exc_info=True)
        finally:
            with self._cycle_lock:
                self._cycle_running = False
            logger.debug("InitiativeService: cycle finished (trigger=%s)", trigger_source)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, bundle, settings: dict, level: str) -> str:
        import litellm

        # Collect candidate providers in priority order:
        #   1. conversational_llm_provider/model (preferred for lightweight calls)
        #   2. agent_provider/model (main agent model — may be expensive)
        #   3. ollama fallback (always available locally)
        candidates = []

        conv_provider = (settings.get("conversational_llm_provider") or "").strip().lower()
        conv_model = (settings.get("conversational_llm_model") or "").strip()
        agent_provider = (settings.get("agent_provider") or "").strip().lower()
        agent_model = (settings.get("agent_model") or "").strip()

        if conv_provider and conv_model:
            candidates.append((conv_provider, conv_model))
        if agent_provider and agent_model and (agent_provider, agent_model) != (conv_provider, conv_model):
            candidates.append((agent_provider, agent_model))
        # Always add Ollama as final fallback
        candidates.append(("ollama", "llama3.2"))

        system_prompt = self._build_system_prompt(settings, bundle, level)
        user_prompt = json.dumps({
            "chat_history": bundle.chat_history,
            "active_project": bundle.active_project,
            "kanban_summary": bundle.kanban_summary,
            "stuck_tasks": bundle.stuck_tasks,
            "unfinished_workflows": bundle.unfinished_workflows,
            "available_tools": bundle.available_tools[:15],
            "snippets": bundle.snippets[:10],
            "recent_audit": bundle.recent_audit[:10],
        }, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        failure_reasons: list[tuple[str, str, str]] = []  # (provider, model, short_reason)
        for provider, model in candidates:
            litellm_model = _litellm_model(provider, model, settings)
            try:
                response = litellm.completion(
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

        if level == "assist":
            role_instruction = (
                "Your role is to SUGGEST helpful next steps based on the context. "
                "You should NOT propose executing anything — only surface observations "
                "and recommendations the user might find useful. "
                "Prefer action_type 'suggestion' or 'none'."
            )
        elif level == "operate":
            role_instruction = (
                "Your role is to follow up on stuck work, run approved routine tasks, "
                "and keep the user updated. You can propose executing routine tasks "
                "(kanban ticket creation, workflow runs) and sending suggestions. "
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

        return (
            f"You are an autonomous agent assistant. Initiative level: {level}.\n"
            f"Current datetime: {bundle.current_datetime}\n"
            f"Boundary settings: {boundary_info}\n\n"
            f"{role_instruction}\n\n"
            "Context available: active project, kanban boards and tickets, "
            "workflows (stuck/unfinished), recent tool audit trail, available tools, "
            "and saved snippets.\n\n"
            "Based on the context, propose ONE action. "
            "Respond with a JSON object (no markdown fences) with fields:\n"
            "  action_type: suggestion | routine_task | external_comms | file_change | sensitive | none\n"
            "  description: what the action does (string)\n"
            "  payload: optional dict with details (e.g. board_id, lane, title for kanban)\n"
            "  draft: optional text draft for the action\n"
            "  telegram_message: optional notification text\n\n"
            "If nothing useful can be done, return {\"action_type\": \"none\"}."
        )


    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    def _dispatch_action(self, action: ProposedAction, settings: dict, decision) -> None:
        from distr.core.initiative.policy import PolicyDecision

        if decision == PolicyDecision.SKIP:
            logger.info("InitiativeService: action skipped (policy=SKIP) action_type=%s",
                        action.action_type)
            return

        if decision == PolicyDecision.SUGGEST_ONLY:
            logger.info("InitiativeService: suggestion: %s", action.description)
            self._deliver_suggestion(action, settings)
            return

        if decision == PolicyDecision.DRAFT_AND_ASK:
            action.requires_confirmation = True
            self._draft_and_ask(action, settings)
            return

        # EXECUTE
        self._execute_action(action, settings)

    def _execute_action(self, action: ProposedAction, settings: dict) -> None:
        """Execute an approved action."""
        allow_routine = settings.get("initiative_allow_routine_tasks", False)

        if action.action_type == "routine_task":
            if not allow_routine:
                logger.info("InitiativeService: routine task blocked by boundary: %s",
                            action.description)
                self._deliver_suggestion(action, settings)
                return

            runner_type = (action.payload or {}).get("runner_type", "")
            if runner_type == "kanban":
                self._dispatch_kanban(action, settings)
            elif runner_type == "workflow":
                self._dispatch_workflow(action, settings)
            else:
                # Default: try kanban if board_id present, otherwise log as suggestion
                if (action.payload or {}).get("board_id"):
                    self._dispatch_kanban(action, settings)
                else:
                    self._deliver_suggestion(action, settings)
            return

        if action.action_type == "suggestion":
            self._deliver_suggestion(action, settings)
            return

        # external_comms, file_change, sensitive with EXECUTE decision
        # These are actions the policy says we can do without asking.
        # Log them and notify the user.
        logger.info("InitiativeService: auto-executing %s: %s",
                     action.action_type, action.description)
        self._log_to_chat(f"[Auto] {action.action_type}: {action.description}", settings)
        self._send_telegram_if_allowed(
            action.telegram_message or f"{action.action_type}: {action.description}",
            settings,
        )

    # ------------------------------------------------------------------
    # Delivery helpers
    # ------------------------------------------------------------------

    def _deliver_suggestion(self, action: ProposedAction, settings: dict) -> None:
        """Deliver a suggestion via chat and optionally Telegram."""
        msg = action.description
        self._log_to_chat(f"Suggestion: {msg}", settings)
        self._send_telegram_if_allowed(
            action.telegram_message or f"Suggestion: {msg}",
            settings,
        )

    def _dispatch_kanban(self, action: ProposedAction, settings: dict) -> None:
        """Create a kanban ticket from the proposed action."""
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
            logger.warning("InitiativeService: no board_id for kanban dispatch, falling back to suggestion")
            self._deliver_suggestion(action, settings)
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
                    logger.info("InitiativeService: created kanban ticket '%s' in lane '%s' (board %s)",
                                title[:50], lane.name, board_id)
                    self._log_to_chat(f"Created kanban ticket: {title[:100]}", settings)
                    self._send_telegram_if_allowed(
                        action.telegram_message or f"Created ticket: {title[:100]}",
                        settings,
                    )
                    return
        except Exception:
            logger.error("InitiativeService: kanban ticket creation failed", exc_info=True)

        self._deliver_suggestion(action, settings)

    def _dispatch_workflow(self, action: ProposedAction, settings: dict) -> None:
        """Trigger a workflow run from the proposed action."""
        payload = action.payload or {}
        workflow_id = payload.get("workflow_id")
        if not workflow_id:
            logger.warning("InitiativeService: no workflow_id for workflow dispatch")
            self._deliver_suggestion(action, settings)
            return

        try:
            from distr.core.workflow.dispatcher import start_workflow_run
            start_workflow_run(int(workflow_id))
            logger.info("InitiativeService: triggered workflow %s: %s",
                        workflow_id, action.description)
            self._log_to_chat(f"Started workflow #{workflow_id}: {action.description}", settings)
            self._send_telegram_if_allowed(
                action.telegram_message or f"Started workflow: {action.description}",
                settings,
            )
        except Exception:
            logger.error("InitiativeService: workflow dispatch failed", exc_info=True)
            self._deliver_suggestion(action, settings)

    # ------------------------------------------------------------------
    # Draft queue
    # ------------------------------------------------------------------

    def _surface_draft_queue(self, chat_id: int) -> None:
        """Surface pending drafts as chat messages when a chat starts."""
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
        for draft in drafts:
            msg = (
                f"[Pending action — {draft.action_type}] {draft.description}\n\n"
                f"Draft: {draft.draft}\n\n"
                f"ID: `{draft.id}`\n"
                "Say **approve** or **reject** to respond, "
                "or manage pending actions in Settings → Initiative."
            )
            self.chat_manager.add_assistant_message(current_chat, msg)

    def _draft_and_ask(self, action: ProposedAction, settings: dict) -> None:
        """Queue an action for user approval."""
        now = datetime.now(tz=timezone.utc)
        entry = DraftEntry(
            id=str(uuid.uuid4()),
            action_type=action.action_type,
            description=action.description,
            draft=action.draft or action.description,
            reason=f"Boundary requires confirmation for {action.action_type}",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=48)).isoformat(),
        )
        self._draft_queue.add(entry)
        logger.info("InitiativeService: draft queued %s: %s", entry.id, entry.description)

        # Notify via Telegram if possible
        allow_telegram = settings.get("initiative_allow_telegram", False)
        uid = getattr(self.telegram_manager, "telegram_user_id", None)
        if allow_telegram and uid and uid > 0:
            msg = (
                f"[Initiative] I'd like to: {action.description}\n\n"
                f"Draft:\n{entry.draft}\n\n"
                "Approve or reject this in the app."
            )
            self.telegram_manager.send_to_telegram(text=msg)

        # Also log to chat
        self._log_to_chat(
            f"Pending approval — {action.action_type}: {action.description}",
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
                    current_chat, f"[Initiative] {message}"
                )
        except Exception as e:
            logger.debug("InitiativeService: _log_to_chat failed: %s", e)

    def _send_telegram_if_allowed(self, text: str, settings: dict) -> None:
        allow_telegram = settings.get("initiative_allow_telegram", False)
        if not allow_telegram:
            return
        uid = getattr(self.telegram_manager, "telegram_user_id", None)
        if not uid or uid <= 0:
            return
        if not text.startswith("[Initiative]"):
            text = f"[Initiative] {text}"
        try:
            self.telegram_manager.send_to_telegram(text=text)
            logger.info("InitiativeService: sent Telegram: %s", text[:100])
        except Exception as e:
            logger.warning("InitiativeService: Telegram send failed: %s", e)
