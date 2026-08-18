"""Jira email notification → batch local tickets → Telegram intake digest.

Email wakes the loop; Jira REST is the source of truth. Nothing writes back to
Jira here — completion writes live in jira_work_lifecycle behind Telegram approve.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import text

from distr.core.db import engine, get_session

logger = logging.getLogger(__name__)

# PROJECT-123 style keys. Reject common false friends via classifier, not the regex alone.
_ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_FALSE_PROJECT_KEYS = frozenset({
    "UTF", "ISO", "RFC", "CVE", "PR", "HTTP", "HTTPS", "SSH", "SSL", "TLS", "API",
})

_JIRA_FROM_HINTS = (
    "jira@",
    "atlassian.net",
    "noreply@jira",
    "jira@",
    "no-reply@atlassian",
    "notifications@atlassian",
)

_JIRA_SUBJECT_HINTS = (
    "jira",
    "mentioned you",
    "assigned you",
    "updated",
    "commented",
    "created",
    "moved",
)


def extract_jira_issue_keys(*parts: str) -> list[str]:
    """Return ordered unique Jira issue keys from free text."""
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        for match in _ISSUE_KEY_RE.finditer(str(part or "")):
            key = match.group(1).upper()
            # UTF-8 and similar lookalike traps: require a letter before the dash segment.
            project = key.split("-", 1)[0]
            if not any(ch.isalpha() for ch in project):
                continue
            if project in _FALSE_PROJECT_KEYS:
                continue
            if len(project) < 2 or len(project) > 10:
                continue
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def is_jira_notification_email(
    *,
    from_addr: str = "",
    subject: str = "",
    headers: Optional[dict[str, Any]] = None,
) -> bool:
    """True when the mail looks like an Atlassian/Jira notification."""
    from_l = str(from_addr or "").strip().lower()
    subject_l = str(subject or "").strip().lower()
    header_blob = ""
    if isinstance(headers, dict):
        header_blob = " ".join(str(v) for v in headers.values()).lower()
    hay = f"{from_l} {header_blob}"
    if any(hint in hay for hint in _JIRA_FROM_HINTS):
        return True
    if "jira" in subject_l and any(h in subject_l for h in _JIRA_SUBJECT_HINTS):
        return True
    if extract_jira_issue_keys(subject) and ("atlassian" in hay or "jira" in hay or "jira" in subject_l):
        return True
    return False


def _adf_to_plain(node: Any) -> str:
    """Flatten Atlassian Document Format (or nested lists) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(part for part in (_adf_to_plain(item) for item in node) if part)
    if not isinstance(node, dict):
        return str(node)
    ntype = node.get("type") or ""
    if ntype == "text":
        return str(node.get("text") or "")
    content = node.get("content") or []
    inner = _adf_to_plain(content)
    if ntype in {"paragraph", "heading", "blockquote", "listItem"}:
        return inner.strip()
    if ntype in {"bulletList", "orderedList"}:
        return inner.strip()
    return inner


