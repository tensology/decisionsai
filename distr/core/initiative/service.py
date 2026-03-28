import json
import logging
import dataclasses
import threading
from dataclasses import dataclass, field

from PyQt6.QtCore import QTimer

from distr.core.initiative.context import ContextAssembler
from distr.core.initiative.draft_queue import DraftQueue

logger = logging.getLogger("distr.core.initiative.service")

VALID_ACTION_TYPES = {"suggestion", "routine_task", "external_comms", "file_change", "sensitive", "none"}


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
    # Strip markdown code fences
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


class InitiativeService:
    IDLE_TIMEOUT_MS = 300_000  # 300 seconds default
    SCHEDULE_TICK_MS = 60_000  # 60 seconds

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
            migrate_initiative_level(level)  # validates/migrates
        except Exception:
            logger.error("InitiativeService: failed to run settings migration on init", exc_info=True)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopped = False
        from distr.core.signals import signal_manager
        signal_manager.chat_stream_finished.connect(self._reset_idle_timer)
        signal_manager.chat_stream_started.connect(self._surface_draft_queue)
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
            signal_manager.chat_stream_started.disconnect(self._surface_draft_queue)
        except Exception:
            pass
        self._idle_timer.stop()
        self._schedule_timer.stop()
        logger.debug("InitiativeService: stopped")

    def _reset_idle_timer(self, chat_id: int = 0) -> None:
        # Called from signal — may be on any thread, but QTimer.start must be on main thread
        # Since signals are connected on main thread, this should be fine
        self._idle_timer.start(self.IDLE_TIMEOUT_MS)
        logger.debug("InitiativeService: idle timer reset (chat_id=%s)", chat_id)

    def _on_idle_timer_expired(self) -> None:
        try:
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
        except Exception:
            logger.error("InitiativeService: failed to load settings on idle timer expiry", exc_info=True)
            return
        level = settings.get("initiative_level", "assist")
        from distr.core.initiative.policy import migrate_initiative_level
        level = migrate_initiative_level(level)
        if level in ("observe", "assist"):
            logger.debug("InitiativeService: idle timer expired but level=%s, skipping", level)
            return
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
        level = settings.get("initiative_level", "assist")
        from distr.core.initiative.policy import migrate_initiative_level
        level = migrate_initiative_level(level)
        if level not in ("operate", "own"):
            return
        # NOTE: kanban board scheduling is handled by the existing step_runner_scheduler_timer
        # in Application (via check_kanban_schedules). We do NOT call it here to avoid
        # double-firing agents. The initiative cycle only reads kanban state as context
        # for the LLM — it does not drive the kanban scheduler itself.
        with self._cycle_lock:
            if self._cycle_running:
                return
        QTimer.singleShot(0, lambda: self._dispatch_cycle("schedule_tick"))

    def _dispatch_cycle(self, trigger_source: str) -> None:
        """Dispatch _run_initiative_cycle in a daemon thread."""
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

    def _surface_draft_queue(self, chat_id: int) -> None:
        drafts = self._draft_queue.get_all()
        if not drafts:
            return
        logger.info("InitiativeService: surfacing %d pending draft(s) for chat_id=%s", len(drafts), chat_id)
        # Inject drafts as a system message into the chat context
        # (For now, log them — full injection requires chat_manager integration)
        for draft in drafts:
            logger.info(
                "InitiativeService: pending draft [%s] %s: %s",
                draft.action_type, draft.id, draft.description,
            )

    def _run_initiative_cycle(self, trigger_source: str) -> None:
        logger.debug("InitiativeService: cycle started (trigger=%s)", trigger_source)
        try:
            from distr.core.utils import load_settings_from_db
            from distr.core.initiative.policy import evaluate, migrate_initiative_level, PolicyDecision

            # Load and migrate settings
            try:
                settings = load_settings_from_db()
            except Exception:
                logger.error("InitiativeService: load_settings_from_db failed", exc_info=True)
                return

            level = migrate_initiative_level(settings.get("initiative_level", "assist"))
            if level in ("observe", "assist"):
                return

            # Expire old drafts
            self._draft_queue.expire_old()

            # Assemble context
            bundle = self._context_assembler.build(settings)

            # Call LLM
            try:
                raw = self._call_llm(bundle, settings)
            except Exception:
                logger.error("InitiativeService: LLM call failed", exc_info=True)
                self._reset_idle_timer()
                return

            # Parse action
            action = parse_llm_response(raw)
            logger.info("InitiativeService: proposed action_type=%s description=%s", action.action_type, action.description)

            if action.action_type == "none":
                return

            # Evaluate policy
            boundaries = {
                "initiative_allow_telegram": settings.get("initiative_allow_telegram", True),
                "initiative_allow_routine_tasks": settings.get("initiative_allow_routine_tasks", True),
                "initiative_ask_external_comms": settings.get("initiative_ask_external_comms", True),
                "initiative_ask_file_changes": settings.get("initiative_ask_file_changes", True),
                "initiative_ask_sensitive": settings.get("initiative_ask_sensitive", True),
            }
            decision = evaluate(action, level, boundaries)
            logger.debug("InitiativeService: policy decision=%s", decision)

            self._dispatch_action(action, settings, decision)

        except Exception:
            logger.error("InitiativeService: unhandled exception in cycle", exc_info=True)
        finally:
            with self._cycle_lock:
                self._cycle_running = False
            logger.debug("InitiativeService: cycle finished (trigger=%s)", trigger_source)

    def _call_llm(self, bundle, settings: dict) -> str:
        import litellm
        import json as _json

        provider = (
            settings.get("conversational_llm_provider")
            or settings.get("agent_provider")
            or "openai"
        )
        model = (
            settings.get("conversational_llm_model")
            or settings.get("agent_model")
            or "gpt-4o-mini"
        )

        system_prompt = (
            f"You are an autonomous agent assistant. Current initiative level: {settings.get('initiative_level', 'assist')}.\n"
            f"Current datetime: {bundle.current_datetime}\n"
            f"Boundary settings: {_json.dumps({k: v for k, v in settings.items() if k.startswith('initiative_')})}\n\n"
            "Based on the context, propose ONE action the agent should take. "
            "Respond with a JSON object only (no markdown fences) with fields: "
            "action_type (suggestion|routine_task|external_comms|file_change|sensitive|none), "
            "description (string), payload (dict, optional), draft (string, optional), "
            "telegram_message (string, optional)."
        )

        user_prompt = _json.dumps({
            "chat_history": bundle.chat_history,
            "scheduled_sessions": bundle.scheduled_sessions,
            "kanban_summary": bundle.kanban_summary,
            "stuck_tasks": bundle.stuck_tasks,
            "unfinished_workflows": bundle.unfinished_workflows,
        }, ensure_ascii=False)

        response = litellm.completion(
            model=f"{provider}/{model}",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=512,
            temperature=0.4,
        )
        return response.choices[0].message.content

    def _dispatch_action(self, action: ProposedAction, settings: dict, decision) -> None:
        from distr.core.initiative.policy import PolicyDecision

        if decision == PolicyDecision.SKIP:
            logger.info("InitiativeService: action skipped (policy=SKIP) action_type=%s", action.action_type)
            return

        if decision == PolicyDecision.SUGGEST_ONLY:
            logger.info("InitiativeService: action downgraded to suggestion: %s", action.description)
            # Surface as Telegram message if allowed, else draft queue
            self._send_telegram_or_queue(action, settings, reason="suggest_only")
            return

        if decision == PolicyDecision.DRAFT_AND_ASK:
            action.requires_confirmation = True
            self._draft_and_ask(action, settings)
            return

        # EXECUTE
        runner_type = (action.payload or {}).get("runner_type", "")
        allow_routine = settings.get("initiative_allow_routine_tasks", True)

        if action.action_type == "routine_task" and runner_type == "step_runner" and allow_routine:
            self._dispatch_step_runner(action, settings)
        elif action.action_type == "routine_task" and runner_type == "kanban" and allow_routine:
            self._dispatch_kanban(action, settings)
        elif action.action_type == "suggestion":
            self._send_telegram_or_queue(action, settings, reason="suggestion")
        else:
            # For external_comms, file_change, sensitive with EXECUTE decision
            logger.info("InitiativeService: executing action_type=%s: %s", action.action_type, action.description)
            self._send_telegram_or_queue(action, settings, reason="execute")

    def _dispatch_step_runner(self, action: ProposedAction, settings: dict) -> None:
        from distr.core.step_runner.service import create_scheduled_session
        instruction = action.description
        payload = action.payload or {}
        schedule = payload.get("schedule", "daily")
        try:
            session_id = create_scheduled_session(instruction=instruction, schedule=schedule)
            if session_id:
                logger.info("InitiativeService: created step runner session %s for: %s", session_id, instruction)
                msg = action.telegram_message or f"[Initiative] Started: {instruction}"
                self._send_telegram_if_allowed(msg, settings)
            else:
                logger.warning("InitiativeService: create_scheduled_session returned None for: %s", instruction)
        except Exception:
            logger.error("InitiativeService: step runner dispatch failed", exc_info=True)

    def _dispatch_kanban(self, action: ProposedAction, settings: dict) -> None:
        # The kanban board scheduler (check_kanban_schedules / step_runner_scheduler_timer)
        # owns the scheduling of KanbanAgentCheckIn runs. The initiative service must NOT
        # fire a second check-in independently — that would cause duplicate agent runs on
        # the same board within the same tick window.
        #
        # Instead, we surface the suggestion via Telegram/draft so the user is aware,
        # and let the existing scheduler handle the actual execution when the board is due.
        payload = action.payload or {}
        board_id = payload.get("board_id", "unknown")
        msg = action.telegram_message or f"Kanban check-in for board {board_id} is scheduled — the agent will run it at its next due time."
        logger.info("InitiativeService: kanban action deferred to existing scheduler for board_id=%s", board_id)
        self._send_telegram_or_queue(action, settings, reason="kanban_deferred_to_scheduler")

    def _send_telegram_if_allowed(self, text: str, settings: dict) -> None:
        allow_telegram = settings.get("initiative_allow_telegram", True)
        if not allow_telegram:
            logger.info("InitiativeService: Telegram send skipped (allow_telegram=False)")
            return
        uid = getattr(self.telegram_manager, "telegram_user_id", None)
        if not uid or uid <= 0:
            logger.info("InitiativeService: Telegram not connected, skipping send")
            return
        if not text.startswith("[Initiative]"):
            text = f"[Initiative] {text}"
        self.telegram_manager.send_to_telegram(text=text)
        logger.info("InitiativeService: sent Telegram message: %s", text[:100])

    def _send_telegram_or_queue(self, action: ProposedAction, settings: dict, reason: str) -> None:
        allow_telegram = settings.get("initiative_allow_telegram", True)
        uid = getattr(self.telegram_manager, "telegram_user_id", None)
        msg = action.telegram_message or action.description
        if allow_telegram and uid and uid > 0:
            if not msg.startswith("[Initiative]"):
                msg = f"[Initiative] {msg}"
            self.telegram_manager.send_to_telegram(text=msg)
            logger.info("InitiativeService: sent Telegram (%s): %s", reason, msg[:100])
        else:
            self._add_to_draft_queue(action, settings, reason=reason)

    def _draft_and_ask(self, action: ProposedAction, settings: dict) -> None:
        import uuid
        from datetime import datetime, timezone, timedelta
        from distr.core.initiative.draft_queue import DraftEntry

        now = datetime.now(tz=timezone.utc)
        entry = DraftEntry(
            id=str(uuid.uuid4()),
            action_type=action.action_type,
            description=action.description,
            draft=action.draft or action.description,
            reason=f"Boundary requires confirmation for action_type={action.action_type}",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=48)).isoformat(),
        )
        self._draft_queue.add(entry)
        logger.info("InitiativeService: draft-and-ask queued entry %s: %s", entry.id, entry.description)

        allow_telegram = settings.get("initiative_allow_telegram", True)
        uid = getattr(self.telegram_manager, "telegram_user_id", None)
        if allow_telegram and uid and uid > 0:
            msg = (
                f"[Initiative] I want to: {action.description}\n\n"
                f"Draft:\n{entry.draft}\n\n"
                "Please approve or reject this action."
            )
            self.telegram_manager.send_to_telegram(text=msg)
            logger.info("InitiativeService: sent draft-and-ask via Telegram for entry %s", entry.id)
        else:
            logger.info("InitiativeService: draft stored (Telegram not available) for entry %s", entry.id)

    def _add_to_draft_queue(self, action: ProposedAction, settings: dict, reason: str) -> None:
        import uuid
        from datetime import datetime, timezone, timedelta
        from distr.core.initiative.draft_queue import DraftEntry

        now = datetime.now(tz=timezone.utc)
        entry = DraftEntry(
            id=str(uuid.uuid4()),
            action_type=action.action_type,
            description=action.description,
            draft=action.draft or action.description,
            reason=reason,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=48)).isoformat(),
        )
        self._draft_queue.add(entry)
        logger.info("InitiativeService: added to draft queue (%s) entry %s: %s", reason, entry.id, entry.description)
