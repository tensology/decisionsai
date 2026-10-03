"""Durable WhatsApp message -> ticket -> execution -> reply-draft lifecycle.

Post-completion sequence (local assist only):
  verify → (learn if negative feedback) → deploy suggest/confirm → client draft
  → Paul confirm → WhatsApp send (respects DECISIONSAI_WHATSAPP_DRY_RUN).

Deploy assist never deploys — it only records production vs development.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import Any, Iterable

from sqlalchemy import text

from distr.core.db import engine, get_session

logger = logging.getLogger(__name__)

WAITING_KIND_VERIFICATION = "verification_review"
WAITING_KIND_DEPLOY = "deploy_assist"

# Canonical judgment labels (learning only on these three):
#   "looks good" | "redo" | "this is terrible, and this is why"
_TERRIBLE_VERIFICATION_RE = re.compile(
    r"^\s*this\s+is\s+terrible(?:\s*,?\s*and\s+this\s+is\s+why)?\b",
    re.I,
)
_NEGATIVE_VERIFICATION_RE = re.compile(
    r"^\s*(?:redo|this\s+is\s+terrible(?:\s*,?\s*and\s+this\s+is\s+why)?)\b",
    re.I,
)
_OK_VERIFICATION_RE = re.compile(
    r"^\s*looks\s+good\s*[.!?]?\s*$",
    re.I,
)


def _audit_ticket_event(
    *, ticket_id: int, run_id: int | None, status: str, summary: str, details: str = "",
) -> None:
    """Mirror channel lifecycle transitions into the Decisions ticket audit trail."""
    try:
        from distr.core.kanban.ticket_audit import append_ticket_audit_entry

        with get_session() as db:
            append_ticket_audit_entry(
                db,
                ticket_id=int(ticket_id),
                run_id=int(run_id) if run_id is not None else None,
                step_id=None,
                step_result_id=None,
                execution_lane="workflow",
                status=status,
                final_verdict=None,
                summary=summary,
                details=details,
            )
            db.commit()
    except Exception:
        # Channel telemetry must never prevent the underlying ticket workflow
        # from completing.
        logger.debug("Could not append WhatsApp lifecycle audit event", exc_info=True)


def ensure_tables() -> None:
    try:
        from distr.core.workflow.skill_judgment_memory import ensure_tables as _ensure_skill_judgment_tables
        _ensure_skill_judgment_tables()
    except Exception:
        logger.debug("Could not ensure skill_judgment tables", exc_info=True)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS whatsapp_work_lifecycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL UNIQUE,
                board_id INTEGER,
                project_id INTEGER,
                source_jid VARCHAR,
                source_phone VARCHAR,
                source_contact VARCHAR,
                message_ids TEXT NOT NULL DEFAULT '[]',
                execution_kind VARCHAR,
                run_id INTEGER,
                status VARCHAR NOT NULL DEFAULT 'ticket_created',
                reply_draft TEXT,
                reply_status VARCHAR,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                error TEXT
            )
        """))
        for col, decl in (
            ("deploy_target", "VARCHAR"),
            ("deploy_status", "VARCHAR"),
            ("verification_status", "VARCHAR"),
            ("result_summary", "TEXT"),
            ("skill_id", "VARCHAR"),
            ("skill_name", "VARCHAR"),
            ("skill_ids_json", "TEXT"),
            ("harness_category", "VARCHAR"),
            ("execution_lane", "VARCHAR"),
        ):
            try:
                conn.execute(text(f"ALTER TABLE whatsapp_work_lifecycles ADD COLUMN {col} {decl}"))
            except Exception:
                pass
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS whatsapp_reply_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token VARCHAR NOT NULL UNIQUE,
                lifecycle_id INTEGER NOT NULL,
                telegram_chat_id VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                resolved_action VARCHAR,
                error TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_whatsapp_reply_reviews_pending "
            "ON whatsapp_reply_reviews(status, telegram_chat_id, created_at)"
        ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS whatsapp_gate_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token VARCHAR NOT NULL UNIQUE,
                lifecycle_id INTEGER NOT NULL,
                gate_kind VARCHAR NOT NULL,
                telegram_chat_id VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                suggestion VARCHAR,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                resolved_action VARCHAR,
                error TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_whatsapp_gate_reviews_pending "
            "ON whatsapp_gate_reviews(status, gate_kind, telegram_chat_id, created_at)"
        ))


def record_ticket_created(
    *, ticket_id: int, board_id: int | None, project_id: int | None,
    source_jid: str, source_phone: str, source_contact: str,
    message_ids: Iterable[int],
) -> dict[str, Any]:
    ensure_tables()
    now = time.time()
    normalized_message_ids = [int(value) for value in message_ids if value]
    payload = json.dumps(normalized_message_ids)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO whatsapp_work_lifecycles(
                ticket_id, board_id, project_id, source_jid, source_phone,
                source_contact, message_ids, status, created_at, updated_at
            ) VALUES (
                :ticket_id, :board_id, :project_id, :source_jid, :source_phone,
                :source_contact, :message_ids, 'ticket_created', :now, :now
            ) ON CONFLICT(ticket_id) DO UPDATE SET
                board_id=excluded.board_id, project_id=excluded.project_id,
                source_jid=excluded.source_jid, source_phone=excluded.source_phone,
                source_contact=excluded.source_contact, message_ids=excluded.message_ids,
                updated_at=excluded.updated_at
        """), {
            "ticket_id": int(ticket_id), "board_id": board_id, "project_id": project_id,
            "source_jid": source_jid or "", "source_phone": source_phone or "",
            "source_contact": source_contact or "", "message_ids": payload, "now": now,
        })
        row = conn.execute(text(
            "SELECT * FROM whatsapp_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).mappings().first()
    _audit_ticket_event(
        ticket_id=ticket_id,
        run_id=None,
        status="source_ingested",
        summary="WhatsApp messages were ingested and linked to this ticket.",
        details=f"source_jid={source_jid or 'unknown'}; message_count={len(normalized_message_ids)}",
    )
    return dict(row or {})


def mark_execution_started(*, ticket_id: int, execution_kind: str, run_id: int | None = None) -> None:
    ensure_tables()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_work_lifecycles SET execution_kind=:kind, run_id=:run_id,
              status='executing', updated_at=:now, error=NULL WHERE ticket_id=:ticket_id
        """), {"kind": execution_kind, "run_id": run_id, "now": time.time(), "ticket_id": int(ticket_id)})
    _audit_ticket_event(
        ticket_id=ticket_id,
        run_id=run_id,
        status="executing",
        summary="Work started for a WhatsApp-sourced ticket.",
        details=f"execution_kind={execution_kind or 'workflow'}",
    )


