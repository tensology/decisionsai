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
from sqlalchemy.exc import OperationalError

from distr.core.db import engine

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 24 * 60 * 60
SQLITE_LOCK_RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4)


def _with_sqlite_lock_retry(operation):
    """Retry a short, idempotent interaction transaction on SQLite contention."""
    for attempt in range(len(SQLITE_LOCK_RETRY_DELAYS_S) + 1):
        try:
            return operation()
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt >= len(SQLITE_LOCK_RETRY_DELAYS_S):
                raise
            delay = SQLITE_LOCK_RETRY_DELAYS_S[attempt]
            logger.warning(
                "Workflow interaction database locked; retrying in %.2fs (attempt %s)",
                delay,
                attempt + 2,
            )
            time.sleep(delay)


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


def _expire_stale_interactions(conn, *, now: float) -> None:
    """Expire timed-out decisions and decisions whose run is no longer waiting."""
    conn.execute(text("""
        UPDATE workflow_interactions SET status='expired'
        WHERE status='pending' AND (
            expires_at <= :now
            OR run_id IN (
                SELECT id FROM auto_workflow_runs
                WHERE LOWER(COALESCE(status, '')) != 'waiting'
            )
        )
    """), {"now": now})


def allowed_actions_for_kind(kind: str) -> list[str]:
    normalized = (kind or "").strip().lower()
    if normalized == "qualification_telegram_approval":
        return ["approve", "stop"]
    if normalized == "qualification_telegram_voice":
        return ["continue", "stop"]
    if normalized == "qualification_telegram_steer":
        return ["feedback", "stop"]
    if normalized == "qualification_telegram_stop":
        return ["stop"]
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
    def _create() -> dict[str, Any]:
        _ensure_table()
        with engine.begin() as conn:
            _expire_stale_interactions(conn, now=now)
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
        return {
            "token": token, "run_id": run_id, "step_id": step_id,
            "kind": normalized_kind, "allowed_actions": json.dumps(actions),
        }

    interaction = _with_sqlite_lock_retry(_create)
    token = str(interaction["token"])
    logger.info("Workflow interaction created run=%s step=%s kind=%s token=%s", run_id, step_id, normalized_kind, token[:6])
    return interaction