def jira_issue_to_intake_draft(issue: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Jira issue JSON payload into a local ticket draft."""
    key = str(issue.get("key") or "").strip().upper()
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    if not fields and issue.get("summary"):
        fields = issue
    summary = str(fields.get("summary") or issue.get("summary") or key or "Jira issue").strip()
    raw_desc = fields.get("description")
    if raw_desc is None:
        raw_desc = issue.get("description")
    if isinstance(raw_desc, (dict, list)):
        description = _adf_to_plain(raw_desc).strip()
    else:
        description = str(raw_desc or "").strip()
    if not description:
        description = f"Imported from Jira issue {key or summary}."
    attachments = fields.get("attachment") or issue.get("attachment") or []
    if not isinstance(attachments, list):
        attachments = []
    attachment_meta = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        attachment_meta.append({
            "filename": str(att.get("filename") or att.get("name") or "").strip(),
            "id": str(att.get("id") or "").strip(),
            "mime_type": str(att.get("mimeType") or att.get("mime_type") or "").strip(),
            "content_url": str(att.get("content") or att.get("url") or "").strip(),
            "size": att.get("size"),
        })
    title = f"{key}: {summary}" if key and not summary.upper().startswith(key) else summary
    return {
        "key": key,
        "title": title[:240],
        "description": description,
        "external_id": key,
        "attachments_meta": attachment_meta,
        "priority": str((fields.get("priority") or {}).get("name") or fields.get("priority") or "medium").lower(),
    }


def filter_new_jira_keys(keys: Iterable[str], existing_external_ids: Iterable[str]) -> list[str]:
    """Drop keys already represented as local external_id values."""
    existing = {str(x or "").strip().upper() for x in existing_external_ids if str(x or "").strip()}
    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        clean = str(key or "").strip().upper()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        if clean in existing:
            continue
        out.append(clean)
    return out


def existing_jira_external_ids(*, board_id: int | None = None) -> set[str]:
    """Load external_ids already on local tickets (optionally scoped to a board)."""
    from distr.core.db.kanban import KanbanLane, KanbanTicket

    with get_session() as db:
        query = db.query(KanbanTicket.external_id).filter(KanbanTicket.external_id.isnot(None))
        if board_id:
            query = query.join(KanbanLane).filter(KanbanLane.board_id == int(board_id))
        rows = query.all()
    return {str(row[0]).strip().upper() for row in rows if row and row[0]}


def jira_domain_from_account(acct: dict[str, Any]) -> str:
    domain = str(acct.get("domain") or "").strip()
    if domain:
        return domain.replace("https://", "").replace("http://", "").strip("/")
    server_url = str(acct.get("server_url") or "").strip().rstrip("/")
    if server_url:
        return server_url.replace("https://", "").replace("http://", "").split("/")[0]
    return ""


def load_jira_account() -> dict[str, Any] | None:
    """Return the first valid connected Jira account from settings."""
    import json as _json

    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db() or {}
    raw = settings.get("connected_accounts") or "[]"
    if isinstance(raw, str):
        try:
            accounts = _json.loads(raw) or []
        except Exception:
            accounts = []
    else:
        accounts = raw if isinstance(raw, list) else []
    for acct in accounts:
        if not isinstance(acct, dict):
            continue
        if str(acct.get("provider") or "").lower() != "jira":
            continue
        if acct.get("email") and acct.get("api_token") and jira_domain_from_account(acct):
            if acct.get("is_valid", True):
                return acct
    return None


def fetch_jira_issues(
    acct: dict[str, Any],
    keys: Iterable[str],
    *,
    http_get: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch issues by key. Partial failures skip the bad key instead of aborting."""
    import requests
    from requests.auth import HTTPBasicAuth

    domain = jira_domain_from_account(acct)
    if not domain:
        return []
    getter = http_get or requests.get
    auth = HTTPBasicAuth(acct["email"], acct["api_token"])
    issues: list[dict[str, Any]] = []
    for key in keys:
        clean = str(key or "").strip().upper()
        if not clean:
            continue
        try:
            resp = getter(
                f"https://{domain}/rest/api/3/issue/{clean}",
                auth=auth,
                headers={"Accept": "application/json"},
                params={"fields": "summary,description,priority,attachment,status"},
                timeout=10,
            )
            if getattr(resp, "status_code", 0) != 200:
                logger.info("Jira fetch skipped %s HTTP %s", clean, getattr(resp, "status_code", "?"))
                continue
            payload = resp.json() if hasattr(resp, "json") else resp
            if isinstance(payload, dict):
                issues.append(payload)
        except Exception:
            logger.exception("Jira fetch failed for %s", clean)
    return issues


def format_jira_intake_digest(tickets: list[dict[str, Any]]) -> str:
    """Human-scannable multi-item Telegram digest body."""
    if not tickets:
        return ""
    lines = [f"{len(tickets)} new Jira ticket(s) ready on your board:"]
    for idx, ticket in enumerate(tickets[:20], start=1):
        key = str(ticket.get("external_id") or ticket.get("key") or "").strip()
        title = str(ticket.get("title") or "").strip()
        if key and title.upper().startswith(f"{key.upper()}:"):
            label = title
        elif key:
            label = f"{key}: {title}" if title else key
        else:
            label = title or f"ticket #{ticket.get('id')}"
        tid = ticket.get("id")
        suffix = f" (#{tid})" if tid else ""
        lines.append(f"{idx}. {label}{suffix}")
    lines.append("")
    lines.append("Run them, mark prioritized, or ignore this batch.")
    return "\n".join(lines)


def ensure_intake_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS jira_intake_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token VARCHAR NOT NULL UNIQUE,
                board_id INTEGER,
                ticket_ids TEXT NOT NULL DEFAULT '[]',
                issue_keys TEXT NOT NULL DEFAULT '[]',
                status VARCHAR NOT NULL DEFAULT 'pending',
                telegram_chat_id VARCHAR,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                error TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_jira_intake_batches_status "
            "ON jira_intake_batches(status, created_at)"
        ))