def _client_draft(ticket_title: str, result_summary: str, contact: str) -> str:
    from distr.core.kanban.client_message_humanize import build_client_work_update

    return build_client_work_update(
        contact=contact,
        work_title=ticket_title,
        result_summary=result_summary,
    )


def _lifecycle_by_ticket(ticket_id: int) -> dict[str, Any] | None:
    ensure_tables()
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM whatsapp_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).mappings().first()
    return dict(row) if row else None


def _set_run_waiting_kind(run_id: int | None, waiting_kind: str, *, prompt: str = "") -> None:
    if not run_id:
        return
    try:
        from distr.core.db.workflow import AutoWorkflowRun

        with get_session() as db:
            run = db.get(AutoWorkflowRun, int(run_id))
            if not run:
                return
            try:
                run_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                run_data = {}
            run_data["waiting_kind"] = waiting_kind
            if prompt:
                run_data["waiting_prompt"] = prompt[:1500]
            run.status = "waiting"
            run.run_data = json.dumps(run_data)
            db.commit()
    except Exception:
        logger.debug("Could not set waiting_kind=%s on run %s", waiting_kind, run_id, exc_info=True)


def _clear_run_waiting_kind(run_id: int | None) -> None:
    if not run_id:
        return
    try:
        from distr.core.db.workflow import AutoWorkflowRun

        with get_session() as db:
            run = db.get(AutoWorkflowRun, int(run_id))
            if not run:
                return
            try:
                run_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                run_data = {}
            run_data.pop("waiting_kind", None)
            run_data.pop("waiting_prompt", None)
            if run.status == "waiting":
                run.status = "running"
            run.run_data = json.dumps(run_data)
            db.commit()
    except Exception:
        logger.debug("Could not clear waiting_kind on run %s", run_id, exc_info=True)


def is_negative_verification_feedback(value: str) -> bool:
    """True for the two negative judgment labels: redo / this is terrible…"""
    clean = str(value or "").strip()
    if not clean:
        return False
    if _OK_VERIFICATION_RE.match(clean):
        return False
    if _NEGATIVE_VERIFICATION_RE.match(clean):
        return True
    low = clean.lower()
    return low == "redo" or low.startswith("this is terrible")


def suggest_deploy_target(*, result_summary: str = "", ticket_title: str = "", contact: str = "") -> str:
    """Suggest production vs development from local context. Never deploys."""
    blob = f"{ticket_title} {result_summary} {contact}".lower()
    prod_hits = ("production", "prod", "live", "hotfix", "customer-facing", "release")
    dev_hits = ("development", "staging", "spike", "experiment", "prototype", "local only", "dev only")
    if any(h in blob for h in prod_hits) and not any(h in blob for h in dev_hits):
        return "production"
    if any(h in blob for h in dev_hits):
        return "development"
    # Client WhatsApp work that already passed verification defaults toward production;
    # exploratory / empty summaries stay on development.
    if (result_summary or "").strip() and contact:
        return "production"
    return "development"


def _lesson_summary_from_feedback(
    feedback: str,
    *,
    ticket_title: str = "",
    result_summary: str = "",
) -> str:
    clean = " ".join(str(feedback or "").split()).strip()
    title = (ticket_title or "similar WhatsApp work").strip()
    if _OK_VERIFICATION_RE.match(clean) or clean.lower() == "looks good":
        base = f"Verification passed on {title}: keep the same quality bar for similar work."
        if result_summary:
            return f"{base} Prior summary: {result_summary[:240]}"
        return base
    if (
        not clean
        or clean.lower() == "redo"
        or _TERRIBLE_VERIFICATION_RE.match(clean)
        or _NEGATIVE_VERIFICATION_RE.fullmatch(clean)
    ):
        base = (
            f"Verification rejected on {title}: do not treat similar work as verified "
            f"until quality is redone and re-checked."
        )
        if result_summary:
            return f"{base} Prior summary: {result_summary[:240]}"
        return base
    return (
        f"Verification lesson for future {title}: {clean[:400]}. "
        "Apply this before marking similar tickets verified."
    )


def record_verification_lesson(
    *,
    run_id: int | None,
    board_id: int | None,
    project_id: int | None,
    ticket_id: int | None,
    feedback: str,
    ticket_title: str = "",
    result_summary: str = "",
    skill_id: str | None = None,
    skill_name: str | None = None,
    skill_ids: list[str] | None = None,
    harness_category: str | None = None,
    execution_lane: str | None = None,
    judgment_label: str | None = None,
) -> dict[str, Any]:
    """Persist a durable verification lesson scoped to skill/harness + user board.

    Never rewrites a shipped skill definition. Negative judgments accumulate
    per board+skill and may draft a pending proposal for Paul to approve.
    """
    from distr.core.workflow.skill_judgment_memory import (
        normalize_judgment_label,
        record_skill_judgment_event,
        resolve_judgment_skill_context,
    )

    lesson = _lesson_summary_from_feedback(
        feedback, ticket_title=ticket_title, result_summary=result_summary,
    )
    ctx = resolve_judgment_skill_context(
        run_id=run_id,
        ticket_title=ticket_title,
        result_summary=result_summary,
        execution_lane=execution_lane,
        skill_id=skill_id,
        skill_name=skill_name,
        skill_ids=skill_ids,
        harness_category=harness_category,
    )
    label = normalize_judgment_label(judgment_label or feedback)
    recorded = False
    judgment_event: dict[str, Any] = {}
    try:
        judgment_event = record_skill_judgment_event(
            board_id=board_id,
            project_id=project_id,
            skill_id=ctx.get("skill_id"),
            skill_name=ctx.get("skill_name"),
            skill_ids=list(ctx.get("skill_ids") or []),
            harness_category=ctx.get("harness_category"),
            execution_lane=ctx.get("execution_lane"),
            judgment_label=label,
            feedback_text=str(feedback or ""),
            lesson_summary=lesson,
            run_id=run_id,
            ticket_id=ticket_id,
            source="telegram_verification",
            payload={"result_summary": (result_summary or "")[:500]},
        )
        recorded = True
    except Exception:
        logger.exception("Could not record skill-scoped judgment event")

    if run_id:
        try:
            from distr.core.workflow.steering_memory import record_run_steering_feedback

            record_run_steering_feedback(
                run_id=int(run_id),
                message=lesson,
                source="telegram_verification",
                event_type="changes_requested" if label != "looks good" else "verification_ok",
                board_id=int(board_id) if board_id else None,
                ticket_id=int(ticket_id) if ticket_id else None,
                project_id=int(project_id) if project_id else None,
                capture_standard=label != "looks good",
                rule_type="verification_lesson",
            )
            recorded = True
        except Exception:
            logger.exception("Could not record verification steering feedback")

    # Board-scoped learned rule with skill tags in payload — never global skill packs.
    # Positive judgments stay as lighter evidence (disabled until repeated).
    try:
        from distr.core.orchestrator import record_learning_signal

        positive = label == "looks good"
        record_learning_signal(
            scope="board" if board_id else "project" if project_id else "global",
            scope_id=board_id or project_id,
            rule_type="verification_lesson",
            summary=lesson[:500],
            payload={
                "run_id": run_id,
                "ticket_id": ticket_id,
                "source": "telegram_verification",
                "raw_feedback": str(feedback or "")[:500],
                "judgment_label": label,
                "skill_id": ctx.get("skill_id"),
                "skill_name": ctx.get("skill_name"),
                "skill_ids": list(ctx.get("skill_ids") or []),
                "harness_category": ctx.get("harness_category"),
                "execution_lane": ctx.get("execution_lane"),
                "skill_judgment_event_id": judgment_event.get("event_id"),
                "proposal": judgment_event.get("proposal"),
            },
            enabled=False if positive else True,
            promote_after=3 if positive else 1,
        )
        recorded = True
    except Exception:
        logger.debug("Could not upsert verification learned rule", exc_info=True)
    return {
        "recorded": recorded,
        "lesson": lesson,
        "skill_id": ctx.get("skill_id"),
        "skill_name": ctx.get("skill_name"),
        "skill_ids": list(ctx.get("skill_ids") or []),
        "harness_category": ctx.get("harness_category"),
        "execution_lane": ctx.get("execution_lane"),
        "judgment_label": label,
        "judgment_event_id": judgment_event.get("event_id"),
        "proposal": judgment_event.get("proposal"),
    }


