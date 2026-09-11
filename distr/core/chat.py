"""
chat.py - ChatService (pure DB operations) + backward-compat ChatManager alias.

The actual ChatManager logic now lives in distr.core.chat_manager.ChatManagerCore.
The Qt signal bridge is in distr.core.chat_qt_adapter.ChatManagerQt.
"""

from typing import Any, List, Optional, Tuple
import logging
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import text
from distr.core.db import get_session, Chat, Settings

logger = logging.getLogger(__name__)


def _chat_params(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}

_PROVIDER_NORMALIZE = {
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "kilocode": "KiloCode",
    "gemini": "Google Gemini",
    "google gemini": "Google Gemini",
    "nvidia": "NVIDIA",
    "kokoro": "Kokoro",
    "elevenlabs": "ElevenLabs",
}


def valid_llm_providers() -> list[str]:
    """Canonical display names for chat LLM provider fields."""
    from distr.core.agent.constants import PROVIDER_TO_ENGINE

    return sorted(PROVIDER_TO_ENGINE.keys())


def _normalize_provider(provider: Optional[str]) -> str:
    if not provider or not str(provider).strip():
        return "Ollama"
    key = str(provider).strip().lower()
    return _PROVIDER_NORMALIZE.get(key, provider.strip())


def provider_slug(provider: Optional[str]) -> str:
    """Map display name or slug to canonical lowercase provider id."""
    if not provider or not str(provider).strip():
        return "ollama"
    key = str(provider).strip().lower()
    if key in _PROVIDER_NORMALIZE:
        return key
    for slug, display in _PROVIDER_NORMALIZE.items():
        if key == display.lower():
            return slug
    return key