def intake_markup(token: str) -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": "Run all", "callback_data": f"ji:{token}:run"},
        {"text": "Prioritize", "callback_data": f"ji:{token}:prio"},
        {"text": "Ignore", "callback_data": f"ji:{token}:ignore"},
    ]]}


def stage_jira_intake_batch(
    *,
    board_id: int,
    drafts: list[dict[str, Any]],
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Create local tickets for Jira drafts. Does not start workflows."""
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.kanban.ticket_policy import resolve_ticket_complexity

    if not drafts:
        return {"created": [], "skipped": [], "board_id": int(board_id)}
    if len(drafts) > 25:
        raise ValueError("A Jira intake batch may contain at most 25 items")

    # ponytail: ensure lifecycle DDL outside the staging write lock (avoids sqlite locked)
    try:
        from distr.core.kanban import jira_work_lifecycle as jlife

        jlife.ensure_tables()
    except Exception:
        logger.exception("Could not ensure Jira lifecycle tables before staging")

    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    pending_lifecycle: list[dict[str, Any]] = []
    with get_session() as db:
        board = db.get(KanbanBoard, int(board_id))
        if not board:
            raise ValueError(f"Board {board_id} not found")
        lane_name = (board.agent_source_lane or "").strip()
        lane_query = db.query(KanbanLane).filter(KanbanLane.board_id == int(board_id))
        lane = None
        if lane_name:
            lane = lane_query.filter(KanbanLane.name.ilike(lane_name)).first()
        if not lane:
            lane = lane_query.order_by(KanbanLane.position).first()
        if not lane:
            raise ValueError(f"Board {board_id} has no lanes")

        existing_titles = {
            (row.title or "").strip().casefold()
            for row in db.query(KanbanTicket).join(KanbanLane).filter(KanbanLane.board_id == int(board_id)).all()
        }
        existing_ext = {
            str(row.external_id or "").strip().upper()
            for row in db.query(KanbanTicket).join(KanbanLane).filter(
                KanbanLane.board_id == int(board_id),
                KanbanTicket.external_id.isnot(None),
            ).all()
            if row.external_id
        }
        max_pos = (
            db.query(KanbanTicket.position)
            .filter(KanbanTicket.lane_id == lane.id)
            .order_by(KanbanTicket.position.desc())
            .limit(1)
            .scalar()
        )
        max_pos = int(max_pos) if max_pos is not None else -1
        default_project_id = board.default_project_id
        for raw in drafts:
            draft = dict(raw)
            title = str(draft.get("title") or "").strip()
            description = str(draft.get("description") or "").strip()
            external_id = str(draft.get("external_id") or draft.get("key") or "").strip().upper()
            if not title or not description:
                raise ValueError("Every Jira intake draft needs title and description")
            if skip_existing and (
                title.casefold() in existing_titles
                or (external_id and external_id in existing_ext)
            ):
                skipped.append(external_id or title)
                continue
            priority = str(draft.get("priority") or "medium").lower()
            if priority not in {"low", "medium", "high", "critical"}:
                # Jira names like "Highest" map down.
                if "high" in priority or "critical" in priority or "blocker" in priority:
                    priority = "high"
                elif "low" in priority:
                    priority = "low"
                else:
                    priority = "medium"
            max_pos += 1
            ticket = KanbanTicket(
                lane_id=lane.id,
                title=title,
                description=description,
                priority=priority,
                complexity=resolve_ticket_complexity(title, description, requested="auto", file_count=0),
                position=max_pos,
                linked_project_id=board.default_project_id,
                linked_workflow_id=None if board.send_to_cli else board.default_workflow_id,
                linked_action_id=None if board.send_to_cli else board.default_action_id,
                send_to_cli=bool(board.send_to_cli),
                source_provider="jira",
                source_external_id=external_id or None,
                source_label="Jira",
                external_id=external_id or None,
            )
            db.add(ticket)
            db.flush()
            existing_titles.add(title.casefold())
            if external_id:
                existing_ext.add(external_id)
            created.append({
                "id": int(ticket.id),
                "title": title,
                "external_id": external_id,
                "key": external_id,
                "attachments_meta": list(draft.get("attachments_meta") or []),
            })
            pending_lifecycle.append({
                "ticket_id": int(ticket.id),
                "board_id": int(board_id),
                "project_id": default_project_id,
                "issue_key": external_id,
            })

    # After staging commits, record lifecycle rows (separate short writes).
    if pending_lifecycle:
        try:
            from distr.core.kanban import jira_work_lifecycle as jlife

            for row in pending_lifecycle:
                try:
                    jlife.record_ticket_created(**row)
                except Exception:
                    logger.exception("Could not record Jira lifecycle for ticket %s", row.get("ticket_id"))
        except Exception:
            logger.exception("Could not record Jira lifecycle batch")
    return {"created": created, "skipped": skipped, "board_id": int(board_id)}


def attach_intake_files(
    *,
    ticket_id: int,
    attachments_meta: list[dict[str, Any]],
    download_fn: Callable[[dict[str, Any]], Optional[str]] | None = None,
) -> list[str]:
    """Soft-attach downloaded files. Failures skip; staging still succeeds."""
    if not attachments_meta:
        return []
    attached: list[str] = []
    if not download_fn:
        return attached
    from distr.core.db.kanban import KanbanTicket, KanbanTicketFile

    with get_session() as db:
        ticket = db.get(KanbanTicket, int(ticket_id))
        if not ticket:
            return attached
        for meta in attachments_meta:
            try:
                path = download_fn(meta)
            except Exception:
                logger.exception("Jira attachment download failed for ticket %s", ticket_id)
                continue
            if not path:
                continue
            filename = str(meta.get("filename") or path.rsplit("/", 1)[-1] or "attachment")
            db.add(KanbanTicketFile(
                ticket_id=int(ticket_id),
                filename=filename,
                file_path=path,
                description="jira attachment",
            ))
            attached.append(path)
    return attached


def record_intake_batch(
    *,
    board_id: int | None,
    ticket_ids: Iterable[int],
    issue_keys: Iterable[str],
) -> dict[str, Any]:
    ensure_intake_tables()
    token = secrets.token_urlsafe(12)
    now = time.time()
    payload_ids = json.dumps([int(x) for x in ticket_ids if x])
    payload_keys = json.dumps([str(x).upper() for x in issue_keys if x])
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO jira_intake_batches(
                token, board_id, ticket_ids, issue_keys, status, created_at, updated_at
            ) VALUES (
                :token, :board_id, :ticket_ids, :issue_keys, 'pending', :now, :now
            )
        """), {
            "token": token,
            "board_id": board_id,
            "ticket_ids": payload_ids,
            "issue_keys": payload_keys,
            "now": now,
        })
        row = conn.execute(text(
            "SELECT * FROM jira_intake_batches WHERE token=:token"
        ), {"token": token}).mappings().first()
    return dict(row or {"token": token})