def verification_lessons_context(
    *,
    board_id: int | None,
    limit: int = 6,
    skill_id: str | None = None,
    skill_ids: list[str] | None = None,
    harness_category: str | None = None,
) -> str:
    """Learned verification rules for the next similar ticket / run context.

    Filters by matching skill/harness so a UI lesson does not poison a backend skill.
    """
    if not board_id:
        return ""
    skill_block = ""
    try:
        from distr.core.workflow.skill_judgment_memory import (
            format_skill_judgment_lessons,
            matching_skill_judgment_lessons,
        )

        scoped = matching_skill_judgment_lessons(
            board_id=int(board_id),
            skill_id=skill_id,
            skill_ids=skill_ids,
            harness_category=harness_category,
            limit=limit,
            negative_only=True,
        )
        skill_block = format_skill_judgment_lessons(scoped)
    except Exception:
        logger.debug("Could not load skill-scoped judgment lessons", exc_info=True)

    try:
        from distr.core.orchestrator import build_learned_rules_context, list_learned_rules

        general = build_learned_rules_context(int(board_id), limit=limit)
        rows = list_learned_rules(board_id=int(board_id), enabled_only=True, limit=limit * 2)
        verification = []
        active_ids = {str(s).strip() for s in (skill_ids or []) if str(s).strip()}
        if skill_id and str(skill_id).strip():
            active_ids.add(str(skill_id).strip())
        wanted_harness = str(harness_category or "").strip()
        for r in rows:
            if str(r.get("rule_type") or "") != "verification_lesson":
                continue
            payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            if not isinstance(payload, dict):
                payload = {}
            # Nested latest/merged shapes from record_learning_signal upserts.
            latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else payload
            lesson_skill = str(latest.get("skill_id") or "").strip()
            lesson_harness = str(latest.get("harness_category") or "").strip()
            if active_ids:
                if not lesson_skill or lesson_skill not in active_ids:
                    continue
            elif wanted_harness:
                if lesson_skill:
                    continue
                if lesson_harness and lesson_harness != wanted_harness:
                    continue
                if not lesson_harness:
                    continue
            else:
                if lesson_skill:
                    continue
            verification.append(r)
            if len(verification) >= limit:
                break
        lines = ["[VERIFICATION LESSONS]"]
        for rule in verification:
            summary = " ".join(str(rule.get("summary") or "").split()).strip()
            if summary:
                lines.append(f"- {summary[:320]}")
        block = "\n".join(lines) if len(lines) > 1 else ""
        parts = [p for p in (skill_block, block) if p]
        if not parts:
            # No skill filter active: fall back to general board rules.
            if not active_ids and not wanted_harness:
                return general
            return skill_block or ""
        combined = "\n".join(parts)
        if general and not active_ids and not wanted_harness and "[VERIFICATION LESSONS]" not in general:
            return f"{combined}\n{general}"
        return combined
    except Exception:
        return skill_block


def verification_markup(token: str) -> dict[str, Any]:
    return {"inline_keyboard": [
        [
            {"text": "looks good", "callback_data": f"wv:{token}:ok"},
            {"text": "redo", "callback_data": f"wv:{token}:redo"},
        ],
        [
            {
                "text": "this is terrible, and this is why",
                "callback_data": f"wv:{token}:terrible",
            },
        ],
    ]}


def deploy_markup(token: str, *, suggestion: str = "development") -> dict[str, Any]:
    prod_label = "Production ✓" if suggestion == "production" else "Production"
    dev_label = "Development ✓" if suggestion == "development" else "Development"
    return {"inline_keyboard": [[
        {"text": prod_label, "callback_data": f"wd:{token}:prod"},
        {"text": dev_label, "callback_data": f"wd:{token}:dev"},
    ]]}


_REPLY_ACTION = {
    "yes": "send",
    "send": "send",
    "no": "leave",
    "leave": "leave",
    "add": "revise",
    "revise": "revise",
}


def reply_card_instructions() -> str:
    """User-facing Yes / No / Add copy. Mechanics stay send / leave / revise."""
    return (
        "Reply **Yes** to send this WhatsApp reply.\n"
        "Reply **No** to not send it.\n"
        "Reply **Add** to add something before sending."
    )


def review_markup(token: str) -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": "Yes", "callback_data": f"wa:{token}:yes"},
        {"text": "No", "callback_data": f"wa:{token}:no"},
        {"text": "Add", "callback_data": f"wa:{token}:add"},
    ]]}


