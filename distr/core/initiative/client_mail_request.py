"""Paul asks, in chat, Telegram or the remote app, to look at a mail and make it a proposal.

One direct path, so the request never wanders in the chat tool loop:
find the mail (read only) -> is it already a proposal? -> assess -> investigate (read only)
-> file the curated proposal in Incoming (Tensology sends its Accept/Decline card to Telegram)
-> wait for his approval. Each step is reported through ``job_progress``.

Nothing is sent to a client or anyone else. The proposal is the end of this path; accepting it
is what creates the thread and starts the work, through the existing proposal flow.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import quote

from distr.core.initiative import client_mail_watch as w
from distr.core.initiative import job_progress

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 14
GMAIL_ID = re.compile(r"^[0-9a-f]{15,17}$")
_MESSAGE_ID_LINE = re.compile(r"Message ID:\s*([0-9A-Za-z_-]{8,40})")


# ---------------------------------------------------------------------------
# Finding the mail (read only)
# ---------------------------------------------------------------------------

def _gmail_row(mail: dict[str, Any]) -> dict[str, Any]:
    date = str(mail.get("date") or "")
    try:
        date = parsedate_to_datetime(date).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    return {"_id": str(mail.get("id") or ""), "thread_id": str(mail.get("threadId") or mail.get("id") or ""),
            "from": str(mail.get("from") or ""), "subject": str(mail.get("subject") or ""), "date": date,
            "attachments": [{"filename": a.get("filename", "")} for a in mail.get("attachments") or []],
            "preview": str(mail.get("snippet") or ""), "_body": str(mail.get("body") or mail.get("text_body") or ""),
            "_source": "gmail"}


def _find_in_mailshot(client: Any, *, message_id: str, query: str, now: datetime) -> dict[str, Any] | None:
    best = None
    words = [t for t in re.findall(r"[a-z0-9@.\-]{3,}", query.lower()) if t not in {"the", "and", "mail", "email", "from"}]
    for folder, row in w.fetch_rows(client, since=now - timedelta(days=LOOKBACK_DAYS)):
        if message_id and str(row.get("_id")) == message_id:
            return {**row, "_folder": folder, "_source": "mailshot"}
        hay = f"{row.get('from') or ''} {row.get('subject') or ''} {row.get('preview') or ''}".lower()
        if not message_id and words and all(t in hay for t in words):
            when = w._utc(row.get("date"))
            if best is None or (when and when > best[0]):
                best = (when or now, {**row, "_folder": folder, "_source": "mailshot"})
    return best[1] if best else None


def _find_in_gmail(gmail: Any, *, message_id: str, query: str) -> dict[str, Any] | None:
    if message_id:
        mail = gmail.get_email(message_id)
        return _gmail_row(mail) if mail else None
    if not query.strip():
        return None
    found = gmail.check_inbox(max_results=5, query=quote(f"newer_than:{LOOKBACK_DAYS}d {query}".strip()))
    return _gmail_row(found[0]) if found else None


def find_mail(*, message_id: str = "", query: str = "", mailshot: Any = None, gmail: Any = None,
              now: datetime | None = None) -> dict[str, Any] | None:
    """Mailshot first (what the watcher reads), then Gmail. A Gmail-shaped id goes to Gmail first."""
    now = now or datetime.now(timezone.utc)
    order = ["gmail", "mailshot"] if GMAIL_ID.match(message_id or "") else ["mailshot", "gmail"]
    for source in order:
        try:
            if source == "mailshot":
                client = mailshot if mailshot is not None else w._client()
                row = _find_in_mailshot(client, message_id=message_id, query=query, now=now)
                if row:
                    row["_body"] = w.read_body(client, row)
                    return row
            else:
                conn = gmail
                if conn is None:
                    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

                    conn = GoogleWorkspaceConnector()
                row = _find_in_gmail(conn, message_id=message_id, query=query)
                if row:
                    return row
        except Exception as exc:  # noqa: BLE001
            logger.info("client mail request: %s lookup failed (%s)", source, type(exc).__name__)
    return None


def mail_reference_from_chat(chat_id: int | None) -> str:
    """"That mail": the Gmail message id the previous turns of this chat just read, if any."""
    if not chat_id:
        return ""
    try:
        from distr.core.db import ChatTurnEvent, get_session

        with get_session() as session:
            rows = (session.query(ChatTurnEvent.summary).filter(ChatTurnEvent.chat_id == int(chat_id),
                    ChatTurnEvent.event_type == "tool_completed", ChatTurnEvent.summary.like("%Message ID:%"))
                    .order_by(ChatTurnEvent.id.desc()).limit(3).all())
        for (summary,) in rows:
            match = _MESSAGE_ID_LINE.search(str(summary or ""))
            if match:
                return match.group(1)
    except Exception:
        logger.debug("client mail request: no mail reference in this chat", exc_info=True)
    return ""


# ---------------------------------------------------------------------------
# The proposal
# ---------------------------------------------------------------------------

def _existing_proposal(mid: str, thread_id: str, existing_fn: Callable | None) -> dict[str, Any] | None:
    """An earlier proposal or card for this mail: our own record first, then Tensology's pending list."""
    seen = (w._load_state().get("seen") or {}).get(mid)
    if seen and seen.get("status") in {"proposed", "carded"}:
        return {"id": seen.get("proposal") or seen.get("key"), "via": seen["status"]}
    try:
        if existing_fn is None:
            from distr.core.tensology_client import fetch_pending_work_requests as existing_fn
        rows = existing_fn()
    except Exception:
        return None
    for row in rows or []:
        src = row.get("source") if isinstance(row, dict) and isinstance(row.get("source"), dict) else {}
        if mid in [str(m) for m in src.get("message_ids") or []] or (thread_id and str(src.get("source_thread_id")) == thread_id):
            return {"id": row.get("id"), "via": "pending"}
    return None


