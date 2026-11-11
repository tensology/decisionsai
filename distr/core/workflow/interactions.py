"""Durable, idempotent human interactions for workflow checkpoints."""

from __future__ import annotations

import json
import hashlib
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


class CheckpointScopeChanged(ValueError):
    status_code = 409


def guard_workflow_checkpoint(db, run_id: int, expected_checkpoint: dict | None) -> None:
    """Validate a scoped decision inside its short mutation transaction."""
    if expected_checkpoint is None:return
    if db.get_bind().dialect.name=='sqlite':db.connection().exec_driver_sql('BEGIN IMMEDIATE')
    try:
        current=_checkpoint_scope(db.connection(),int(run_id),require_waiting=False)
    except ValueError:
        current=None
    if current!=expected_checkpoint:
        raise CheckpointScopeChanged('The workflow checkpoint or its work scope changed. Review the current question.')


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
                error TEXT,
                scope_json TEXT
            )
        """))
        columns = {row[1] for row in conn.exec_driver_sql('PRAGMA table_info(workflow_interactions)')}
        if 'scope_json' not in columns:
            try:
                conn.exec_driver_sql('ALTER TABLE workflow_interactions ADD COLUMN scope_json TEXT')
            except OperationalError as exc:
                if 'duplicate column name' not in str(exc).lower(): raise
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
            OR scope_json IS NULL
            OR NOT EXISTS (
                SELECT 1 FROM auto_workflow_runs r
                WHERE r.id=workflow_interactions.run_id AND r.workflow_id=workflow_interactions.workflow_id
                  AND LOWER(COALESCE(r.status, ''))='waiting'
                  AND COALESCE(r.current_step_id,-1)=COALESCE(workflow_interactions.step_id,-1)
            )
        )
    """), {"now": now})