def _create_gate_token(*, lifecycle_id: int, gate_kind: str, suggestion: str = "") -> str:
    token = secrets.token_urlsafe(12)
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE whatsapp_gate_reviews SET status='superseded', updated_at=:now "
            "WHERE lifecycle_id=:id AND gate_kind=:kind AND status IN ('pending','awaiting_details')"
        ), {"now": now, "id": int(lifecycle_id), "kind": gate_kind})
        conn.execute(text("""
            INSERT INTO whatsapp_gate_reviews(
                token, lifecycle_id, gate_kind, status, suggestion, created_at, updated_at
            ) VALUES (:token, :lifecycle_id, :gate_kind, 'pending', :suggestion, :now, :now)
        """), {
            "token": token,
            "lifecycle_id": int(lifecycle_id),
            "gate_kind": gate_kind,
            "suggestion": suggestion or "",
            "now": now,
        })
    return token


def _gate_row(token: str) -> dict[str, Any] | None:
    ensure_tables()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT g.*, l.ticket_id, l.board_id, l.project_id, l.run_id,
                   l.source_jid, l.source_phone, l.source_contact,
                   l.result_summary, l.deploy_status, l.deploy_target,
                   l.verification_status, l.status AS lifecycle_status,
                   l.skill_id, l.skill_name, l.skill_ids_json,
                   l.harness_category, l.execution_lane
            FROM whatsapp_gate_reviews g
            JOIN whatsapp_work_lifecycles l ON l.id=g.lifecycle_id
            WHERE g.token=:token
        """), {"token": token}).mappings().first()
    return dict(row) if row else None


def _ticket_title(ticket_id: int) -> str:
    try:
        from distr.core.db.kanban import KanbanTicket

        with get_session() as db:
            ticket = db.get(KanbanTicket, int(ticket_id))
            if ticket and ticket.title:
                return str(ticket.title)
    except Exception:
        pass
    return ""



def _lifecycle_skill_context(lifecycle: dict[str, Any] | None, *, run_id: int | None = None,
                             ticket_title: str = "", result_summary: str = "") -> dict[str, Any]:
    """Skill/harness tags stored on the lifecycle, falling back to the active run."""
    from distr.core.workflow.skill_judgment_memory import resolve_judgment_skill_context
    import json as _json

    lifecycle = lifecycle or {}
    stored_ids: list[str] = []
    raw_ids = lifecycle.get("skill_ids_json")
    if raw_ids:
        try:
            loaded = _json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
            if isinstance(loaded, list):
                stored_ids = [str(x).strip() for x in loaded if str(x).strip()]
        except Exception:
            stored_ids = []
    return resolve_judgment_skill_context(
        run_id=run_id or lifecycle.get("run_id"),
        ticket_title=ticket_title,
        result_summary=result_summary or str(lifecycle.get("result_summary") or ""),
        execution_lane=lifecycle.get("execution_lane"),
        skill_id=lifecycle.get("skill_id"),
        skill_name=lifecycle.get("skill_name"),
        skill_ids=stored_ids or None,
        harness_category=lifecycle.get("harness_category"),
    )


def begin_post_completion_gates(
    *, ticket_id: int, run_id: int, status: str, result_summary: str = "",
) -> dict[str, Any] | None:
    """Start verification review after completed work (before deploy / client draft)."""
    ensure_tables()
    normalized = (status or "").strip().lower()
    lifecycle = _lifecycle_by_ticket(ticket_id)
    if not lifecycle:
        return None
    if normalized != "completed":
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE whatsapp_work_lifecycles SET status='execution_failed', run_id=:run_id,
                  updated_at=:now, error=:error WHERE ticket_id=:ticket_id
            """), {
                "run_id": int(run_id), "now": time.time(),
                "error": normalized or "failed", "ticket_id": int(ticket_id),
            })
        return None

    title = _ticket_title(ticket_id) or "the requested work"
    contact = str(lifecycle.get("source_contact") or "").strip()
    summary = (result_summary or "").strip()[:1200]
    skill_ctx = _lifecycle_skill_context(
        lifecycle, run_id=run_id, ticket_title=title, result_summary=summary,
    )
    lessons = verification_lessons_context(
        board_id=lifecycle.get("board_id"),
        skill_id=skill_ctx.get("skill_id"),
        skill_ids=list(skill_ctx.get("skill_ids") or []),
        harness_category=skill_ctx.get("harness_category"),
    )
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_work_lifecycles
            SET run_id=:run_id,
                execution_kind=COALESCE(execution_kind, 'workflow'),
                status='awaiting_verification',
                verification_status='pending',
                deploy_status=NULL,
                deploy_target=NULL,
                result_summary=:summary,
                skill_id=:skill_id,
                skill_name=:skill_name,
                skill_ids_json=:skill_ids_json,
                harness_category=:harness_category,
                execution_lane=:execution_lane,
                reply_draft=NULL,
                reply_status=NULL,
                updated_at=:now,
                error=NULL
            WHERE ticket_id=:ticket_id
        """), {
            "run_id": int(run_id), "summary": summary,
            "skill_id": skill_ctx.get("skill_id"),
            "skill_name": skill_ctx.get("skill_name"),
            "skill_ids_json": json.dumps(list(skill_ctx.get("skill_ids") or [])),
            "harness_category": skill_ctx.get("harness_category"),
            "execution_lane": skill_ctx.get("execution_lane") or "workflow",
            "now": now, "ticket_id": int(ticket_id),
        })
        lifecycle_id = conn.execute(text(
            "SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).scalar_one()

    token = _create_gate_token(lifecycle_id=int(lifecycle_id), gate_kind="verification")
    prompt = (
        f"Verification check for **{title}**"
        + (f" ({contact})" if contact else "")
        + ".\n\n"
        + (f"Result summary:\n{summary}\n\n" if summary else "")
        + (f"{lessons}\n\n" if lessons else "")
        + "Judge this finished work with one of: **looks good**, **redo**, "
        "or **this is terrible, and this is why**. "
        "Learning only runs on those three; after a negative judgment it remembers in the background."
    )
    _set_run_waiting_kind(run_id, WAITING_KIND_VERIFICATION, prompt=prompt)
    _audit_ticket_event(
        ticket_id=ticket_id,
        run_id=run_id,
        status="awaiting_verification",
        summary="Verified completion is waiting on Paul verification review before deploy assist.",
        details=f"contact={contact or 'unknown'}",
    )
    return {
        "token": token,
        "ticket_id": int(ticket_id),
        "gate_kind": "verification",
        "waiting_kind": WAITING_KIND_VERIFICATION,
        "title": title,
        "contact": contact,
        "result_summary": summary,
        "text": prompt,
        "reply_markup": verification_markup(token),
    }


def notify_telegram_verification(gate: dict[str, Any]) -> bool:
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        manager = _telegram_manager_from_app()
        if not manager:
            return False
        return bool(manager.send_to_telegram(
            gate.get("text") or "Verification check ready.",
            reply_markup=gate.get("reply_markup") or verification_markup(str(gate["token"])),
        ))
    except Exception:
        logger.exception("Could not send WhatsApp verification review to Telegram")
        return False


def _start_deploy_gate(*, lifecycle_id: int, ticket_id: int, run_id: int | None) -> dict[str, Any]:
    lifecycle = _lifecycle_by_ticket(ticket_id)
    title = _ticket_title(ticket_id) or "the requested work"
    summary = str((lifecycle or {}).get("result_summary") or "")
    contact = str((lifecycle or {}).get("source_contact") or "")
    suggestion = suggest_deploy_target(
        result_summary=summary, ticket_title=title, contact=contact,
    )
    token = _create_gate_token(
        lifecycle_id=int(lifecycle_id), gate_kind="deploy", suggestion=suggestion,
    )
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_work_lifecycles
            SET status='awaiting_deploy_assist', verification_status='approved',
                deploy_status='pending', deploy_target=NULL, updated_at=:now
            WHERE id=:id
        """), {"now": now, "id": int(lifecycle_id)})
    suggest_label = "production" if suggestion == "production" else "development"
    other = "development" if suggestion == "production" else "production"
    prompt = (
        f"Deploy assist for **{title}** (local decision only — nothing will be deployed).\n\n"
        f"Based on context I suggest **{suggest_label}**"
        f" (you can still choose {other}).\n\n"
        "Confirm **Production** or **Development**. "
        "The client WhatsApp draft is offered only after you confirm."
    )
    _set_run_waiting_kind(run_id, WAITING_KIND_DEPLOY, prompt=prompt)
    _audit_ticket_event(
        ticket_id=ticket_id,
        run_id=run_id,
        status="awaiting_deploy_assist",
        summary="Verification approved; waiting for production vs development confirm.",
        details=f"suggestion={suggestion}",
    )
    return {
        "token": token,
        "ticket_id": int(ticket_id),
        "gate_kind": "deploy",
        "waiting_kind": WAITING_KIND_DEPLOY,
        "suggestion": suggestion,
        "text": prompt,
        "reply_markup": deploy_markup(token, suggestion=suggestion),
    }