def generic_proposal(row: dict[str, Any], mid: str, a: dict[str, Any]) -> dict[str, Any]:
    """A curated proposal from the assessment alone, for a mail the records probe does not cover.
    It says plainly that no records were checked, so nothing here is presented as a root cause."""
    plan = a.get("fix_plan") or ["Read the message", "Find the cause", "Fix it and check the result"]
    summary = a.get("summary") or str(row.get("subject") or "Client mail")
    waiting = a.get("waiting_on_paul") or ["Production changes, restarts, money and anything sent to anyone are yours to approve."]
    desc = (f"What the client wrote:\n- {summary}\n\n"
            "Root cause:\n- Not investigated yet: no records check applies to this message. The first step below finds it.\n\n"
            "The fix:\n" + "\n".join(f"- {s}" for s in plan) +
            ("\n\nI need to know:\n- " + a["needs_info"] if a.get("needs_info") else "") +
            "\n\nWaiting on you:\n" + "\n".join(f"- {s}" for s in waiting))
    return {"curated": True,
            "source": {"channel": str(row.get("_source") or "mailshot"),
                       "source_thread_id": str(row.get("thread_id") or mid), "message_ids": [mid]},
            "title": summary[:200], "description": desc, "tasks": list(plan),
            "rationale": "Asked for by Paul in chat; assessed from the message only.", "priority": "high",
            "kind": "client_issue", "root_cause": [], "waiting_on_paul": list(waiting),
            "reply_to": w._addr(row.get("from"))}


def _result(status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "message": message, **extra}


