"""Durable, idempotent human interactions for workflow checkpoints."""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from typing import Any

from sqlalchemy import text

from distr.core.db import engine

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 24 * 60 * 60


def _ensure_table() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS workflow_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token VARCHAR NOT NULL UNIQUE,
                workflow_id INTEGER NOT NULL,
                run_id INTEGER NOT NULL,
                step_id INTEGER,
                kind VARCHAR NOT NULL,
                allowed_actions TEXT NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                telegram_chat_id VARCHAR,
                telegram_message_id VARCHAR,
                created_at FLOAT NOT NULL,
                expires_at FLOAT NOT NULL,
                resolved_at FLOAT,
                resolved_action VARCHAR,
                response_text TEXT,
                response_source VARCHAR,
                resolver_id VARCHAR,
                error TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_workflow_interactions_pending "
            "ON workflow_interactions(status, telegram_chat_id, created_at)"
        ))


def allowed_actions_for_kind(kind: str) -> list[str]:
    normalized = (kind or "").strip().lower()
    if normalized == "route_approval":
        return ["approve", "reject"]
    if normalized == "provider_preflight":
        return ["approve", "stop"]
    if normalized == "pre_execution_approval":
        return ["approve", "stop"]
    if normalized in {"approval", "run_briefing"}:
        return ["approve", "stop", "feedback"]
    return ["continue", "stop", "feedback"]


