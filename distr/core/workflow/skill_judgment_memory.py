"""Per-user, per-skill judgment events and propose-only skill change drafts.

Judgments from WhatsApp / Telegram verification are scoped to the skill or
harness context that was active when Paul judged the work. A single judgment
never rewrites a shipped skill definition — events accumulate per board
(user/tenant) + skill, and at most a pending proposal is drafted for Paul to
approve or reject.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import text

from distr.core.db import engine, get_session

logger = logging.getLogger(__name__)

# Draft a pending proposal as soon as a negative judgment lands for a skill.
# Never auto-applies; Paul must approve before anything is modified.
NEGATIVE_PROPOSAL_THRESHOLD = 1
POSITIVE_LABEL = "looks good"
REDO_LABEL = "redo"
TERRIBLE_LABEL = "this is terrible, and this is why"


def ensure_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS skill_judgment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                board_id INTEGER,
                project_id INTEGER,
                user_key VARCHAR,
                skill_id VARCHAR,
                skill_name VARCHAR,
                skill_ids_json TEXT NOT NULL DEFAULT '[]',
                harness_category VARCHAR,
                execution_lane VARCHAR,
                judgment_label VARCHAR NOT NULL,
                feedback_text TEXT,
                lesson_summary TEXT,
                run_id INTEGER,
                ticket_id INTEGER,
                source VARCHAR,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at FLOAT NOT NULL
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_skill_judgment_events_scope "
            "ON skill_judgment_events(board_id, skill_id, created_at)"
        ))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS skill_judgment_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                board_id INTEGER,
                project_id INTEGER,
                user_key VARCHAR,
                skill_id VARCHAR,
                skill_name VARCHAR,
                harness_category VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                proposal_text TEXT NOT NULL,
                evidence_event_ids TEXT NOT NULL DEFAULT '[]',
                evidence_count INTEGER NOT NULL DEFAULT 0,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                decided_at FLOAT,
                decided_note TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_skill_judgment_proposals_pending "
            "ON skill_judgment_proposals(board_id, skill_id, status, updated_at)"
        ))


def _user_key(*, board_id: int | None, project_id: int | None = None) -> str:
    """Stable tenant key — board is the local user-scoped store in Decisions."""
    if board_id is not None:
        return f"board:{int(board_id)}"
    if project_id is not None:
        return f"project:{int(project_id)}"
    return "local:default"


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def _json_loads(raw: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _skill_display_name(skill_id: str) -> str:
    clean = str(skill_id or "").strip()
    if not clean:
        return ""
    try:
        from distr.core.skills.catalog import registry_entry_for

        entry = registry_entry_for(clean) or {}
        name = str(entry.get("name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    return clean


def _infer_harness_category(blob: str) -> str | None:
    try:
        from distr.core.orchestrator_routing import _infer_harness_category as infer

        return infer(blob or "")
    except Exception:
        return None


def resolve_judgment_skill_context(
    *,
    run_id: int | None = None,
    ticket_title: str = "",
    result_summary: str = "",
    execution_lane: str | None = None,
    skill_id: str | None = None,
    skill_name: str | None = None,
    skill_ids: list[str] | None = None,
    harness_category: str | None = None,
) -> dict[str, Any]:
    """Resolve active skill / harness context from explicit args or the run."""
    collected: list[str] = []
    for value in skill_ids or []:
        clean = str(value or "").strip()
        if clean and clean not in collected:
            collected.append(clean)
    explicit_id = str(skill_id or "").strip()
    if explicit_id and explicit_id not in collected:
        collected.insert(0, explicit_id)

    lane = str(execution_lane or "").strip() or None
    category = str(harness_category or "").strip() or None
    run_data: dict[str, Any] = {}

    if run_id and (not collected or not lane or not category):
        try:
            from distr.core.db.workflow import AutoWorkflowRun

            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.id == int(run_id)
                ).first()
                if run and run.run_data:
                    run_data = _json_loads(run.run_data, {}) or {}
        except Exception:
            logger.debug("Could not load run_data for skill judgment context", exc_info=True)
            run_data = {}

        meta = {}
        for key in ("runtime_metadata", "metadata", "development_metadata"):
            candidate = run_data.get(key)
            if isinstance(candidate, dict):
                meta = candidate
                break

        for source in (
            run_data.get("turn_skill_ids"),
            run_data.get("skill_ids"),
            run_data.get("selected_skills"),
            meta.get("turn_skill_ids"),
            meta.get("skill_ids"),
            meta.get("selected_skills"),
        ):
            if isinstance(source, list):
                for value in source:
                    clean = str(value or "").strip()
                    if clean and clean not in collected:
                        collected.append(clean)
            elif isinstance(source, str) and source.strip():
                clean = source.strip()
                if clean and clean not in collected:
                    collected.append(clean)

        if not lane:
            for key in ("execution_lane", "lane", "execution_kind"):
                raw = run_data.get(key) or meta.get(key)
                if raw:
                    lane = str(raw).strip() or None
                    if lane:
                        break
        if not category:
            for key in ("harness_category", "category"):
                raw = run_data.get(key) or meta.get(key)
                if raw:
                    category = str(raw).strip() or None
                    if category:
                        break

    primary = collected[0] if collected else (explicit_id or "")
    name = str(skill_name or "").strip() or (_skill_display_name(primary) if primary else "")
    if not category:
        category = _infer_harness_category(f"{ticket_title} {result_summary}")
    return {
        "skill_id": primary or None,
        "skill_name": name or None,
        "skill_ids": collected,
        "harness_category": category,
        "execution_lane": lane or "workflow",
    }


def normalize_judgment_label(feedback: str) -> str:
    """Map free text onto the three canonical judgment labels."""
    clean = str(feedback or "").strip()
    low = clean.lower()
    if low == "looks good" or low.startswith("looks good"):
        return POSITIVE_LABEL
    if low == "redo" or low.startswith("redo"):
        return REDO_LABEL
    if "terrible" in low:
        return TERRIBLE_LABEL
    return clean[:80] or "unknown"


def is_negative_judgment_label(label: str) -> bool:
    low = str(label or "").strip().lower()
    return low == REDO_LABEL or low.startswith("this is terrible")


def record_skill_judgment_event(
    *,
    board_id: int | None,
    project_id: int | None = None,
    skill_id: str | None = None,
    skill_name: str | None = None,
    skill_ids: list[str] | None = None,
    harness_category: str | None = None,
    execution_lane: str | None = None,
    judgment_label: str,
    feedback_text: str = "",
    lesson_summary: str = "",
    run_id: int | None = None,
    ticket_id: int | None = None,
    source: str = "telegram_verification",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a per-user per-skill judgment event. Never mutates skill packs."""
    ensure_tables()
    label = normalize_judgment_label(judgment_label)
    ids = [str(s).strip() for s in (skill_ids or []) if str(s).strip()]
    primary = str(skill_id or "").strip() or (ids[0] if ids else "")
    if primary and primary not in ids:
        ids.insert(0, primary)
    name = str(skill_name or "").strip() or (_skill_display_name(primary) if primary else "")
    user_key = _user_key(board_id=board_id, project_id=project_id)
    now = time.time()
    with engine.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO skill_judgment_events(
                board_id, project_id, user_key, skill_id, skill_name, skill_ids_json,
                harness_category, execution_lane, judgment_label, feedback_text,
                lesson_summary, run_id, ticket_id, source, payload_json, created_at
            ) VALUES (
                :board_id, :project_id, :user_key, :skill_id, :skill_name, :skill_ids_json,
                :harness_category, :execution_lane, :judgment_label, :feedback_text,
                :lesson_summary, :run_id, :ticket_id, :source, :payload_json, :created_at
            )
        """), {
            "board_id": int(board_id) if board_id is not None else None,
            "project_id": int(project_id) if project_id is not None else None,
            "user_key": user_key,
            "skill_id": primary or None,
            "skill_name": name or None,
            "skill_ids_json": _json_dumps(ids),
            "harness_category": str(harness_category or "").strip() or None,
            "execution_lane": str(execution_lane or "").strip() or "workflow",
            "judgment_label": label,
            "feedback_text": str(feedback_text or "")[:2000],
            "lesson_summary": str(lesson_summary or "")[:2000],
            "run_id": int(run_id) if run_id is not None else None,
            "ticket_id": int(ticket_id) if ticket_id is not None else None,
            "source": str(source or "telegram_verification")[:80],
            "payload_json": _json_dumps(payload or {}),
            "created_at": now,
        })
        event_id = int(result.lastrowid)

    proposal: dict[str, Any] | None = None
    if is_negative_judgment_label(label) and (primary or harness_category):
        proposal = maybe_draft_skill_proposal(
            board_id=board_id,
            project_id=project_id,
            skill_id=primary or None,
            skill_name=name or None,
            harness_category=harness_category,
            event_id=event_id,
            feedback_text=feedback_text,
            lesson_summary=lesson_summary,
        )

    return {
        "event_id": event_id,
        "user_key": user_key,
        "skill_id": primary or None,
        "skill_name": name or None,
        "skill_ids": ids,
        "harness_category": harness_category,
        "execution_lane": str(execution_lane or "").strip() or "workflow",
        "judgment_label": label,
        "proposal": proposal,
    }


def _build_proposal_text(
    *,
    skill_id: str | None,
    skill_name: str | None,
    harness_category: str | None,
    feedback_text: str,
    lesson_summary: str,
    evidence_count: int,
) -> str:
    target = skill_name or skill_id or (f"harness:{harness_category}" if harness_category else "unscoped work")
    why = " ".join(str(feedback_text or lesson_summary or "").split()).strip()[:600]
    return (
        f"Proposed skill/harness adjustment for **{target}** "
        f"(based on {evidence_count} judgment"
        f"{'s' if evidence_count != 1 else ''}).\n\n"
        f"Observed feedback: {why or '(no detail)'}\n\n"
        "This is a draft only. Do not rewrite the shipped skill pack until Paul "
        "approves. Keep the change scoped to this user/board — never global across "
        "all downloaders of the tool."
    )


def maybe_draft_skill_proposal(
    *,
    board_id: int | None,
    project_id: int | None = None,
    skill_id: str | None,
    skill_name: str | None = None,
    harness_category: str | None = None,
    event_id: int | None = None,
    feedback_text: str = "",
    lesson_summary: str = "",
    threshold: int = NEGATIVE_PROPOSAL_THRESHOLD,
) -> dict[str, Any] | None:
    """Upsert a pending proposal after enough negative evidence. Never applies it."""
    ensure_tables()
    if not skill_id and not harness_category:
        return None
    user_key = _user_key(board_id=board_id, project_id=project_id)
    now = time.time()

    with engine.begin() as conn:
        # Count negative events for this board+skill (or harness when no skill).
        if skill_id:
            count = conn.execute(text("""
                SELECT COUNT(*) FROM skill_judgment_events
                WHERE user_key=:user_key
                  AND skill_id=:skill_id
                  AND judgment_label IN ('redo', 'this is terrible, and this is why')
            """), {"user_key": user_key, "skill_id": skill_id}).scalar_one()
            pending = conn.execute(text("""
                SELECT id, evidence_event_ids, evidence_count FROM skill_judgment_proposals
                WHERE user_key=:user_key AND skill_id=:skill_id AND status='pending'
                ORDER BY updated_at DESC LIMIT 1
            """), {"user_key": user_key, "skill_id": skill_id}).mappings().first()
        else:
            count = conn.execute(text("""
                SELECT COUNT(*) FROM skill_judgment_events
                WHERE user_key=:user_key
                  AND (skill_id IS NULL OR skill_id='')
                  AND harness_category=:cat
                  AND judgment_label IN ('redo', 'this is terrible, and this is why')
            """), {"user_key": user_key, "cat": harness_category}).scalar_one()
            pending = conn.execute(text("""
                SELECT id, evidence_event_ids, evidence_count FROM skill_judgment_proposals
                WHERE user_key=:user_key
                  AND (skill_id IS NULL OR skill_id='')
                  AND harness_category=:cat
                  AND status='pending'
                ORDER BY updated_at DESC LIMIT 1
            """), {"user_key": user_key, "cat": harness_category}).mappings().first()

        evidence_count = int(count or 0)
        if evidence_count < max(1, int(threshold or 1)):
            return None

        evidence_ids = _json_loads(pending["evidence_event_ids"] if pending else "[]", []) or []
        if event_id and int(event_id) not in evidence_ids:
            evidence_ids.append(int(event_id))
        proposal_text = _build_proposal_text(
            skill_id=skill_id,
            skill_name=skill_name,
            harness_category=harness_category,
            feedback_text=feedback_text,
            lesson_summary=lesson_summary,
            evidence_count=evidence_count,
        )

        if pending:
            conn.execute(text("""
                UPDATE skill_judgment_proposals
                SET proposal_text=:text, evidence_event_ids=:ids,
                    evidence_count=:count, updated_at=:now,
                    skill_name=COALESCE(:skill_name, skill_name),
                    harness_category=COALESCE(:cat, harness_category)
                WHERE id=:id
            """), {
                "text": proposal_text,
                "ids": _json_dumps(evidence_ids),
                "count": evidence_count,
                "now": now,
                "skill_name": skill_name or None,
                "cat": harness_category,
                "id": int(pending["id"]),
            })
            proposal_id = int(pending["id"])
        else:
            result = conn.execute(text("""
                INSERT INTO skill_judgment_proposals(
                    board_id, project_id, user_key, skill_id, skill_name,
                    harness_category, status, proposal_text, evidence_event_ids,
                    evidence_count, created_at, updated_at
                ) VALUES (
                    :board_id, :project_id, :user_key, :skill_id, :skill_name,
                    :cat, 'pending', :text, :ids, :count, :now, :now
                )
            """), {
                "board_id": int(board_id) if board_id is not None else None,
                "project_id": int(project_id) if project_id is not None else None,
                "user_key": user_key,
                "skill_id": skill_id or None,
                "skill_name": skill_name or None,
                "cat": harness_category,
                "text": proposal_text,
                "ids": _json_dumps(evidence_ids),
                "count": evidence_count,
                "now": now,
            })
            proposal_id = int(result.lastrowid)

    return {
        "proposal_id": proposal_id,
        "status": "pending",
        "skill_id": skill_id,
        "skill_name": skill_name,
        "harness_category": harness_category,
        "evidence_count": evidence_count,
        "proposal_text": proposal_text,
        "applied": False,
    }


def list_skill_judgment_events(
    *,
    board_id: int | None,
    skill_id: str | None = None,
    harness_category: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    ensure_tables()
    user_key = _user_key(board_id=board_id)
    clauses = ["user_key=:user_key"]
    params: dict[str, Any] = {"user_key": user_key, "limit": max(1, min(int(limit or 40), 200))}
    if skill_id:
        clauses.append("skill_id=:skill_id")
        params["skill_id"] = skill_id
    if harness_category:
        clauses.append("harness_category=:cat")
        params["cat"] = harness_category
    where = " AND ".join(clauses)
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT * FROM skill_judgment_events
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT :limit
        """), params).mappings().all()
    out = []
    for row in rows:
        item = dict(row)
        item["skill_ids"] = _json_loads(item.pop("skill_ids_json", "[]"), [])
        item["payload"] = _json_loads(item.pop("payload_json", "{}"), {})
        out.append(item)
    return out