def notify_jira_intake_digest(
    *,
    tickets: list[dict[str, Any]],
    batch: dict[str, Any],
) -> bool:
    message = format_jira_intake_digest(tickets)
    if not message:
        return False
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        manager = _telegram_manager_from_app()
        if not manager:
            return False
        return bool(manager.send_to_telegram(
            message,
            reply_markup=intake_markup(str(batch["token"])),
        ))
    except Exception:
        logger.exception("Could not send Jira intake digest to Telegram")
        return False


def _intake_row(token: str) -> dict[str, Any] | None:
    ensure_intake_tables()
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM jira_intake_batches WHERE token=:token"
        ), {"token": token}).mappings().first()
    return dict(row) if row else None


def handle_jira_intake_telegram_reply(
    value: str,
    *,
    chat_id: int | str | None = None,
) -> dict[str, Any] | None:
    """Resolve intake digest callbacks. Never writes to Jira."""
    ensure_intake_tables()
    clean = str(value or "").strip()
    callback = re.fullmatch(r"ji:([A-Za-z0-9_-]+):(run|prio|ignore)", clean)
    if not callback:
        return None
    token, action = callback.groups()
    row = _intake_row(token)
    if not row:
        return {"handled": True, "text": "That Jira intake batch no longer exists."}
    if row["status"] in {"ignored", "running", "prioritized", "executed"}:
        return {"handled": True, "text": "That Jira intake batch was already handled."}

    ticket_ids = json.loads(row.get("ticket_ids") or "[]")
    now = time.time()
    if action == "ignore":
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE jira_intake_batches SET status='ignored', telegram_chat_id=:chat,
                  updated_at=:now WHERE token=:token AND status='pending'
            """), {"chat": str(chat_id) if chat_id is not None else None, "now": now, "token": token})
        return {"handled": True, "text": "Ignored that Jira intake batch. Local tickets stay on the board."}

    if action == "prio":
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE jira_intake_batches SET status='prioritized', telegram_chat_id=:chat,
                  updated_at=:now WHERE token=:token AND status='pending'
            """), {"chat": str(chat_id) if chat_id is not None else None, "now": now, "token": token})
        return {
            "handled": True,
            "text": f"Marked {len(ticket_ids)} Jira ticket(s) as prioritized. Say when to run them.",
            "ticket_ids": ticket_ids,
            "action": "prioritize",
        }

    # run
    with engine.begin() as conn:
        claimed = conn.execute(text("""
            UPDATE jira_intake_batches SET status='running', telegram_chat_id=:chat,
              updated_at=:now WHERE token=:token AND status='pending'
        """), {"chat": str(chat_id) if chat_id is not None else None, "now": now, "token": token}).rowcount
    if not claimed:
        return {"handled": True, "text": "That Jira intake batch is already being handled."}

    exec_result = start_execution_for_tickets(ticket_ids)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE jira_intake_batches SET status='executed', updated_at=:now, error=:error
            WHERE token=:token
        """), {
            "now": time.time(),
            "token": token,
            "error": exec_result.get("error"),
        })
    started = exec_result.get("started") or []
    return {
        "handled": True,
        "text": (
            f"Starting work on {len(started)} Jira ticket(s). "
            "I will ask on Telegram before any client send when each one finishes."
        ),
        "ticket_ids": ticket_ids,
        "started": started,
        "action": "run",
    }


def start_execution_for_tickets(ticket_ids: Iterable[int]) -> dict[str, Any]:
    """Mark Jira lifecycles executing and kick workflow/CLI where linked."""
    from distr.core.db.kanban import KanbanTicket
    from distr.core.kanban import jira_work_lifecycle as jlife

    started: list[dict[str, Any]] = []
    errors: list[str] = []
    ids = [int(x) for x in ticket_ids if x]
    with get_session() as db:
        for ticket_id in ids:
            ticket = db.get(KanbanTicket, ticket_id)
            if not ticket:
                errors.append(f"missing:{ticket_id}")
                continue
            jlife.record_ticket_created(
                ticket_id=ticket_id,
                board_id=ticket.lane.board_id if ticket.lane else None,
                project_id=ticket.linked_project_id,
                issue_key=str(ticket.external_id or ""),
            )
            kind = "cli" if (ticket.send_to_cli or (ticket.linked_project_id and not ticket.linked_workflow_id)) else "workflow"
            jlife.mark_execution_started(ticket_id=ticket_id, execution_kind=kind)
            run_meta: dict[str, Any] = {"ticket_id": ticket_id, "kind": kind}
            try:
                if kind == "cli" and ticket.linked_project_id:
                    # ponytail: CLI kick uses existing tool path when available
                    from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool

                    KanbanTicketTool()._run(action="send_to_cli", ticket_id=ticket_id)
                    run_meta["dispatched"] = "cli"
                elif ticket.linked_workflow_id:
                    from distr.core.workflow.ticket_dispatch import build_ticket_run_item
                    from distr.core.workflow.dispatcher import start_workflow_ticket_group

                    item = build_ticket_run_item(ticket_id)
                    start_workflow_ticket_group(
                        int(ticket.linked_workflow_id),
                        [item],
                        dispatch_async=True,
                    )
                    run_meta["dispatched"] = "workflow"
                else:
                    run_meta["dispatched"] = "none"
                    run_meta["note"] = "no linked workflow or CLI project"
            except Exception as exc:
                logger.exception("Failed to start execution for Jira ticket %s", ticket_id)
                errors.append(f"{ticket_id}:{exc}")
                run_meta["error"] = str(exc)
            started.append(run_meta)
    return {"started": started, "error": "; ".join(errors) if errors else None}


def enable_jira_morning_intake(*, enable_email_scan: bool = True) -> dict[str, Any]:
    """Voice/UI entry: install the morning automation and turn on email scan."""
    from distr.core.initiative.draft_execute import install_automation_preset
    from distr.core.settings import load_settings_from_db, save_settings_to_db

    settings_changed = False
    if enable_email_scan:
        try:
            settings = load_settings_from_db() or {}
            if not settings.get("initiative_scan_email", False):
                settings["initiative_scan_email"] = True
                settings_changed = True
            if not settings.get("initiative_scan_external_boards", False):
                settings["initiative_scan_external_boards"] = True
                settings_changed = True
            if settings_changed:
                save_settings_to_db(settings)
        except Exception:
            logger.exception("Could not enable Jira intake scan settings")

    installed = install_automation_preset("jira_morning_intake")
    status = str(installed.get("status") or "")
    automation = installed.get("automation") if isinstance(installed.get("automation"), dict) else {}
    schedule = automation.get("schedule") if isinstance(automation.get("schedule"), dict) else {}
    when = str(schedule.get("time") or "08:00")
    if status == "exists":
        spoken = (
            f"Jira morning intake is already on. It runs around {when}, "
            "batches Jira email notifications, and asks you on Telegram before updating Jira."
        )
    else:
        spoken = (
            f"Jira morning intake is on. I'll check around {when}, "
            "batch new Jira emails onto your board, ping you on Telegram, "
            "and only update Jira after you approve."
        )
    if settings_changed:
        spoken += " Email and external board scanning are enabled for Initiative."
    return {
        "success": True,
        "action": "enable_jira_intake",
        "status": status or "created",
        "spoken_summary": spoken,
        "automation_id": automation.get("id"),
        "preset_id": "jira_morning_intake",
        "settings_changed": settings_changed,
        "schedule": schedule,
    }


def collect_jira_keys_from_emails(messages: Iterable[dict[str, Any]]) -> list[str]:
    """Pull unique issue keys from a list of Gmail-like message dicts."""
    keys: list[str] = []
    for msg in messages:
        if not is_jira_notification_email(
            from_addr=str(msg.get("from") or ""),
            subject=str(msg.get("subject") or ""),
            headers=msg.get("headers") if isinstance(msg.get("headers"), dict) else None,
        ):
            # Still accept explicit keys when subject/body clearly reference Jira issues
            # only if from/subject already smells like Jira — classifier gate above.
            continue
        keys.extend(extract_jira_issue_keys(
            str(msg.get("subject") or ""),
            str(msg.get("snippet") or ""),
            str(msg.get("body") or ""),
        ))
    return extract_jira_issue_keys(" ".join(keys))


def mailshot_message_to_intake_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Tensology Mailshot email row into the Gmail-shaped intake dict."""
    snippet = str(raw.get("preview") or raw.get("snippet") or raw.get("body") or "").strip()
    return {
        "id": str(raw.get("_id") or raw.get("id") or raw.get("messageId") or "").strip(),
        "from": str(raw.get("from") or raw.get("from_addr") or raw.get("sender") or "").strip(),
        "subject": str(raw.get("subject") or "").strip(),
        "snippet": snippet[:500],
        "body": str(raw.get("body") or snippet or ""),
        "date": str(raw.get("date") or "").strip(),
        "source": "mailshot",
    }