def _checkpoint_scope(conn, run_id: int, *, require_waiting=True) -> dict:
    run = conn.execute(text('SELECT * FROM auto_workflow_runs WHERE id=:id'),{'id':int(run_id)}).mappings().first()
    if not run or (require_waiting and str(run['status'] or '').lower()!='waiting'):
        raise ValueError('The workflow checkpoint is no longer waiting.')
    workflow = conn.execute(text('SELECT id FROM auto_workflows WHERE id=:id'),{'id':run['workflow_id']}).first()
    if not workflow:
        raise ValueError('The workflow checkpoint owner no longer exists.')
    try:
        data=json.loads(run['run_data'] or '{}')
        if not isinstance(data,dict): raise ValueError()
    except (TypeError,ValueError):
        raise ValueError('The workflow checkpoint context is invalid. Review it before continuing.')
    root_id,root_project=None,None
    if run['chat_id'] is not None:
        chat=conn.execute(text('SELECT id,parent_id,project_id FROM chats WHERE id=:id'),{'id':run['chat_id']}).mappings().first()
        if chat and chat['parent_id'] is not None:
            chat=conn.execute(text('SELECT id,parent_id,project_id FROM chats WHERE id=:id'),{'id':chat['parent_id']}).mappings().first()
        if not chat:
            raise ValueError('The workflow checkpoint thread no longer exists.')
        root_id,root_project=chat['id'],chat['project_id']
    step=None
    if run['current_step_id'] is not None:
        step=conn.execute(text('SELECT * FROM auto_workflow_steps WHERE id=:id'),{'id':run['current_step_id']}).mappings().first()
        if not step or step['workflow_id']!=run['workflow_id']:
            raise ValueError('The workflow checkpoint step no longer belongs to this workflow.')
    def digest(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
    kind=str(data.get('waiting_kind') or 'feedback').strip().lower()
    owner={key:run[key] for key in ('id','workflow_id','chat_id','board_id','ticket_id')}
    owner.update(root_id=root_id,root_project_id=root_project,
                 context={key:data.get(key) for key in ('project_id','board_id','ticket_id','chat_id')})
    checkpoint={'step_id':run['current_step_id'],'kind':kind,'prompt':digest(data.get('waiting_prompt'))}
    counts=data.get('checkpoint_counts') if isinstance(data.get('checkpoint_counts'),dict) else {}
    checkpoint['generation']=counts.get(kind)
    owner['project_bindings']={}
    for value in (root_project,data.get('project_id')):
        if value is not None and str(value).isdigit():
            project=conn.execute(text('SELECT folder_location,deleted_at FROM projects WHERE id=:id'),{'id':int(value)}).first()
            owner['project_bindings'][str(value)]=digest(list(project)) if project else None
    if run['ticket_id'] is not None:
        ticket=conn.execute(text('SELECT t.linked_project_id,t.source_chat_id,l.board_id FROM kanban_tickets t '
            'LEFT JOIN kanban_lanes l ON l.id=t.lane_id WHERE t.id=:id'),{'id':run['ticket_id']}).first()
        owner['ticket_binding']=list(ticket) if ticket else None
    if run['board_id'] is not None:
        board=conn.execute(text('SELECT default_project_id,archived FROM kanban_boards WHERE id=:id'),{'id':run['board_id']}).first()
        owner['board_binding']=list(board) if board else None
    if step:
        checkpoint['step_input']=digest({key:step[key] for key in
            ('action_type','step_type','instruction','config','validation_type','validation_prompt','on_pass_step_id','on_fail_step_id') if key in step})
    checkpoint['execution_route']=digest({key:data.get(key) for key in ('execution_route','step_routes','step_role_routes')})
    if kind in {'provider_preflight','route_approval'}:
        checkpoint['route']=digest({key:data.get(key) for key in ('provider_free_candidates','pending_route_approval','execution_route','provider_fallback_route')})
    return {'owner':owner,'checkpoint':checkpoint,'status':str(run['status'] or '').lower()}


def _scope_matches(conn,item,*,owner_only=False):
    try:
        saved=json.loads(item.get('scope_json') or 'null')
        current=_checkpoint_scope(conn,int(item['run_id']),require_waiting=not owner_only)
        return isinstance(saved,dict) and (saved.get('owner')==current['owner'] if owner_only else saved==current)
    except (TypeError,ValueError):
        return False


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
    def _create() -> dict[str, Any]:
        _ensure_table()
        with engine.begin() as conn:
            if conn.dialect.name=='sqlite': conn.exec_driver_sql('BEGIN IMMEDIATE')
            _expire_stale_interactions(conn, now=now)
            scope=_checkpoint_scope(conn,int(run_id))
            if (scope['owner']['workflow_id']!=int(workflow_id)
                    or scope['checkpoint']['step_id']!=step_id
                    or scope['checkpoint']['kind']!=normalized_kind):
                raise ValueError('The workflow checkpoint changed before its controls were created.')
            current_actions=actions
            if normalized_kind=='provider_preflight':
                raw=conn.execute(text('SELECT run_data FROM auto_workflow_runs WHERE id=:id'),{'id':int(run_id)}).scalar()
                candidates=json.loads(raw or '{}').get('provider_free_candidates') or []
                available=[index for index,candidate in enumerate(candidates[:3])
                           if isinstance(candidate,dict) and not candidate.get('readiness_failed')]
                current_actions=([f'model_{index}' for index in available]+['stop']) if available else ['approve','stop']
            existing = conn.execute(text("""
                SELECT * FROM workflow_interactions
                WHERE status IN ('pending','resolving') AND run_id=:run_id
                  AND COALESCE(step_id, -1)=COALESCE(:step_id, -1) AND kind=:kind
                ORDER BY id DESC LIMIT 1
            """), {"run_id": int(run_id), "step_id": step_id, "kind": normalized_kind}).mappings().first()
            if existing:
                if _scope_matches(conn,dict(existing)):
                    return dict(existing)
                conn.execute(text("UPDATE workflow_interactions SET status='expired' WHERE id=:id AND status='pending'"),{'id':existing['id']})
            token = secrets.token_urlsafe(12)
            conn.execute(text("""
                INSERT INTO workflow_interactions(
                    token, workflow_id, run_id, step_id, kind, allowed_actions,
                    telegram_chat_id, created_at, expires_at, scope_json
                ) VALUES (
                    :token, :workflow_id, :run_id, :step_id, :kind, :actions,
                    :chat_id, :created_at, :expires_at, :scope
                )
            """), {
                "token": token, "workflow_id": int(workflow_id), "run_id": int(run_id),
                "step_id": step_id, "kind": normalized_kind,
                "actions": json.dumps(current_actions), "scope":json.dumps(scope,sort_keys=True),
                "chat_id": str(telegram_chat_id) if telegram_chat_id is not None else None,
                "created_at": now, "expires_at": now + max(60, int(ttl_s)),
            })
        return {
            "token": token, "run_id": run_id, "step_id": step_id,
            "kind": normalized_kind, "allowed_actions": json.dumps(current_actions),
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
        voice_body=None,
        state_fingerprint=(
            f"workflow-interaction:{int(run_id)}:{waiting_kind}:"
            f"reissue:{time.time_ns()}"
        ),
        priority="high",
        requires_response=True,
        audible=False,
        allow_voice=waiting_kind not in {"provider_preflight", "worker_needs_input"},
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
    from distr.core.incoming.telegram_updates import _recipient_id

    clean_token = str(token or "").strip()
    chat_id = _recipient_id(telegram_chat_id)
    message_id = _recipient_id(telegram_message_id)
    if not clean_token or chat_id is None or message_id is None:
        return False
    _ensure_table()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE workflow_interactions "
                "SET telegram_chat_id=:chat_id, "
                "telegram_message_id=:message_id, error=:error "
                "WHERE token=:token AND status='pending'"
                " AND (telegram_chat_id IS NULL OR telegram_chat_id=:chat_id)"
                " AND (:controls OR telegram_message_id IS NULL OR error IS NOT NULL)"
            ),
            {
                "token": clean_token,
                "chat_id": str(chat_id),
                "message_id": str(message_id),
                "controls": reply_markup_sent is True,
                "error": None if reply_markup_sent is True else "Telegram acknowledged the message without its controls",
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
                " AND (telegram_message_id IS NULL OR error IS NOT NULL)"
            ),
            {"token": clean_token, "error": str(error or "Telegram delivery failed")[:1000]},
        )
    return bool(result.rowcount)