def create_workflow_interaction(
    *, workflow_id: int, run_id: int, step_id: int | None, kind: str,
    telegram_chat_id: int | str | None = None, ttl_s: int = DEFAULT_TTL_S,
) -> dict[str, Any]:
    """Create or reuse the pending interaction for the same run checkpoint."""
    _ensure_table()
    now = time.time()
    normalized_kind = (kind or "feedback").strip().lower()
    actions = allowed_actions_for_kind(normalized_kind)
    if normalized_kind == "provider_preflight":
        try:
            with engine.connect() as conn:
                run_row = conn.execute(
                    text("SELECT run_data FROM auto_workflow_runs WHERE id=:run_id"),
                    {"run_id": int(run_id)},
                ).mappings().first()
            run_data = json.loads((run_row or {}).get("run_data") or "{}")
            candidates = run_data.get("provider_free_candidates") or []
            available = [
                index for index, candidate in enumerate(candidates[:3])
                if not isinstance(candidate, dict) or not candidate.get("readiness_failed")
            ]
            if available:
                actions = [f"model_{index}" for index in available] + ["stop"]
        except Exception:
            logger.debug("Could not load provider candidates for interaction", exc_info=True)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE workflow_interactions SET status='expired'
            WHERE status='pending' AND expires_at <= :now
        """), {"now": now})
        existing = conn.execute(text("""
            SELECT * FROM workflow_interactions
            WHERE status='pending' AND run_id=:run_id
              AND COALESCE(step_id, -1)=COALESCE(:step_id, -1) AND kind=:kind
            ORDER BY id DESC LIMIT 1
        """), {"run_id": int(run_id), "step_id": step_id, "kind": normalized_kind}).mappings().first()
        if existing:
            return dict(existing)
        token = secrets.token_urlsafe(12)
        conn.execute(text("""
            INSERT INTO workflow_interactions(
                token, workflow_id, run_id, step_id, kind, allowed_actions,
                telegram_chat_id, created_at, expires_at
            ) VALUES (
                :token, :workflow_id, :run_id, :step_id, :kind, :actions,
                :chat_id, :created_at, :expires_at
            )
        """), {
            "token": token, "workflow_id": int(workflow_id), "run_id": int(run_id),
            "step_id": step_id, "kind": normalized_kind,
            "actions": json.dumps(actions),
            "chat_id": str(telegram_chat_id) if telegram_chat_id is not None else None,
            "created_at": now, "expires_at": now + max(60, int(ttl_s)),
        })
    logger.info("Workflow interaction created run=%s step=%s kind=%s token=%s", run_id, step_id, normalized_kind, token[:6])
    return {"token": token, "run_id": run_id, "step_id": step_id, "kind": normalized_kind, "allowed_actions": json.dumps(actions)}


def telegram_reply_markup(interaction: dict[str, Any]) -> dict[str, Any]:
    actions = interaction.get("allowed_actions") or []
    if isinstance(actions, str):
        actions = json.loads(actions)
    labels = {"approve": "Approve", "reject": "Reject", "continue": "Continue", "stop": "Stop"}
    token = interaction["token"]
    buttons = []
    for action in actions:
        if action in labels:
            buttons.append({"text": labels[action], "callback_data": f"wf:{token}:{action}"})
        elif re.fullmatch(r"model_[0-2]", action):
            buttons.append({"text": f"Try {int(action.rsplit('_', 1)[1]) + 1}", "callback_data": f"wf:{token}:{action}"})
    return {"inline_keyboard": [buttons[:3], buttons[3:]]} if len(buttons) > 3 else ({"inline_keyboard": [buttons]} if buttons else {})


def classify_reply(value: str, allowed_actions: list[str]) -> tuple[str | None, str]:
    clean = re.sub(r"\s+", " ", str(value or "").strip())
    low = clean.lower()
    option_match = re.search(r"\b(?:try|model|option)?\s*([1-3])\b", low)
    if option_match:
        action = f"model_{int(option_match.group(1)) - 1}"
        if action in allowed_actions:
            return action, clean
    if re.search(r"\b(reject|decline|deny|no|nope|do not|don't)\b", low) and "reject" in allowed_actions:
        return "reject", clean
    if re.search(r"\b(stop|cancel|abort|hold off|not now|no|nope|do not|don't)\b", low) and "stop" in allowed_actions:
        return "stop", clean
    if re.search(r"\b(approve|approved|yes|yep|go ahead|proceed)\b", low) and "approve" in allowed_actions:
        return "approve", clean
    if re.search(r"\b(continue|resume|looks good|carry on)\b", low) and "continue" in allowed_actions:
        return "continue", clean
    if clean and "feedback" in allowed_actions:
        return "feedback", clean
    return None, clean


def pending_interactions(*, chat_id: int | str | None = None) -> list[dict[str, Any]]:
    _ensure_table()
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("UPDATE workflow_interactions SET status='expired' WHERE status='pending' AND expires_at <= :now"), {"now": now})
        rows = conn.execute(text("""
            SELECT * FROM workflow_interactions WHERE status='pending'
              AND (:chat_id IS NULL OR telegram_chat_id IS NULL OR telegram_chat_id=:chat_id)
            ORDER BY created_at DESC, id DESC LIMIT 20
        """), {"chat_id": str(chat_id) if chat_id is not None else None}).mappings().all()
    return [dict(row) for row in rows]


def resolve_interaction(
    *, token: str, action: str, response_text: str = "", source: str = "telegram",
    resolver_id: str = "", chat_id: int | str | None = None,
    background: bool = False,
) -> dict[str, Any]:
    """Atomically claim an interaction, then apply its workflow transition."""
    _ensure_table()
    now = time.time()
    with engine.begin() as conn:
        row = conn.execute(text("SELECT * FROM workflow_interactions WHERE token=:token"), {"token": token}).mappings().first()
        if not row:
            return {"error": "Interaction not found", "status_code": 404}
        item = dict(row)
        if item["status"] != "pending":
            if (
                item["status"] in {"resolving", "resolved"}
                and item.get("resolved_action") == action
            ):
                return {
                    "success": True,
                    "run_id": item["run_id"],
                    "action": action,
                    "idempotent": True,
                }
            return {"error": f"Interaction already {item['status']}", "status_code": 409, "idempotent": True}
        if float(item["expires_at"]) <= now:
            conn.execute(text("UPDATE workflow_interactions SET status='expired' WHERE token=:token AND status='pending'"), {"token": token})
            return {"error": "Interaction expired", "status_code": 410}
        allowed = json.loads(item["allowed_actions"] or "[]")
        if action not in allowed:
            return {"error": f"Action {action!r} is not allowed", "status_code": 400}
        bound_chat = item.get("telegram_chat_id")
        if bound_chat and chat_id is not None and str(chat_id) != str(bound_chat):
            return {"error": "Interaction belongs to another Telegram chat", "status_code": 403}
        claimed = conn.execute(text("""
            UPDATE workflow_interactions SET status='resolving', resolved_action=:action,
              response_text=:response, response_source=:source, resolver_id=:resolver
            WHERE token=:token AND status='pending'
        """), {"action": action, "response": response_text, "source": source, "resolver": resolver_id, "token": token})
        if claimed.rowcount != 1:
            latest = conn.execute(text(
                "SELECT status,resolved_action,run_id FROM workflow_interactions WHERE token=:token"
            ), {"token": token}).mappings().first()
            if (
                latest
                and latest["status"] in {"resolving", "resolved"}
                and latest["resolved_action"] == action
            ):
                return {
                    "success": True,
                    "run_id": latest["run_id"],
                    "action": action,
                    "idempotent": True,
                }
            return {"error": "Interaction was resolved concurrently", "status_code": 409, "idempotent": True}

    if background:
        threading.Thread(
            target=_apply_claimed_interaction,
            kwargs={
                "item": item,
                "token": token,
                "action": action,
                "response_text": response_text,
                "source": source,
                "resolved_at": now,
            },
            name=f"workflow-interaction-{item['run_id']}-{action}",
            daemon=True,
        ).start()
        return {
            "success": True,
            "run_id": item["run_id"],
            "action": action,
            "queued": True,
        }

    return _apply_claimed_interaction(
        item=item,
        token=token,
        action=action,
        response_text=response_text,
        source=source,
        resolved_at=now,
    )


def _apply_claimed_interaction(
    *,
    item: dict[str, Any],
    token: str,
    action: str,
    response_text: str,
    source: str,
    resolved_at: float,
) -> dict[str, Any]:
    """Apply a previously claimed interaction, optionally on a worker thread."""
    try:
        if item["kind"] == "route_approval":
            from distr.core.workflow.service import apply_run_route_approval
            result = apply_run_route_approval(int(item["run_id"]), approved=action == "approve")
        elif item["kind"] == "provider_preflight" and action.startswith("model_"):
            from distr.core.workflow.service import apply_run_provider_model_selection

            result = apply_run_provider_model_selection(
                int(item["run_id"]), int(action.rsplit("_", 1)[1])
            )
        elif item["kind"] == "provider_preflight" and action == "approve":
            from distr.core.workflow.service import apply_run_route_approval
            result = apply_run_route_approval(int(item["run_id"]), approved=True)
        elif item["kind"] == "pre_execution_approval" and action == "approve":
            from distr.core.workflow.dispatcher import approve_pre_execution_step

            result = approve_pre_execution_step(
                int(item["run_id"]),
                int(item["step_id"]),
                response_text=response_text,
            )
        elif action == "stop":
            from distr.core.workflow.dispatcher import cancel_run
            result = {"success": bool(cancel_run(int(item["run_id"]))), "action": "stop"}
        else:
            from distr.core.workflow.dispatcher import continue_waiting_step
            feedback = response_text if action == "feedback" else response_text or action
            result = continue_waiting_step(int(item["run_id"]), feedback)
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE workflow_interactions SET status='pending', error=:error WHERE token=:token AND status='resolving'"), {"error": str(exc)[:2000], "token": token})
        logger.exception("Workflow interaction resolution failed token=%s", token[:6])
        return {"error": str(exc), "status_code": 500}

    with engine.begin() as conn:
        conn.execute(text("UPDATE workflow_interactions SET status='resolved', resolved_at=:now, error=NULL WHERE token=:token AND status='resolving'"), {"now": resolved_at, "token": token})
    try:
        from distr.core.human_engagement import mark_workflow_engagement_answered

        mark_workflow_engagement_answered(int(item["run_id"]))
    except Exception:
        logger.debug("Could not mark workflow engagement answered", exc_info=True)
    logger.info("Workflow interaction resolved run=%s action=%s source=%s", item["run_id"], action, source)
    return {"success": True, "run_id": item["run_id"], "action": action, "result": result}


def handle_telegram_workflow_reply(
    text_value: str, *, chat_id: int | str | None, resolver_id: str = "", source: str = "telegram_text",
    background: bool = False,
) -> dict[str, Any] | None:
    """Resolve an explicit callback or an unambiguous pending workflow reply."""
    clean = str(text_value or "").strip()
    callback = re.fullmatch(r"wf:([A-Za-z0-9_-]+):(approve|reject|continue|stop|model_[0-2])", clean)
    if callback:
        return resolve_interaction(token=callback.group(1), action=callback.group(2), response_text=callback.group(2), source=source, resolver_id=resolver_id, chat_id=chat_id, background=background)
    pending = pending_interactions(chat_id=chat_id)
    if not pending:
        return None
    if len(pending) > 1:
        run_match = re.search(r"(?i)\b(?:run\s*)?#?(\d+)\b", clean)
        if run_match:
            requested_run = int(run_match.group(1))
            matches = [item for item in pending if int(item.get("run_id") or 0) == requested_run]
            if len(matches) == 1:
                item = matches[0]
                allowed = json.loads(item["allowed_actions"] or "[]")
                without_run = re.sub(r"(?i)\b(?:run\s*)?#?" + re.escape(run_match.group(1)) + r"\b", "", clean).strip()
                action, response = classify_reply(without_run, allowed)
                if action:
                    return resolve_interaction(
                        token=item["token"], action=action, response_text=response,
                        source=source, resolver_id=resolver_id, chat_id=chat_id,
                        background=background,
                    )
        # Never guess which run a short response controls.
        short_decision = re.fullmatch(r"(?i)\s*(yes|no|approve|reject|continue|stop|go ahead|resume)\s*[.!]?\s*", clean)
        if short_decision:
            return {"error": "ambiguous", "status_code": 409, "pending": pending}
        return None
    item = pending[0]
    allowed = json.loads(item["allowed_actions"] or "[]")
    action, response = classify_reply(clean, allowed)
    if not action:
        return None
    return resolve_interaction(token=item["token"], action=action, response_text=response, source=source, resolver_id=resolver_id, chat_id=chat_id, background=background)


def workflow_reply_message(result: dict[str, Any], *, voice: bool = False) -> str:
    """Return one consistent Telegram acknowledgement for a resolver result."""
    if result.get("error") == "ambiguous":
        pending = result.get("pending") or []
        runs = ", ".join(f"#{item.get('run_id')}" for item in pending[:8])
        if voice:
            return f"More than one workflow needs a decision: {runs}. Please say the run number with your decision."
        choices = "\n".join(
            f"- Run #{item.get('run_id')} ({item.get('kind')})"
            for item in pending[:8]
        )
        return "More than one workflow needs a decision. Specify the run number:\n" + choices
    if result.get("error"):
        return f"I could not apply that workflow decision: {result['error']}"
    action = str(result.get("action") or "continue")
    if re.fullmatch(r"model_[0-2]", action):
        return f"Workflow run #{result.get('run_id')} is checking free-model option {int(action[-1]) + 1}."
    return f"Workflow run #{result.get('run_id')} accepted: {action}."