def fetch_mailshot_intake_messages(
    *,
    limit: int = 50,
    max_pages: int = 5,
    jira_only: bool = True,
) -> list[dict[str, Any]]:
    """Read Tensology Mailshot inbox rows shaped for Jira intake classifiers.

    Mailshot is the preferred wake source when Jira notifications land on
    Tensology mail rather than Gmail.
    """
    try:
        from distr.core.tensology_client import TensologyApiError, configured_tensology_client
    except Exception:
        return []

    try:
        client = configured_tensology_client(source="decisionsai-jira-intake")
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    try:
        for page in range(1, max(1, int(max_pages)) + 1):
            data = client.get("mail/messages", {"limit": int(limit), "page": int(page)}) or {}
            emails = data.get("emails") if isinstance(data, dict) else None
            if not isinstance(emails, list) or not emails:
                break
            for raw in emails:
                if not isinstance(raw, dict):
                    continue
                msg = mailshot_message_to_intake_dict(raw)
                if jira_only and not is_jira_notification_email(
                    from_addr=msg.get("from") or "",
                    subject=msg.get("subject") or "",
                ):
                    continue
                out.append(msg)
            total = int((data.get("total") if isinstance(data, dict) else 0) or 0)
            if page * int(limit) >= total:
                break
    except TensologyApiError:
        logger.info("Mailshot unavailable for Jira intake", exc_info=True)
        return []
    except Exception:
        logger.exception("Mailshot Jira intake scan failed")
        return []
    return out


