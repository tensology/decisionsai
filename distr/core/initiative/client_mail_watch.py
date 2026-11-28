"""Client-mail watch: the orchestrator notices client problems in Mailshot by itself.

Mailshot files mail into Inbox, Archive, Spam and Bin, and its Archive/"work" label can
take a client's message out of the Inbox before Paul sees it. This watcher reads all four
folders (list rows only), decides what is a client issue, and puts one Telegram card in
front of Paul with the problem, a fix plan and a drafted reply.

Rules that shape it:
* Nothing is ever sent to a client. The draft reply is text on the card only.
* A card creates nothing until Paul taps "Start the fix". That creates one ticket.
* Only mail from people Paul already corresponds with (his Sent folder, linked boards)
  or a human reporting a clear problem is a candidate. Newsletters, bots and Paul's own
  systems are never candidates.
* Mail already answered by Paul, or older than the first run, never raises a card.
* ``dry_run`` returns the cards that would be sent and touches nothing.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SETTING_KEY = "initiative_client_mail_watch"  # default on; no UI
MIN_INTERVAL_S = 300.0
LOOKBACK_DAYS = 3
FOLDERS = ("inbox", "archived", "spam", "bin")
MAX_BODY_CHARS = 4000
MAX_CARDS_PER_RUN = 3
SENT_PAGES = 3

_LOCK = threading.Lock()
_last_run = 0.0

FREEMAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "live.co.za", "yahoo.com",
    "icloud.com", "me.com", "zohomail.com", "proton.me", "protonmail.com", "webmail.co.za", "mweb.co.za",
})
# Paul's own systems and addresses: never a client.
SELF_DOMAINS = frozenset({"tensology.com", "decisions.tensology.com"})
# Bulk-mail platforms: whatever they carry is a newsletter.
BULK_PLATFORMS = frozenset({"substack.com", "beehiiv.com", "mailchimp.com", "sendgrid.net", "mcsv.net",
                            "constantcontact.com", "convertkit.com", "klaviyo.com", "mailerlite.com"})
# Reports Paul's own agents and trading system mail to him.
_SELF_SUBJECT = re.compile(r"^(?:nightly repo|\[trader\]|currency trader|easyequities market brief)", re.I)

# Vendors Paul pays. A failed payment from one of them is never a newsletter: it can switch a
# service off. Matched on the exact domain or a subdomain of it, never on a display name.
VENDOR_DOMAINS = frozenset({
    "digitalocean.com", "hetzner.com", "hetzner.de", "linode.com", "akamai.com", "vultr.com", "cloudflare.com",
    "amazon.com", "amazonaws.com", "aws.amazon.com", "google.com", "googlecloud.com", "microsoft.com",
    "github.com", "godaddy.com", "namecheap.com", "name.com", "xneelo.co.za", "afrihost.com", "openai.com",
    "anthropic.com", "openrouter.ai", "stripe.com", "paddle.com", "fastspring.com", "sendgrid.com",
    "twilio.com", "mailgun.com", "postmarkapp.com", "render.com", "fly.io", "vercel.com", "netlify.com",
})
_PAYMENT_FAILED = re.compile(
    r"payment (?:has )?(?:failed|was declined|declined|unsuccessful|issue|problem|could not be)|"
    r"(?:failed|declined|unsuccessful|unable to (?:process|charge|collect)) (?:payment|charge|transaction|to charge)|"
    r"(?:card|payment method) (?:was )?(?:declined|expired|failed|on file)|"
    r"(?:billing|payment) (?:issue|problem|failure|problem)|past due|overdue (?:invoice|payment|balance)|"
    r"(?:service|account|droplet|server)s? (?:will be |has been |have been |is |are )?(?:suspended|shut ?down|terminated|locked)|"
    r"update your (?:payment|billing|card)|we (?:were )?(?:unable|couldn'?t) (?:to )?(?:charge|process|collect)|"
    r"action required.{0,40}(?:payment|billing|card)", re.I)

_BULK_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|newsletters?|news|marketing|mailer|mailer-daemon|postmaster|bounces?|"
    r"notifications?|updates?|offers?|promo\w*|deals|hello|info|team|communications?|alerts?)$", re.I)
_BULK_HOST = re.compile(r"^(e|em|email|mail|news|marketing|info|mailer|comms|e-comms|go|send|click\w*|links?|"
                        r"\d+\.ac-inbox|marketing\.\w+)\.", re.I)
_BULK_SUBJECT = re.compile(
    r"webinar|\d+% off|black friday|special offer|newsletter|unsubscribe|\bsale\b|don'?t miss|last chance|"
    r"free gift|limited time|survey|digest|recap|highlights|weekly update|best .* ever|new tricks|"
    r"invitation:|canceled event|statement (?:for|of)|account statement|investment statement|"
    r"customer satisfaction|new chapter|scam checker|confirm your|verify your|verification code|"
    r"reset your password|one-time (?:pin|password)|welcome to|your free|how did you like", re.I)
_ALERT_SUBJECT = re.compile(
    r"\[(?:django|sentry|prod\w*|alert|error)\]|internal server error|task failed|traceback|"
    r"\bERROR\b.*\(", re.I)
_STRONG = re.compile(
    r"\b(correct(?:ion|ed)?|incorrect(?:ly)?|wrong|mistake|discrepanc\w+|over-?charg\w+|double[- ]?charg\w+|"
    r"credit note|refund|not working|doesn'?t work|isn'?t working|broken|bug|crash(?:ed|es|ing)|outage|"
    r"(?:is|are|went|gone) down|fail(?:ed|ing|ure|s)?|can'?t (?:log ?in|access|see)|cannot (?:log ?in|access)|urgent(?:ly)?|asap|"
    r"immediately|escalat\w+|complain\w*|unacceptable|should (?:have been|be) \d|instead of|"
    r"server error|internal server error|error (?:on|when|while)|\b5\d\d error)\b", re.I)
_WEAK = re.compile(r"\b(invoice|quote|payment|unpaid|overdue|deadline|expir\w+|renew\w*|access|question|"
                   r"can you|could you|please|need)\b|\?", re.I)
_ACK = re.compile(r"^\W*(?:thanks?(?: you)?|thank you|ok(?:ay)?|noted|received|great|perfect|cheers|got it|"
                  r"will do|sounds good)\b", re.I)


def _utc(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _addr(value: Any) -> str:
    return parseaddr(str(value or ""))[1].strip().lower()


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def _data_dir() -> Path:
    from distr.core.initiative.outcome_flow import _data_dir as data_dir

    return data_dir()


def _state_path() -> Path:
    return _data_dir() / "client_mail_watch.json"


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    seen = state.get("seen") or {}
    if len(seen) > 2000:  # keep the newest
        keep = sorted(seen.items(), key=lambda kv: kv[1].get("at", 0))[-1500:]
        state["seen"] = dict(keep)
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    tmp.replace(_state_path())


# ---------------------------------------------------------------------------
# Who counts as a client
# ---------------------------------------------------------------------------

def known_correspondents(sent_rows: list[dict[str, Any]], extra_addresses: set[str] | None = None) -> dict[str, set[str]]:
    """Addresses and (non-freemail) domains Paul writes to. Built from his Sent folder."""
    addresses: set[str] = set(extra_addresses or ())
    own: set[str] = set()
    for row in sent_rows:
        own.add(_addr(row.get("from")))
        for field in ("to", "cc"):
            for part in re.split(r"[,;]", str(row.get(field) or "")):
                a = _addr(part)
                if a and _domain(a) not in SELF_DOMAINS:
                    addresses.add(a)
    domains = {_domain(a) for a in addresses if _domain(a) and _domain(a) not in FREEMAIL}
    addresses -= own  # Paul's own addresses are never a client
    return {"addresses": addresses, "domains": domains, "own": own - {""}}


def _is_known(address: str, known: dict[str, set[str]]) -> bool:
    return address in known["addresses"] or _domain(address) in known["domains"]


# ---------------------------------------------------------------------------
# Stage 1: deterministic triage of one list row
# ---------------------------------------------------------------------------

# A vendor subject worth opening the body for, unless it is plainly routine.
_VENDOR_BILLING = re.compile(r"payment|billing|\bcard\b|suspend|past due|overdue|action required|account (?:issue|notice)", re.I)
_VENDOR_ROUTINE = re.compile(r"is available|receipt|thank(?:s| you) for your payment|payment (?:received|successful|"
                             r"confirmation|processed)|successfully (?:paid|charged)|invoice for|statement", re.I)


VENDOR_FIX_PLAN = (
    "Open the vendor's billing page by typing its address, not through a link in the email",
    "Check the payment method on file: expiry, limit and the balance behind it",
    "Retry the payment or update the card, then confirm the invoice shows as paid",
    "Check nothing was suspended or switched off in the meantime and bring it back up if it was",
)


def _vendor(domain: str) -> str:
    """The vendor domain this address belongs to, or ''."""
    for d in VENDOR_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return d
    return ""


def classify(row: dict[str, Any], known: dict[str, set[str]], *, folder: str = "") -> dict[str, Any]:
    """Return {"category", "important", "reasons"} for a Mailshot list row.

    Categories: self, bulk, vendor_payment, client_alert, client, unknown_human, other.
    ``important`` means the orchestrator must look at the message body.
    """
    sender = _addr(row.get("from"))
    local, domain = (sender.split("@", 1) + [""])[:2] if "@" in sender else (sender, "")
    subject = str(row.get("subject") or "")
    text = f"{subject}\n{row.get('preview') or ''}"
    reasons: list[str] = []
    if domain in SELF_DOMAINS or sender in known.get("own", ()) or _SELF_SUBJECT.search(subject):
        return {"category": "self", "important": False, "reasons": ["Paul's own system"]}
    if domain in BULK_PLATFORMS:
        return {"category": "bulk", "important": False, "reasons": ["bulk-mail platform"]}
    vendor = _vendor(domain)
    if vendor and _PAYMENT_FAILED.search(text):
        # Before the bulk rules: vendors send from no-reply@, and this one can take a service offline.
        return {"category": "vendor_payment", "important": True, "vendor": vendor,
                "reasons": [f"failed-payment notice from {vendor}"]}
    if vendor and _VENDOR_BILLING.search(subject) and not _VENDOR_ROUTINE.search(subject):
        return {"category": "vendor_payment", "important": True, "vendor": vendor, "confirm": True,
                "reasons": [f"billing notice from {vendor}; body decides"]}
    known_sender = _is_known(sender, known)
    alert = bool(_ALERT_SUBJECT.search(subject))
    bulkish = bool(
        _BULK_LOCAL.match(local) or _BULK_HOST.match(domain) or _BULK_SUBJECT.search(subject)
        or (folder == "spam" and not known_sender)
    )
    if alert:  # a client's system reporting its own errors is never buried, whoever's domain it is
        return {"category": "client_alert", "important": True, "reasons": ["error alert from a system"]}
    # A known correspondent is never bulk because of a "hello@"/"info@" address or a spam flag.
    if bulkish and not known_sender:
        return {"category": "bulk", "important": False, "reasons": ["newsletter/marketing/bot pattern"]}
    if bulkish and known_sender and _BULK_SUBJECT.search(subject) and not _STRONG.search(text):
        return {"category": "bulk", "important": False, "reasons": ["known sender but a bulk subject"]}
    strong = bool(_STRONG.search(text))
    weak = bool(_WEAK.search(text)) or bool(row.get("attachments"))
    if known_sender:
        if _ACK.match(subject) and not strong:
            return {"category": "client", "important": False, "reasons": ["acknowledgement"]}
        reasons.append("known correspondent")
        if strong:
            reasons.append("problem wording")
        elif weak:
            reasons.append("request/invoice wording")
        if folder in {"archived", "spam", "bin"}:
            reasons.append(f"filed in {folder}")
        # A client's mail is read, whatever the wording: the preview is often empty.
        return {"category": "client", "important": True, "reasons": reasons}
    if _STRONG.search(subject) and folder != "bin":
        return {"category": "unknown_human", "important": True, "reasons": ["unknown sender reporting a problem"]}
    return {"category": "other", "important": False, "reasons": ["no client or problem signal"]}


# ---------------------------------------------------------------------------
# Stage 2: read the message and assess it
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are the orchestrator for Paul, a solo developer who runs Tensology. You read one email "
    "that reached his Mailshot account and decide whether a client is reporting something Paul "
    "must act on: a billing or invoice error, a bug or outage, an access problem, an urgent "
    "request, or a question that blocks the client. Newsletters, receipts, automated notices and "
    "thanks are not actionable.\n"
    "Return only JSON: {\"actionable\": bool, \"kind\": \"billing_error|bug|outage|access|urgent_request|question|vendor_payment|other|none\", "
    "\"urgency\": \"high|normal|low\", \"client\": string, \"summary\": string, \"fix_plan\": [string], "
    "\"draft_reply\": string, \"needs_info\": string}.\n"
    "- client: the sender's first name, with their company in brackets only if the email states it, "
    "for example Ursula (Crystal Logic).\n"
    "- summary: two sentences at most. Say what is wrong, with exact figures, invoice numbers and "
    "line items quoted from the email.\n"
    "- fix_plan: 2-5 concrete steps for Paul's side, ending with re-checking the result. For a billing "
    "error include finding where the wrong amount came from and taking the right amount from the client's "
    "agreed rate on file, never from a default. Invoices and replies are drafts only.\n"
    "- draft_reply: written as Paul, a single developer. Start with the person's first name. Use 'I', "
    "never 'we'. No pleasantries, no restating their problem back to them, no explaining their own setup. "
    "2-3 short sentences. When the error is Paul's (a wrong invoice line, a bug he shipped) give one brief "
    "apology, then say the correction follows. Never promise a date or an amount you were not given. "
    "Empty string when no reply is needed.\n"
    "- needs_info: only when Paul must answer before any work can start. Never ask for a figure that the "
    "plan finds on file (a client's agreed rate). Otherwise empty.\n"
    "- vendor_payment is for a supplier Paul pays (hosting, domains, APIs) saying a payment failed or a service "
    "will be suspended. Set client to the vendor, draft_reply to an empty string, and fix_plan to checking the "
    "card on file via the vendor's own site.\n"
    "Never invent figures. If the email is not actionable set actionable=false and kind=none."
)


def _parse_assessment(raw: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", str(raw or ""), re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or "actionable" not in data:
        return None
    plan = data.get("fix_plan")
    return {
        "actionable": bool(data.get("actionable")),
        "kind": str(data.get("kind") or "other").strip().lower(),
        "urgency": str(data.get("urgency") or "normal").strip().lower(),
        "client": str(data.get("client") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "fix_plan": [str(s).strip() for s in plan if str(s).strip()][:6] if isinstance(plan, list) else [],
        "draft_reply": str(data.get("draft_reply") or "").strip(),
        "needs_info": str(data.get("needs_info") or "").strip(),
    }


def _rules_assessment(row: dict[str, Any], body: str) -> dict[str, Any]:
    """Conservative fallback when no model answers. Never invents a reply."""
    text = f"{row.get('subject') or ''}\n{body}"
    strong = bool(_STRONG.search(text))
    name, _ = parseaddr(str(row.get("from") or ""))
    vendor = _vendor(_domain(_addr(row.get("from"))))
    if vendor:
        return {"actionable": bool(_PAYMENT_FAILED.search(text)), "kind": "vendor_payment", "urgency": "high",
                "client": (name or vendor).strip("'\" "), "summary": f"{vendor} says a payment failed: \u201c{str(row.get('subject') or '')[:100]}\u201d.",
                "fix_plan": list(VENDOR_FIX_PLAN), "draft_reply": "", "needs_info": ""}
    return {
        "actionable": strong, "urgency": "high" if strong else "normal",
        "kind": "billing_error" if strong and re.search(r"\binvoice\b|\bI\d{4,6}\b", text, re.I) else "other",
        "client": name or _addr(row.get("from")),
        "summary": f"{name or 'A client'} wrote \u201c{str(row.get('subject') or '(no subject)')[:100]}\u201d and it reads like a problem.",
        "fix_plan": ["Read the message", "Find the cause", "Fix it and check the result"],
        "draft_reply": "", "needs_info": "",
    }


def assess(row: dict[str, Any], body: str, *, settings: dict[str, Any],
           complete: Callable | None = None) -> dict[str, Any]:
    from distr.core.incoming.telegram_reply_intent import complete_strong

    context = {
        "from": str(row.get("from") or ""), "subject": str(row.get("subject") or ""),
        "received": str(row.get("date") or ""), "attachments": [str(a.get("filename") or "") for a in row.get("attachments") or []],
        "body": body[:MAX_BODY_CHARS],
    }
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    try:
        raw, model = complete_strong(messages, settings, complete=complete, timeout=40.0)
        parsed = _parse_assessment(raw)
        if parsed:
            return {**parsed, "model": model}
        logger.warning("client mail watch: model reply was not valid JSON; using rules")
    except Exception as exc:  # noqa: BLE001
        logger.warning("client mail watch: no model answered (%s); using rules", exc)
    return {**_rules_assessment(row, body), "model": "rules"}


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

_KIND_LABEL = {"billing_error": "billing error", "vendor_payment": "failed vendor payment", "bug": "bug", "outage": "outage", "access": "access problem",
               "urgent_request": "urgent request", "question": "question", "other": "issue"}


def card_text(row: dict[str, Any], a: dict[str, Any], folder: str) -> str:
    client = a.get("client") or parseaddr(str(row.get("from") or ""))[0] or "A client"
    kind = _KIND_LABEL.get(a.get("kind"), "issue")
    where = f" It was sitting in {folder}, not your inbox." if folder in {"archived", "spam", "bin"} else ""
    vendor = a.get("kind") == "vendor_payment"
    lines = [f"{client} reports a {kind}: {a.get('summary') or row.get('subject')}{where}" if not vendor else
             f"{client}: {a.get('summary') or row.get('subject')}{where}"]
    if a.get("root_cause"):
        lines += ["", "Root cause:"] + [f"- {c}" for c in a["root_cause"]]
    if a.get("fix_plan"):
        lines += ["", "The fix:" if a.get("root_cause") else "My plan:"] + [f"{i}. {s}" for i, s in enumerate(a["fix_plan"], 1)]
    if a.get("needs_info"):
        lines += ["", f"I need to know: {a['needs_info']}"]
    waiting = a.get("waiting_on_paul") or ["Production changes, restarts, money and anything sent to anyone are yours to approve."]
    lines += ["", "Waiting on you:"] + [f"- {w}" for w in waiting]
    if vendor:
        lines += ["", "Nothing is paid or sent from here. Open the vendor's site yourself to check."]
    else:
        lines += ["", "Start the fix and I'll make the change, check it, then draft the reply from what actually changed. Nothing goes to the client until you tap send."]
    return "\n".join(lines)


def card_markup(key: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "Start the fix", "callback_data": f"oc:{key}:start"},
                                  {"text": "Not now", "callback_data": f"oc:{key}:cancel"}]]}


def _flow_key(message_id: str) -> str:
    from distr.core.initiative.outcome_flow import _flow_key as flow_key

    return flow_key("client_issue", message_id)


# ---------------------------------------------------------------------------
# Mailshot access (list rows only; reading a body is the one call that marks it read)
# ---------------------------------------------------------------------------

def _client():
    from distr.core.tensology_client import configured_tensology_client

    return configured_tensology_client(source="decisionsai-client-mail-watch")


def fetch_rows(client: Any, *, since: datetime, folders: tuple[str, ...] = FOLDERS) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for folder in folders:
        for page in range(1, 4):
            data = client.get("mail/messages", {"folder": folder, "limit": 50, "page": page}) or {}
            rows = [r for r in data.get("emails") or [] if isinstance(r, dict)]
            for r in rows:
                at = _utc(r.get("date"))
                mid = str(r.get("_id") or "")
                if mid and mid not in seen and at and at >= since:
                    seen.add(mid)
                    out.append((folder, r))
            oldest = _utc(rows[-1].get("date")) if rows else None
            if not rows or (oldest and oldest < since) or page >= int(data.get("total_pages") or 1):
                break
    return out


def _sent_brief(row: dict[str, Any]) -> dict[str, str]:
    return {"to": f"{row.get('to') or ''},{row.get('cc') or ''}", "date": str(row.get("date") or "")}


def fetch_known(client: Any, state: dict[str, Any]) -> dict[str, set[str]]:
    def sent_page(page: int) -> dict[str, Any]:
        return client.get("mail/messages", {"folder": "sent", "limit": 100, "page": page}) or {}

    cached = state.get("known") or {}
    if cached and time.time() - float(cached.get("at") or 0) < 3600:
        # Who Paul writes to changes slowly. What he sent in the last minutes decides "already answered".
        state["sent_index"] = [_sent_brief(r) for r in sent_page(1).get("emails") or [] if isinstance(r, dict)]
        return {"addresses": set(cached["addresses"]), "domains": set(cached["domains"]),
                "own": set(cached.get("own") or ())}
    rows: list[dict[str, Any]] = []
    for page in range(1, SENT_PAGES + 1):
        data = sent_page(page)
        rows += [r for r in data.get("emails") or [] if isinstance(r, dict)]
        if page >= int(data.get("total_pages") or 1):
            break
    extra: set[str] = set()
    try:  # senders linked to a board are clients even before Paul has written to them
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanBoard

        with get_session() as session:
            for board in session.query(KanbanBoard).filter(KanbanBoard.archived.is_(False)).all():
                policy = board.orchestrator_policy if isinstance(getattr(board, "orchestrator_policy", None), dict) else {}
                for key in ("senders", "addresses", "emails"):
                    extra.update(_addr(v) for v in policy.get(key) or [] if _addr(v))
    except Exception:
        logger.debug("client mail watch: board senders unavailable", exc_info=True)
    known = known_correspondents(rows, extra)
    state["known"] = {"at": time.time(), "addresses": sorted(known["addresses"]), "domains": sorted(known["domains"]),
                      "own": sorted(known["own"])}
    state["sent_index"] = [_sent_brief(r) for r in rows[:100]]
    return known


def answered_since(row: dict[str, Any], sent_index: list[dict[str, Any]]) -> bool:
    """True when Paul has already written to this sender after their message."""
    sender, at = _addr(row.get("from")), _utc(row.get("date"))
    if not sender or not at:
        return False
    for s in sent_index:
        sent_at = _utc(s.get("date"))
        if sent_at and sent_at > at and sender in str(s.get("to") or "").lower():
            return True
    return False


def read_body(client: Any, row: dict[str, Any]) -> str:
    detail = client.get(f"mail/messages/{row['_id']}") or {}
    email = detail.get("email") if isinstance(detail.get("email"), dict) else detail
    body = str(email.get("body") or email.get("text") or "").strip()
    if not body:
        body = re.sub(r"<[^>]+>", " ", str(email.get("body_html") or ""))
    return re.sub(r"\s+", " ", body).strip() or str(row.get("preview") or "")


# ---------------------------------------------------------------------------
# Investigation and the proposal (the existing Incoming machinery carries it from here)
# ---------------------------------------------------------------------------

def investigate_issue(row: dict[str, Any], body: str, a: dict[str, Any], *, settings: dict[str, Any],
                      fn: Callable | None = None) -> dict[str, Any] | None:
    """Read-only look at the records the complaint is about. None when there is nothing to look at
    (only billing complaints naming an invoice have a probe today) or the look failed."""
    if a.get("kind") != "billing_error":
        return None
    from distr.core.initiative import client_issue_pipeline as pipe

    text = f"{row.get('subject') or ''}\n{body}"
    if not pipe.read_complaint(text)["invoices"]:
        return None
    try:
        if fn is not None:
            inv = fn(text)
        else:
            inv = pipe.investigate(text, pipe.Runner(target=str(settings.get(pipe.SSH_TARGET_KEY) or pipe.DEFAULT_SSH_TARGET)))
        inv["plan"] = pipe.plan_change(inv)
        return inv
    except Exception as exc:  # noqa: BLE001
        logger.warning("client mail watch: investigation failed (%s)", type(exc).__name__)
        return None


def enrich_assessment(a: dict[str, Any], investigation: dict[str, Any]) -> dict[str, Any]:
    from distr.core.initiative import client_issue_pipeline as pipe

    text = pipe.describe_plan(investigation["plan"], investigation)
    return {**a, "fix_plan": text["tasks"], "root_cause": text.get("root_cause", []),
            "waiting_on_paul": text.get("waiting_on_paul", []), "draft_reply": "",
            "summary": a.get("summary") or text["title"], "investigated": True}


def _proposal(row: dict[str, Any], body: str, mid: str, a: dict[str, Any], investigation: dict[str, Any]) -> dict[str, Any]:
    from distr.core.initiative import client_issue_pipeline as pipe

    flow = {"thread_id": str(row.get("thread_id") or mid), "message_id": mid, "sender": _addr(row.get("from")),
            "client": a.get("client")}
    return pipe.proposal_payload(flow, investigation["plan"], investigation)


def publish_proposal(proposal: dict[str, Any], *, settings: dict[str, Any], publish: Callable | None = None) -> dict[str, Any]:
    """Put the curated proposal into Tensology's Incoming, which shows it in Decisions and sends the
    Accept/Decline card to Telegram. Returns {"ok": False, "error": ...} instead of raising."""
    try:
        if publish is not None:
            out = publish(proposal)
        else:
            from distr.core.tensology_client import create_curated_work_request

            out = create_curated_work_request(proposal, idempotency_key=f"client-issue-{proposal['source']['message_ids'][0]}"[:120])
        return {"ok": True, "id": (out or {}).get("id"), "created": (out or {}).get("created")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run_once(settings: dict[str, Any] | None = None, *, dry_run: bool = False, force: bool = False,
             client: Any = None, complete: Callable | None = None, send: Callable | None = None,
             now: datetime | None = None, lookback_days: int = LOOKBACK_DAYS,
             ignore_state: bool = False, investigate_fn: Callable | None = None,
             publish_fn: Callable | None = None) -> dict[str, Any]:
    """Scan, assess and raise cards. ``dry_run`` sends nothing and saves nothing.

    ``ignore_state`` (used by simulations) also ignores the first-run baseline and the seen list.
    """
    from distr.core.initiative import outcome_flow as of

    settings = of.load_settings() if settings is None else settings
    now = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {"scanned": 0, "important": 0, "cards": [], "skipped": [], "dry_run": dry_run}
    state = {} if ignore_state else _load_state()
    first_run = not state.get("baseline")
    baseline = _utc(state.get("baseline")) or now
    client = client or _client()
    known = fetch_known(client, state)
    since = now - timedelta(days=lookback_days)
    seen: dict[str, Any] = state.setdefault("seen", {})
    for folder, row in fetch_rows(client, since=since):
        mid = str(row["_id"])
        result["scanned"] += 1
        triage = classify(row, known, folder=folder)
        if not triage["important"]:
            continue
        result["important"] += 1
        at = _utc(row.get("date"))
        if not ignore_state and (mid in seen or (first_run and at and at <= baseline) or (at and at < baseline)):
            result["skipped"].append({"id": mid, "domain": _domain(_addr(row.get("from"))), "subject": str(row.get("subject") or "")[:60], "why": "already seen" if mid in seen else "older than the first run"})
            continue
        if answered_since(row, state.get("sent_index") or []):
            result["skipped"].append({"id": mid, "domain": _domain(_addr(row.get("from"))), "subject": str(row.get("subject") or "")[:60], "why": "Paul already replied"})
            seen[mid] = {"at": time.time(), "status": "answered"}
            continue
        if len(result["cards"]) >= MAX_CARDS_PER_RUN:
            break  # the rest wait for the next run
        try:
            body = read_body(client, row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("client mail watch: could not read a message (%s)", type(exc).__name__)
            continue
        if triage["category"] == "vendor_payment":
            # Deterministic: a vendor notice never needs a model to read, and a model must not rewrite its figures.
            a = {**_rules_assessment(row, body), "model": "rules"}
            if triage.get("confirm") and not _PAYMENT_FAILED.search(f"{row.get('subject') or ''}\n{body}"):
                a["actionable"] = False
        else:
            a = assess(row, body, settings=settings, complete=complete)
        if not a["actionable"]:
            result["skipped"].append({"id": mid, "domain": _domain(_addr(row.get("from"))), "subject": str(row.get("subject") or "")[:60], "why": "not actionable", "kind": a.get("kind")})
            seen[mid] = {"at": time.time(), "status": "not_actionable"}
            continue
        key = _flow_key(mid)
        investigation = investigate_issue(row, body, a, settings=settings, fn=investigate_fn)
        proposal = None
        if investigation and investigation.get("plan", {}).get("line_updates"):
            a = enrich_assessment(a, investigation)
            proposal = investigation["proposal"] = _proposal(row, body, mid, a, investigation)
        text = card_text(row, a, folder)
        card = {"id": mid, "key": key, "folder": folder, "category": triage["category"], "reasons": triage["reasons"],
                "assessment": a, "text": text, "markup": card_markup(key), "investigation": investigation,
                "proposal": proposal}
        if dry_run:
            result["cards"].append({**card, "delivered": False})
            continue
        if proposal is not None:
            published = publish_proposal(proposal, settings=settings, publish=publish_fn)
            card["published"] = published
            if published.get("ok"):
                # The proposal is now in Incoming and on Telegram through the normal proposal flow.
                of.audit("client_mail_proposal", message_id=mid, folder=folder, kind=a.get("kind"),
                         proposal_id=published.get("id"), status="published")
                seen[mid] = {"at": time.time(), "status": "proposed", "proposal": published.get("id")}
                result["cards"].append({**card, "delivered": True, "via": "proposal"})
                continue
            logger.info("client mail watch: proposal not published (%s); using the Telegram card", published.get("error"))
        flow = of.save_flow(key, expected_status="", kind="client_issue", level="operate", status="awaiting_decision",
                            message_id=mid, thread_id=str(row.get("thread_id") or mid), client=a.get("client"),
                            sender=_addr(row.get("from")), subject=str(row.get("subject") or ""),
                            source="mailshot", assessment=a, body=body[:MAX_BODY_CHARS])
        if flow is None:
            seen[mid] = {"at": time.time(), "status": "duplicate"}
            continue
        sender_fn = send or of.send_telegram
        delivered = bool(sender_fn(text, reply_markup=card["markup"], settings=settings,
                                   interaction_token=f"{of.OC_CARD_PREFIX}{key}"))
        of.save_flow(key, expected_status="awaiting_decision", status="awaiting_decision" if delivered else "notification_failed")
        of.audit("client_mail_card", message_id=mid, folder=folder, kind=a.get("kind"), delivered=delivered,
                 model=a.get("model"), status="sent" if delivered else "not_delivered")
        if delivered:
            seen[mid] = {"at": time.time(), "status": "carded", "key": key}
        result["cards"].append({**card, "delivered": delivered})
    if not dry_run and not ignore_state:
        state.setdefault("baseline", now.isoformat())
        state["last_run"] = now.isoformat()
        _save_state(state)
    return result


def maybe_run(settings: dict[str, Any]) -> None:
    """Schedule-tick entry. Throttled; never raises."""
    global _last_run
    if not settings.get(SETTING_KEY, True) or not settings.get("initiative_scan_email", True):
        return
    from distr.core.initiative.outcome_flow import current_level, telegram_allowed

    if current_level(settings) == "observe" or not telegram_allowed(settings):
        return
    with _LOCK:
        if time.monotonic() - _last_run < MIN_INTERVAL_S:
            return
        _last_run = time.monotonic()
    try:
        out = run_once(settings)
        if out["cards"]:
            logger.info("client mail watch: %s card(s) raised", len(out["cards"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("client mail watch failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Tap handlers (called from outcome_flow._handle_flow_action)
# ---------------------------------------------------------------------------

def ticket_text(flow: dict[str, Any]) -> str:
    a = flow.get("assessment") or {}
    plan = "\n".join(f"{i}. {s}" for i, s in enumerate(a.get("fix_plan") or [], 1))
    cause = "\n".join(f"- {c}" for c in a.get("root_cause") or [])
    return (
        f"Client issue from {flow.get('client') or flow.get('sender')}: {a.get('summary') or flow.get('subject')}\n\n"
        f"Their email (subject: {flow.get('subject')}):\n{flow.get('body') or ''}\n\n"
        + (f"Root cause found by the read-only investigation:\n{cause}\n\n" if cause else "")
        + f"Plan:\n{plan}\n\n"
        "Order of work: make the change, re-read the records to verify it, and only then draft the reply from "
        "what actually changed (attach any corrected document). Rules: draft only. Send nothing to the client. "
        "Production restarts and money are Paul's to approve. Put the verified result and the draft in front of Paul."
    )


def start_fix(flow: dict[str, Any]) -> dict[str, Any]:
    """Create one ticket for the issue through the normal work intake. Sends nothing."""
    from distr.core.work_intake import WorkIntake, get_work_intake_service

    sender = str(flow.get("sender") or "")
    project_hint = ""
    try:
        from distr.core.kanban.email_intake_rules import resolve_email_link

        link = resolve_email_link(sender)
        project_hint = str(link.project_id or "")
    except Exception:
        logger.debug("client mail watch: no project link for sender", exc_info=True)
    intake = WorkIntake.from_payload({
        "source": "mailshot", "user_text": "Create a ticket: " + ticket_text(flow), "source_user_id": sender,
        "source_thread_id": str(flow.get("thread_id") or ""), "source_message_id": str(flow.get("message_id") or ""),
        "project_hint": project_hint, "urgency": str((flow.get("assessment") or {}).get("urgency") or "normal"),
        "requested_outcome": "Create a ticket for this client issue and work it. Draft only; send nothing.",
        "intake_uid": f"client-mail:{flow.get('message_id')}",
        "metadata": {"intake_approval_confirmed": True, "email_sender": sender, "email_provider": "mailshot",
                     "client_mail_watch": True},
    })
    decision = get_work_intake_service().ingest(intake, execute=True)
    return {"ticket_id": decision.ticket_id, "status": decision.status, "response_text": decision.response_text,
            "action": getattr(decision.action, "value", str(decision.action))}