def handle_request(request: str = "", *, message_id: str = "", query: str = "", parent: str | None = None,
                   settings: dict[str, Any] | None = None, mailshot: Any = None, gmail: Any = None,
                   complete: Callable | None = None, investigate_fn: Callable | None = None,
                   publish_fn: Callable | None = None, send: Callable | None = None,
                   existing_fn: Callable | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Run the path once. Never raises; returns {status, message, ...}. Sends nothing to a client."""
    from distr.core.initiative import outcome_flow as of

    settings = of.load_settings() if settings is None else settings
    job = f"mail:{(message_id or query or request or 'request')[:40]}"
    say = lambda kind, text: job_progress.report(job, kind, text, title="Mail to proposal", parent=parent)  # noqa: E731
    say("picked_up", f"your request: {(query or request or 'the mail you meant')[:90]}")
    try:
        say("step", "looking for the mail (read only)")
        row = find_mail(message_id=message_id, query=query, mailshot=mailshot, gmail=gmail, now=now)
        if row is None:
            say("blocked", "could not find that mail; tell me the sender or subject")
            return _result("not_found", "I could not find that mail. Ask Paul for the sender or the subject.")
        mid, body = str(row["_id"]), str(row.get("_body") or row.get("preview") or "")
        who = parseaddr(str(row.get("from") or ""))[0] or w._addr(row.get("from"))
        say("step", f"found: \u201c{str(row.get('subject') or '(no subject)')[:70]}\u201d from {who}")

        say("step", "checking whether it is already a proposal")
        prior = _existing_proposal(mid, str(row.get("thread_id") or ""), existing_fn)
        if prior:
            say("done", f"already a proposal ({prior['via']}{' #' + str(prior['id']) if prior.get('id') else ''}); nothing new filed")
            return _result("already_proposed", f"This mail is already a proposal ({prior['via']}). Nothing new was filed.", proposal=prior)

        say("step", "assessing the problem")
        a = w.assess(row, body, settings=settings, complete=complete)
        a = {**a, "actionable": True}  # Paul asked for it by name: file it even if the watcher would have skipped it
        if not a.get("summary"):
            a["summary"] = str(row.get("subject") or "Client mail")

        say("step", "investigating the records (read only)")
        investigation = w.investigate_issue(row, body, a, settings=settings, fn=investigate_fn)
        if investigation and investigation.get("plan", {}).get("line_updates"):
            a = w.enrich_assessment(a, investigation)
            proposal = w._proposal(row, body, mid, a, investigation)
            say("step", f"found the cause on the records: {len(investigation['plan']['line_updates'])} line(s) wrong")
        else:
            proposal = generic_proposal(row, mid, a)
            say("step", "no records check applies; the proposal comes from the message")
        proposal["source"]["channel"] = str(row.get("_source") or proposal["source"].get("channel") or "mailshot")

        say("step", "filing the proposal in Incoming")
        published = w.publish_proposal(proposal, settings=settings, publish=publish_fn)
        state = w._load_state()
        seen = state.setdefault("seen", {})
        if published.get("ok"):
            say("proposal", f"filed in Incoming{' #' + str(published['id']) if published.get('id') else ''}; Accept/Decline card sent to Telegram")
            of.audit("client_mail_proposal", message_id=mid, kind=a.get("kind"), proposal_id=published.get("id"),
                     status="published", requested_by="chat")
            seen[mid] = {"at": time.time(), "status": "proposed", "proposal": published.get("id")}
            w._save_state(state)
            say("waiting", "your approval of the proposal; accepting it creates the thread and starts the work")
            return _result("proposed", f"Proposal filed in Incoming and sent to Telegram: {proposal['title']}. "
                           "Nothing has been sent to anyone. Waiting for Paul's approval.",
                           proposal=published, title=proposal["title"])
        # Incoming would not take it: fall back to the Telegram card, which still asks before anything is made.
        say("step", f"Incoming did not accept it ({published.get('error', 'unknown')[:60]}); sending the Telegram card instead")
        key = w._flow_key(mid)
        flow = of.save_flow(key, expected_status="", kind="client_issue", level="operate", status="awaiting_decision",
                            message_id=mid, thread_id=str(row.get("thread_id") or mid), client=a.get("client"),
                            sender=w._addr(row.get("from")), subject=str(row.get("subject") or ""),
                            source=str(row.get("_source") or "mailshot"), assessment=a, body=body[:w.MAX_BODY_CHARS])
        if flow is None:
            say("blocked", "a card for this mail already exists")
            return _result("already_proposed", "A Telegram card for this mail already exists.")
        delivered = bool((send or of.send_telegram)(w.card_text(row, a, str(row.get("_folder") or "inbox")),
                         reply_markup=w.card_markup(key), settings=settings, interaction_token=f"{of.OC_CARD_PREFIX}{key}"))
        of.save_flow(key, expected_status="awaiting_decision", status="awaiting_decision" if delivered else "notification_failed")
        if delivered:
            seen[mid] = {"at": time.time(), "status": "carded", "key": key}
            w._save_state(state)
            say("waiting", "your tap on Start the fix in Telegram")
            return _result("card_only", "Incoming did not take the proposal, so I sent Paul the Telegram card instead. Nothing has been sent to anyone.",
                           error=published.get("error"))
        say("blocked", "could not deliver the Telegram card (is Telegram connected and allowed?)")
        return _result("failed", "I could not file the proposal or send the Telegram card.", error=published.get("error"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("client mail request failed: %s", type(exc).__name__, exc_info=True)
        say("failed", f"{type(exc).__name__}: {str(exc)[:80]}")
        return _result("failed", f"The mail-to-proposal path failed ({type(exc).__name__}).")