def fetch_gmail_intake_messages(*, max_results: int = 20) -> list[dict[str, Any]]:
    """Optional Gmail fallback when Mailshot has nothing usable."""
    try:
        from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

        connector = GoogleWorkspaceConnector()
        if not connector.is_connected():
            return []
        return connector.check_inbox(
            max_results=max_results,
            query='in:inbox newer_than:7d (from:atlassian.net OR from:jira OR subject:jira OR subject:"[JIRA]")',
        ) or []
    except Exception:
        logger.info("Gmail unavailable for Jira intake", exc_info=True)
        return []


def load_intake_email_messages() -> dict[str, Any]:
    """Prefer Mailshot, then Gmail. Returns messages + which source produced them."""
    mailshot = fetch_mailshot_intake_messages()
    if mailshot:
        return {"messages": mailshot, "source": "mailshot"}
    gmail = fetch_gmail_intake_messages()
    if gmail:
        return {"messages": gmail, "source": "gmail"}
    return {"messages": [], "source": None}


def run_jira_morning_intake(
    *,
    board_id: int,
    messages: list[dict[str, Any]] | None = None,
    keys: list[str] | None = None,
    acct: dict[str, Any] | None = None,
    fetch_fn: Callable[..., list[dict[str, Any]]] | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Collate Jira notification emails into one staged batch + optional Telegram digest."""
    resolved_keys = list(keys or [])
    if not resolved_keys:
        resolved_keys = collect_jira_keys_from_emails(messages or [])
    if not resolved_keys:
        return {"created": [], "skipped": [], "keys": [], "notified": False, "reason": "no_keys"}

    existing = existing_jira_external_ids(board_id=board_id)
    new_keys = filter_new_jira_keys(resolved_keys, existing)
    if not new_keys:
        return {"created": [], "skipped": resolved_keys, "keys": [], "notified": False, "reason": "all_known"}

    account = acct or load_jira_account()
    if not account:
        return {"created": [], "skipped": [], "keys": new_keys, "notified": False, "reason": "no_jira_account"}

    fetcher = fetch_fn or fetch_jira_issues
    issues = fetcher(account, new_keys)
    drafts = [jira_issue_to_intake_draft(issue) for issue in issues]
    drafts = [d for d in drafts if d.get("key")]
    if not drafts:
        return {"created": [], "skipped": [], "keys": new_keys, "notified": False, "reason": "fetch_empty"}

    staged = stage_jira_intake_batch(board_id=board_id, drafts=drafts, skip_existing=True)
    created = staged.get("created") or []
    if not created:
        return {**staged, "keys": new_keys, "notified": False, "reason": "nothing_created"}

    batch = record_intake_batch(
        board_id=board_id,
        ticket_ids=[c["id"] for c in created],
        issue_keys=[c.get("external_id") or c.get("key") for c in created],
    )
    notified = False
    if notify:
        notified = notify_jira_intake_digest(tickets=created, batch=batch)
    return {
        **staged,
        "keys": new_keys,
        "batch_token": batch.get("token"),
        "notified": notified,
        "reason": "ok",
    }


def scan_jira_email_proposals(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a single collated Initiative proposal from Jira notification emails."""
    keys = collect_jira_keys_from_emails(messages)
    if not keys:
        return None
    preview = ", ".join(keys[:8]) + ("…" if len(keys) > 8 else "")
    return {
        "action_type": "jira_intake",
        "description": (
            f"I see {len(keys)} new Jira item(s) in your email ({preview}). "
            "I'll stage them as one batch on your board and ping you on Telegram "
            "before any client send."
        ),
        "telegram_message": (
            f"New Jira work in email: {preview}. Staging onto your board now."
        ),
        "payload": {
            "source": "jira_email",
            "issue_keys": keys,
            "confidence": 0.8,
            "risk_level": "medium",
            "collated": True,
        },
    }