def notify_telegram_deploy(gate: dict[str, Any]) -> bool:
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        manager = _telegram_manager_from_app()
        if not manager:
            return False
        suggestion = str(gate.get("suggestion") or "development")
        return bool(manager.send_to_telegram(
            gate.get("text") or "Deploy assist ready.",
            reply_markup=gate.get("reply_markup") or deploy_markup(
                str(gate["token"]), suggestion=suggestion,
            ),
        ))
    except Exception:
        logger.exception("Could not send WhatsApp deploy assist to Telegram")
        return False


def prepare_completed_reply(
    *, ticket_id: int, run_id: int, status: str, result_summary: str = "",
) -> dict[str, Any] | None:
    """Create a WhatsApp draft and Telegram review token after deploy gate is settled.

    Client Yes/No/Add (send/leave/revise) is intentionally gated: call
    ``begin_post_completion_gates`` first, then settle deploy. Direct callers
    that already settled deploy may pass status=completed.
    """
    ensure_tables()
    normalized = (status or "").strip().lower()
    lifecycle = _lifecycle_by_ticket(ticket_id)
    if not lifecycle:
        return None
    if normalized != "completed":
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE whatsapp_work_lifecycles SET status='execution_failed', run_id=:run_id,
                  updated_at=:now, error=:error WHERE ticket_id=:ticket_id
            """), {"run_id": int(run_id), "now": time.time(), "error": normalized or "failed", "ticket_id": int(ticket_id)})
        return None

    if str(lifecycle.get("deploy_status") or "") != "settled":
        # Draft must not be offered until Paul confirms prod vs dev.
        return None

    from distr.core.db.kanban import KanbanTicket
    from distr.core.kanban.whatsapp_compose_drafts import save_compose_draft

    with get_session() as db:
        ticket = db.get(KanbanTicket, int(ticket_id))
        if not ticket:
            return None
        title = ticket.title or "the requested work"
    contact = str(lifecycle.get("source_contact") or "").strip()
    summary = (result_summary or str(lifecycle.get("result_summary") or "")).strip()
    draft = _client_draft(title, summary, contact)
    phone = str(lifecycle.get("source_phone") or "").strip()
    jid = str(lifecycle.get("source_jid") or "").strip()
    if not phone and jid:
        phone = jid.split("@", 1)[0]
    if not phone:
        return None
    save_compose_draft(
        jid_phone=phone, jid=jid, contact_name=contact, board_id=lifecycle.get("board_id"),
        text=draft, source="agent", sanitize=True,
    )
    token = secrets.token_urlsafe(12)
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_work_lifecycles SET run_id=:run_id, execution_kind=COALESCE(execution_kind, 'workflow'),
              status='awaiting_reply_review', reply_draft=:draft, reply_status='pending',
              updated_at=:now, error=NULL WHERE ticket_id=:ticket_id
        """), {"run_id": int(run_id), "draft": draft, "now": now, "ticket_id": int(ticket_id)})
        lifecycle_id = conn.execute(text(
            "SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).scalar_one()
        conn.execute(text(
            "UPDATE whatsapp_reply_reviews SET status='superseded', updated_at=:now "
            "WHERE lifecycle_id=:id AND status IN ('pending','awaiting_revision')"
        ), {"now": now, "id": lifecycle_id})
        conn.execute(text("""
            INSERT INTO whatsapp_reply_reviews(token, lifecycle_id, status, created_at, updated_at)
            VALUES (:token, :lifecycle_id, 'pending', :now, :now)
        """), {"token": token, "lifecycle_id": lifecycle_id, "now": now})
    _clear_run_waiting_kind(run_id)
    deploy_target = str(lifecycle.get("deploy_target") or "")
    _audit_ticket_event(
        ticket_id=ticket_id,
        run_id=run_id,
        status="awaiting_reply_review",
        summary="Deploy gate settled; WhatsApp reply draft awaiting Telegram approval.",
        details=f"contact={contact or phone}; deploy_target={deploy_target or 'unknown'}; source_jid={jid or 'unknown'}",
    )
    return {
        "token": token,
        "ticket_id": int(ticket_id),
        "draft": draft,
        "contact": contact or phone,
        "deploy_target": deploy_target,
    }


def notify_telegram_review(review: dict[str, Any]) -> bool:
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app
        manager = _telegram_manager_from_app()
        if not manager:
            return False
        deploy_bit = ""
        if review.get("deploy_target"):
            deploy_bit = f" (deploy assist: {review['deploy_target']})"
        return bool(manager.send_to_telegram(
            f"The work is in QA{deploy_bit}. I prepared this WhatsApp reply for {review['contact']}:\n\n"
            f"{review['draft']}\n\n{reply_card_instructions()}",
            reply_markup=review_markup(review["token"]),
        ))
    except Exception:
        logger.exception("Could not send WhatsApp reply review to Telegram")
        return False


def notify_post_completion(ticket_id: int, run_id: int, status: str, result_summary: str = "") -> dict[str, Any] | None:
    """Dispatcher/CLI entry: verification → (later) deploy → draft."""
    gate = begin_post_completion_gates(
        ticket_id=ticket_id,
        run_id=run_id,
        status=status,
        result_summary=result_summary,
    )
    if not gate:
        return None
    notify_telegram_verification(gate)
    return gate


def _review_row(token: str) -> dict[str, Any] | None:
    ensure_tables()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT r.*, l.ticket_id, l.source_jid, l.source_phone, l.source_contact,
                   l.board_id, l.reply_draft, l.deploy_status, l.deploy_target
            FROM whatsapp_reply_reviews r
            JOIN whatsapp_work_lifecycles l ON l.id=r.lifecycle_id
            WHERE r.token=:token
        """), {"token": token}).mappings().first()
    return dict(row) if row else None


def _pending_gate_for_chat(chat_id: int | str | None, gate_kind: str) -> list[dict[str, Any]]:
    ensure_tables()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT g.token, g.status FROM whatsapp_gate_reviews g
            WHERE g.status IN ('pending', 'awaiting_details') AND g.gate_kind=:kind
              AND (:chat IS NULL OR g.telegram_chat_id IS NULL OR g.telegram_chat_id=:chat)
            ORDER BY g.updated_at DESC LIMIT 2
        """), {
            "kind": gate_kind,
            "chat": str(chat_id) if chat_id is not None else None,
        }).mappings().all()
    return [dict(r) for r in rows]