def reissue_workflow_interaction(
    run_id: int,
    *,
    workflow_id: int | None = None,
) -> dict[str, Any]:
    """Re-send a waiting prompt while preserving its original checkpoint token."""
    _ensure_table()
    with engine.connect() as conn:
        run = conn.execute(
            text(
                "SELECT id, workflow_id, current_step_id, status, run_data "
                "FROM auto_workflow_runs WHERE id=:run_id"
            ),
            {"run_id": int(run_id)},
        ).mappings().first()
        if not run:
            return {"error": "Workflow run does not exist", "status_code": 404}
        if workflow_id is not None and int(run["workflow_id"]) != int(workflow_id):
            return {"error": "Workflow run does not belong to this workflow", "status_code": 404}
        if str(run.get("status") or "").lower() != "waiting":
            return {"error": "Workflow run is not waiting for a response", "status_code": 409}
        try:
            run_data = json.loads(run.get("run_data") or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            run_data = {}
        waiting_kind = str(run_data.get("waiting_kind") or "feedback").strip() or "feedback"
        prompt = str(run_data.get("waiting_prompt") or "").strip()
        pending = conn.execute(
            text(
                "SELECT id FROM workflow_interactions "
                "WHERE run_id=:run_id AND status='pending' "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"run_id": int(run_id)},
        ).mappings().first()
    if not prompt:
        return {"error": "Waiting workflow has no question to reissue", "status_code": 409}

    from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_progress

    notify_ticket_workflow_progress(
        run_id=int(run_id),
        step_id=int(run["current_step_id"]) if run.get("current_step_id") is not None else None,
        body=prompt,
        voice_body=prompt,
        state_fingerprint=(
            f"workflow-interaction:{int(run_id)}:{waiting_kind}:"
            f"reissue:{time.time_ns()}"
        ),
        priority="high",
        requires_response=True,
        audible=True,
    )
    return {
        "success": True,
        "run_id": int(run_id),
        "workflow_id": int(run["workflow_id"]),
        "interaction_id": int(pending["id"]) if pending else None,
        "waiting_kind": waiting_kind,
        "prompt": prompt,
        "reissued": True,
    }


def telegram_reply_markup(interaction: dict[str, Any]) -> dict[str, Any]:
    actions = interaction.get("allowed_actions") or []
    if isinstance(actions, str):
        actions = json.loads(actions)
    kind = str(interaction.get("kind") or "").strip().lower()
    if kind == "qualification_telegram_voice":
        # This checkpoint is proving the voice path.  Rendering Continue here
        # invites a callback shortcut that cannot satisfy that proof.
        actions = [action for action in actions if action != "continue"]
    labels = {"approve": "Approve", "reject": "Reject", "continue": "Continue", "stop": "Stop"}
    token = interaction["token"]
    buttons = []
    for action in actions:
        if action in labels:
            buttons.append({"text": labels[action], "callback_data": f"wf:{token}:{action}"})
        elif re.fullmatch(r"model_[0-2]", action):
            buttons.append({"text": f"Try {int(action.rsplit('_', 1)[1]) + 1}", "callback_data": f"wf:{token}:{action}"})
    return {"inline_keyboard": [buttons[:3], buttons[3:]]} if len(buttons) > 3 else ({"inline_keyboard": [buttons]} if buttons else {})


def record_telegram_delivery(
    *,
    token: str,
    telegram_chat_id: int | str | None,
    telegram_message_id: int | str | None,
    reply_markup_sent: bool,
) -> bool:
    """Persist the Telegram API acknowledgment for one durable interaction."""
    clean_token = str(token or "").strip()
    if not clean_token:
        return False
    _ensure_table()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE workflow_interactions "
                "SET telegram_chat_id=COALESCE(:chat_id, telegram_chat_id), "
                "telegram_message_id=:message_id, error=:error "
                "WHERE token=:token AND status='pending'"
            ),
            {
                "token": clean_token,
                "chat_id": str(telegram_chat_id) if telegram_chat_id is not None else None,
                "message_id": str(telegram_message_id) if telegram_message_id is not None else None,
                "error": None if reply_markup_sent else "Telegram acknowledged the message without its controls",
            },
        )
    return bool(result.rowcount)


def record_telegram_delivery_error(*, token: str, error: str) -> bool:
    """Attach a correlated relay/Telegram failure to a pending interaction."""
    clean_token = str(token or "").strip()
    if not clean_token:
        return False
    _ensure_table()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE workflow_interactions SET error=:error "
                "WHERE token=:token AND status='pending'"
            ),
            {"token": clean_token, "error": str(error or "Telegram delivery failed")[:1000]},
        )
    return bool(result.rowcount)


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
        _expire_stale_interactions(conn, now=now)
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
        _expire_stale_interactions(conn, now=now)
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
        if (
            str(item.get("kind") or "").lower() == "qualification_telegram_voice"
            and action == "continue"
            and str(source or "").lower() != "telegram_voice"
        ):
            return {
                "error": "Send a Telegram voice note to continue this checkpoint",
                "status_code": 400,
            }
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
        if str(item.get("kind") or "").startswith("qualification_telegram_"):
            result = _advance_telegram_qualification_probe(
                item=item,
                action=action,
                response_text=response_text,
            )
        else:
            result = _apply_standard_workflow_interaction(
                item=item,
                action=action,
                response_text=response_text,
                source=source,
            )
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


def _apply_standard_workflow_interaction(
    *, item: dict[str, Any], action: str, response_text: str, source: str
) -> dict[str, Any]:
    """Apply a normal workflow checkpoint after the durable claim succeeds."""
    synchronous_redispatch = str(source or "").strip().lower() in {
        "cli",
        "qualification",
        "test_harness",
    }
    if item["kind"] == "route_approval":
        from distr.core.workflow.service import apply_run_route_approval

        kwargs = {"synchronous_redispatch": True} if synchronous_redispatch else {}
        result = apply_run_route_approval(
            int(item["run_id"]),
            approved=action == "approve",
            **kwargs,
        )
    elif item["kind"] == "provider_preflight" and action.startswith("model_"):
        from distr.core.workflow.service import apply_run_provider_model_selection

        kwargs = {"synchronous_redispatch": True} if synchronous_redispatch else {}
        result = apply_run_provider_model_selection(
            int(item["run_id"]), int(action.rsplit("_", 1)[1]), **kwargs
        )
    elif item["kind"] == "provider_preflight" and action == "approve":
        from distr.core.workflow.service import apply_run_route_approval

        kwargs = {"synchronous_redispatch": True} if synchronous_redispatch else {}
        result = apply_run_route_approval(int(item["run_id"]), approved=True, **kwargs)
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
    return result