_QUESTION_LEAD_RE = re.compile(
    r"^(?:what|what's|whats|which|who|whom|whose|when|where|why|how|"
    r"is|are|am|was|were|do|does|did|can|could|should|would|will|"
    r"has|have|had|any)\b",
    re.IGNORECASE,
)


def _looks_like_status_question(text: str) -> bool:
    """A question about a run is not steering — it must reach the agent."""
    clean = str(text or "").strip()
    if not clean:
        return False
    if clean.endswith("?"):
        return True
    return bool(_QUESTION_LEAD_RE.match(clean))


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
    if re.search(r"\b(continue|resume|looks good|carry on|go ahead|proceed)\b", low) and "continue" in allowed_actions:
        return "continue", clean
    if _looks_like_status_question(clean):
        # "what's actually waiting?" is a question, not a steering instruction.
        # Return None so the conversational agent answers it (with the run
        # context) instead of silently feeding it to the worker as feedback.
        return None, clean
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
        pending=[]
        for row in rows:
            item=dict(row)
            if _scope_matches(conn,item):
                pending.append(item)
            else:
                conn.execute(text("UPDATE workflow_interactions SET status='expired' WHERE id=:id AND status='pending'"),{'id':item['id']})
    return pending


def resolve_interaction(
    *, token: str, action: str, response_text: str = "", source: str = "telegram",
    resolver_id: str = "", chat_id: int | str | None = None,
    background: bool = False,
) -> dict[str, Any]:
    """Atomically claim an interaction, then apply its workflow transition."""
    _ensure_table()
    now = time.time()
    with engine.begin() as conn:
        if conn.dialect.name=='sqlite': conn.exec_driver_sql('BEGIN IMMEDIATE')
        _expire_stale_interactions(conn, now=now)
        row = conn.execute(text("SELECT * FROM workflow_interactions WHERE token=:token"), {"token": token}).mappings().first()
        if not row:
            return {"error": "Interaction not found", "status_code": 404}
        item = dict(row)
        bound_chat=item.get('telegram_chat_id')
        telegram_source=str(source or '').lower().startswith('telegram')
        if ((telegram_source and (bound_chat is None or chat_id is None))
                or (bound_chat is not None and chat_id is not None and str(chat_id)!=str(bound_chat))):
            return {'error':'Interaction belongs to another or unverified Telegram chat','status_code':403}
        if item["status"] != "pending":
            if (
                item["status"] in {"resolving", "resolved"}
                and item.get("resolved_action") == action
                and _scope_matches(conn,item,owner_only=True)
            ):
                return {
                    "success": True,
                    "run_id": item["run_id"],
                    "action": action,
                    "idempotent": True,
                }
            return {"error": f"Interaction already {item['status']}", "status_code": 409, "idempotent": True}
        if not _scope_matches(conn,item):
            conn.execute(text("UPDATE workflow_interactions SET status='expired' WHERE token=:token AND status='pending'"),{'token':token})
            return {'error':'The workflow checkpoint or its work scope changed. Review the current question.','status_code':409}
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
                "resolver_id": resolver_id,
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
        resolver_id=resolver_id,
    )