def list_skill_judgment_proposals(
    *,
    board_id: int | None,
    skill_id: str | None = None,
    status: str | None = "pending",
    limit: int = 20,
) -> list[dict[str, Any]]:
    ensure_tables()
    user_key = _user_key(board_id=board_id)
    clauses = ["user_key=:user_key"]
    params: dict[str, Any] = {"user_key": user_key, "limit": max(1, min(int(limit or 20), 100))}
    if skill_id:
        clauses.append("skill_id=:skill_id")
        params["skill_id"] = skill_id
    if status:
        clauses.append("status=:status")
        params["status"] = status
    where = " AND ".join(clauses)
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT * FROM skill_judgment_proposals
            WHERE {where}
            ORDER BY updated_at DESC
            LIMIT :limit
        """), params).mappings().all()
    out = []
    for row in rows:
        item = dict(row)
        item["evidence_event_ids"] = _json_loads(item.get("evidence_event_ids"), [])
        out.append(item)
    return out


def decide_skill_judgment_proposal(
    proposal_id: int,
    *,
    approve: bool,
    note: str = "",
) -> dict[str, Any]:
    """Mark a proposal approved/rejected. Still does not rewrite skill files."""
    ensure_tables()
    status = "approved" if approve else "rejected"
    now = time.time()
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT * FROM skill_judgment_proposals WHERE id=:id"
        ), {"id": int(proposal_id)}).mappings().first()
        if not row:
            return {"ok": False, "error": "proposal_not_found"}
        if row["status"] != "pending":
            return {"ok": False, "error": "already_decided", "status": row["status"]}
        conn.execute(text("""
            UPDATE skill_judgment_proposals
            SET status=:status, decided_at=:now, decided_note=:note, updated_at=:now
            WHERE id=:id AND status='pending'
        """), {
            "status": status,
            "now": now,
            "note": str(note or "")[:1000],
            "id": int(proposal_id),
        })
    return {
        "ok": True,
        "proposal_id": int(proposal_id),
        "status": status,
        "applied": False,
        "note": (
            "Proposal recorded. Skill pack files were not modified — apply the "
            "approved text manually or via a separate explicit edit."
        ),
    }


def skill_judgment_matches_context(
    event: dict[str, Any],
    *,
    skill_id: str | None = None,
    skill_ids: list[str] | None = None,
    harness_category: str | None = None,
) -> bool:
    """True when a stored judgment is safe to inject into the active context."""
    active_ids = {str(s).strip() for s in (skill_ids or []) if str(s).strip()}
    if skill_id and str(skill_id).strip():
        active_ids.add(str(skill_id).strip())
    lesson_skill = str(event.get("skill_id") or "").strip()
    lesson_harness = str(event.get("harness_category") or "").strip()
    wanted_harness = str(harness_category or "").strip()

    if active_ids:
        # Skill-scoped run: only inject lessons tagged to one of the active skills.
        return bool(lesson_skill) and lesson_skill in active_ids
    if wanted_harness:
        if lesson_skill:
            return False
        if lesson_harness:
            return lesson_harness == wanted_harness
        return False
    # No active skill/harness filter: only unscoped lessons (legacy / general).
    return not lesson_skill


def matching_skill_judgment_lessons(
    *,
    board_id: int | None,
    skill_id: str | None = None,
    skill_ids: list[str] | None = None,
    harness_category: str | None = None,
    limit: int = 6,
    negative_only: bool = True,
) -> list[dict[str, Any]]:
    """Return board-local lessons filtered to the active skill/harness context."""
    if not board_id:
        return []
    events = list_skill_judgment_events(board_id=board_id, limit=max(limit * 4, 24))
    matched: list[dict[str, Any]] = []
    for event in events:
        if negative_only and not is_negative_judgment_label(event.get("judgment_label") or ""):
            continue
        if not skill_judgment_matches_context(
            event,
            skill_id=skill_id,
            skill_ids=skill_ids,
            harness_category=harness_category,
        ):
            continue
        matched.append(event)
        if len(matched) >= limit:
            break
    return matched


def format_skill_judgment_lessons(events: list[dict[str, Any]]) -> str:
    if not events:
        return ""
    lines = ["[SKILL-SCOPED VERIFICATION LESSONS]"]
    for event in events:
        summary = " ".join(str(event.get("lesson_summary") or event.get("feedback_text") or "").split()).strip()
        if not summary:
            continue
        scope = event.get("skill_id") or event.get("harness_category") or "general"
        lines.append(f"- ({scope}) {summary[:320]}")
    return "\n".join(lines) if len(lines) > 1 else ""