def _approve_verification(token: str, *, chat_id: int | str | None) -> dict[str, Any]:
    row = _gate_row(token)
    if not row or row.get("gate_kind") != "verification":
        return {"handled": True, "text": "That verification review no longer exists."}
    if row["status"] not in {"pending", "awaiting_details"}:
        return {"handled": True, "text": "That verification decision was already applied."}
    # Lighter-weight positive signal, still scoped to the active skill/harness.
    try:
        title = _ticket_title(int(row["ticket_id"]))
        skill_ctx = _lifecycle_skill_context(
            row,
            run_id=row.get("run_id"),
            ticket_title=title,
            result_summary=str(row.get("result_summary") or ""),
        )
        record_verification_lesson(
            run_id=row.get("run_id"),
            board_id=row.get("board_id"),
            project_id=row.get("project_id"),
            ticket_id=row.get("ticket_id"),
            feedback="looks good",
            ticket_title=title,
            result_summary=str(row.get("result_summary") or ""),
            skill_id=skill_ctx.get("skill_id"),
            skill_name=skill_ctx.get("skill_name"),
            skill_ids=list(skill_ctx.get("skill_ids") or []),
            harness_category=skill_ctx.get("harness_category"),
            execution_lane=skill_ctx.get("execution_lane"),
            judgment_label="looks good",
        )
    except Exception:
        logger.debug("Could not record positive verification signal", exc_info=True)
    now = time.time()
    with engine.begin() as conn:
        claimed = conn.execute(text("""
            UPDATE whatsapp_gate_reviews
            SET status='resolved', resolved_action='ok',
                telegram_chat_id=COALESCE(:chat, telegram_chat_id), updated_at=:now
            WHERE token=:token AND status IN ('pending', 'awaiting_details')
        """), {
            "chat": str(chat_id) if chat_id is not None else None,
            "now": now,
            "token": token,
        }).rowcount
    if not claimed:
        return {"handled": True, "text": "That verification decision was already applied."}
    deploy = _start_deploy_gate(
        lifecycle_id=int(row["lifecycle_id"]),
        ticket_id=int(row["ticket_id"]),
        run_id=row.get("run_id"),
    )
    notify_telegram_deploy(deploy)
    return {
        "handled": True,
        "text": deploy["text"],
        "reply_markup": deploy["reply_markup"],
        "token": deploy["token"],
        "action": "verification_ok",
        "waiting_kind": WAITING_KIND_DEPLOY,
    }