def _apply_claimed_interaction(
    *,
    item: dict[str, Any],
    token: str,
    action: str,
    response_text: str,
    source: str,
    resolved_at: float,
    resolver_id: str = "",
) -> dict[str, Any]:
    """Apply a previously claimed interaction, optionally on a worker thread."""
    with engine.begin() as conn:
        current=conn.execute(text('SELECT * FROM workflow_interactions WHERE token=:token'),{'token':token}).mappings().first()
        if (not current or current['status']!='resolving' or current['resolved_action']!=action
                or not _scope_matches(conn,dict(current))):
            conn.execute(text("UPDATE workflow_interactions SET status='expired' WHERE token=:token AND status='resolving'"),{'token':token})
            return {'error':'The workflow checkpoint changed before this decision was applied. Review the current question.','status_code':409}
    failure_status=None
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
                resolver_id=resolver_id,
            )
        nested=result.get('dispatch') if isinstance(result.get('dispatch'),dict) else {}
        failed=next((entry for entry in (result,nested) if entry.get('error') or entry.get('success') is False),None)
        if failed is not None:
            failure_status=failed.get('status_code')
            raise RuntimeError(str(failed.get('error') or failed.get('message') or 'The workflow decision was not applied.'))
    except Exception as exc:
        with engine.begin() as conn:
            retryable=_scope_matches(conn,item)
            conn.execute(text("UPDATE workflow_interactions SET status=:status, error=:error WHERE token=:token AND status='resolving'"),
                         {'status':'pending' if retryable else 'expired',"error": str(exc)[:2000], "token": token})
        logger.exception("Workflow interaction resolution failed token=%s", token[:6])
        return {"error": str(exc), "status_code": getattr(exc,'status_code',failure_status or 500)}

    with engine.begin() as conn:
        conn.execute(text("UPDATE workflow_interactions SET status='resolved', resolved_at=:now, error=NULL WHERE token=:token AND status='resolving'"), {"now": resolved_at, "token": token})
    try:
        from distr.core.human_engagement import mark_workflow_engagement_answered

        mark_workflow_engagement_answered(int(item["run_id"]))
    except Exception:
        logger.debug("Could not mark workflow engagement answered", exc_info=True)
    logger.info("Workflow interaction resolved run=%s action=%s source=%s", item["run_id"], action, source)
    return {"success": True, "run_id": item["run_id"], "action": action, "result": result}


def interaction_approver(source: str, resolver_id: str = "") -> str:
    """Real approver identity for a resolved interaction, or '' if unknown."""
    who = str(resolver_id or "").strip()
    if not who:
        return ""
    channel = str(source or "interaction").strip() or "interaction"
    if channel.lower().startswith("telegram"):
        return f"telegram user {who} via {channel}"
    return f"{who} via {channel}"