def _json_loads_obj(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _thread_root_chat_id(session, chat_id: int) -> int:
    root_query = text("""
        WITH RECURSIVE parents(id, parent_id) AS (
            SELECT id, parent_id FROM chats WHERE id = :start_id
            UNION ALL
            SELECT c.id, c.parent_id FROM chats c JOIN parents p ON c.id = p.parent_id
        )
        SELECT id FROM parents WHERE parent_id IS NULL
    """)
    root_result = session.execute(root_query, {"start_id": chat_id}).fetchone()
    return int(root_result[0]) if root_result else int(chat_id)


def get_compact_checkpoint_prompt(chat_id: Optional[int]) -> str:
    """Compact summary block for agent system prompts (fork + compact flows)."""
    if not chat_id:
        return ""
    try:
        with get_session() as session:
            root_id = _thread_root_chat_id(session, int(chat_id))
            root = session.get(Chat, root_id)
            if not root:
                return ""
            additional_context = _json_loads_obj(getattr(root, "additional_context", None))
            checkpoint = additional_context.get("compact_checkpoint")
            if not isinstance(checkpoint, dict) or checkpoint.get("active") is False:
                return ""
            summary = str(checkpoint.get("summary") or "").strip()
            if not summary:
                return ""
            return (
                "Chat compact checkpoint. Use this as the durable prior "
                "conversation state instead of reconstructing the older "
                "raw transcript:\n\n"
                + summary
            )
    except Exception:
        logger.debug("get_compact_checkpoint_prompt failed for chat %s", chat_id, exc_info=True)
        return ""


def _title_from_question(question: str, max_len: int = 50) -> str:
    if not question or not question.strip():
        return "New Chat"
    line = " ".join(question.strip().split())
    return (line[:max_len] + "\u2026") if len(line) > max_len else line


def _chat_audit_preview(message: str, *, max_len: int = 1000) -> str:
    clean = " ".join(str(message or "").split())
    if not clean:
        return ""
    try:
        from distr.core.orchestrator import redact_handoff_payload

        redacted = redact_handoff_payload(clean)
        clean = redacted if isinstance(redacted, str) else clean
    except Exception:
        pass
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rstrip() + "..."


def record_chat_audit_event(
    *,
    chat_id: int,
    chat_row_id: int | None,
    role: str,
    content: str,
    source_platform: str | None = None,
    hidden: bool = False,
) -> None:
    """Mirror visible chat turns into the Orchestrator ledger."""
    if hidden:
        return
    clean = (content or "").strip()
    role_clean = (role or "").strip().lower()
    if not clean or role_clean not in {"user", "assistant", "tool", "workflow"}:
        return
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        preview = _chat_audit_preview(clean)
        surface = (source_platform or "chat").strip().lower()
        row_token = chat_row_id if chat_row_id is not None else "root"
        emit_orchestration_event(
            source="chat",
            event_type="chat_message_added",
            status=role_clean,
            summary=f"Chat {role_clean} message: {preview}",
            payload={
                "surface": surface,
                "subtype": f"chat_{role_clean}_message",
                "correlation_id": f"chat:{chat_id}:{row_token}:{role_clean}",
                "thread_id": str(chat_id),
                "is_workflow_attached": False,
                "chat_id": int(chat_id),
                "chat_row_id": int(chat_row_id) if chat_row_id is not None else None,
                "role": role_clean,
                "source_platform": source_platform or "",
                "content_preview": preview,
                "content_hash": hashlib.sha256(clean.encode("utf-8")).hexdigest(),
                "content_length": len(clean),
            },
        )
        if role_clean == "user":
            try:
                from distr.core.orchestrator_memory import extract_and_record_user_memories_from_text

                extract_and_record_user_memories_from_text(
                    clean,
                    source_type=surface or "chat",
                    source_id=f"chat:{chat_id}:{row_token}",
                    source_chat_id=int(chat_id),
                )
            except Exception:
                logger.debug("chat memory extraction failed", exc_info=True)
    except Exception:
        logger.debug("record_chat_audit_event failed", exc_info=True)


_CHAT_TRANSCRIPT_AUDIT_SUBTYPES = {
    "chat_user_message",
    "chat_assistant_message",
    "chat_tool_message",
    "chat_workflow_message",
}


def _is_chat_transcript_audit_payload(payload: dict) -> bool:
    orchestration = (
        payload.get("orchestration")
        if isinstance(payload.get("orchestration"), dict)
        else {}
    )
    subtype = str(orchestration.get("subtype") or payload.get("subtype") or "").strip()
    role = str(payload.get("role") or "").strip().lower()
    if subtype in _CHAT_TRANSCRIPT_AUDIT_SUBTYPES:
        return True
    return bool(
        payload.get("content_hash")
        and subtype.startswith("chat_")
        and subtype.endswith("_message")
        and role in {"user", "assistant", "tool", "workflow"}
    )


def remove_chat_transcript_audit_events(chat_id: int) -> int:
    """Remove Hermes transcript rows for a deleted or cleared chat thread.

    Durable Hermes memories live in separate tables/events and must survive this.
    """
    try:
        target_id = int(chat_id)
    except (TypeError, ValueError):
        return 0
    deleted = 0
    try:
        from distr.core.db.orchestrator import OrchestratorEvent

        with get_session() as session:
            rows = session.query(OrchestratorEvent).filter(OrchestratorEvent.source == "chat").all()
            for row in rows:
                try:
                    payload = json.loads(row.payload or "{}")
                    if not isinstance(payload, dict):
                        payload = {}
                except Exception:
                    payload = {}
                if not _is_chat_transcript_audit_payload(payload):
                    continue
                orchestration = (
                    payload.get("orchestration")
                    if isinstance(payload.get("orchestration"), dict)
                    else {}
                )
                payload_chat_id = payload.get("chat_id")
                thread_id = orchestration.get("thread_id") or payload.get("thread_id")
                matches_chat_id = False
                try:
                    matches_chat_id = int(payload_chat_id) == target_id
                except (TypeError, ValueError):
                    matches_chat_id = False
                if matches_chat_id or str(thread_id or "") == str(target_id):
                    session.delete(row)
                    deleted += 1
            if deleted:
                session.commit()
    except Exception:
        logger.debug("remove_chat_transcript_audit_events failed", exc_info=True)
        return 0
    return deleted


def cleanup_chat_dependencies(
    session,
    chat_id: int,
    *,
    delete_owned_ticket: bool = True,
    delete_owned_workflows: bool = True,
) -> dict[str, int]:
    """Remove durable state owned by one root chat identity.

    This is deliberately explicit instead of relying on SQLite foreign-key
    cascades, which may be disabled in existing installations. It is safe to
    call immediately after allocating a new root row because no new
    Development state exists at that point.
    """
    from sqlalchemy import or_

    from distr.core.db import ChatTurnEvent, TrelloTicket
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.workflow import (
        AutoWorkflow,
        AutoWorkflowRun,
        AutoWorkflowStep,
        AutoWorkflowStepResult,
        DevelopmentCommand,
        DevelopmentPlanRevision,
        DevelopmentWorkItem,
        StudioArtifact,
    )

    target_id = int(chat_id)
    counts: dict[str, int] = {}

    work_items = (
        session.query(DevelopmentWorkItem)
        .filter(DevelopmentWorkItem.chat_id == target_id)
        .all()
    )
    ticket_ids = {
        int(row.local_ticket_id)
        for row in work_items
        if row.local_ticket_id is not None
    }
    owned_workflow_ids = {
        int(row.id)
        for row in session.query(AutoWorkflow.id)
        .filter(AutoWorkflow.chat_id == target_id)
        .all()
    }
    run_rows = (
        session.query(AutoWorkflowRun)
        .filter(
            or_(
                AutoWorkflowRun.chat_id == target_id,
                AutoWorkflowRun.workflow_id.in_(owned_workflow_ids)
                if owned_workflow_ids
                else False,
            )
        )
        .all()
    )
    run_ids = {int(row.id) for row in run_rows}

    counts["turn_events"] = (
        session.query(ChatTurnEvent)
        .filter(ChatTurnEvent.chat_id == target_id)
        .delete(synchronize_session=False)
    )
    counts["artifacts"] = (
        session.query(StudioArtifact)
        .filter(StudioArtifact.chat_id == target_id)
        .delete(synchronize_session=False)
    )
    counts["plans"] = (
        session.query(DevelopmentPlanRevision)
        .filter(DevelopmentPlanRevision.chat_id == target_id)
        .delete(synchronize_session=False)
    )
    counts["commands"] = (
        session.query(DevelopmentCommand)
        .filter(DevelopmentCommand.chat_id == target_id)
        .delete(synchronize_session=False)
    )
    counts["work_items"] = (
        session.query(DevelopmentWorkItem)
        .filter(DevelopmentWorkItem.chat_id == target_id)
        .delete(synchronize_session=False)
    )
    counts["trello_links"] = (
        session.query(TrelloTicket)
        .filter(TrelloTicket.chat_id == target_id)
        .update({TrelloTicket.chat_id: None}, synchronize_session=False)
    )

    if run_ids:
        counts["step_results"] = (
            session.query(AutoWorkflowStepResult)
            .filter(AutoWorkflowStepResult.run_id.in_(run_ids))
            .delete(synchronize_session=False)
        )
    else:
        counts["step_results"] = 0
    for row in run_rows:
        session.delete(row)
    counts["runs"] = len(run_rows)

    if delete_owned_ticket:
        owned_tickets = (
            session.query(KanbanTicket)
            .filter(
                or_(
                    KanbanTicket.source_chat_id == target_id,
                    KanbanTicket.id.in_(ticket_ids) if ticket_ids else False,
                )
            )
            .all()
        )
        for row in owned_tickets:
            session.delete(row)
        counts["tickets"] = len(owned_tickets)
    else:
        counts["tickets"] = 0

    if delete_owned_workflows and owned_workflow_ids:
        # Delete result rows first for installations where SQLite FK cascades
        # were not enabled when the database was created.
        step_ids = {
            int(row.id)
            for row in session.query(AutoWorkflowStep.id)
            .filter(AutoWorkflowStep.workflow_id.in_(owned_workflow_ids))
            .all()
        }
        if step_ids:
            session.query(AutoWorkflowStepResult).filter(
                AutoWorkflowStepResult.step_id.in_(step_ids)
            ).delete(synchronize_session=False)
        workflows = (
            session.query(AutoWorkflow)
            .filter(AutoWorkflow.id.in_(owned_workflow_ids))
            .all()
        )
        for row in workflows:
            session.delete(row)
        counts["workflows"] = len(workflows)
    else:
        counts["workflows"] = 0

    return counts


def _setting_val(settings, key: str):
    """Read a Settings column from a SQLAlchemy row or dict."""
    if settings is None:
        return None
    if isinstance(settings, dict):
        return settings.get(key)
    return getattr(settings, key, None)


def resolve_voice_model_from_global_settings(
    tts_provider: Optional[str], settings
) -> Optional[str]:
    """Voice ID stored in Settings for the active TTS provider; else registry default.

    Avoids mixing providers (e.g. Kokoro ``af_heart`` applied to Coqui), which caused
    invalid speaker errors when creating a new chat without an explicit voice.
    """
    if not settings:
        return None
    vp = (tts_provider or "").strip()
    if not vp:
        return None

    from distr.core.agent.constants import normalize_voice_provider

    nid = normalize_voice_provider(vp)

    try:
        from distr.core.agent.services.tts.registry import tts_registry

        desc = tts_registry.get(nid)
        stored = (_setting_val(settings, desc.settings_key) or "").strip()
        if stored:
            return stored
        dv = (desc.default_voice or "").strip()
        if dv:
            return dv
    except KeyError:
        pass
    except Exception:
        logger.debug(
            "resolve_voice_model_from_global_settings: registry lookup failed",
            exc_info=True,
        )

    # Descriptor missing or no default — map canonical provider id → Settings column
    _VOICE_KEYS = {
        "kokoro": "kokoro_voice",
        "openai": "openai_voice",
        "elevenlabs": "elevenlabs_voice",
        "coqui": "coqui_voice",
        "f5tts": "f5tts_voice",
        "voxcpm": "voxcpm_voice",
        "supertonic": "supertonic_voice",
        "chatterbox": "chatterbox_voice",
    }
    sk = _VOICE_KEYS.get(nid)
    if sk:
        v = (_setting_val(settings, sk) or "").strip()
        if v:
            return v

    # Last resort only (ambiguous provider): first non-empty voice column
    for key in (
        "kokoro_voice",
        "openai_voice",
        "elevenlabs_voice",
        "coqui_voice",
        "f5tts_voice",
        "voxcpm_voice",
        "supertonic_voice",
        "chatterbox_voice",
    ):
        v = (_setting_val(settings, key) or "").strip()
        if v:
            return v
    return None


class ChatService:
    """Central service for chat DB and settings. Used by web routes and ChatManager. No Qt/signals."""

    @staticmethod
    def create_new_chat(
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        tts_provider: Optional[str] = None,
        tts_voice: Optional[str] = None,
        title: Optional[str] = None,
        starting_question: Optional[str] = None,
        project_id: Optional[int] = None,
        route_mode: str = "auto",
        execution_profile: str = "code",
        autonomy_level: str = "full",
        starting_metadata: Optional[dict[str, Any]] = None,
        activate: bool = True,
        _session_provider=None,
    ) -> Tuple[int, Optional[str]]:
        """Create a chat, optionally making it the active conversational agent chat."""
        session_provider = _session_provider or get_session
        with session_provider() as session:
            settings = session.query(Settings).first()
            if settings:
                if not llm_provider:
                    llm_provider = getattr(
                        settings, "conversational_llm_provider", None
                    ) or getattr(settings, "agent_provider", None)
                if not llm_model:
                    llm_model = getattr(
                        settings, "conversational_llm_model", None
                    ) or getattr(settings, "agent_model", None)
                if not tts_provider:
                    tts_provider = getattr(settings, "tts_provider", None) or getattr(
                        settings, "voice_provider", None
                    )
                if not tts_voice:
                    tts_voice = resolve_voice_model_from_global_settings(
                        tts_provider, settings
                    )

        provider = _normalize_provider(llm_provider or "ollama")
        model_name = (llm_model or "").strip() or None
        voice_provider = (tts_provider or "").strip() or None
        voice_model = (tts_voice or "").strip() or None
        starting_question = (starting_question or "").strip() or None
        if not title and starting_question:
            title = _title_from_question(starting_question)
        title = (title or "").strip() or "New Chat"
        from distr.core.db.time import utc_now_naive

        now = utc_now_naive()
        with session_provider() as session:
            root = Chat(
                parent_id=None,
                project_id=project_id,
                title=title,
                input=None,
                response=None,
                provider=provider,
                model_name=model_name,
                route_mode=(route_mode or "auto").strip().lower(),
                execution_profile=(execution_profile or "code").strip().lower(),
                autonomy_level=(autonomy_level or "full").strip().lower(),
                voice_provider=voice_provider,
                voice_model=voice_model,
                created_date=now,
                modified_date=now,
            )
            session.add(root)
            session.commit()
            session.refresh(root)
            chat_id = root.id
            # SQLite can reuse the id of a deleted root chat. Older releases only
            # removed the transcript rows, leaving Development state keyed by that
            # id behind. Clear any such orphaned state before this new identity is
            # exposed to the harness or given its first turn.
            cleanup_chat_dependencies(
                session,
                int(chat_id),
                delete_owned_ticket=True,
                delete_owned_workflows=True,
            )
            session.commit()
            if starting_question:
                child = Chat(
                    parent_id=chat_id,
                    project_id=project_id,
                    title=None,
                    input=starting_question,
                    params=json.dumps({"development_turn": starting_metadata}, ensure_ascii=False, default=str) if starting_metadata else None,
                    response=None,
                    provider=provider,
                    model_name=model_name,
                    route_mode=(route_mode or "auto").strip().lower(),
                    execution_profile=(execution_profile or "code").strip().lower(),
                    autonomy_level=(autonomy_level or "full").strip().lower(),
                    voice_provider=voice_provider,
                    voice_model=voice_model,
                    created_date=now,
                    modified_date=now,
                )
                session.add(child)
                session.commit()
                session.refresh(child)
                params = _chat_params(root.params)
                params["active_turn_chat_row_id"] = int(child.id)
                root.params = json.dumps(params)
                session.commit()
                try:
                    from distr.core.chat_turns import ensure_turn_started

                    ensure_turn_started(int(chat_id), int(child.id))
                except Exception:
                    logger.debug("Could not initialize durable chat turn", exc_info=True)
                record_chat_audit_event(
                    chat_id=int(chat_id),
                    chat_row_id=int(child.id) if child.id is not None else None,
                    role="user",
                    content=starting_question,
                )
            settings_row = session.query(Settings).first()
            if settings_row and activate:
                settings_row.last_chat_id = chat_id
                settings_row.agent_current_chat_id = chat_id
                session.commit()
                logger.info(
                    "ChatService: set last_chat_id and agent_current_chat_id to %s",
                    chat_id,
                )
            elif not settings_row:
                logger.warning("ChatService: no Settings row to update")
        return (chat_id, starting_question if starting_question else None)

    @staticmethod
    def get_chat_history(chat_id: int) -> List[dict]:
        """Return list of {role, content} for the thread (no system message)."""
        with get_session() as session:
            chat = session.get(Chat, chat_id)
            if not chat:
                return []
            root_id = _thread_root_chat_id(session, chat_id)
            root = session.get(Chat, root_id)
            additional_context = _json_loads_obj(getattr(root, "additional_context", None))
            checkpoint = additional_context.get("compact_checkpoint")
            checkpoint_row_id = None
            if isinstance(checkpoint, dict) and checkpoint.get("active") is not False:
                try:
                    checkpoint_row_id = int(checkpoint.get("chat_row_id") or 0) or None
                except (TypeError, ValueError):
                    checkpoint_row_id = None

            thread_query = text("""
                WITH RECURSIVE chat_tree(id, parent_id, input, response, is_hidden, created_date) AS (
                    SELECT id, parent_id, input, response, is_hidden, created_date
                    FROM chats WHERE id = :root_id
                    UNION ALL
                    SELECT c.id, c.parent_id, c.input, c.response, c.is_hidden, c.created_date
                    FROM chats c JOIN chat_tree ct ON c.parent_id = ct.id
                )
                SELECT id, input, response, is_hidden FROM chat_tree ORDER BY created_date
            """)
            rows = session.execute(thread_query, {"root_id": root_id}).fetchall()
            messages = []
            checkpoint_prompt = get_compact_checkpoint_prompt(root_id)
            if checkpoint_prompt:
                messages.append({"role": "system", "content": checkpoint_prompt})
            for row in rows:
                if checkpoint_row_id is not None and int(row.id or 0) <= checkpoint_row_id:
                    continue
                if row.input:
                    messages.append({"role": "user", "content": row.input})
                if row.response:
                    messages.append({"role": "assistant", "content": row.response})
            return messages

    @staticmethod
    def add_user_message(chat_id: int, message: str, *, metadata: Optional[dict[str, Any]] = None) -> None:
        """Append a user message to the chat thread in the DB."""
        cleaned = (message or "").strip()
        if not cleaned:
            return
        with get_session() as session:
            chat = session.get(Chat, chat_id)
            if not chat:
                logger.warning(
                    "ChatService.add_user_message: Chat %s doesn't exist, message not persisted",
                    chat_id,
                )
                return
            root_query = text("""
                WITH RECURSIVE parents(id, parent_id) AS (
                    SELECT id, parent_id FROM chats WHERE id = :start_id
                    UNION ALL
                    SELECT c.id, c.parent_id FROM chats c JOIN parents p ON c.id = p.parent_id
                )
                SELECT id FROM parents WHERE parent_id IS NULL
            """)
            root_row = session.execute(root_query, {"start_id": chat_id}).fetchone()
            root_id = root_row[0] if root_row else chat_id
            root = session.get(Chat, root_id)
            if not root:
                return
            child = Chat(
                parent_id=root_id,
                title=cleaned.split("\n")[0][:50] if cleaned else None,
                input=cleaned,
                response=None,
                params=json.dumps({"development_turn": metadata}, ensure_ascii=False, default=str) if metadata else None,
                provider=root.provider,
                model_name=root.model_name,
                voice_provider=root.voice_provider,
                voice_model=root.voice_model,
                created_date=datetime.now(timezone.utc),
                modified_date=datetime.now(timezone.utc),
            )
            session.add(child)
            session.commit()
            session.refresh(child)
            params = _chat_params(root.params)
            params["active_turn_chat_row_id"] = int(child.id)
            root.params = json.dumps(params)
            session.commit()
            try:
                from distr.core.chat_turns import ensure_turn_started

                ensure_turn_started(int(root_id), int(child.id))
            except Exception:
                logger.debug("Could not initialize durable chat turn", exc_info=True)
            record_chat_audit_event(
                chat_id=int(root_id),
                chat_row_id=int(child.id) if child.id is not None else None,
                role="user",
                content=cleaned,
            )

    @staticmethod
    def append_assistant_notice(chat_id: int, message: str, *, hidden: bool = False) -> Optional[int]:
        """Append an assistant-only row (no user message) for system/board notices."""
        cleaned = (message or "").strip()
        if not cleaned:
            return False
        with get_session() as session:
            chat = session.get(Chat, chat_id)
            if not chat:
                logger.warning(
                    "ChatService.append_assistant_notice: Chat %s missing, skipped",
                    chat_id,
                )
                return None
            root_query = text("""
                WITH RECURSIVE parents(id, parent_id) AS (
                    SELECT id, parent_id FROM chats WHERE id = :start_id
                    UNION ALL
                    SELECT c.id, c.parent_id FROM chats c JOIN parents p ON c.id = p.parent_id
                )
                SELECT id FROM parents WHERE parent_id IS NULL
            """)
            root_row = session.execute(root_query, {"start_id": chat_id}).fetchone()
            root_id = root_row[0] if root_row else chat_id
            root = session.get(Chat, root_id)
            if not root:
                return None
            child = Chat(
                parent_id=root_id,
                title=None,
                input=None,
                response=cleaned,
                provider=root.provider,
                model_name=root.model_name,
                voice_provider=root.voice_provider,
                voice_model=root.voice_model,
                is_hidden=hidden,
                created_date=datetime.now(timezone.utc),
                modified_date=datetime.now(timezone.utc),
            )
            session.add(child)
            root.modified_date = datetime.now(timezone.utc)
            session.commit()
            # SQLAlchemy expires ORM instances on commit by default. Capture
            # the generated id while the session is still active so callers
            # can safely receive it after the context closes.
            child_id = int(child.id) if child.id is not None else None
            record_chat_audit_event(
                chat_id=int(root_id),
                chat_row_id=child_id,
                role="assistant",
                content=cleaned,
                hidden=hidden,
            )
        return child_id

    @staticmethod
    def get_current_chat_id() -> Optional[int]:
        with get_session() as session:
            settings = session.query(Settings).first()
            if settings is None:
                return None
            from distr.core.workflow.development_threads import is_development_thread

            contaminated = False
            for key in ("agent_current_chat_id", "last_chat_id"):
                candidate = getattr(settings, key, None)
                if candidate is None:
                    continue
                chat = session.get(Chat, int(candidate))
                if chat is not None and not is_development_thread(session, chat):
                    if contaminated:
                        session.commit()
                    return int(chat.id)
                setattr(settings, key, None)
                contaminated = True
            if contaminated:
                session.commit()
            return None

    @staticmethod
    def set_current_chat_id(chat_id: Optional[int]) -> None:
        with get_session() as session:
            if chat_id is not None:
                chat = session.get(Chat, int(chat_id))
                if chat is None:
                    raise LookupError("Chat not found.")
                from distr.core.workflow.development_threads import is_development_thread

                if is_development_thread(session, chat):
                    raise ValueError(
                        "Development tasks cannot become the current Chat conversation."
                    )
            settings = session.query(Settings).first()
            if settings:
                settings.last_chat_id = chat_id
                settings.agent_current_chat_id = chat_id
                session.commit()


# Backward-compat: ``from distr.core.chat import ChatManager`` continues to work.
# Lazy import via __getattr__ to avoid circular imports with chat_manager.py.
def __getattr__(name):
    if name == "ChatManager":
        from distr.core.chat_manager import ChatManagerCore

        return ChatManagerCore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