def _reject_verification(
    token: str,
    *,
    chat_id: int | str | None,
    feedback: str,
) -> dict[str, Any]:
    row = _gate_row(token)
    if not row or row.get("gate_kind") != "verification":
        return {"handled": True, "text": "That verification review no longer exists."}
    if row["status"] not in {"pending", "awaiting_details"}:
        return {"handled": True, "text": "That verification decision was already applied."}
    title = _ticket_title(int(row["ticket_id"]))
    skill_ctx = _lifecycle_skill_context(
        row,
        run_id=row.get("run_id"),
        ticket_title=title,
        result_summary=str(row.get("result_summary") or ""),
    )
    lesson = record_verification_lesson(
        run_id=row.get("run_id"),
        board_id=row.get("board_id"),
        project_id=row.get("project_id"),
        ticket_id=row.get("ticket_id"),
        feedback=feedback or "redo",
        ticket_title=title,
        result_summary=str(row.get("result_summary") or ""),
        skill_id=skill_ctx.get("skill_id"),
        skill_name=skill_ctx.get("skill_name"),
        skill_ids=list(skill_ctx.get("skill_ids") or []),
        harness_category=skill_ctx.get("harness_category"),
        execution_lane=skill_ctx.get("execution_lane"),
    )
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_gate_reviews
            SET status='resolved', resolved_action='redo',
                telegram_chat_id=COALESCE(:chat, telegram_chat_id), updated_at=:now
            WHERE token=:token AND status IN ('pending', 'awaiting_details')
        """), {
            "chat": str(chat_id) if chat_id is not None else None,
            "now": now,
            "token": token,
        })
        conn.execute(text("""
            UPDATE whatsapp_work_lifecycles
            SET status='verification_rejected', verification_status='rejected',
                updated_at=:now WHERE id=:id
        """), {"now": now, "id": row["lifecycle_id"]})
    _clear_run_waiting_kind(row.get("run_id"))
    _audit_ticket_event(
        ticket_id=int(row["ticket_id"]),
        run_id=row.get("run_id"),
        status="verification_rejected",
        summary="Paul rejected verification; durable lesson recorded for similar future work.",
        details=(lesson.get("lesson") or "")[:500],
    )
    return {
        "handled": True,
        "text": (
            "Got it — I will not treat that as verified. "
            f"Recorded lesson for similar future work:\n{lesson.get('lesson')}\n\n"
            "No client draft will be offered until verification and deploy assist both pass."
        ),
        "action": "verification_redo",
        "lesson": lesson.get("lesson"),
    }


def _settle_deploy(token: str, *, target: str, chat_id: int | str | None) -> dict[str, Any]:
    row = _gate_row(token)
    if not row or row.get("gate_kind") != "deploy":
        return {"handled": True, "text": "That deploy assist decision no longer exists."}
    if row["status"] != "pending":
        return {"handled": True, "text": "That deploy assist decision was already applied."}
    normalized = "production" if target in {"prod", "production"} else "development"
    now = time.time()
    with engine.begin() as conn:
        claimed = conn.execute(text("""
            UPDATE whatsapp_gate_reviews
            SET status='resolved', resolved_action=:action,
                telegram_chat_id=COALESCE(:chat, telegram_chat_id), updated_at=:now
            WHERE token=:token AND status='pending'
        """), {
            "action": normalized,
            "chat": str(chat_id) if chat_id is not None else None,
            "now": now,
            "token": token,
        }).rowcount
        if claimed:
            conn.execute(text("""
                UPDATE whatsapp_work_lifecycles
                SET deploy_status='settled', deploy_target=:target,
                    status='deploy_settled', updated_at=:now
                WHERE id=:id
            """), {"target": normalized, "now": now, "id": row["lifecycle_id"]})
    if not claimed:
        return {"handled": True, "text": "That deploy assist decision was already applied."}

    _audit_ticket_event(
        ticket_id=int(row["ticket_id"]),
        run_id=row.get("run_id"),
        status="deploy_settled",
        summary=f"Deploy assist settled as {normalized} (decision only — nothing deployed).",
        details=f"target={normalized}",
    )
    review = prepare_completed_reply(
        ticket_id=int(row["ticket_id"]),
        run_id=int(row.get("run_id") or 0),
        status="completed",
        result_summary=str(row.get("result_summary") or ""),
    )
    if not review:
        return {
            "handled": True,
            "text": (
                f"Recorded deploy assist as **{normalized}** (nothing was deployed). "
                "I could not prepare the WhatsApp draft yet — check the source contact/phone."
            ),
            "action": "deploy_settled",
            "deploy_target": normalized,
        }
    notify_telegram_review(review)
    return {
        "handled": True,
        "text": (
            f"Deploy assist settled as **{normalized}** (nothing was deployed).\n\n"
            f"The work is in QA. I prepared this WhatsApp reply for {review['contact']}:\n\n"
            f"{review['draft']}\n\n{reply_card_instructions()}"
        ),
        "reply_markup": review_markup(review["token"]),
        "token": review["token"],
        "action": "deploy_settled",
        "deploy_target": normalized,
        "draft": review["draft"],
    }


def handle_telegram_reply(value: str, *, chat_id: int | str | None = None) -> dict[str, Any] | None:
    """Resolve verification / deploy / reply-review callbacks and plain replies."""
    ensure_tables()
    clean = str(value or "").strip()

    verification_cb = re.fullmatch(r"wv:([A-Za-z0-9_-]+):(ok|redo|terrible)", clean, re.I)
    if verification_cb:
        token, action = verification_cb.group(1), verification_cb.group(2).lower()
        if action == "ok":
            return _approve_verification(token, chat_id=chat_id)
        if action == "terrible":
            return _request_terrible_why(token, chat_id=chat_id)
        return _reject_verification(token, chat_id=chat_id, feedback="redo")

    deploy_cb = re.fullmatch(r"wd:([A-Za-z0-9_-]+):(prod|dev|production|development)", clean, re.I)
    if deploy_cb:
        token, action = deploy_cb.group(1), deploy_cb.group(2).lower()
        target = "production" if action in {"prod", "production"} else "development"
        return _settle_deploy(token, target=target, chat_id=chat_id)

    callback = re.fullmatch(r"wa:([A-Za-z0-9_-]+):(yes|no|add|send|revise|leave)", clean, re.I)
    if callback:
        token, action = callback.group(1), _REPLY_ACTION[callback.group(2).lower()]
        row = _review_row(token)
        if not row:
            return {"handled": True, "text": "That WhatsApp draft review no longer exists."}
        if str(row.get("deploy_status") or "") != "settled":
            return {
                "handled": True,
                "text": "Deploy assist is not settled yet. Confirm Production or Development first.",
            }
        if row["status"] in {"sent", "left_draft"}:
            return {"handled": True, "text": "That WhatsApp draft decision was already applied."}
        if row["status"] == "resolving":
            return {"handled": True, "text": "That WhatsApp draft decision is already being applied."}
        if action == "revise":
            with engine.begin() as conn:
                changed = conn.execute(text(
                    "UPDATE whatsapp_reply_reviews SET status='awaiting_revision', "
                    "telegram_chat_id=:chat, updated_at=:now WHERE token=:token AND status='pending'"
                ), {
                    "chat": str(chat_id) if chat_id is not None else None,
                    "now": time.time(),
                    "token": token,
                }).rowcount
            if not changed:
                return {"handled": True, "text": "That WhatsApp draft is no longer waiting for revision."}
            return {
                "handled": True,
                "text": "Send me what you want to add in your next Telegram message. I will show the card again before sending.",
            }
        with engine.begin() as conn:
            claimed = conn.execute(text(
                "UPDATE whatsapp_reply_reviews SET status='resolving', telegram_chat_id=:chat, "
                "updated_at=:now WHERE token=:token AND status='pending'"
            ), {
                "chat": str(chat_id) if chat_id is not None else None,
                "now": time.time(),
                "token": token,
            }).rowcount
        if not claimed:
            return {"handled": True, "text": "That WhatsApp draft decision is already being applied or has expired."}
        if action == "leave":
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE whatsapp_reply_reviews SET status='left_draft', resolved_action='leave', "
                    "updated_at=:now WHERE token=:token"
                ), {"now": time.time(), "token": token})
                conn.execute(text(
                    "UPDATE whatsapp_work_lifecycles SET status='reply_draft_ready', "
                    "reply_status='left_draft', updated_at=:now WHERE id=:id"
                ), {"now": time.time(), "id": row["lifecycle_id"]})
            _audit_ticket_event(
                ticket_id=row["ticket_id"],
                run_id=None,
                status="reply_draft_ready",
                summary="Telegram approval left the WhatsApp reply as a draft.",
            )
            return {"handled": True, "text": "I left the reply in the WhatsApp composer as a draft."}
        from distr.core.integrations.whatsapp.relay_client import send_message_via_relay
        try:
            result = send_message_via_relay(jid=row["source_jid"], text=row["reply_draft"])
        except Exception as exc:
            logger.exception("WhatsApp relay failed while sending reviewed draft")
            result = {"success": False, "error": str(exc)}
        if not result.get("success"):
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE whatsapp_reply_reviews SET status='pending', error=:error, "
                    "updated_at=:now WHERE token=:token AND status='resolving'"
                ), {
                    "error": str(result.get("error") or "send failed"),
                    "now": time.time(),
                    "token": token,
                })
            _audit_ticket_event(
                ticket_id=row["ticket_id"],
                run_id=None,
                status="reply_send_failed",
                summary="Approved WhatsApp reply could not be sent; the draft remains saved.",
            )
            return {
                "handled": True,
                "text": (
                    f"WhatsApp did not accept the message: {result.get('error') or 'send failed'}. "
                    "The draft is still saved."
                ),
            }
        from distr.core.kanban.whatsapp_compose_drafts import delete_compose_draft
        delete_compose_draft(row["source_phone"] or str(row["source_jid"] or "").split("@", 1)[0])
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE whatsapp_reply_reviews SET status='sent', resolved_action='send', "
                "updated_at=:now WHERE token=:token"
            ), {"now": time.time(), "token": token})
            conn.execute(text(
                "UPDATE whatsapp_work_lifecycles SET status='reply_sent', reply_status='sent', "
                "updated_at=:now WHERE id=:id"
            ), {"now": time.time(), "id": row["lifecycle_id"]})
        _audit_ticket_event(
            ticket_id=row["ticket_id"],
            run_id=None,
            status="reply_sent",
            summary="Telegram approval sent the reply back to the originating WhatsApp chat.",
            details=f"source_jid={row['source_jid'] or 'unknown'}",
        )
        return {
            "handled": True,
            "text": "WhatsApp message sent. The ticket remains in QA until you move it to Complete.",
        }

    # Plain-text verification replies — only the three canonical judgments.
    pending_verification = _pending_gate_for_chat(chat_id, "verification")
    if len(pending_verification) == 1 and clean:
        token = pending_verification[0]["token"]
        gate_status = str(pending_verification[0].get("status") or "pending")
        if gate_status == "awaiting_details":
            return _reject_verification(
                token,
                chat_id=chat_id,
                feedback=f"this is terrible, and this is why: {clean}",
            )
        if _OK_VERIFICATION_RE.match(clean):
            return _approve_verification(token, chat_id=chat_id)
        if re.fullmatch(r"redo", clean, re.I):
            return _reject_verification(token, chat_id=chat_id, feedback="redo")
        if _TERRIBLE_VERIFICATION_RE.match(clean):
            rest = _TERRIBLE_VERIFICATION_RE.sub("", clean, count=1).strip(" :,-")
            if rest:
                return _reject_verification(
                    token,
                    chat_id=chat_id,
                    feedback=f"this is terrible, and this is why: {rest}",
                )
            return _request_terrible_why(token, chat_id=chat_id)

    pending_deploy = _pending_gate_for_chat(chat_id, "deploy")
    if len(pending_deploy) == 1 and clean:
        low = re.sub(r"\s+", " ", clean.lower()).strip(" .!?")
        if low in {"production", "prod", "ship to production", "go production"}:
            return _settle_deploy(pending_deploy[0]["token"], target="production", chat_id=chat_id)
        if low in {"development", "dev", "go development", "keep on development"}:
            return _settle_deploy(pending_deploy[0]["token"], target="development", chat_id=chat_id)

    # Yes / No / Add words on a pending send-back card. wi: intake is a different handler.
    if not _pending_gate_for_chat(chat_id, "verification") and not _pending_gate_for_chat(chat_id, "deploy"):
        word = re.sub(r"\s+", " ", clean.lower()).strip(" .!?")
        mapped = {"yes": "yes", "no": "no", "add": "add"}.get(word)
        if mapped:
            with engine.connect() as conn:
                pending_send = conn.execute(text("""
                    SELECT token FROM whatsapp_reply_reviews
                    WHERE status='pending' AND (:chat IS NULL OR telegram_chat_id IS NULL OR telegram_chat_id=:chat)
                    ORDER BY updated_at DESC LIMIT 2
                """), {"chat": str(chat_id) if chat_id is not None else None}).mappings().all()
            if len(pending_send) == 1:
                return handle_telegram_reply(f"wa:{pending_send[0]['token']}:{mapped}", chat_id=chat_id)

    with engine.connect() as conn:
        pending = conn.execute(text("""
            SELECT token FROM whatsapp_reply_reviews
            WHERE status='awaiting_revision' AND (:chat IS NULL OR telegram_chat_id IS NULL OR telegram_chat_id=:chat)
            ORDER BY updated_at DESC LIMIT 2
        """), {"chat": str(chat_id) if chat_id is not None else None}).mappings().all()
    if len(pending) != 1 or not clean:
        return None
    row = _review_row(pending[0]["token"])
    if not row or str(row.get("deploy_status") or "") != "settled":
        return {
            "handled": True,
            "text": "Deploy assist is not settled yet. Confirm Production or Development first.",
        }
    from distr.core.kanban.whatsapp_compose_drafts import save_compose_draft
    save_compose_draft(
        jid_phone=row["source_phone"], jid=row["source_jid"], contact_name=row["source_contact"],
        board_id=row["board_id"], text=clean, source="agent", sanitize=True,
    )
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE whatsapp_reply_reviews SET status='pending', updated_at=:now WHERE token=:token"
        ), {"now": time.time(), "token": row["token"]})
        conn.execute(text(
            "UPDATE whatsapp_work_lifecycles SET reply_draft=:draft, updated_at=:now WHERE id=:id"
        ), {"draft": clean, "now": time.time(), "id": row["lifecycle_id"]})
    return {
        "handled": True,
        "text": (
            f"Updated WhatsApp draft:\n\n{clean}\n\n{reply_card_instructions()}"
        ),
        "reply_markup": review_markup(row["token"]),
        "token": row["token"],
    }