def _apply_standard_workflow_interaction(
    *, item: dict[str, Any], action: str, response_text: str, source: str, resolver_id: str = ""
) -> dict[str, Any]:
    """Apply a normal workflow checkpoint after the durable claim succeeds."""
    approver = interaction_approver(source, resolver_id)
    checkpoint_scope=json.loads(item['scope_json'])
    synchronous_redispatch = str(source or "").strip().lower() in {
        "cli",
        "qualification",
        "test_harness",
    }
    if item["kind"] == "route_approval":
        from distr.core.workflow.service import apply_run_route_approval

        kwargs = {"synchronous_redispatch": True} if synchronous_redispatch else {}
        kwargs['expected_checkpoint']=checkpoint_scope
        result = apply_run_route_approval(
            int(item["run_id"]),
            approved=action == "approve",
            approved_by=approver,
            **kwargs,
        )
    elif item["kind"] == "provider_preflight" and action.startswith("model_"):
        from distr.core.workflow.service import apply_run_provider_model_selection

        kwargs = {"synchronous_redispatch": True} if synchronous_redispatch else {}
        kwargs['expected_checkpoint']=checkpoint_scope
        result = apply_run_provider_model_selection(
            int(item["run_id"]), int(action.rsplit("_", 1)[1]), approved_by=approver, **kwargs
        )
    elif item["kind"] == "provider_preflight" and action == "approve":
        from distr.core.workflow.service import apply_run_route_approval

        kwargs = {"synchronous_redispatch": True} if synchronous_redispatch else {}
        kwargs['expected_checkpoint']=checkpoint_scope
        result = apply_run_route_approval(int(item["run_id"]), approved=True,
                                          approved_by=approver, **kwargs)
    elif item["kind"] == "pre_execution_approval" and action == "approve":
        from distr.core.workflow.dispatcher import approve_pre_execution_step

        result = approve_pre_execution_step(
            int(item["run_id"]),
            int(item["step_id"]),
            response_text=response_text,
            expected_checkpoint=checkpoint_scope,
        )
    elif action == "stop":
        from distr.core.workflow.dispatcher import cancel_run

        result = {"success": bool(cancel_run(int(item["run_id"]),expected_checkpoint=checkpoint_scope)), "action": "stop"}
    else:
        from distr.core.workflow.dispatcher import continue_waiting_step

        feedback = response_text if action == "feedback" else response_text or action
        result = continue_waiting_step(int(item["run_id"]), feedback,expected_checkpoint=checkpoint_scope)
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
    checkpoint_scope=json.loads(item['scope_json'])

    if action == "stop":
        from distr.core.workflow.dispatcher import cancel_run

        return {"success": bool(cancel_run(int(item["run_id"]),expected_checkpoint=checkpoint_scope)), "action": "stop"}
    next_phase = _TELEGRAM_QUALIFICATION_PHASES.get(str(item.get("kind") or ""))
    if not next_phase:
        return {"error": "Telegram qualification phase cannot advance"}
    next_kind, prompt = next_phase
    with get_session() as db:
        guard_workflow_checkpoint(db,int(item['run_id']),checkpoint_scope)
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
    if clean.startswith('wf:'):
        return {'error':'This workflow control is invalid or no longer supported. Review the current question.','status_code':400}
    if str(source or '').lower()=='telegram_callback':
        return None
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
        return f"I couldn't apply that workflow decision: {result['error']}"
    action = str(result.get("action") or "continue")
    run_id = result.get("run_id")
    run_label = f"run #{run_id}" if run_id else "the run"
    if result.get('queued'):
        return f"I've queued your decision for {run_label}."
    if re.fullmatch(r"model_[0-2]", action):
        return f"I've asked {run_label} to try free-model option {int(action[-1]) + 1}."
    phrases = {
        "approve": f"Done — I've let {run_label} carry on.",
        "continue": f"Got it — {run_label} is carrying on.",
        "reject": f"I've declined the route override for {run_label}.",
        "stop": f"Understood — I've stopped {run_label}.",
        "feedback": f"Thanks — I've passed that to {run_label}; it'll pick up where it paused.",
    }
    return phrases.get(action, f"Got it — I've applied that to {run_label}.")
