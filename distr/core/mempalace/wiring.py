"""Wiring hooks: dual-write / prefer-read when mempalace_memory_backend is ON.

Legacy stores are never deleted. When the flag is OFF these helpers are no-ops
(or return None) so call sites keep using existing paths.
"""

from __future__ import annotations

import logging
from typing import Any

from distr.core.mempalace.flags import is_mempalace_memory_backend_enabled
from distr.core.mempalace.mapping import (
    ROOM_LEARNINGS,
    ROOM_MARKDOWN_MEMORY,
    ROOM_ORCHESTRATOR,
    ROOM_RAG,
    ROOM_STEERING,
    ROOM_TICKET_CLI,
    ROOM_WORKFLOW,
    ROOM_WORKSPACE,
    WING_DECISIONS,
    WING_SEED_CORPUS_PACK,
)

logger = logging.getLogger(__name__)


def _enabled(settings: dict[str, Any] | None = None) -> bool:
    try:
        return is_mempalace_memory_backend_enabled(settings)
    except Exception:
        return False


def _adapter():
    from distr.core.mempalace.adapter import get_adapter

    return get_adapter()


def dual_write_markdown_section(
    *,
    file_key: str,
    section_text: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """When flag ON, also file a markdown memory section into MemPalace."""
    if not _enabled(settings):
        return None
    text = (section_text or "").strip()
    if not text:
        return None
    try:
        payload = "[" + str(file_key) + "]\n" + text
        return _adapter().add_drawer(
            payload,
            wing=WING_DECISIONS,
            room=ROOM_MARKDOWN_MEMORY,
            source_file="models/memory/" + str(file_key) + ".md",
            added_by="decisions.memory.files",
            extra_meta={"file_key": file_key, "surface": "markdown_memory"},
        )
    except Exception:
        logger.debug("mempalace dual_write_markdown_section failed", exc_info=True)
        return None


def prefer_read_markdown_context(
    query: str,
    *,
    settings: dict[str, Any] | None = None,
    n_results: int = 5,
) -> str | None:
    """When flag ON, return MemPalace context for markdown room (else None → legacy)."""
    if not _enabled(settings):
        return None
    try:
        ctx = _adapter().format_search_as_context(
            query,
            wing=WING_DECISIONS,
            room=ROOM_MARKDOWN_MEMORY,
            n_results=n_results,
        )
        return ctx or None
    except Exception:
        logger.debug("mempalace prefer_read_markdown_context failed", exc_info=True)
        return None


def dual_write_learning(
    *,
    insight: str,
    key: str = "",
    learning_type: str = "",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _enabled(settings):
        return None
    text = (insight or "").strip()
    if not text:
        return None
    body = "type=" + (learning_type or "insight") + " key=" + (key or "-") + "\n" + text
    try:
        return _adapter().add_drawer(
            body,
            wing=WING_DECISIONS,
            room=ROOM_LEARNINGS,
            source_file=".decisions/learnings/learnings.jsonl",
            added_by="decisions.learnings.keeper",
            extra_meta={"learning_key": key or "", "learning_type": learning_type or ""},
        )
    except Exception:
        logger.debug("mempalace dual_write_learning failed", exc_info=True)
        return None


def prefer_read_learnings(
    query: str,
    *,
    settings: dict[str, Any] | None = None,
    n_results: int = 3,
) -> str | None:
    if not _enabled(settings):
        return None
    try:
        ctx = _adapter().format_search_as_context(
            query,
            wing=WING_DECISIONS,
            room=ROOM_LEARNINGS,
            n_results=n_results,
        )
        return ctx or None
    except Exception:
        logger.debug("mempalace prefer_read_learnings failed", exc_info=True)
        return None


def dual_write_orchestrator_memory(
    *,
    content: str,
    category: str = "",
    memory_uid: str = "",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _enabled(settings):
        return None
    text = (content or "").strip()
    if not text:
        return None
    try:
        return _adapter().add_drawer(
            text,
            wing=WING_DECISIONS,
            room=ROOM_ORCHESTRATOR,
            source_file="orchestrator_user_memory",
            added_by="decisions.orchestrator_memory",
            extra_meta={"category": category or "", "memory_uid": memory_uid or ""},
        )
    except Exception:
        logger.debug("mempalace dual_write_orchestrator_memory failed", exc_info=True)
        return None


def prefer_read_orchestrator_context(
    query: str,
    *,
    settings: dict[str, Any] | None = None,
    n_results: int = 5,
) -> str | None:
    if not _enabled(settings):
        return None
    try:
        ctx = _adapter().format_search_as_context(
            query,
            wing=WING_DECISIONS,
            room=ROOM_ORCHESTRATOR,
            n_results=n_results,
        )
        return ctx or None
    except Exception:
        logger.debug("mempalace prefer_read_orchestrator_context failed", exc_info=True)
        return None


def dual_write_steering(
    *,
    message: str,
    run_id: int | None = None,
    event_type: str = "",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _enabled(settings):
        return None
    text = (message or "").strip()
    if not text:
        return None
    try:
        return _adapter().add_drawer(
            text,
            wing=WING_DECISIONS,
            room=ROOM_STEERING,
            source_file="workflow_run/" + str(run_id or "unknown"),
            added_by="decisions.steering_memory",
            extra_meta={"event_type": event_type or "", "run_id": int(run_id or 0)},
        )
    except Exception:
        logger.debug("mempalace dual_write_steering failed", exc_info=True)
        return None


def dual_write_workspace_event(
    *,
    entity_type: str,
    entity_id: int | str,
    message: str,
    event_type: str = "",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _enabled(settings):
        return None
    text = (message or "").strip()
    if not text:
        return None
    room = ROOM_WORKFLOW if entity_type in {"workflows", "runs"} else ROOM_WORKSPACE
    if entity_type == "tickets":
        room = ROOM_TICKET_CLI
    try:
        return _adapter().add_drawer(
            text,
            wing=WING_DECISIONS,
            room=room,
            source_file="workspace/" + str(entity_type) + "/" + str(entity_id),
            added_by="decisions.workspace_memory",
            extra_meta={
                "entity_type": str(entity_type),
                "entity_id": str(entity_id),
                "event_type": event_type or "",
            },
        )
    except Exception:
        logger.debug("mempalace dual_write_workspace_event failed", exc_info=True)
        return None


def prefer_read_rag_context(
    query: str,
    *,
    settings: dict[str, Any] | None = None,
    n_results: int = 5,
) -> str | None:
    """When flag ON, query MemPalace for RAG-style context instead of LlamaIndex.

    Prefers the bundled ``seed_corpus_pack`` wing, then all wings, then the
    Decisions ``rag`` room. LlamaIndex ``./llama_index_storage`` is left intact.
    Callers should fall back to LlamaIndex when this returns None.
    """
    if not _enabled(settings):
        return None
    try:
        # 1) Seed corpus wing (default curated knowledge)
        ctx = _adapter().format_search_as_context(
            query,
            wing=WING_SEED_CORPUS_PACK,
            room=None,
            n_results=n_results,
        )
        if not ctx:
            # 2) Entire palace (includes decisions dual-writes + seed)
            ctx = _adapter().format_search_as_context(
                query,
                wing=None,
                room=None,
                n_results=n_results,
            )
        if not ctx:
            ctx = _adapter().format_search_as_context(
                query,
                wing=WING_DECISIONS,
                room=ROOM_RAG,
                n_results=n_results,
            )
        return ctx or None
    except Exception:
        logger.debug("mempalace prefer_read_rag_context failed", exc_info=True)
        return None


def backend_status() -> dict[str, Any]:
    enabled = _enabled()
    st = _adapter().status()
    st["flag_enabled"] = enabled
    st["mode"] = "mempalace" if enabled and st.get("available") else "legacy"
    st["default_seed_wing"] = WING_SEED_CORPUS_PACK
    try:
        from distr.core.mempalace.seed import last_seed_status

        st["seed"] = last_seed_status()
    except Exception as exc:
        st["seed"] = {"error": str(exc)}
    return st