_TELEGRAM_QUALIFICATION_PHASES = {
    "qualification_telegram_approval": (
        "qualification_telegram_voice",
        "Approval received. Now send a Telegram voice note saying: continue the remote-control check.",
    ),
    "qualification_telegram_voice": (
        "qualification_telegram_steer",
        "Voice control received. Now send a Telegram text instruction to steer this run, for example: keep the final report concise.",
    ),
    "qualification_telegram_steer": (
        "qualification_telegram_stop",
        "Steering received. Tap Stop to prove that Telegram can safely stop this same run.",
    ),
}


def start_telegram_qualification_probe(run_id: int, step_id: int | None) -> dict[str, Any]:
    """Enter the first durable checkpoint for the explicit Telegram release probe."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_progress

    with get_session() as db:
        run = db.get(AutoWorkflowRun, int(run_id))
        if not run:
            return {"error": "Workflow run does not exist"}
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            run_data = {}
        if not (
            str(run_data.get("qualification_scenario_id") or "")
            == "telegram_control_round_trip"
            and bool(run_data.get("qualification_remote_control_probe"))
        ):
            return {"error": "Run is not an explicit Telegram qualification probe"}
        run.status = "waiting"
        run.current_step_id = int(step_id) if step_id is not None else run.current_step_id
        run_data["waiting_kind"] = "qualification_telegram_approval"
        run_data["waiting_prompt"] = (
            "Telegram remote-control proof is ready. Tap Approve in Telegram to begin."
        )
        run_data["telegram_qualification_phase"] = "approval"
        run.run_data = json.dumps(run_data)
        db.commit()
    notify_ticket_workflow_progress(
        run_id=int(run_id),
        step_id=step_id,
        body="Telegram remote-control proof is ready. Tap Approve to begin.",
        voice_body="The Telegram control check is ready. Tap Approve to begin.",
        state_fingerprint=f"telegram-qualification:{run_id}:approval",
        priority="high",
        requires_response=True,
        audible=True,
    )
    return {"success": True, "status": "waiting", "waiting_kind": "qualification_telegram_approval"}


def _advance_telegram_qualification_probe(
    *, item: dict[str, Any], action: str, response_text: str
) -> dict[str, Any]:
    """Advance only the opt-in release probe; normal workflow logic is untouched."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflowRun

    if action == "stop":
        from distr.core.workflow.dispatcher import cancel_run

        return {"success": bool(cancel_run(int(item["run_id"]))), "action": "stop"}
    next_phase = _TELEGRAM_QUALIFICATION_PHASES.get(str(item.get("kind") or ""))
    if not next_phase:
        return {"error": "Telegram qualification phase cannot advance"}
    next_kind, prompt = next_phase
    with get_session() as db:
        run = db.get(AutoWorkflowRun, int(item["run_id"]))
        if not run:
            return {"error": "Workflow run does not exist"}
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            run_data = {}
        if not bool(run_data.get("qualification_remote_control_probe")):
            return {"error": "Run is not an explicit Telegram qualification probe"}
        run.status = "waiting"
        run_data["waiting_kind"] = next_kind
        run_data["waiting_prompt"] = prompt
        run_data["telegram_qualification_phase"] = next_kind.removeprefix(
            "qualification_telegram_"
        )
        if str(item.get("kind") or "") == "qualification_telegram_steer":
            run_data["telegram_qualification_steering"] = str(response_text or "")[:2000]
        run.run_data = json.dumps(run_data)
        db.commit()

    from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_progress

    notify_ticket_workflow_progress(
        run_id=int(item["run_id"]),
        step_id=item.get("step_id"),
        body=prompt,
        voice_body=prompt,
        state_fingerprint=f"telegram-qualification:{item['run_id']}:{next_kind}",
        priority="high",
        requires_response=True,
        audible=True,
    )
    return {"success": True, "status": "waiting", "waiting_kind": next_kind}


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
