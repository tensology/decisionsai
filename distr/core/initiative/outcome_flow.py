"""Initiative outcome flows: Own-outcomes autopilot and the Standard Telegram loop.

Glue only. Every action routes to existing machinery:

* intake staging/acceptance  -> ``kanban.whatsapp_intake_approval`` (``_execute_ready``
  creates the ticket, Development thread and workflow run)
* client replies             -> ``workflow.reply_delivery.deliver_ticket_reply`` /
  ``_deliver_prepared`` (WhatsApp, Gmail, Mailshot adapters + idempotency ledger)
* thread follow-ups          -> ``initiative.action_handlers.control_development_thread``
  (``steer``)
* Incoming proposals         -> ``incoming.proposals.list_incoming_proposals`` /
  ``create_proposal_thread`` / ``dismiss_proposal``

Purpose: help the owner decide. Cards and round-ups surface the options, a
recommendation and what each choice leads to; execution then follows the posture.

Postures: ``observe`` (Quiet), ``operate`` (Standard), ``own`` (Own outcomes).
Own outcomes keeps the owner posted (announce, progress, finish/blocked) on the
chosen channel: Telegram, WhatsApp to their own number, voice, or log only.
All operator- and client-facing text passes through :func:`humanize`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("distr.core.initiative.outcome_flow")

SOURCE_LABELS = {
    "whatsapp": "WhatsApp",
    "gmail": "Gmail",
    "email": "Gmail",
    "mailshot": "Mailshot",
    "telegram": "Telegram",
    "jira": "Jira",
    "trello": "Trello",
    "tensology": "Tensology",
}
SOURCE_TOGGLES = {
    "whatsapp": "initiative_scan_whatsapp",
    "gmail": "initiative_scan_email",
    "email": "initiative_scan_email",
    "mailshot": "initiative_scan_email",
    "telegram": "initiative_scan_telegram",
    "jira": "initiative_scan_external_boards",
    "trello": "initiative_scan_external_boards",
}
FLOW_TTL_S = 3 * 24 * 3600
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Settings / posture helpers
# ---------------------------------------------------------------------------

def load_settings() -> dict[str, Any]:
    try:
        from distr.core.utils import load_settings_from_db

        return dict(load_settings_from_db() or {})
    except Exception:
        logger.debug("outcome_flow: settings unavailable", exc_info=True)
        return {}


def current_level(settings: dict[str, Any] | None = None) -> str:
    from distr.core.initiative.policy import migrate_initiative_level

    settings = load_settings() if settings is None else settings
    return migrate_initiative_level(str(settings.get("initiative_level") or "operate"))


def _flag(settings: dict[str, Any], key: str, default: bool = False) -> bool:
    value = settings.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def normalize_source(source: Any, provider: Any = None) -> str:
    raw = str(provider or source or "").strip().lower()
    if raw in {"mailshot", "tensology_mail", "tensology-mail"}:
        return "mailshot"
    if raw in {"gmail", "email", "google"}:
        return "gmail"
    return raw or "unknown"


def source_label(source: Any) -> str:
    return SOURCE_LABELS.get(normalize_source(source), str(source or "the original channel").title())


def source_enabled(source: Any, settings: dict[str, Any]) -> bool:
    key = SOURCE_TOGGLES.get(normalize_source(source))
    return True if key is None else _flag(settings, key, True)


def telegram_allowed(settings: dict[str, Any]) -> bool:
    return _flag(settings, "initiative_allow_telegram", False)


# ---------------------------------------------------------------------------
# Humanized text
# ---------------------------------------------------------------------------

_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):.*", re.S)
_JSON_RE = re.compile(r"[\{\[][^\{\}\[\]]*[\"'][A-Za-z_]+[\"']\s*:[^\{\}]*[\}\]]", re.S)
_ID_PHRASE_RE = re.compile(
    r"\b(ticket|run|workflow run|thread|development thread|chat|turn|proposal|request|step|job|task)\s*#?\s*\d+\b",
    re.I,
)
_HASH_NUM_RE = re.compile(r"#\d+\b")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX_RE = re.compile(r"\b[0-9a-f]{12,}\b", re.I)
_TOKEN_RE = re.compile(r"\b(?:wi|oc|ip|wa):[A-Za-z0-9_-]+(?::[a-z]+)?\b")
_PATH_RE = re.compile(r"(?:/[\w.\-]+){2,}")
_EXC_RE = re.compile(r"\b[A-Z][A-Za-z]+(?:Error|Exception)\b:?")
_CODE_RE = re.compile(r"`+")
_BOLD_RE = re.compile(r"\*\*|__")


def humanize(text: Any, *, max_len: int = 900, strict: bool = True) -> str:
    """Strip technical noise from text.

    ``strict`` (operator Telegram text) also removes internal IDs, hashes and
    paths. Client-facing bodies use ``strict=False`` so legitimate references a
    client may need (order numbers, phone numbers) survive; JSON, tracebacks,
    internal tokens and markdown are always removed.
    """
    raw = str(text or "")
    raw = _TRACEBACK_RE.sub("", raw)
    raw = _JSON_RE.sub("", raw)
    raw = _UUID_RE.sub("", raw)
    raw = _TOKEN_RE.sub("", raw)
    if strict:
        raw = _ID_PHRASE_RE.sub(
            lambda m: "the work" if m.group(1).lower() in {"run", "workflow run", "chat", "turn", "step", "job"}
            else m.group(1).split()[-1],
            raw,
        )
        raw = _HASH_NUM_RE.sub("", raw)
        raw = _HEX_RE.sub("", raw)
        raw = _PATH_RE.sub("", raw)
    raw = _EXC_RE.sub("a hiccup:", raw)
    raw = _CODE_RE.sub("", raw)
    raw = _BOLD_RE.sub("", raw)
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip(" \t") for line in raw.splitlines()]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    out = re.sub(r"[ \t]+([.,!?])", r"\1", out)
    if len(out) > max_len:
        out = out[: max_len - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def client_body(text: Any) -> str:
    """Client-facing text: no JSON, tracebacks or internal tokens; references kept."""
    return humanize(text, max_len=4000, strict=False)


def looks_technical(text: Any) -> bool:
    """True when text still carries IDs, JSON, tokens or tracebacks."""
    raw = str(text or "")
    return bool(
        _TRACEBACK_RE.search(raw) or _JSON_RE.search(raw) or _UUID_RE.search(raw)
        or _TOKEN_RE.search(raw) or _HASH_NUM_RE.search(raw) or _HEX_RE.search(raw)
        or "`" in raw or "**" in raw
    )


def _first_name(contact: Any) -> str:
    raw = str(contact or "").strip()
    if not raw:
        return "the client"
    if "<" in raw:
        raw = raw.split("<", 1)[0].strip().strip('"') or raw
    if "@" in raw and " " not in raw:
        raw = raw.split("@", 1)[0].replace(".", " ").title()
    if re.fullmatch(r"[+\d\s()-]{6,}", raw):
        return "the client"
    return raw


def _preview(text: Any, limit: int = 220) -> str:
    clean = humanize(text, max_len=limit)
    return " ".join(clean.split())


# ---------------------------------------------------------------------------
# Durable flow state + audit
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    base = os.environ.get("DECISIONS_DB_DIR") or os.path.expanduser("~/.decisions")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path() -> Path:
    return _data_dir() / "initiative_outcome_flows.json"


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    now = time.time()
    flows = state.get("flows") or {}
    state["flows"] = {k: v for k, v in flows.items() if now - float(v.get("updated", now)) < FLOW_TTL_S}
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, default=str, indent=1), encoding="utf-8")
    tmp.replace(_state_path())


def _flow_key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def save_flow(key: str, *, expected_status: str | None = None, **fields: Any) -> dict[str, Any] | None:
    with _LOCK:
        state = _load_state()
        flows = state.setdefault("flows", {})
        flow = dict(flows.get(key) or {})
        if expected_status is not None and flow.get("status", "") != expected_status:
            return None
        flow.update(fields)
        flow["updated"] = time.time()
        flow.setdefault("created", flow["updated"])
        flow.setdefault("key", key)
        flows[key] = flow
        _save_state(state)
        return flow


def get_flow(key: str) -> dict[str, Any] | None:
    return (_load_state().get("flows") or {}).get(key)


def find_workflow_reply_review(ticket_id: int, workflow: dict) -> dict[str, Any] | None:
    """Return the review already created for this exact workflow run and turn."""
    for key, flow in (_load_state().get("flows") or {}).items():
        if (not isinstance(flow, dict) or flow.get("kind") != "reply" or flow.get("ticket_id") != ticket_id
                or flow.get("approval_context") != workflow):
            continue
        status = flow.get("status")
        if status == "notification_failed":
            continue
        return {"delivered": status == "sent", "unconfirmed": status in {"sending", "unconfirmed"},
                "pending_approval": status in {"awaiting_decision", "continuing", "checking", "confirming", "awaiting_rework"},
                "telegram_flow_key": key, "channel": flow.get("source")}
    return None


def posture_handles_reply(ticket_id: Any) -> bool:
    """True when the Initiative flow already sent this ticket's client update or holds it for the owner."""
    live = {"awaiting_decision", "continuing", "checking", "confirming", "awaiting_rework", "sending", "sent", "unconfirmed"}
    for flow in (_load_state().get("flows") or {}).values():
        if flow.get("kind") == "reply" and str(flow.get("ticket_id")) == str(ticket_id) and flow.get("status") in live:
            return True
    return False


def latest_open_flow() -> tuple[str, dict[str, Any]] | None:
    flows = _load_state().get("flows") or {}
    open_flows = [
        (k, v) for k, v in flows.items()
        if v.get("status") in {"awaiting_decision", "continuing", "checking"} and v.get("chat_id")
    ]
    if not open_flows:
        return None
    return max(open_flows, key=lambda kv: float(kv[1].get("updated", 0)))


def audit(event: str, *, orchestrator_scope: dict[str, int] | None = None, **payload: Any) -> None:
    """Append-only audit of every autopilot action and client send (full content)."""
    scope = orchestrator_scope or {}
    record = {"ts": datetime.now().astimezone().isoformat(), "event": event, **payload, **scope}
    logger.warning("InitiativeOutcomeAudit %s", json.dumps(record, default=str))
    try:
        log_dir = _data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "initiative_outcome_audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        logger.debug("outcome audit file write failed", exc_info=True)
    try:
        from distr.core.orchestrator import emit_event

        emit_event(source="initiative", event_type=f"outcome_{event}", status=str(payload.get("status") or event),
                   summary=str(payload.get("summary") or event)[:300], payload=record, **scope)
    except Exception:
        logger.debug("outcome audit event not recorded", exc_info=True)


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def _telegram_manager():
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        return _telegram_manager_from_app()
    except Exception:
        return None


def send_telegram(text: str, *, reply_markup: dict | None = None, settings: dict[str, Any] | None = None,
                  require_toggle: bool = True, interaction_token: str | None = None,
                  force_reply: bool = False) -> bool:
    settings = load_settings() if settings is None else settings
    if require_toggle and not telegram_allowed(settings):
        return False
    manager = _telegram_manager()
    if not manager:
        return False
    body = humanize(text, max_len=3500)
    markup = {"force_reply": True, "selective": True} if force_reply else reply_markup
    kwargs: dict[str, Any] = {}
    if markup:
        kwargs["reply_markup"] = markup
    if interaction_token:
        kwargs["interaction_token"] = interaction_token
    try:
        try:
            ok = bool(manager.send_to_telegram(body, **kwargs) if kwargs else manager.send_to_telegram(body))
        except TypeError:
            ok = bool(manager.send_to_telegram(body, reply_markup=markup) if markup
                      else manager.send_to_telegram(body))
    except Exception:
        logger.warning("outcome_flow: Telegram send failed", exc_info=True)
        return False
    mid = getattr(manager, "last_telegram_message_id", None)
    if ok and interaction_token and str(interaction_token).startswith(OC_CARD_PREFIX) and mid:
        record_card_message(str(interaction_token)[len(OC_CARD_PREFIX):], mid,
                            getattr(manager, "last_telegram_chat_id", None))
    return ok


def _kb(*rows: list[tuple[str, str]]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows]}


OC_CARD_PREFIX = "oc-card:"
REWORK_PROMPT = "What should I change?"
CANCELLED_REPLACED_TEXT = "Cancelled — replaced by newer card"
SUPERSEDED_TEXT = CANCELLED_REPLACED_TEXT
FORCE_REPLY = {"force_reply": True, "selective": True}
# A Rework prompt waits this long for an answer. After that a message is no longer taken as the
# answer, so an old tap cannot swallow what Paul says next.
REWORK_TTL_S = 20 * 60
_TERMINAL_FLOW = {"sent", "sending", "unconfirmed", "superseded", "cancelled"}


def _as_telegram_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def record_card_message(key: str, message_id: Any, chat_id: Any = None) -> None:
    """Remember a Telegram message id so a later Rework or cancel can edit that card."""
    mid = _as_telegram_id(message_id)
    if not mid or not key:
        return
    key = str(key).removeprefix(OC_CARD_PREFIX)
    flow = get_flow(key)
    if not flow:
        return
    ids = [int(x) for x in (flow.get("telegram_message_ids") or []) if _as_telegram_id(x)]
    if mid not in ids:
        ids.append(mid)
    fields: dict[str, Any] = {"telegram_message_ids": ids}
    cid = chat_id if chat_id is not None else flow.get("telegram_chat_id")
    if cid is not None:
        fields["telegram_chat_id"] = cid
    save_flow(key, **fields)


def edit_telegram(text: str, *, message_id: Any, chat_id: Any = None,
                  reply_markup: dict | None = None, settings: dict[str, Any] | None = None) -> bool:
    settings = load_settings() if settings is None else settings
    if not telegram_allowed(settings):
        return False
    manager = _telegram_manager()
    if not manager or not hasattr(manager, "edit_telegram_message"):
        return False
    try:
        return bool(manager.edit_telegram_message(
            text, message_id=message_id, chat_id=chat_id,
            reply_markup={"inline_keyboard": []} if reply_markup is None else reply_markup,
        ))
    except Exception:
        logger.warning("outcome_flow: Telegram card edit failed", exc_info=True)
        return False



def _send_failure_text(exc: BaseException) -> str:
    """One short status line; never double up 'Nothing was sent'."""
    reason = str(exc or "").strip()
    reason = re.sub(r"^(Nothing was sent[:.]\s*)+", "", reason, flags=re.I).strip()
    if reason.lower().startswith("couldn't send:"):
        return reason
    return f"Couldn't send: {reason or 'the send failed.'}"


def _retire_other_cards(flow: dict[str, Any] | None, keep_id: Any, chat_id: Any = None) -> None:
    """Strip buttons from older cards in this flow so only the current one stays live."""
    keep = _as_telegram_id(keep_id)
    cid = chat_id if chat_id is not None else (flow or {}).get("telegram_chat_id")
    for other in (flow or {}).get("telegram_message_ids") or []:
        oid = _as_telegram_id(other)
        if oid and oid != keep:
            _edit_card_once(flow, oid, CANCELLED_REPLACED_TEXT, cid)


def _reply_on_card(text: str, *, message_id: Any = None, chat_id: Any = None,
                   flow: dict[str, Any] | None = None, reply_markup: dict[str, Any] | None = None,
                   retire_others: bool = True, interaction_token: str | None = None) -> dict[str, Any]:
    """Edit the tapped card in place. Fall back to a new message if the edit fails."""
    markup = {"inline_keyboard": []} if reply_markup is None else reply_markup
    mid = _as_telegram_id(message_id)
    edited = bool(mid) and edit_telegram(text, message_id=mid, chat_id=chat_id, reply_markup=markup)
    if retire_others:
        _retire_other_cards(flow, mid, chat_id)
    out: dict[str, Any] = {"text": text}
    if reply_markup is not None:
        out["reply_markup"] = reply_markup
    if interaction_token:
        out["interaction_token"] = interaction_token
    if edited:
        out["suppress"] = True
        out["edit_message_id"] = mid
    return out


def _edit_card_once(flow: dict[str, Any] | None, mid: Any, text: str, chat_id: Any = None) -> bool:
    """Edit a card to ``text`` unless it already shows it.

    Telegram answers 400 "message is not modified" for a repeat edit, which used to
    log four relay errors per rework and could not tell a real failure from a repeat.
    """
    key = (flow or {}).get("key")
    current = (get_flow(key) if key else flow) or flow or {}
    done = dict(current.get("edited_cards") or {})
    if done.get(str(mid)) == text:
        return True
    ok = edit_telegram(text, message_id=mid, chat_id=chat_id)
    if ok and key:
        done[str(mid)] = text
        save_flow(key, edited_cards=done)
    return ok


def _edit_flow_cards(key: str, flow: dict[str, Any] | None = None, text: str = SUPERSEDED_TEXT) -> None:
    flow = flow or get_flow(key) or {}
    chat_id = flow.get("telegram_chat_id")
    for mid in flow.get("telegram_message_ids") or []:
        _edit_card_once({**flow, "key": flow.get("key") or key}, mid, text, chat_id)


def _supersede_flow(key: str, flow: dict[str, Any] | None = None, *, status: str = "superseded") -> dict[str, Any] | None:
    saved = save_flow(key, status=status)
    _edit_flow_cards(key, saved or flow or get_flow(key))
    return saved


def latest_awaiting_rework() -> tuple[str, dict[str, Any]] | None:
    flows = _load_state().get("flows") or {}
    now = time.time()
    open_flows = [(k, v) for k, v in flows.items()
                  if v.get("status") == "awaiting_rework" and now - float(v.get("updated", 0)) < REWORK_TTL_S]
    if not open_flows:
        return None
    return max(open_flows, key=lambda kv: float(kv[1].get("updated", 0)))


def _is_group_destination(destination: Any, envelope: dict[str, Any] | None = None) -> bool:
    env = envelope or {}
    resolved = env.get("resolved_target") if isinstance(env.get("resolved_target"), dict) else {}
    dest = destination if isinstance(destination, dict) else {}
    raw = str(dest.get("id") or dest.get("jid") or dest.get("thread_id") or resolved.get("chat_id")
              or (destination if isinstance(destination, str) else "") or "")
    return raw.endswith("@g.us") or bool(env.get("is_group") or resolved.get("is_group"))


def _saved_whatsapp_contact_name(target: dict[str, Any] | None) -> str:
    target = target or {}
    jid = str(target.get("jid") or target.get("id") or target.get("thread_id") or "")
    if not jid:
        return ""
    try:
        from distr.core.db import get_session
        from distr.core.incoming.source_reply import _linked_whatsapp_contact_name

        with get_session() as db:
            return _linked_whatsapp_contact_name(db, jid) or ""
    except Exception:
        logger.debug("outcome_flow: contact display name lookup failed", exc_info=True)
        return ""


def thread_contact_name(target: dict[str, Any] | None, label: str = "") -> str:
    """Decisions contact display name for a 1:1 chat; never the raw WhatsApp label."""
    target = target or {}
    names = [str(n).strip() for n in (target.get("recipient_names") or []) if str(n).strip()]
    if names:
        return ", ".join(names)
    saved = _saved_whatsapp_contact_name(target)
    if saved:
        return saved
    if _is_group_destination(target):
        return str(label or "").strip()
    return ""


# ---------------------------------------------------------------------------
# Intake (WhatsApp / Gmail / Mailshot linked batches)
# ---------------------------------------------------------------------------

def _intake_context(token: str) -> dict[str, Any]:
    try:
        from distr.core.kanban.whatsapp_intake_approval import _row

        row = _row(token) or {}
        payload = json.loads(row.get("intake_json") or "{}") or {}
    except Exception:
        payload = {}
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source = normalize_source(payload.get("source"), meta.get("email_provider"))
    client = _first_name(meta.get("contact_name") or meta.get("email_sender") or payload.get("source_user_id"))
    return {
        "source": source,
        "source_label": source_label(source),
        "client": client,
        "project": str(meta.get("linked_project_name") or "").strip(),
        "project_id": meta.get("linked_project_id"),
        "client_ids": [str(v) for v in (meta.get("contact_name"), meta.get("email_sender"), payload.get("source_user_id"),
                                        payload.get("source_thread_id"), meta.get("jid")) if v],
        "summary": _preview(payload.get("user_text") or "", 280),
    }


def _intake_set_aside(ctx: dict[str, Any]) -> str:
    """Why this incoming work is set aside (ignored project or client), or ''."""
    try:
        from distr.core.initiative import attention

        entry = attention.client_ignored(*(ctx.get("client_ids") or []), ctx.get("client"), source=ctx.get("source") or "")
        if entry:
            return f"you asked me not to worry about {entry.get('label') or ctx.get('client') or 'this client'}"
        if attention.project_ignored(ctx.get("project_id"), ctx.get("project")):
            return f"you asked me to set {ctx.get('project') or 'that project'} aside"
    except Exception:
        logger.debug("outcome_flow: attention lookup for intake failed", exc_info=True)
    return ""


def intake_card(token: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = ctx or _intake_context(token)
    project = f" for {ctx['project']}" if ctx.get("project") else ""
    text = (
        f"Something new just came in from {ctx['client']} on {ctx['source_label']}{project}.\n\n"
        f"In short: {ctx['summary'] or 'they need a hand with something.'}\n\n"
        f"Your call: Accept and I'll start on it now and keep you posted. Decline and I'll set it aside, "
        f"and nothing is sent to {ctx['client']}. Or add a note first to steer how I handle it.\n\n"
        "Shall I take it on?"
    )
    markup = _kb([("Accept", f"wi:{token}:yes"), ("Decline", f"wi:{token}:no")], [("Add a note first", f"wi:{token}:add")])
    return {"text": humanize(text), "reply_markup": markup}


def on_intake_staged(pending: dict[str, Any], settings: dict[str, Any] | None = None) -> bool | None:
    """Posture-aware handling of a freshly staged linked intake.

    Returns True/False when handled here, or None to fall back to the legacy card.
    """
    settings = load_settings() if settings is None else settings
    token = str(pending.get("token") or "")
    if not token or pending.get("deduplicated"):
        return None
    level = current_level(settings)
    ctx = _intake_context(token)
    if level == "observe":
        # Quiet: no proactive Telegram about incoming work. It stays in Incoming.
        return False
    set_aside = _intake_set_aside(ctx)
    if level == "own":
        if not source_enabled(ctx["source"], settings):
            audit("own_intake_skipped_source_off", source=ctx["source"], token_hash=_flow_key(token))
            return False
        if set_aside:
            # Ignored project or client: never auto-start, but never go quiet on a waiting client either.
            tension = (f"Heads up: {set_aside}, but {ctx['client']} just sent something new on {ctx['source_label']}"
                       f"{(' for ' + ctx['project']) if ctx.get('project') else ''}, so I haven't started it. "
                       "They may be waiting on you.")
            audit("own_intake_held_set_aside", source=ctx["source"], client=ctx["client"], project=ctx.get("project"),
                  token_hash=_flow_key(token), reason=set_aside, status="held")
            if not (own_channel_choice(settings) == "telegram" and telegram_allowed(settings)):
                # The Telegram card below only reaches the owner when Telegram is on; otherwise say it elsewhere.
                notify_owner(tension + " It's in Incoming when you want to look.", settings, kind="own_tension",
                             source=ctx["source"], client=ctx["client"])
            card = intake_card(token, ctx)
            pending["text"] = humanize(f"{tension}\n\n{card['text']}")
            pending["reply_markup"] = card["reply_markup"]
            return None
        threading.Thread(target=_own_accept, args=(token, ctx, settings), daemon=True,
                         name="initiative-own-intake").start()
        return True
    if level == "operate":
        # Standard: humanized Accept/Decline card. The legacy sender still owns
        # delivery so the Telegram conversations toggle is checked in one place.
        card = intake_card(token, ctx)
        pending["text"] = card["text"]
        if set_aside:
            pending["text"] = humanize(f"Heads up: {set_aside}, but {ctx['client']} is asking for something new.\n\n{card['text']}")
        pending["reply_markup"] = card["reply_markup"]
        return None
    return None


def _own_accept(token: str, ctx: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    from distr.core.kanban.whatsapp_intake_approval import _execute_ready

    announce_own_start(
        f"{ctx['client']}'s request from {ctx['source_label']}" + (f" for {ctx['project']}" if ctx.get("project") else ""),
        f"{ctx.get('summary') or 'they need a hand with something'}. I'll set up a ticket, open a Development "
        f"thread and run it end to end, then send {ctx['client']} a short update on {ctx['source_label']}.",
        settings, source=ctx["source"], client=ctx["client"],
    )
    result = _execute_ready(token, chat_id=None) or {}
    key = _flow_key("intake", token)
    save_flow(key, kind="intake", level="own", source=ctx["source"], client=ctx["client"],
              project=ctx.get("project"), ticket_id=result.get("ticket_id"),
              chat_id=result.get("development_chat_id"), run_id=result.get("workflow_run_id"),
              status="running" if result.get("action") == "ready" else "failed")
    audit("own_intake_accepted", source=ctx["source"], client=ctx["client"], project=ctx.get("project"),
          ticket_id=result.get("ticket_id"), development_chat_id=result.get("development_chat_id"),
          status=result.get("action"))
    if result.get("action") != "ready":
        notify_owner(
            f"I'm blocked on {ctx['client']}'s request from {ctx['source_label']}: I couldn't get it started yet. "
            "It's still waiting in Incoming. You can start it from there, or tell me to try again.",
            settings, kind="own_blocked", source=ctx["source"], client=ctx["client"],
        )
    return result


def humanize_intake_result(result: dict[str, Any]) -> dict[str, Any]:
    """Rewrite the legacy Accept/Decline/Add acknowledgement in a human voice."""
    action = str(result.get("action") or "")
    token = str(result.get("token") or "")
    raw_text = str(result.get("text") or "")
    if action == "ready":
        ctx = _intake_context(token) if token else {"client": "the client", "source": "unknown"}
        key = _flow_key("intake", token or result.get("ticket_id"))
        save_flow(key, kind="intake", level="operate", source=ctx.get("source"), client=ctx.get("client"),
                  project=ctx.get("project"), ticket_id=result.get("ticket_id"),
                  chat_id=result.get("development_chat_id"), run_id=result.get("workflow_run_id"), status="running")
        return {**result, "text": "Great, I'm on it. I've set up the work and started it, and I'll keep you "
                                  "posted here as it moves along.", "reply_markup": None}
    if action == "no":
        return {**result, "text": "No problem, I've set that one aside.", "reply_markup": None}
    if action == "add":
        out = {**result, "text": "Got it, I've added that note. Tap Accept when you're happy for me to start."}
        if token:
            out["reply_markup"] = intake_card(token)["reply_markup"]
        return out
    if action == "ready_failed":
        return {**result, "text": "I couldn't get that started just yet. It's still waiting, so you can tap "
                                  "Accept again in a moment."}
    if action == "scope_rejected":
        return {**result, "text": "I held off on that one because it no longer matches the project it was "
                                  "linked to. Nothing was started."}
    if "already" in raw_text.lower():
        return {**result, "text": "That one's already been taken care of."}
    if "instruction" in raw_text.lower() or "add" in raw_text.lower():
        return {**result, "text": "Sure, what would you like me to add? Just reply with the note."}
    return {**result, "text": humanize(raw_text or "Got it.")}


# ---------------------------------------------------------------------------
# Closing client reply (called from reply_delivery.deliver_ticket_reply)
# ---------------------------------------------------------------------------

def ask_before_client_messages(settings: dict[str, Any] | None = None) -> bool:
    """'Ask before messaging clients or others' (initiative_ask_external_comms). Default on, as in the DB and UI."""
    settings = load_settings() if settings is None else settings
    return _flag(settings, "initiative_ask_external_comms", True)


ASK_FIRST_REASON = "you asked me to check with you before anything is sent to a client"


def _reply_attention(ticket_id: int, target: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    """Is this client or their project set aside? (durable store + projects.agent_ignored)"""
    out: dict[str, Any] = {"project_ignored": False, "client_ignored": None}
    try:
        from distr.core.initiative import attention

        pid = envelope.get("project_id")
        out["project_ignored"] = attention.project_ignored(pid)
        out["client_ignored"] = attention.client_ignored(
            getattr(target, "contact", "") or envelope.get("source_contact"),
            getattr(target, "thread_id", "") or envelope.get("thread_id"), envelope.get("recipient"),
            source=str(getattr(target, "channel", "") or envelope.get("channel") or ""),
        )
    except Exception:
        logger.debug("outcome_flow: attention lookup failed", exc_info=True)
    return out


def _hold_in_telegram(*, level: str, ticket_id: int, shaped: str, source: str, client: str, destination: Any,
                      approval_context: dict | None, settings: dict[str, Any], note: str = "",
                      envelope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Work-done card with Continue / Check it / Send message to client. None when the card can't be shown."""
    ctx = approval_context or {}
    identity = ("reply", ticket_id, hashlib.sha256(shaped.encode()).hexdigest())
    key = _flow_key(*identity, ctx["run_id"]) if ctx.get("run_id") else _flow_key(*identity)
    # Reuse an existing card, including a scoped card created before the key included the run.
    legacy_key = _flow_key(*identity)
    legacy = get_flow(legacy_key)
    if legacy and legacy.get("approval_context") == ctx:
        key = legacy_key
    flow = save_flow(key, expected_status="", kind="reply", level=level, ticket_id=int(ticket_id), body=shaped, source=source,
                     client=client, destination=destination, chat_id=ctx.get("chat_id"),
                     approval_context=ctx, status="awaiting_decision")
    text = work_done_text(
        client, source, client_body(shaped),
        recipient=recipient_label(client, source, destination, envelope),
        answering=_answering_text(ticket_id, envelope),
    )
    if note:
        text = f"{note}\n\n{text}"
    if flow is not None and not send_telegram(text, reply_markup=work_done_markup(key), settings=settings,
                                               interaction_token=f"{OC_CARD_PREFIX}{key}"):
        save_flow(key, expected_status="awaiting_decision", status="notification_failed")
        return None
    saved = get_flow(key) or {}
    status = saved.get("status")
    if status == "notification_failed":
        return None
    if status == "sent":
        return {"delivered": True, "channel": source}
    if status in {"sending", "unconfirmed"} and not saved.get("delivery_key"):
        return {"delivered": False, "unconfirmed": True, "channel": source}
    return {"delivered": False, "pending_approval": True, "body": shaped,
            "telegram_flow_key": key, "reason": "Waiting for your go-ahead in Telegram before anything is sent."}


def before_client_reply(*, ticket_id: int, shaped: str, envelope: dict[str, Any], target: Any,
                        adapter: Any, force: bool, approval_context: dict | None,
                        settings: dict[str, Any] | None = None, ask_first: bool | None = None) -> dict[str, Any] | None:
    """Posture routing for a ticket's client reply. None = keep the legacy path.

    A result with ``needs_approval_gate`` asks reply_delivery to hold the exact text in
    the approval queue (the reply is never sent automatically).
    """
    if force:
        return None
    settings = load_settings() if settings is None else settings
    if ask_first is None:
        ask_first = ask_before_client_messages(settings)
    level = current_level(settings)
    source = normalize_source(getattr(target, "channel", "") or envelope.get("channel"),
                              (envelope.get("resolved_target") or {}).get("channel") if isinstance(envelope.get("resolved_target"), dict) else None)
    client = _first_name(getattr(target, "contact", "") or envelope.get("source_contact"))
    destination = envelope.get("recipient")
    body_key = _flow_key("reply", ticket_id, hashlib.sha256(client_body(shaped).encode()).hexdigest())
    if level == "own":
        # Telegram and Jira updates obey their Initiative switches: blocked, audited, owner told.
        switched_off = ""
        if source == "telegram" and not telegram_allowed(settings):
            switched_off = "Telegram is switched off in your Initiative settings"
        elif source in {"telegram", "jira"} and not source_enabled(source, settings):
            switched_off = f"{source_label(source)} is switched off in your Initiative settings"
        if switched_off:
            body = client_body(shaped)
            audit("own_client_reply_blocked_setting_off", ticket_id=ticket_id, source=source, destination=destination,
                  client=client, body=body, delivered=False, status="not_sent", reason=switched_off)
            save_flow(body_key, kind="reply", level="own", ticket_id=int(ticket_id), body=body, source=source, client=client,
                      destination=destination, status="blocked")
            notify_owner(f"I finished the work for {client} but didn't send them the update on "
                         f"{source_label(source)}, because {switched_off}. It's waiting for you to look at.",
                         kind="own_blocked", settings=settings)
            return {"delivered": False, "blocked": True, "body": body, "reason": f"Nothing was sent. {switched_off}."}
        if not source_enabled(source, settings):
            # The source switch is off: nothing goes out automatically, and the record says so.
            reason = f"{source_label(source)} is switched off in your Initiative settings"
            body = client_body(shaped)
            audit("own_client_reply_blocked_source_off", ticket_id=ticket_id, source=source, destination=destination,
                  client=client, body=body, delivered=False, status="not_sent", reason=reason)
            save_flow(body_key, kind="reply", level="own", ticket_id=int(ticket_id), body=body, source=source, client=client,
                      destination=destination, status="blocked")
            notify_owner(f"I finished the work for {client} but didn't send them the update on {source_label(source)}, "
                         f"because {reason}. The reply is waiting for you to look at.", kind="own_blocked", settings=settings)
            return {"delivered": False, "blocked": True, "body": body, "reason": f"Nothing was sent. {reason}."}
        attention_state = _reply_attention(ticket_id, target, envelope)
        set_aside = ""
        if attention_state.get("client_ignored"):
            set_aside = f"you asked me not to worry about {attention_state['client_ignored'].get('label') or client}"
        elif attention_state.get("project_ignored"):
            set_aside = "you asked me to set this project aside"
        if ask_first or set_aside:
            why = f"{set_aside[0].upper()}{set_aside[1:]}, so I didn't reply on my own." if set_aside else ""
            if telegram_allowed(settings) and _telegram_manager() is not None:
                held = _hold_in_telegram(level="own", ticket_id=ticket_id, shaped=shaped, source=source, client=client,
                                         destination=destination, approval_context=approval_context, settings=settings,
                                         note=why, envelope=envelope)
                if held is not None:
                    audit("own_client_reply_held_for_approval", ticket_id=ticket_id, source=source, destination=destination,
                          client=client, body=client_body(shaped), delivered=False, status="held",
                          reason=set_aside or ASK_FIRST_REASON, surface="telegram")
                    return held
            audit("own_client_reply_held_for_approval", ticket_id=ticket_id, source=source, destination=destination,
                  client=client, body=client_body(shaped), delivered=False, status="held",
                  reason=set_aside or ASK_FIRST_REASON, surface="approval_queue")
            save_flow(body_key, kind="reply", level="own", ticket_id=int(ticket_id), body=client_body(shaped), source=source,
                      client=client, destination=destination, status="held_for_approval")
            notify_owner(f"I finished the work for {client}. {why + ' ' if why else ''}The update for them on "
                         f"{source_label(source)} is waiting for your OK in the app before anything is sent.",
                         kind="own_held", settings=settings)
            return {"needs_approval_gate": True, "reason": set_aside or ASK_FIRST_REASON, "level": "own"}
        from distr.core.workflow.reply_delivery import _deliver_prepared

        body = client_body(shaped)
        outcome = _deliver_prepared(int(ticket_id), body, envelope, adapter, workflow=approval_context)
        delivered = outcome.get("delivered") is True
        awaiting = outcome.get("delivery_status") == "sending" and outcome.get("delivery_key")
        state = "sent" if delivered else "sending" if awaiting else "unconfirmed" if outcome.get("unconfirmed") else "not_sent"
        audit("own_client_reply_sent" if delivered else "own_client_reply_not_sent", ticket_id=ticket_id, source=source,
              destination=destination, client=client, body=body, delivered=delivered, status=state,
              reason=None if delivered else (outcome.get("reason") or outcome.get("error")))
        save_flow(body_key, kind="reply", level="own",
                  ticket_id=int(ticket_id), body=body, source=source, client=client, destination=destination,
                  status=state if state != "not_sent" else "blocked", delivery_key=outcome.get("delivery_key"))
        if awaiting:
            return outcome
        notify_owner(
            (f"Finished: the work for {client} is done and I've sent them an update on {source_label(source)}:"
             f"\n\n\u201c{_preview(body, 500)}\u201d")
            if delivered else
            f"I finished the work for {client} but I'm blocked on sending them the update on {source_label(source)}. "
            "It's waiting for you to look at.",
            kind="own_finished" if delivered else "own_blocked",
            settings=settings,
        )
        return outcome
    if level == "operate" and telegram_allowed(settings):
        return _hold_in_telegram(level="operate", ticket_id=ticket_id, shaped=shaped, source=source, client=client,
                                 destination=destination, approval_context=approval_context, settings=settings,
                                 envelope=envelope)
    return None


def recipient_label(client: str, source: str, destination: Any, envelope: dict[str, Any] | None = None) -> str:
    """Who exactly gets it: the chat or group name, not just a first name."""
    env = envelope or {}
    resolved = env.get("resolved_target") if isinstance(env.get("resolved_target"), dict) else {}
    dest = destination if isinstance(destination, dict) else {}
    name = next((str(v).strip() for v in (
        env.get("conversation_label"), env.get("chat_name"), env.get("group_name"),
        resolved.get("chat_name"), resolved.get("name"), dest.get("chat_name"), dest.get("name"),
    ) if v and str(v).strip() and str(v).strip() != "(No subject)"), "")
    group = _is_group_destination(destination, env)
    # 1:1 chats use the Decisions contact display name, not the raw WhatsApp chat label.
    who = (name or str(client or "the client")) if group else (str(client or "").strip() or "the client")
    kind = "group" if group else "chat"
    return f"{who} ({source_label(source)} {kind})"


def work_done_text(client: str, source: str, body: str, *, recipient: str = "", answering: str = "") -> str:
    """The confirm card: who gets it, what it answers, and the full final text."""
    lines = [f"Ready to send to {recipient or client}:"]
    if answering:
        lines.append(f"Answering: \u201c{_preview(answering, 300)}\u201d")
    lines.append("")
    lines.append(f"\u201c{_preview(body, 3200)}\u201d")
    lines.append("")
    lines.append("Send message to client sends exactly this. Continue if there's more to do, or Check it first.")
    return "\n".join(lines)


def _answering_text(ticket_id: int, envelope: dict[str, Any] | None = None) -> str:
    env = envelope or {}
    for key in ("source_message", "quoted_text", "inbound_text", "client_message"):
        if env.get(key):
            return str(env[key])
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanTicket

        with get_session() as db:
            ticket = db.get(KanbanTicket, int(ticket_id)) if ticket_id else None
            return str(getattr(ticket, "title", "") or "")
    except Exception:
        return ""


def confirm_send_markup(key: str) -> dict[str, Any]:
    return _kb([("Yes, send it", f"oc:{key}:confirm"), ("Rework", f"oc:{key}:rework")],
               [("Not yet", f"oc:{key}:cancel")])


def thread_reply_markup(key: str) -> dict[str, Any]:
    return _kb([("Send message to client", f"oc:{key}:send"), ("Rework", f"oc:{key}:rework")],
               [("Not yet", f"oc:{key}:cancel")])



def _supersede_open_thread_replies(chat_id: int, keep_key: str | None = None) -> None:
    """Retire earlier confirm cards for this thread so only the new one stays live."""
    for key, flow in list((_load_state().get("flows") or {}).items()):
        if key == keep_key or not isinstance(flow, dict):
            continue
        if flow.get("kind") != "thread_reply":
            continue
        try:
            if int(flow.get("chat_id") or 0) != int(chat_id):
                continue
        except (TypeError, ValueError):
            continue
        if flow.get("status") in _TERMINAL_FLOW:
            continue
        _supersede_flow(key, flow)


def hold_thread_reply(chat_id: int, draft: dict[str, Any], *, answering: str = "", label: str = "",
                      settings: dict[str, Any] | None = None) -> str | None:
    """Telegram confirm card for a reply drafted in a Development thread.

    Same full-context card as workflow replies: who gets it, what it answers,
    and the exact text. Nothing is sent until "Yes, send it". Returns the key.
    """
    target = draft.get("target") if isinstance(draft.get("target"), dict) else {}
    client = thread_contact_name(target, label) or "the client"
    source = str(target.get("channel") or "")
    body = str(draft.get("text") or "")
    if not draft.get("id") or not body.strip():
        return None
    key = _flow_key("thread_reply", chat_id, draft["id"])
    _supersede_open_thread_replies(int(chat_id), keep_key=key)
    group = _is_group_destination(target)
    flow = save_flow(key, expected_status="", kind="thread_reply", level="operate", ticket_id=0, body=body,
                     source=source, client=client, destination=target, chat_id=int(chat_id),
                     draft_id=str(draft["id"]), answering=answering,
                     source_key=draft.get("source_key"), response_id=draft.get("response_id"),
                     status="awaiting_decision")
    if flow is None:
        return key  # a card for this exact draft already exists
    text = work_done_text(
        client, source, body,
        recipient=recipient_label(client, source, target, {"chat_name": label} if group else {}),
        answering=answering,
    ).replace(" Continue if there's more to do, or Check it first.", "")
    if not send_telegram(text, reply_markup=thread_reply_markup(key), settings=settings,
                         interaction_token=f"{OC_CARD_PREFIX}{key}"):
        save_flow(key, expected_status="awaiting_decision", status="notification_failed")
        return None
    return key


def _handle_thread_reply_action(key: str, flow: dict[str, Any], action: str, status: str,
                                *, telegram_message_id: Any = None,
                                telegram_chat_id: Any = None) -> dict[str, Any]:
    client = flow.get("client") or "the client"
    src = source_label(flow.get("source"))
    body = str(flow.get("body") or "")
    card_chat = telegram_chat_id or flow.get("telegram_chat_id")
    live = {"awaiting_decision", "confirming"}

    def card_reply(text: str, *, reply_markup: dict[str, Any] | None = None,
                   retire_others: bool = True) -> dict[str, Any]:
        token = f"{OC_CARD_PREFIX}{key}" if reply_markup else None
        return _reply_on_card(
            text, message_id=telegram_message_id, chat_id=card_chat, flow=get_flow(key) or flow,
            reply_markup=reply_markup, retire_others=retire_others, interaction_token=token,
        )

    if action == "send":
        if not save_flow(key, expected_status=status, status="confirming"):
            return _handle_flow_action(key, "send", telegram_message_id=telegram_message_id,
                                       telegram_chat_id=telegram_chat_id)
        return card_reply(
            (f"Here's the message I'd send:\n\nTo: {client}\nVia: {src}\n\n\u201c{body}\u201d\n\n"
             f"If you send it, {client} gets exactly this on {src} straight away. Shall I send it?"),
            reply_markup=confirm_send_markup(key),
        )
    if action == "rework":
        return _begin_rework(key, flow, status)
    if action == "cancel":
        try:
            from distr.core.incoming.source_reply import act_on_draft
            act_on_draft(int(flow["chat_id"]), str(flow["draft_id"]), "cancel")
        except Exception:
            logger.warning("Thread reply cancel from Telegram could not close the draft", exc_info=True)
        _supersede_flow(key, flow, status="cancelled")
        return card_reply(CANCELLED_REPLACED_TEXT, retire_others=True)
    if action == "confirm":
        # A Yes tap on a live card sends. Don't bounce the owner back to "look first".
        if status not in live and status != "sending":
            return card_reply(CANCELLED_REPLACED_TEXT)
        if status != "sending" and not save_flow(key, expected_status=status, status="sending"):
            return _handle_flow_action(key, "confirm", telegram_message_id=telegram_message_id,
                                       telegram_chat_id=telegram_chat_id)
        from distr.core.incoming.source_reply import act_on_draft

        try:
            # Exactly the text shown on the card, through the thread's own approval claim.
            result = act_on_draft(int(flow["chat_id"]), str(flow["draft_id"]), "approve", text=body)
        except Exception as exc:
            save_flow(key, expected_status="sending", status="confirming")
            logger.warning("Thread reply send from Telegram failed", exc_info=True)
            return card_reply(_send_failure_text(exc), reply_markup=confirm_send_markup(key))
        sent = str(result.get("status") or "") == "sent" or result.get("already_sent")
        save_flow(key, expected_status="sending", status="sent" if sent else "unconfirmed")
        audit("thread_client_reply_confirmed", chat_id=flow.get("chat_id"), source=flow.get("source"),
              client=client, body=body, delivered=bool(sent), status="sent" if sent else "unconfirmed")
        if sent:
            return card_reply(f"Sent to {client} on {src}.")
        return card_reply("Couldn't send: waiting for WhatsApp to confirm. Check the conversation before trying again.")
    return card_reply("Use Send message to client, Rework, or Not yet on this one.")


def work_done_markup(key: str) -> dict[str, Any]:
    return _kb([("Continue", f"oc:{key}:continue"), ("Check it", f"oc:{key}:check")],
               [("Send message to client", f"oc:{key}:send"), ("Rework", f"oc:{key}:rework")])


def _thread_steer(chat_id: Any, instruction: str) -> bool:
    if not chat_id:
        return False
    from distr.core.initiative.action_handlers import control_development_thread

    control_development_thread({"operation": "steer", "chat_id": int(chat_id), "instruction": instruction,
                                "request_uid": f"initiative-flow-{_flow_key(chat_id, instruction, time.time())}"})
    return True


def _work_status(ticket_id: Any) -> str:
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanLane, KanbanTicket
        from distr.core.db.workflow import AutoWorkflowRun

        with get_session() as db:
            ticket = db.get(KanbanTicket, int(ticket_id))
            lane = db.get(KanbanLane, ticket.lane_id) if ticket and ticket.lane_id else None
            run = (db.query(AutoWorkflowRun).filter(AutoWorkflowRun.ticket_id == int(ticket_id))
                   .order_by(AutoWorkflowRun.id.desc()).first())
            title = humanize(ticket.title) if ticket else "this piece of work"
            lane_name = (lane.name if lane else "").strip()
            run_status = (run.status if run else "") or ""
    except Exception:
        return "I couldn't pull up the latest status just now, sorry. I'll try again shortly."
    phrase = {
        "completed": "has finished running",
        "running": "is still being worked on",
        "waiting": "is waiting on something",
        "failed": "ran into a problem and stopped",
        "cancelled": "was stopped",
    }.get(run_status.lower(), "is in progress")
    where = f" and it's sitting in {lane_name}" if lane_name else ""
    return f"Here's where things stand: \u201c{title}\u201d {phrase}{where}."


# ---------------------------------------------------------------------------
# Incoming proposals on demand (works in every posture, incl. Quiet)
# ---------------------------------------------------------------------------

_PROPOSALS_RE = re.compile(
    r"^\s*/proposals\b|"
    r"\b(update|list|show|summary|overview|rundown)\b.{0,40}\b(proposals?|incoming)\b|"
    r"\b(proposals?)\b.{0,30}\b(come in|came in|incoming|pending|waiting)\b|"
    r"\bwhat(?:'s| is| has)?\b.{0,20}\b(in incoming|come in)\b|"
    r"^\s*(?:are\s+there\s+)?any\s+(?:new\s+)?proposals?\b",
    re.I,
)
_RECOMMEND_RE = re.compile(
    r"^\s*/(recommendations|outstanding|status)\b|"
    r"\b(what'?s|what is)\b.{0,20}\b(outstanding|left to do|still open)\b|"
    r"\b(project|projects)\b.{0,20}\b(update|status|rundown|recommendations)\b",
    re.I,
)
_NOT_DONE_RE = re.compile(
    r"\b(not done|isn'?t done|not finished|isn'?t finished|not working|doesn'?t work|still (?:broken|missing|wrong)|"
    r"you missed|missed (?:a|the)|still need|it'?s broken|not fixed|isn'?t fixed)\b",
    re.I,
)


def is_proposals_request(text: str) -> bool:
    raw = str(text or "")
    if re.match(r"^\s*(create\s+(?:a\s+)?ticket|run|queue|dismiss|send)\s+(all|\d+)\s*$", raw, re.I):
        return False  # Tensology numbered decisions keep their handler.
    if re.search(r"\b(draft|drafts|approval|approvals)\b", raw, re.I) and not re.search(r"proposal", raw, re.I):
        return False  # Initiative draft read-outs keep their handler.
    return bool(_PROPOSALS_RE.search(raw))


def _proposal_key_hash(proposal: dict[str, Any]) -> str:
    from distr.core.incoming.proposals import _proposal_handoff_key, proposal_review_snapshot

    snapshot = json.dumps([_proposal_handoff_key(proposal), proposal_review_snapshot(proposal)],
                          sort_keys=True, default=str)
    return hashlib.sha256(snapshot.encode()).hexdigest()[:16]


def _pending_proposals(limit: int = 10) -> list[dict[str, Any]]:
    from distr.core.incoming.proposals import list_incoming_proposals

    payload = list_incoming_proposals(limit=50) or {}
    rows = [p for p in payload.get("proposals") or [] if isinstance(p, dict)]
    ignored = ignored_project_names()
    rows = [p for p in rows if str(p.get("project_name") or "").strip().lower() not in ignored]
    return rows[:limit]


_WHATSAPP_RE = re.compile(r"\bwhats\s?app\b", re.I)
_CHECK_RE = re.compile(
    r"\b(check|look|see|overview|update|review|summar\w*|anything|what|which|new|status|linked|proposals?)\b|"
    r"\b(came|come) in\b|\bgoing on\b",
    re.I,
)
_OUTBOUND_RE = re.compile(
    r"\b(send|reply|respond|draft|write|forward|notify)\b|\b(?:tell|ask)\s+(?!me\b|us\b)\w|"
    r"\b(?:and|then)\s+message\b|\blet\s+(?!me\b|us\b)\w.{0,40}\bknow\b",
    re.I,
)


def is_whatsapp_check_request(text: str) -> bool:
    """Asked to check or look at WhatsApp (not to send or reply there)."""
    raw = str(text or "")
    if not _WHATSAPP_RE.search(raw) or _OUTBOUND_RE.search(raw):
        return False
    return bool(_CHECK_RE.search(raw))


def _clock(ts: int) -> str:
    when = datetime.fromtimestamp(int(ts))
    return when.strftime("%H:%M") if when.date() == datetime.now().date() else when.strftime("%d %b %H:%M")


def whatsapp_activity_text(hours: int = 24, limit: int = 8) -> str:
    """Plain summary of recent WhatsApp messages and the project each chat is linked to."""
    from distr.core.db import WhatsAppMessage, WhatsAppPhoneLink, get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.incoming.project_links import project_links

    remote = {str(row.get("source_thread_id")): row for row in project_links() or []
              if isinstance(row, dict) and row.get("source") == "whatsapp"}
    ignored = ignored_project_names()
    since = int(time.time()) - hours * 3600
    chats: dict[str, dict[str, Any]] = {}
    with get_session() as db:
        boards: dict[str, list[str]] = {}
        for jid, name in db.query(WhatsAppPhoneLink.phone_jid, KanbanBoard.name).join(
                KanbanBoard, KanbanBoard.id == WhatsAppPhoneLink.board_id):
            boards.setdefault(str(jid), []).append(str(name))
        rows = db.query(WhatsAppMessage.jid, WhatsAppMessage.from_me, WhatsAppMessage.text,
                        WhatsAppMessage.caption, WhatsAppMessage.media_type,
                        WhatsAppMessage.whatsapp_timestamp).filter(
            WhatsAppMessage.whatsapp_timestamp >= since).order_by(WhatsAppMessage.whatsapp_timestamp).all()
    for jid, from_me, text, caption, media_type, ts in rows:
        chat = chats.setdefault(str(jid), {"count": 0, "last": None, "last_ts": 0})
        chat["count"] += 1
        if not from_me:
            chat["last"] = text or caption or (f"[{media_type}]" if media_type else "")
            chat["last_ts"] = int(ts or 0)
    linked, unlinked = [], 0
    for jid, chat in sorted(chats.items(), key=lambda item: -item[1]["last_ts"]):
        link = remote.get(jid) or {}
        if link.get("is_archived"):
            continue
        projects = [str(p.get("name") or "") for p in link.get("projects") or [] if isinstance(p, dict)]
        projects = [name for name in projects if name and name.lower() not in ignored]
        board_names = sorted(set(boards.get(jid, [])))
        if not projects and not board_names:
            unlinked += 1
            continue
        label = str(link.get("label") or "a linked chat")
        where = ", ".join(projects) if projects else f"{', '.join(board_names)} board only, not linked to a project"
        line = f"- {label} ({where}): {chat['count']} message{'s' if chat['count'] != 1 else ''}"
        if chat["last"] is not None:
            line += f", latest from them at {_clock(chat['last_ts'])}: \u201c{_preview(chat['last'], 120)}\u201d"
        else:
            line += ", none from them"
        linked.append(line)
    if not linked:
        lines = [f"No messages in the last {hours} hours from WhatsApp chats linked to a project."]
    else:
        lines = [f"WhatsApp, last {hours} hours:", *linked[:limit]]
    if unlinked:
        lines.append(f"{unlinked} other chat{'s' if unlinked != 1 else ''} had messages but "
                     f"{'aren' if unlinked != 1 else 'isn'}'t linked to a project.")
    return "\n".join(lines)


def _whatsapp_evidence() -> str:
    try:
        return whatsapp_activity_text()
    except Exception:
        logger.warning("outcome_flow: WhatsApp activity read failed", exc_info=True)
        return "I couldn't read the WhatsApp messages just now."


def whatsapp_overview() -> dict[str, Any]:
    """Asked to check WhatsApp: recent linked messages, their projects, and waiting proposals."""
    proposals = proposals_overview(evidence=False)
    out = {"ack": "Okay, checking WhatsApp now.",
           "text": humanize(_whatsapp_evidence() + "\n\n" + proposals["text"], max_len=3500)}
    if proposals.get("reply_markup"):
        out["reply_markup"] = proposals["reply_markup"]
    return out


def proposals_overview(*, evidence: bool = True) -> dict[str, Any]:
    try:
        rows = _pending_proposals()
    except Exception:
        logger.warning("outcome_flow: proposal list failed", exc_info=True)
        return {"text": "I couldn't reach Incoming just now. Give it a moment and ask me again."}
    if not rows:
        text = "No proposals are waiting for your decision in Incoming right now."
        if evidence:
            text += "\n\n" + _whatsapp_evidence()
        return {"text": humanize(text, max_len=3500)}
    lines = [f"Here's what's come in ({len(rows)}):", ""]
    buttons: list[list[tuple[str, str]]] = []
    for i, proposal in enumerate(rows, 1):
        title = _preview(proposal.get("title") or "Untitled request", 90)
        src = source_label(normalize_source(proposal.get("source"), proposal.get("provider")))
        project = str(proposal.get("project_name") or "").strip()
        lines.append(f"{i}. {title} ({src}{', ' + project if project else ''})")
        h = _proposal_key_hash(proposal)
        buttons.append([(f"Accept {i}", f"ip:{h}:accept"), (f"Decline {i}", f"ip:{h}:decline")])
    lines += ["", "Accept starts the work now and I'll keep you posted. Decline sets it aside, and nothing is "
                  "sent to anyone."]
    return {"text": humanize("\n".join(lines), max_len=3500), "reply_markup": _kb(*buttons)}


def decide_proposal(key_hash: str, decision: str, *, chat_id: Any = None) -> dict[str, Any]:
    from distr.core.incoming.telegram_updates import _recipient_id

    if decision not in {"accept", "decline"}:
        return {"text": "Choose Accept or Decline on the proposal card."}
    recipient = _recipient_id(chat_id)
    if recipient is None:
        return {"text": "Review the proposal in your paired private Telegram chat. Nothing changed."}
    try:
        rows = _pending_proposals(limit=50)
    except Exception:
        return {"text": "I couldn't reach Incoming just now, so nothing changed. Try again in a moment."}
    match = next((p for p in rows if _proposal_key_hash(p) == key_hash), None)
    if match is None:
        return {"text": "That proposal changed or has already been handled. Ask for proposals to review the current list."}
    title = _preview(match.get("title") or "that request", 90)
    try:
        if decision == "accept":
            from distr.core.incoming.proposals import create_proposal_thread

            create_proposal_thread(match, start_work=True, require_review=True, telegram_chat_id=recipient)
            audit("proposal_accepted_from_telegram", title=title, key_hash=key_hash)
            return {"text": f"Great, I've started work on \u201c{title}\u201d."}
        from distr.core.incoming.proposals import dismiss_proposal

        dismiss_proposal(match)
        audit("proposal_declined_from_telegram", title=title, key_hash=key_hash)
        return {"text": f"Done, I've set \u201c{title}\u201d aside."}
    except Exception as exc:
        logger.warning("outcome_flow: proposal %s failed", decision, exc_info=True)
        return {"text": f"I couldn't {'start' if decision == 'accept' else 'set aside'} \u201c{title}\u201d just now. "
                        "It's still waiting in Incoming."}


# ---------------------------------------------------------------------------
# Projects: ignored + pinned + recommendations report
# ---------------------------------------------------------------------------

def ignored_project_ids() -> set[int]:
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as db:
            return {int(p.id) for p in db.query(Project).filter(Project.agent_ignored.is_(True)).all()}
    except Exception:
        return set()


def ignored_project_names() -> set[str]:
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as db:
            return {str(p.name).strip().lower() for p in db.query(Project).filter(Project.agent_ignored.is_(True)).all()}
    except Exception:
        return set()


def pinned_board_ids() -> list[int]:
    try:
        from distr.core.workflow.development_sidebar import load_pinned_boards

        keys = load_pinned_boards(migrate=False) or []
    except Exception:
        return []
    out = []
    for key in keys:
        if str(key).startswith("decisions:"):
            try:
                out.append(int(str(key).split(":", 1)[1]))
            except ValueError:
                continue
    return out


# ---------------------------------------------------------------------------
# Own outcomes: how I keep the owner posted (Telegram / WhatsApp / voice / log)
# ---------------------------------------------------------------------------

OWN_CHANNELS = ("telegram", "whatsapp", "voice", "log")
_PROGRESS_SKIP_KINDS = {"initiative_suggestion"}


def own_channel_choice(settings: dict[str, Any]) -> str:
    """Chosen Own-outcomes update channel. Unset = Telegram if it's on, else log only."""
    raw = str(settings.get("initiative_own_channel") or "").strip().lower()
    if raw in OWN_CHANNELS:
        return raw
    return "telegram" if telegram_allowed(settings) else "log"


def owner_whatsapp_jid(settings: dict[str, Any]) -> str:
    """The owner's own WhatsApp chat ("Message yourself"). Empty when not set."""
    raw = str(settings.get("initiative_owner_whatsapp") or "").strip()
    if raw.endswith("@s.whatsapp.net"):
        return raw
    digits = re.sub(r"\D", "", raw)
    return f"{digits}@s.whatsapp.net" if len(digits) >= 8 else ""


def _speak(text: str) -> bool:
    from distr.core.signals import speak_text_directly_event_queue

    return bool(speak_text_directly_event_queue(text))


def _whatsapp_to_owner(jid: str, text: str) -> bool:
    from distr.core.integrations.whatsapp.relay_client import send_message_via_relay

    result = send_message_via_relay(jid=jid, text=text) or {}
    return bool(result.get("success"))


def notify_owner(text: str, settings: dict[str, Any] | None = None, *, kind: str = "own_update",
                 **meta: Any) -> dict[str, Any]:
    """Tell the owner something through the chosen Own-outcomes channel.

    WhatsApp goes to the owner's own number (relay, honours WhatsApp dry-run);
    voice uses the existing direct-TTS path; Telegram uses the existing sender and
    its toggle; ``log`` writes the audit trail only. A channel that can't deliver
    falls back to Telegram (when allowed) and then to the log, and says so.
    Every update lands in ``initiative_outcome_audit.jsonl`` with full text.
    """
    settings = load_settings() if settings is None else settings
    body = humanize(text, max_len=1500)
    chosen = own_channel_choice(settings)
    channel, delivered, fallback = chosen, False, None
    fallback_channel = "telegram" if telegram_allowed(settings) else "log"
    if chosen == "whatsapp":
        jid = owner_whatsapp_jid(settings)
        if not jid:
            fallback, channel = "no WhatsApp number of your own is set", fallback_channel
        else:
            try:
                delivered = _whatsapp_to_owner(jid, body)
            except Exception:
                logger.warning("outcome_flow: owner WhatsApp update failed", exc_info=True)
                delivered = False
            if not delivered:
                fallback, channel = "WhatsApp didn't accept the update", fallback_channel
    elif chosen == "voice":
        try:
            delivered = _speak(_preview(body, 400))
        except Exception:
            logger.warning("outcome_flow: spoken update failed", exc_info=True)
            delivered = False
        if not delivered:
            fallback, channel = "voice isn't available right now", fallback_channel
    if channel == "telegram" and not delivered:
        if telegram_allowed(settings):
            delivered = send_telegram(body, settings=settings)
            if not delivered:
                fallback, channel = (fallback or "Telegram didn't accept the update"), "log"
        else:
            fallback, channel = (fallback or "Telegram conversations are off"), "log"
    if channel == "log":
        delivered = True  # the audit record below is the delivery
    audit(kind, channel=channel, chosen_channel=chosen, delivered=delivered, fallback=fallback, text=body,
          status="delivered" if delivered else "not_delivered", **meta)
    return {"channel": channel, "chosen_channel": chosen, "delivered": delivered, "fallback": fallback, "text": body}


def route_own_update(text: str, settings: dict[str, Any], *, kind: str = "initiative_update",
                     requires_response: bool = False) -> bool:
    """Own outcomes: send a proactive progress update through the chosen channel.

    Returns True when handled here. Telegram, questions that need an answer and
    other postures fall through (False) to the existing Telegram sender.
    """
    if requires_response or kind in _PROGRESS_SKIP_KINDS:
        return False
    if current_level(settings) != "own":
        return False
    if own_channel_choice(settings) == "telegram":
        audit("own_progress", channel="telegram", chosen_channel="telegram", delivered=None,
              text=humanize(text, max_len=1500), progress_kind=kind, status="handed_to_telegram_sender")
        return False
    notify_owner(text, settings, kind="own_progress", progress_kind=kind)
    return True


def announce_own_start(what: str, plan: str, settings: dict[str, Any] | None = None, **meta: Any) -> dict[str, Any]:
    """'I'm going to start this work; here's what I'm doing: …' via the chosen channel."""
    what = _preview(what or "a piece of work", 160).rstrip(".")
    plan = _preview(plan or "I'll take it from here and keep you posted.", 400)
    return notify_owner(f"I'm going to start this work: {what}. Here's what I'm doing: {plan}",
                        settings, kind="own_announce", **meta)


# ---------------------------------------------------------------------------
# Recommendations: a proactive observer across projects, inbox, calendar and memory
# ---------------------------------------------------------------------------

STUCK_DAYS = 5
RECOMMENDATION_REPEAT_DAYS = 3
_STOP = set(
    "the a an and or to of for in on with is are be it this that you your i my me we our at as by from "
    "have has was were will would about into what when where which their them they there these those".split()
)


def profile_hints(limit: int = 3) -> list[str]:
    """Short notes about the user from MemPalace. Empty when it's unavailable."""
    try:
        from distr.core.mempalace.adapter import get_adapter

        res = get_adapter().search(
            "who the user is: role, goals, priorities, preferences and working style", n_results=5, timeout=4
        ) or {}
    except Exception:
        logger.debug("outcome_flow: mempalace unavailable", exc_info=True)
        return []
    hits = res.get("results") or []
    if not hits and isinstance(res.get("documents"), list):
        hits = [{"content": d} for d in res["documents"]]
    out: list[str] = []
    for hit in hits:
        raw = (hit.get("content") or hit.get("text") or hit.get("document")) if isinstance(hit, dict) else hit
        clean = _preview(raw, 160)
        if clean and clean not in out:
            out.append(clean)
    return out[:limit]


def _keywords(texts: list[str]) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", " ".join(texts).lower()) if w not in _STOP}


def _rec(area: str, subject: str, text: str, *, project: str = "", pinned: bool = False,
         weight: int = 0, state: str = "") -> dict[str, Any]:
    fp = hashlib.sha256(f"{area}|{subject}|{state}".lower().encode()).hexdigest()[:16]
    return {"fp": fp, "area": area, "project": project, "pinned": pinned, "text": text, "weight": weight}


def _project_observations(now: datetime, max_projects: int = 8) -> tuple[list[str], list[dict[str, Any]], dict[str, int]]:
    """(done lines, recommendations, open counts by project name) from local boards."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    ignored = ignored_project_ids()
    pinned = pinned_board_ids()
    since = now - timedelta(days=1)
    stuck_before = now - timedelta(days=STUCK_DAYS)
    done_words = ("done", "complete", "closed", "shipped", "released")
    done_lines: list[str] = []
    recs: list[dict[str, Any]] = []
    open_counts: dict[str, int] = {}
    with get_session() as db:
        projects = [p for p in db.query(Project).filter(Project.deleted_at.is_(None)).all() if int(p.id) not in ignored]

        def board_for(project):
            board_id = getattr(project, "kanban_board_id", None)
            board = db.get(KanbanBoard, int(board_id)) if board_id else None
            if board is None:
                board = db.query(KanbanBoard).filter(KanbanBoard.default_project_id == project.id).first()
            return board

        pairs = [(p, board_for(p)) for p in projects]
        pairs = [(p, b) for p, b in pairs if b is not None and not getattr(b, "archived", False)]
        pairs.sort(key=lambda pb: (0 if int(pb[1].id) in pinned else 1, getattr(pb[0], "position", 0) or 0))
        for project, board in pairs[:max_projects]:
            is_pinned = int(board.id) in pinned
            name = str(project.name or "a project")
            lanes = {l.id: (l.name or "") for l in db.query(KanbanLane).filter(KanbanLane.board_id == board.id).all()}
            tickets = db.query(KanbanTicket).filter(KanbanTicket.lane_id.in_(list(lanes) or [-1])).all()

            def lane(t):
                return lanes.get(t.lane_id, "").lower()

            done = [t for t in tickets if any(w in lane(t) for w in done_words)
                    and (getattr(t, "modified_date", None) or datetime.min) >= since]
            open_ = [t for t in tickets if not any(w in lane(t) for w in done_words) and "backlog" not in lane(t)]
            open_counts[name] = len(open_)
            if done:
                done_lines.append(f"{name}: " + "; ".join(_preview(t.title, 60) for t in done[:4]))
            stuck = sorted(
                [t for t in open_ if (getattr(t, "modified_date", None) or now) <= stuck_before],
                key=lambda t: getattr(t, "modified_date", None) or now,
            )
            for t in stuck[:2]:
                days = max(STUCK_DAYS, (now - t.modified_date).days)
                lane_name = lanes.get(t.lane_id, "its lane").strip() or "its lane"
                recs.append(_rec(
                    "stuck", f"{project.id}:{t.id}",
                    f"\u201c{_preview(t.title, 70)}\u201d in {name} hasn't moved from {lane_name} for {days} days. "
                    "Options: push it forward, hand it to an agent, or drop it so it stops weighing on the board.",
                    project=name, pinned=is_pinned, weight=3, state=f"{t.lane_id}:{days // 7}",
                ))
            if len(open_) >= 8:
                recs.append(_rec(
                    "load", str(project.id),
                    f"{name} has {len(open_)} open items. Picking the top three to finish this week would make the "
                    "rest easier to judge.",
                    project=name, pinned=is_pinned, weight=1, state=str(len(open_) // 5),
                ))
    return done_lines, recs, open_counts


def _inbox_observations(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Waiting work from the existing work scan (WhatsApp, email, Telegram, boards).

    The scan honours the per-source toggles and drops ignored projects; pinned first.
    """
    from distr.core.initiative.work_scanner import build_work_scan

    scan = build_work_scan(settings) or {}
    out = []
    for proposal in (scan.get("proposals") or [])[:6]:
        if not isinstance(proposal, dict):
            continue
        payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
        description = humanize(proposal.get("description") or "", max_len=200)
        if not description:
            continue
        src = source_label(normalize_source(payload.get("source") or proposal.get("action_type")))
        out.append(_rec(
            "waiting", f"{proposal.get('action_type')}|{description[:80]}",
            f"Waiting on you ({src}): {description}",
            pinned=bool(proposal.get("pinned")), weight=2,
        ))
    return out


def _calendar_observations(now: datetime, open_counts: dict[str, int]) -> list[dict[str, Any]]:
    """Meetings in the next day, linked to projects with open work. Needs Google connected."""
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        return []
    events = connector.get_calendar_events(time_min=now.astimezone(), time_max=(now + timedelta(days=1)).astimezone(),
                                           max_results=15) or []
    out = []
    for event in events:
        summary = str(event.get("summary") or "").strip()
        start = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date") or ""
        when = start[11:16] if len(start) >= 16 else "tomorrow"
        for project, count in open_counts.items():
            if count and project and project.lower() in summary.lower():
                out.append(_rec(
                    "calendar", f"{summary}|{start[:10]}",
                    f"Before \u201c{_preview(summary, 60)}\u201d at {when}, {project} has {count} open "
                    f"item{'s' if count != 1 else ''}. A quick look first means you can decide in the room.",
                    project=project, weight=3,
                ))
                break
    if len(events) >= 5:
        out.append(_rec(
            "calendar", f"busy|{now.date().isoformat()}",
            f"The next day is busy: {len(events)} meetings. Worth protecting a block of focus time for what matters most.",
            weight=1,
        ))
    return out


_REPLY_HOLD_STATES = {"awaiting_decision", "held_for_approval", "blocked", "notification_failed"}


def _tension_observations(now: datetime) -> list[dict[str, Any]]:
    """Ignored project or client, but a client is still waiting: always surfaced, never silent."""
    from distr.core.initiative import attention

    out = []
    for item in attention.waiting_tensions(now=now):
        out.append(_rec("tension", f"{item['ticket_id']}|{item['reason']}", attention.tension_text(item),
                        project=item.get("project") or "", weight=6, state=f"{item['days'] // 3}"))
    return out


def _pending_reply_observations(now: datetime) -> list[dict[str, Any]]:
    """Client replies that are written and waiting on the owner (Telegram card, approval queue, blocked)."""
    out: list[dict[str, Any]] = []
    seen_tickets: set[str] = set()
    for key, flow in (_load_state().get("flows") or {}).items():
        if flow.get("kind") != "reply" or flow.get("status") not in _REPLY_HOLD_STATES:
            continue
        tid = str(flow.get("ticket_id"))
        if tid in seen_tickets:
            continue
        seen_tickets.add(tid)
        age_h = max(0, int((now.timestamp() - float(flow.get("created") or now.timestamp())) // 3600))
        status = flow.get("status")
        lead = ("is blocked and hasn't gone out" if status in {"blocked", "notification_failed"}
                else "is ready and waiting for your OK")
        out.append(_rec(
            "reply", f"flow|{key}",
            f"The update for {flow.get('client') or 'a client'} on {source_label(flow.get('source'))} {lead}"
            f"{f' ({age_h} h now)' if age_h >= 2 else ''}: \u201c{_preview(client_body(flow.get('body')), 120)}\u201d",
            weight=5 if status in {"blocked", "notification_failed"} else 4, state=f"{status}:{age_h // 12}",
        ))
    try:
        from distr.core.db import DevApprovalRequest, get_session

        with get_session() as db:
            rows = (db.query(DevApprovalRequest)
                    .filter(DevApprovalRequest.tool == "reply_delivery", DevApprovalRequest.status == "pending").all())
            for row in rows:
                try:
                    details = json.loads(row.details_json or "{}")
                except (TypeError, ValueError):
                    details = {}
                tid = str(details.get("ticket_id") or "")
                if tid and tid in seen_tickets:
                    continue
                seen_tickets.add(tid)
                out.append(_rec(
                    "reply", f"approval|{row.request_id}",
                    f"A reply on {source_label(details.get('channel'))} is waiting for your OK in the app: "
                    f"\u201c{_preview(client_body(details.get('body')), 120)}\u201d",
                    weight=4,
                ))
    except Exception:
        logger.debug("outcome_flow: pending reply approvals unavailable", exc_info=True)
    return out


def _link_observations(now: datetime, project_names: dict[int, str]) -> list[dict[str, Any]]:
    """Project links shape suggestions: linked Telegram groups and email that needs a project decision."""
    out: list[dict[str, Any]] = []
    from distr.core.incoming import project_links as pl
    from distr.core.initiative import attention

    links = pl.project_links(refresh=False) or []
    linked_groups: dict[str, set[int]] = {}
    empty_mail = 0
    for link in links:
        if not isinstance(link, dict):
            continue
        ids = {int(r["decisions_project_id"]) for r in link.get("projects") or []
               if isinstance(r, dict) and type(r.get("decisions_project_id")) is int}
        source = str(link.get("source") or "")
        if source == "telegram" and ids:
            linked_groups[str(link.get("source_thread_id"))] = ids
        if source in {"gmail", "mailshot"} and not ids:
            empty_mail += 1
    if linked_groups:
        try:
            from datetime import timezone

            from distr.core.db import TelegramGroupMessage, get_session

            since = now.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
            with get_session() as db:
                rows = (db.query(TelegramGroupMessage)
                        .filter(TelegramGroupMessage.created_date >= since).all())
            counts: dict[str, int] = {}
            titles: dict[str, str] = {}
            for row in rows:
                chat = str(row.chat_id or "")
                if chat in linked_groups:
                    counts[chat] = counts.get(chat, 0) + 1
                    titles[chat] = str(row.chat_title or row.chat_username or "the group")
            ignored = attention.ignored_project_ids()
            for chat, n in counts.items():
                for pid in sorted(linked_groups[chat]):
                    name = project_names.get(pid, "")
                    if not name or pid in ignored:
                        continue
                    out.append(_rec(
                        "linked", f"tg|{chat}|{pid}",
                        f"{n} new message{'s' if n != 1 else ''} in the {titles[chat]} Telegram group, which is linked to "
                        f"{name}. Worth a look before you decide what's next there.",
                        project=name, weight=2, state=str(n // 5),
                    ))
        except Exception:
            logger.debug("outcome_flow: linked Telegram group activity unavailable", exc_info=True)
    for key, entry in attention.address_links().items():
        ids = [int(p) for p in entry.get("project_ids") or []]
        if len(ids) > 1:
            names = " and ".join(project_names.get(p, f"project {p}") for p in ids)
            out.append(_rec(
                "linked", f"addr|{key}",
                f"Mail from {entry.get('address')} is linked to both {names}, so I won't guess which project new "
                "mail belongs to. Pick one, or tell me how to split it.", weight=3,
            ))
    for flag in attention.link_flags()[-6:]:
        ids = [int(p) for p in flag.get("project_ids") or []]
        names = " or ".join(project_names.get(p, f"project {p}") for p in ids) or "a project"
        subject = _preview(flag.get("subject") or "an email", 60)
        text = {
            "conflict": f"\u201c{subject}\u201d is linked to more than one project ({names}), so I haven't guessed where it "
                        "belongs. Tell me which one.",
            "topic_drift": f"The email thread now about \u201c{subject}\u201d has drifted from what it was linked for "
                           f"({names}). Still the right project?",
            "needs_link": f"\u201c{subject}\u201d arrived on a new thread that looks like your {names} conversation, but it "
                          "isn't linked, so it was left out of triage. Link it?",
        }.get(str(flag.get("kind")))
        if text:
            out.append(_rec("linked", f"flag|{flag.get('kind')}|{flag.get('source')}|{flag.get('thread')}", text,
                            weight=3 if flag.get("kind") != "topic_drift" else 2))
    if empty_mail:
        out.append(_rec(
            "linked", "mail-empty",
            f"{empty_mail} email conversation{'s are' if empty_mail != 1 else ' is'} linked without a project, so new mail "
            "there is left out of triage. Link the sender's address to a project and future mail will route on its own.",
            weight=1, state=str(empty_mail),
        ))
    return out


def _automation_observations(now: datetime, project_names: dict[int, str]) -> list[dict[str, Any]]:
    """Automations that need a decision: overdue, or still scheduled on a set-aside project."""
    from distr.core.automation.store import list_automations
    from distr.core.initiative import attention

    ignored = attention.ignored_project_ids()
    out: list[dict[str, Any]] = []
    for auto in list_automations() or []:
        if str(auto.get("status") or "active").lower() != "active":
            continue
        name = str(auto.get("name") or "An automation")
        pid = auto.get("linked_project_id")
        pname = project_names.get(int(pid), "") if pid is not None else ""
        if pid is not None and int(pid) in ignored:
            out.append(_rec(
                "automation", f"auto-ignored|{auto.get('id')}",
                f"\u201c{_preview(name, 60)}\u201d is still set to run for {pname or 'a project'} you set aside. "
                "I'll hold its automatic runs; run it yourself, pause it, or tell me to keep it going.",
                project=pname, weight=3,
            ))
            continue
        nxt = str(auto.get("next_run_at") or "")
        try:
            due = datetime.fromisoformat(nxt.replace("Z", "+00:00")) if nxt else None
        except ValueError:
            due = None
        if due is not None and now.astimezone() - due.astimezone() > timedelta(days=1):
            out.append(_rec(
                "automation", f"auto-overdue|{auto.get('id')}",
                f"\u201c{_preview(name, 60)}\u201d was due {due.astimezone().strftime('%a %d %b %H:%M')} and hasn't run. "
                "Run it now, fix its schedule, or pause it.", project=pname, weight=2, state=nxt[:10],
            ))
    return out


def attention_hints(limit: int = 4) -> list[str]:
    """What the memory palace says about what matters and what to set aside (separate from who the user is)."""
    try:
        from distr.core.mempalace.adapter import get_adapter

        res = get_adapter().search(
            "which projects and clients matter most right now, and which to ignore, pause or not worry about",
            n_results=6, timeout=4,
        ) or {}
    except Exception:
        return []
    hits = res.get("results") or []
    if not hits and isinstance(res.get("documents"), list):
        hits = [{"content": d} for d in res["documents"]]
    out: list[str] = []
    for hit in hits:
        raw = (hit.get("content") or hit.get("text") or hit.get("document")) if isinstance(hit, dict) else hit
        clean = _preview(raw, 220)
        if clean and clean not in out:
            out.append(clean)
    return out[:limit]


def _project_name_map() -> dict[int, str]:
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as db:
            return {int(p.id): str(p.name or "") for p in db.query(Project).filter(Project.deleted_at.is_(None)).all()}
    except Exception:
        return {}


def build_recommendations(settings: dict[str, Any] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Gather done work, outstanding actions and suggestions. Every source is optional.

    Sources: boards, the work scan, calendar, client replies waiting on the owner, project links
    (Telegram groups, email address links), automations, and ignored-but-waiting clients.
    The memory palace and the durable attention store decide what is boosted and what is
    quieted; a waiting client is never quieted.
    """
    settings = load_settings() if settings is None else settings
    now = now or datetime.now()
    done_lines: list[str] = []
    recs: list[dict[str, Any]] = []
    open_counts: dict[str, int] = {}
    sources: dict[str, str] = {}
    try:
        done_lines, project_recs, open_counts = _project_observations(now)
        recs += project_recs
        sources["projects"] = "ok"
    except Exception:
        logger.warning("outcome_flow: project observations failed", exc_info=True)
        sources["projects"] = "unavailable"
    try:
        recs += _inbox_observations(settings)
        sources["inbox"] = "ok"
    except Exception:
        logger.warning("outcome_flow: work scan for recommendations failed", exc_info=True)
        sources["inbox"] = "unavailable"
    try:
        recs += _calendar_observations(now, open_counts)
        sources["calendar"] = "ok"
    except Exception:
        logger.debug("outcome_flow: calendar observations unavailable", exc_info=True)
        sources["calendar"] = "unavailable"
    names = _project_name_map()
    for label, fn in (("tensions", lambda: _tension_observations(now)),
                      ("pending_replies", lambda: _pending_reply_observations(now)),
                      ("links", lambda: _link_observations(now, names)),
                      ("automations", lambda: _automation_observations(now, names))):
        try:
            recs += fn()
            sources[label] = "ok"
        except Exception:
            logger.debug("outcome_flow: %s observations unavailable", label, exc_info=True)
            sources[label] = "unavailable"
    profile = profile_hints()
    sources["memory"] = "ok" if profile else "empty"
    hints = attention_hints() if profile or sources["memory"] == "ok" else []
    try:
        from distr.core.initiative import attention

        clients = [c.get("label") for c in attention.ignored_clients() if c.get("label")]
        steer = attention.attention_profile([*profile, *hints], names.values(), clients)
    except Exception:
        steer = {"boost": {}, "quiet": set(), "reasons": {}}
    focus = _keywords(profile)
    kept: list[dict[str, Any]] = []
    quieted: list[str] = []
    for rec in recs:
        project = rec.get("project") or ""
        if project in steer["quiet"] and rec["area"] in {"stuck", "load", "calendar", "linked", "automation"}:
            quieted.append(project)  # routine items for a project memory says to set aside
            continue
        rec["relevance"] = len(focus & _keywords([rec["text"], project])) + int(steer["boost"].get(project, 0))
        if project in steer["boost"]:
            rec["focus_reason"] = steer["reasons"].get(project, "")
        kept.append(rec)
    recs = kept
    recs.sort(key=lambda r: (0 if r["area"] == "tension" else 1, 0 if r.get("pinned") else 1,
                             -r.get("relevance", 0), -r.get("weight", 0)))
    return {"done": done_lines, "recommendations": recs, "profile": profile, "sources": sources,
            "focus": sorted(steer["boost"], key=lambda n: -steer["boost"][n]), "quieted": sorted(set(quieted)),
            "focus_reasons": steer["reasons"]}


def recommendations_digest(settings: dict[str, Any] | None = None, *, now: datetime | None = None,
                           dedupe: bool = False, record: bool = False, max_items: int = 8) -> str | None:
    """Humanized round-up. With ``dedupe`` items sent in the last few days are left out;
    returns None when there's nothing new to say."""
    now = now or datetime.now()
    data = build_recommendations(settings, now=now)
    recs = data["recommendations"]
    seen: dict[str, str] = {}
    if dedupe or record:
        seen = dict(_load_state().get("recommendations_seen") or {})
    if dedupe:
        cutoff = (now - timedelta(days=RECOMMENDATION_REPEAT_DAYS)).date().isoformat()
        recs = [r for r in recs if seen.get(r["fp"], "") < cutoff]
    recs = recs[:max_items]
    if dedupe and not recs:
        return None
    if record and recs:
        with _LOCK:
            state = _load_state()
            seen = dict(state.get("recommendations_seen") or {})
            for r in recs:
                seen[r["fp"]] = now.date().isoformat()
            keep_after = (now - timedelta(days=30)).date().isoformat()
            state["recommendations_seen"] = {k: v for k, v in seen.items() if v >= keep_after}
            _save_state(state)
    if not recs and not data["done"]:
        return "Quiet on all fronts: nothing finished recently and nothing that needs a decision from you."
    intro = "Here's what I'd put in front of you so you can decide what happens next"
    intro += ", with your priorities in mind:" if data["profile"] else ":"
    parts = [intro]
    tensions = [r for r in recs if r["area"] == "tension"]
    replies = [r for r in recs if r["area"] == "reply"]
    decide = [r for r in recs if r["area"] in {"stuck", "load", "calendar", "linked", "automation"}]
    waiting = [r for r in recs if r["area"] == "waiting"]
    if tensions:
        parts.append("Still waiting, even though you set them aside\n" + "\n".join(f"- {r['text']}" for r in tensions))
    if replies:
        parts.append("Replies waiting on you\n" + "\n".join(f"- {r['text']}" for r in replies))
    if data.get("focus") or data.get("quieted"):
        bits = []
        if data.get("focus"):
            bits.append("Keeping " + ", ".join(data["focus"][:3]) + " up front")
        if data.get("quieted"):
            bits.append("leaving routine items for " + ", ".join(data["quieted"][:3]) + " out, as you asked")
        parts.append("; ".join(bits) + ".")
    if decide:
        parts.append("Worth a decision\n" + "\n".join(
            f"{i}. {r['text']}{' (pinned)' if r.get('pinned') else ''}" for i, r in enumerate(decide, 1)))
    if waiting:
        parts.append("Waiting on you\n" + "\n".join(f"- {r['text']}" for r in waiting))
    if data["done"]:
        parts.append("Done lately\n" + "\n".join(f"- {line}" for line in data["done"][:6]))
    parts.append("Tell me which of these you'd like me to take on, or say \u201cproposals\u201d to see what's come in.")
    return humanize("\n\n".join(parts), max_len=3500)


def project_report(settings: dict[str, Any] | None = None, *, max_projects: int = 8) -> str:
    """On-demand round-up (no de-duplication): what's done, what's open, what to decide."""
    return recommendations_digest(settings) or "Nothing new to flag right now."


# ---------------------------------------------------------------------------
# "Be my assistant": gentle in-day suggestions (setting key: initiative_recommendations)
# ---------------------------------------------------------------------------
# Same engine as above (build_recommendations + MemPalace re-ranking). What changed
# is delivery: there is no fixed clock. The Initiative tick (every 60 s) calls
# maybe_send_recommendations(); it only re-reads the engine after observed
# activity (a ticket changed on a board) or a slow refresh (new mail, chat,
# Telegram, Jira or calendar can arrive without board activity), holds back while
# a calendar event is running, paces itself, and offers at most a couple of items
# at a time. Each nudge goes to exactly one place, set by the same-channel rule in
# ``initiative.channel_select`` (12 Nov 2026): a reply goes back on the channel the
# incoming message came from; a proactive suggestion has no incoming message, so it
# stays in the in-app list (Settings > Initiative, "From your assistant"). Nothing
# scores activity or picks a "best" channel, and nothing asks where to send it.
# WhatsApp is never used from this path.

ASSISTANT_EVAL_MIN_S = 5 * 60       # earliest re-read after observed activity
ASSISTANT_REFRESH_S = 90 * 60       # re-read anyway so incoming items and calendar are noticed
ASSISTANT_NUDGE_GAP_S = 45 * 60     # never two nudges closer than this
ASSISTANT_MAX_PER_DAY = 6
ASSISTANT_ITEMS_PER_NUDGE = 2
ASSISTANT_NUDGE_TTL_S = 12 * 3600   # unanswered suggestions quietly fade
ASSISTANT_ACTIVE_WINDOW = timedelta(hours=2)
ASSISTANT_KEEP = 30


def _activity_marker(now: datetime) -> tuple[str, set[str]]:
    """What the user is doing in projects: latest board change + projects touched recently."""
    from sqlalchemy import func

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    from datetime import timezone

    # Board timestamps are stored as naive UTC.
    since = now.astimezone(timezone.utc).replace(tzinfo=None) - ASSISTANT_ACTIVE_WINDOW
    with get_session() as db:
        latest = db.query(func.max(KanbanTicket.modified_date)).scalar()
        board_ids = {
            int(b) for (b,) in db.query(KanbanLane.board_id)
            .join(KanbanTicket, KanbanTicket.lane_id == KanbanLane.id)
            .filter(KanbanTicket.modified_date >= since).distinct().all() if b is not None
        }
        active: set[str] = set()
        if board_ids:
            ignored = ignored_project_ids()
            for project in db.query(Project).filter(Project.deleted_at.is_(None)).all():
                if int(project.id) in ignored:
                    continue
                board_id = getattr(project, "kanban_board_id", None)
                if board_id and int(board_id) in board_ids:
                    active.add(str(project.name or ""))
            for board in db.query(KanbanBoard).filter(KanbanBoard.id.in_(list(board_ids))).all():
                pid = getattr(board, "default_project_id", None)
                if pid and int(pid) not in ignored:
                    project = db.get(Project, int(pid))
                    if project is not None and getattr(project, "deleted_at", None) is None:
                        active.add(str(project.name or ""))
    active.discard("")
    return (str(latest or ""), active)


def _in_meeting_now(now: datetime) -> bool:
    """True while a timed calendar event is running (don't break flow). False if unknown."""
    try:
        from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

        connector = GoogleWorkspaceConnector()
        if not connector.is_connected():
            return False
        events = connector.get_calendar_events(time_min=now.astimezone(),
                                               time_max=(now + timedelta(minutes=1)).astimezone(),
                                               max_results=5) or []
    except Exception:
        logger.debug("outcome_flow: meeting check unavailable", exc_info=True)
        return False
    for event in events:
        start = (event.get("start") or {}).get("dateTime")
        end = (event.get("end") or {}).get("dateTime")
        if not start or not end:
            continue  # all-day items aren't meetings
        try:
            if datetime.fromisoformat(start) <= now.astimezone() < datetime.fromisoformat(end):
                return True
        except ValueError:
            continue
    return False


def assistant_nudges(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Open in-app suggestions, newest first (not dismissed, not faded)."""
    ts = (now or datetime.now()).timestamp()
    items = (_load_state().get("assistant") or {}).get("nudges") or []
    # Only what was placed in the app: suggestions sent to Telegram or spoken, or waiting
    # for an answer about where they should go, are not shown here a second time.
    # Suggestions the same channel couldn't take are shown too, marked undelivered:
    # they are never re-sent on a different channel.
    live = [n for n in items if not n.get("dismissed") and not n.get("held_for")
            and ((n.get("channel") or "in_app") == "in_app" or n.get("undelivered"))
            and ts - float(n.get("created_ts") or 0) < ASSISTANT_NUDGE_TTL_S]
    return sorted(live, key=lambda n: -float(n.get("created_ts") or 0))


def dismiss_assistant_nudge(nudge_id: str) -> bool:
    with _LOCK:
        state = _load_state()
        a = dict(state.get("assistant") or {})
        nudges = list(a.get("nudges") or [])
        hit = False
        for n in nudges:
            if n.get("id") == nudge_id and not n.get("dismissed"):
                n["dismissed"] = True
                hit = True
        if hit:
            a["nudges"] = nudges
            state["assistant"] = a
            _save_state(state)
    if hit:
        try:
            from distr.core.initiative.channel_select import record_dismissed

            record_dismissed("in_app")
        except Exception:
            logger.debug("outcome_flow: dismissal not recorded for channel choice", exc_info=True)
    return hit


def maybe_send_recommendations(settings: dict[str, Any], *, now: datetime | None = None) -> bool:
    """"Be my assistant" (Standard / Own, setting on). Quiet is always off.

    Offers a gentle in-app suggestion when observed activity warrants one; no fixed
    daily run. Returns True when a nudge was offered. Name kept for the service tick.
    """
    level = current_level(settings)
    if level not in {"operate", "own"}:
        return False
    if not _flag(settings, "initiative_recommendations", False):
        return False
    now = now or datetime.now()
    ts = now.timestamp()
    today = now.date().isoformat()
    a = dict(_load_state().get("assistant") or {})
    if ts - float(a.get("last_nudge") or 0) < ASSISTANT_NUDGE_GAP_S:
        return False
    if a.get("day") == today and int(a.get("day_count") or 0) >= ASSISTANT_MAX_PER_DAY:
        return False
    try:
        marker, active = _activity_marker(now)
    except Exception:
        logger.debug("outcome_flow: activity marker unavailable", exc_info=True)
        marker, active = "", set()
    since_eval = ts - float(a.get("last_eval") or 0)
    observed = marker != a.get("marker")
    if not ((observed and since_eval >= ASSISTANT_EVAL_MIN_S) or since_eval >= ASSISTANT_REFRESH_S):
        return False
    if _in_meeting_now(now):
        with _LOCK:
            state = _load_state()
            cur = dict(state.get("assistant") or {})
            cur["last_eval"] = ts
            state["assistant"] = cur
            _save_state(state)
        return False
    from distr.core.initiative import channel_select

    try:
        if channel_select.waiting_for_answer(now):
            return False  # asked where suggestions like this should go; wait for the answer
    except Exception:
        logger.debug("outcome_flow: channel question state unavailable", exc_info=True)
    data = build_recommendations(settings, now=now)
    with _LOCK:
        state = _load_state()
        cur = dict(state.get("assistant") or {})
        seen = dict(state.get("recommendations_seen") or {})
        cutoff = (now - timedelta(days=RECOMMENDATION_REPEAT_DAYS)).date().isoformat()
        fresh = [r for r in data["recommendations"] if seen.get(r["fp"], "") < cutoff]
        # Timed around the user's activity: projects they touched recently come first.
        # A client waiting behind an ignore always comes first, then held replies.
        fresh.sort(key=lambda r: (0 if r["area"] == "tension" else 1 if r["area"] == "reply" else 2,
                                  0 if r.get("project") and r["project"] in active else 1))
        picks = fresh[:ASSISTANT_ITEMS_PER_NUDGE]
        cur["last_eval"] = ts
        cur["marker"] = marker
        new_nudges: list[dict[str, Any]] = []
        if picks:
            for r in picks:
                seen[r["fp"]] = today
                why = ("Someone is waiting even though you set this aside" if r["area"] == "tension"
                       else f"You've been working in {r['project']}" if r.get("project") in active
                       else f"You said {r['project']} matters" if r.get("focus_reason")
                       else "New since I last looked")
                new_nudges.append({
                    "id": f"{r['fp']}-{int(ts)}", "fp": r["fp"], "area": r["area"],
                    "project": r.get("project") or "", "pinned": bool(r.get("pinned")),
                    "text": humanize(r["text"], max_len=300), "why": why,
                    "created": now.astimezone().isoformat(timespec="minutes"), "created_ts": ts,
                })
            cur["last_nudge"] = ts
            cur["day_count"] = (int(cur.get("day_count") or 0) if cur.get("day") == today else 0) + 1
            cur["day"] = today
            keep_after = (now - timedelta(days=30)).date().isoformat()
            state["recommendations_seen"] = {k: v for k, v in seen.items() if v >= keep_after}
        state["assistant"] = cur
        _save_state(state)
    if not picks:
        return False
    # Same-channel rule: a proactive suggestion has no incoming message, so it stays in the app.
    try:
        choice = channel_select.decide(now=now, incoming=None)
    except Exception:
        logger.warning("outcome_flow: channel choice unavailable; keeping the suggestion in the app", exc_info=True)
        choice = channel_select.Choice("in_app", 0.0, False, None, ["channel choice unavailable"])
    channel_select.place(new_nudges, choice, settings, now=now, level=level,
                         meta={"active_projects": sorted(active), "sources": data.get("sources")})
    return True


# ---------------------------------------------------------------------------
# Telegram entry point (private chat)
# ---------------------------------------------------------------------------

_CALLBACK_RE = re.compile(r"^(oc|ip|pw):([A-Za-z0-9]+):([a-z_]+)$")
_ASK_CALLBACK_RE = re.compile(r"^ac:([0-9a-f]{12}):(in_app|telegram|spoken)$")


def handle_telegram_text(text: str, *, chat_id: Any = None,
                         telegram_message_id: Any = None) -> dict[str, Any] | None:
    """Route Telegram text/callbacks for outcome flows. None = not ours."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if chat_id is not None:
        # The paired private chat only (callers pass chat_id for private messages).
        # Bookkeeping only; it no longer picks a channel. Old open channel questions can still be closed.
        try:
            from distr.core.initiative import channel_select

            channel_select.record_telegram_user_activity()
            ask = _ASK_CALLBACK_RE.match(raw)
            if ask:
                return {"text": channel_select.answer(ask.group(1), ask.group(2))["text"]}
            if channel_select.waiting_for_answer():
                choice = channel_select.parse_answer(raw, asked_on="telegram")
                if choice:
                    return {"text": channel_select.answer(None, choice)["text"]}
        except Exception:
            logger.warning("outcome_flow: channel answer handling failed", exc_info=True)
    callback = _CALLBACK_RE.match(raw)
    if callback:
        kind, key, action = callback.groups()
        if kind == "ip":
            return decide_proposal(key, action, chat_id=chat_id)
        if kind == "pw":  # a Pi write proposal from chat (pi_write_proposals)
            from distr.core.incoming.telegram_updates import _recipient_id

            if chat_id is not None and _recipient_id(chat_id) is None:
                return {"text": "Review the proposal in your paired private Telegram chat. Nothing changed."}
            from distr.core.initiative.pi_write_proposals import decide

            return decide(key, action)
        return _handle_flow_action(key, action, telegram_message_id=telegram_message_id, telegram_chat_id=chat_id)
    claimed = handle_pending_rework(raw, chat_id=chat_id, telegram_message_id=telegram_message_id, source="text")
    if claimed:
        return claimed
    # Only button presses: plain "yes"/"no" stay with their existing handlers.
    if raw.lower().startswith("wi:"):
        settings = load_settings()
        if current_level(settings) == "operate":
            try:
                from distr.core.kanban.whatsapp_intake_approval import handle_telegram_reply

                result = handle_telegram_reply(raw, chat_id=chat_id)
            except Exception:
                logger.warning("outcome_flow: intake reply failed", exc_info=True)
                return None
            if result:
                return humanize_intake_result(result)
            return None
    if is_whatsapp_check_request(raw):
        return whatsapp_overview()
    if is_proposals_request(raw):
        out = proposals_overview()
        if not raw.startswith("/"):
            out["ack"] = "Okay, checking Incoming now."
        return out
    if _RECOMMEND_RE.search(raw):
        return {"text": project_report()}
    if _NOT_DONE_RE.search(raw):
        open_flow = latest_open_flow()
        if open_flow and not _names_other_work(raw, open_flow[1]):
            key, flow = open_flow
            try:
                _thread_steer(flow.get("chat_id"), f"The user says this isn't done yet: {raw}\nPlease keep going until it is.")
            except Exception:
                logger.warning("outcome_flow: not-done steer failed", exc_info=True)
                return {"text": "I couldn't pass that back to the work just now. Mind trying again in a moment?"}
            save_flow(key, status="continuing", last_feedback=raw)
            audit("not_done_feedback_routed", ticket_id=flow.get("ticket_id"), feedback=raw)
            return {"text": "Thanks for flagging that. I've passed it back and it's being worked on again. "
                            "I'll let you know when it's sorted."}
    return None


def _chat_project_id(chat_id: Any) -> int | None:
    from distr.core.db import Chat, get_session

    with get_session() as db:
        row = db.get(Chat, int(chat_id)) if str(chat_id or "").isdigit() else None
        return int(row.project_id) if row is not None and row.project_id is not None else None


def _names_other_work(text: str, flow: dict[str, Any]) -> bool:
    """'X is still broken' about another ticket or project must not steer the latest flow."""
    tickets = {int(n) for n in re.findall(r"\b(?:ticket|task)\s*#?\s*(\d+)\b", text, re.I)}
    if tickets and tickets != {int(flow.get("ticket_id") or 0)}:
        return True
    from distr.core.work_intake.service import projects_named_in

    named = {pid for pid, _name in projects_named_in(text)}
    if not named:
        return False
    try:
        return _chat_project_id(flow.get("chat_id")) not in named
    except Exception:
        logger.warning("outcome_flow: flow project lookup failed", exc_info=True)
        return True


def _begin_rework(key: str, flow: dict[str, Any], status: str) -> dict[str, Any]:
    if status in _TERMINAL_FLOW:
        if status == "sent":
            return {"text": f"That message already went out to {flow.get('client') or 'the client'}."}
        if status in {"sending", "unconfirmed"}:
            return {"text": "The send was not confirmed. Check the original conversation before sending another reply."}
        return {"text": CANCELLED_REPLACED_TEXT}
    if status == "awaiting_rework":
        return {"text": REWORK_PROMPT, "reply_markup": FORCE_REPLY}
    if not save_flow(key, expected_status=status, status="awaiting_rework"):
        return _handle_flow_action(key, "rework")
    _edit_flow_cards(
        key, get_flow(key) or flow,
        text=(f"Reworking the message to {flow.get('client') or 'the client'}. Say what to change.\n\n"
              f"\u201c{flow.get('body') or ''}\u201d"),
    )
    return {"text": REWORK_PROMPT, "reply_markup": FORCE_REPLY, "interaction_token": f"{OC_CARD_PREFIX}{key}"}


def apply_rework_instruction(key: str, flow: dict[str, Any], instruction: str, *,
                             dictated: str = "", newer_inbound: list[str] | None = None) -> dict[str, Any]:
    """Redraft the held client reply and send a fresh confirm card. Never sends to the client."""
    if flow.get("status") != "awaiting_rework":
        return {"text": "That draft is no longer waiting for a change. Nothing was sent."}
    from distr.core.incoming.client_reply_rework import compose_rework_text

    body = str(flow.get("body") or "")
    try:
        new_text = compose_rework_text(
            body, f"say: {dictated}" if dictated else instruction,
            client=str(flow.get("client") or ""), newer_inbound=newer_inbound,
        )
    except Exception as exc:
        logger.warning("Client reply rework failed", exc_info=True)
        return {"text": f"I couldn't rework that just now: {str(exc)[:200]}. Tell me what to change."}
    if flow.get("kind") == "thread_reply":
        try:
            from distr.core.incoming.source_reply import create_draft, _decode_reply
            from distr.core.db import SourceReplyDraft, get_session

            draft = {}
            try:
                with get_session() as db:
                    row = db.get(SourceReplyDraft, str(flow["draft_id"]))
                    draft = _decode_reply(row) if row is not None else {}
            except Exception:
                logger.debug("Thread reply rework could not load the stored draft", exc_info=True)
            source_key = (draft or {}).get("source_key") or flow.get("source_key")
            response_id = (draft or {}).get("response_id") or flow.get("response_id")
            if not source_key or response_id is None:
                raise ValueError("The previous draft is no longer available to rewrite.")
            created = create_draft(
                int(flow["chat_id"]), response_id, source_key,
                previous=body, instruction=instruction, replaces=str(flow["draft_id"]),
                preset_text=new_text,
            )
        except Exception as exc:
            logger.warning("Thread reply rework could not create a new draft", exc_info=True)
            return {"text": f"I couldn't rework that just now: {str(exc)[:200]}. Tell me what to change."}
        _supersede_flow(key, flow)
        if not created or not created.get("id"):
            return {"text": "I couldn't open a new confirm card. Tell me what to change."}
        return {"suppress": True}
    held = _hold_in_telegram(
        level=str(flow.get("level") or "operate"), ticket_id=int(flow.get("ticket_id") or 0),
        shaped=new_text, source=str(flow.get("source") or ""), client=str(flow.get("client") or "the client"),
        destination=flow.get("destination"), approval_context=flow.get("approval_context") or {},
        settings=load_settings(),
    )
    if not held or not held.get("pending_approval"):
        return {"text": "I couldn't open a new confirm card. Tell me what to change."}
    _supersede_flow(key, flow)
    return {"suppress": True}


_CALLBACKISH = re.compile(r"^(?:oc|ip|ac|wf|wi|wa|wv|wd|tns):\S+$")
_SEEN_CAP = 40
_SEEN_TTL_S = 3600


def _seen_message(token: str) -> str:
    """'handled' or 'released' if this message was already decided, else ''."""
    entry = ((_load_state().get("rework_seen") or {}).get(token) or {})
    return str(entry.get("kind") or "") if time.time() - float(entry.get("ts", 0)) < _SEEN_TTL_S else ""


def _mark_message(token: str, kind: str) -> None:
    """Remember the decision across a restart and after the flow it belonged to is gone."""
    with _LOCK:
        state = _load_state()
        now = time.time()
        seen = {t: e for t, e in (state.get("rework_seen") or {}).items()
                if isinstance(e, dict) and now - float(e.get("ts", 0)) < _SEEN_TTL_S}
        seen[token] = {"kind": kind, "ts": now}
        state["rework_seen"] = dict(sorted(seen.items(), key=lambda kv: kv[1]["ts"])[-_SEEN_CAP:])
        _save_state(state)


def _run_in_background(fn) -> None:
    """Run ``fn`` off the UI thread. Tests replace this to run inline."""
    threading.Thread(target=fn, name="telegram-rework", daemon=True).start()


def newer_client_messages(flow: dict[str, Any], limit: int = 3) -> list[str]:
    """What the client said in the same thread after the message this draft answers.

    A reply to a newer client message must not be mistaken for an edit to an older draft.
    """
    dest = flow.get("destination") if isinstance(flow.get("destination"), dict) else {}
    jid = str(dest.get("jid") or "")
    if not jid or flow.get("source") != "whatsapp":
        return []
    try:
        from distr.core.db import WhatsAppMessage, get_session

        with get_session() as db:
            base = 0
            for mid in dest.get("source_message_ids") or []:
                row = db.query(WhatsAppMessage).filter(WhatsAppMessage.message_id == str(mid)).first()
                if row is not None and row.whatsapp_timestamp:
                    base = max(base, int(row.whatsapp_timestamp))
            if not base:
                return []
            rows = (db.query(WhatsAppMessage)
                    .filter(WhatsAppMessage.jid == jid, WhatsAppMessage.from_me.is_(False),
                            WhatsAppMessage.whatsapp_timestamp > base)
                    .order_by(WhatsAppMessage.whatsapp_timestamp.asc()).limit(limit).all())
            return [f"{r.sender_push_name or 'Client'}: {str(r.text or r.caption or '').strip()[:300]}"
                    for r in rows if str(r.text or r.caption or "").strip()]
    except Exception:
        logger.debug("outcome_flow: newer client messages unavailable", exc_info=True)
        return []


def _classify_rework(raw: str, flow: dict[str, Any], newer: list[str]) -> dict[str, str]:
    from distr.core.incoming.telegram_reply_intent import classify_rework_reply

    other = False
    try:
        other = _names_other_work(raw, flow)
    except Exception:
        logger.debug("outcome_flow: other-work check failed", exc_info=True)
    return classify_rework_reply(
        raw, draft=str(flow.get("body") or ""), client=str(flow.get("client") or ""),
        answering=str(flow.get("answering") or ""), newer_inbound=newer, other_work=other,
    )


def handle_pending_rework(text: str, *, chat_id: Any = None, telegram_message_id: Any = None,
                          source: str = "text") -> dict[str, Any] | None:
    """Claim a typed or spoken message that may answer "What should I change?".

    Returns None when nothing is waiting, the message is a button/callback, or it was
    already released as unrelated. Otherwise the work moves to a background thread, because
    a model call on the UI thread froze the app for up to a minute and dropped the relay
    link (the 11:17 stall on 28 Nov), and this returns {"suppress": True} at once.
    """
    raw = str(text or "").strip()
    if not raw or chat_id is None or _CALLBACKISH.match(raw):
        return None
    token = str(telegram_message_id or "") or hashlib.sha256(raw.encode()).hexdigest()[:16]
    seen = _seen_message(token)
    if seen:
        return {"suppress": True}  # the same message delivered twice; the first copy was already acted on
    pending = latest_awaiting_rework()
    if not pending:
        return None
    key, flow = pending
    _mark_message(token, "handled")
    manager = _telegram_manager()
    try:
        if manager is not None and hasattr(manager, "_start_typing_loop"):
            manager._start_typing_loop("typing")
    except Exception:
        logger.debug("outcome_flow: typing indicator failed", exc_info=True)
    _run_in_background(lambda: _process_rework_reply(key, raw, token, chat_id, source))
    return {"suppress": True}


def _tell_paul(text: str, **kwargs: Any) -> None:
    send_telegram(text, **kwargs)
    manager = _telegram_manager()
    try:
        if manager is not None and hasattr(manager, "_stop_typing_loop"):
            manager._stop_typing_loop()
    except Exception:
        logger.debug("outcome_flow: stop typing failed", exc_info=True)


def _release_to_normal_routing(key: str, raw: str, token: str, chat_id: Any, source: str) -> None:
    """The message is not about the draft: leave the draft waiting and hand the words on."""
    _mark_message(token, "released")
    try:
        from distr.core.tensology_client import handle_proposal_decision_reply

        reply = handle_proposal_decision_reply(raw, source="telegram", telegram_chat_id=chat_id)
        if reply:
            _tell_paul(reply)
            return
    except Exception:
        logger.debug("outcome_flow: proposal reply check failed", exc_info=True)
    try:
        from distr.core.integrations.bus import get_integration_message_bus

        get_integration_message_bus().deliver_telegram_user_input(
            text=raw, image_path=None, telegram_chat_id=int(chat_id) if str(chat_id).lstrip("-").isdigit() else None,
            speak=None, input_type="voice" if source == "voice" else "text",
        )
    except Exception:
        logger.warning("outcome_flow: could not hand an unrelated message on", exc_info=True)
        _tell_paul("That didn't read as a change to the draft, so I left the draft as it is. "
                   "I couldn't pass your message on. Please send it again.")


def _process_rework_reply(key: str, raw: str, token: str, chat_id: Any, source: str) -> None:
    """Interpret Paul's reply to Rework, then act. Runs off the UI thread. Never sends to a client."""
    try:
        flow = get_flow(key) or {}
        if flow.get("status") != "awaiting_rework":
            _tell_paul("That draft already changed. Tap Rework on the newest card, then tell me what to change.")
            return
        newer = newer_client_messages(flow)
        verdict = _classify_rework(raw, flow, newer)
        intent = verdict.get("intent") or "unclear"
        audit("rework_reply_interpreted", ticket_id=flow.get("ticket_id"), chat_id=flow.get("chat_id"),
              source_channel=source, heard=raw, intent=intent, instruction=verdict.get("instruction"),
              model=verdict.get("model"), newer_client_messages=newer, status=intent)
        if intent == "other_topic":
            _release_to_normal_routing(key, raw, token, chat_id, source)
            return
        if intent == "cancel":
            _handle_flow_action(key, "cancel")
            _tell_paul("Okay. I've cancelled that draft and nothing was sent.")
            return
        if intent == "send":
            _tell_paul("I don't send from a message. Tap Send message to client on the card when it reads right.")
            return
        if intent == "unclear":
            _tell_paul(verdict.get("question") or "Is that a change to this draft, or something else? "
                       "Tell me what to change.")
            return
        result = apply_rework_instruction(
            key, flow, verdict.get("instruction") or raw,
            dictated=verdict.get("message") if intent == "dictated_message" else "",
            newer_inbound=newer,
        )
        if result.get("suppress"):
            manager = _telegram_manager()
            if manager is not None and hasattr(manager, "_stop_typing_loop"):
                manager._stop_typing_loop()
        elif result.get("text"):
            _tell_paul(result["text"], reply_markup=result.get("reply_markup"))
    except Exception:
        logger.warning("outcome_flow: rework reply failed", exc_info=True)
        _tell_paul("I couldn't rework that just now. Nothing was sent. Tell me what to change again.")


def _handle_client_issue_action(key: str, flow: dict[str, Any], action: str, status: str, *,
                                telegram_message_id: Any = None, telegram_chat_id: Any = None) -> dict[str, Any]:
    """Taps on a client-issue card raised by the mail watch. Nothing here contacts the client."""
    chat = telegram_chat_id or flow.get("telegram_chat_id")

    def reply(text: str) -> dict[str, Any]:
        return _reply_on_card(text, message_id=telegram_message_id, chat_id=chat, flow=flow)

    if status in {"started", "starting", "dismissed"}:
        return reply("Already started." if status != "dismissed" else "Set aside. Nothing was sent.")
    if action == "cancel":
        save_flow(key, expected_status=status, status="dismissed")
        audit("client_issue_dismissed", message_id=flow.get("message_id"), status="dismissed")
        return reply("Set aside. Nothing was sent.")
    if action != "start":
        return reply("Use Start the fix or Not now on this one.")
    if not save_flow(key, expected_status=status, status="starting"):
        return _handle_flow_action(key, action, telegram_message_id=telegram_message_id, telegram_chat_id=telegram_chat_id)
    try:
        from distr.core.initiative.client_mail_watch import start_fix

        result = start_fix(flow)
    except Exception:
        logger.warning("client issue fix could not start", exc_info=True)
        save_flow(key, expected_status="starting", status=status)
        return reply("I couldn't open the ticket just now. The card is still here, tap Start the fix to try again.")
    started = bool(result.get("ticket_id"))
    save_flow(key, expected_status="starting", status="started" if started else status, ticket_id=result.get("ticket_id"))
    audit("client_issue_started", message_id=flow.get("message_id"), ticket_id=result.get("ticket_id"),
          status="started" if started else "needs_info")
    if started:
        return reply("On it. I've opened a ticket and started on the fix. Nothing has gone to the client.")
    ask = humanize(result.get("response_text") or "", max_len=300)
    return reply(ask or "I need to know which project this belongs to before I can start.")


def _handle_flow_action(key: str, action: str, *, telegram_message_id: Any = None,
                        telegram_chat_id: Any = None) -> dict[str, Any]:
    if telegram_message_id:
        record_card_message(key, telegram_message_id, telegram_chat_id)
    flow = get_flow(key)
    if not flow:
        return _reply_on_card(
            CANCELLED_REPLACED_TEXT,
            message_id=telegram_message_id,
            chat_id=telegram_chat_id,
            flow=None,
            retire_others=False,
        )
    client = flow.get("client") or "the client"
    src = source_label(flow.get("source"))
    status = flow.get("status")
    if status in {"superseded", "cancelled"}:
        return _reply_on_card(
            CANCELLED_REPLACED_TEXT,
            message_id=telegram_message_id,
            chat_id=telegram_chat_id or flow.get("telegram_chat_id"),
            flow=flow,
        )
    if flow.get("kind") == "client_issue":
        return _handle_client_issue_action(key, flow, action, status, telegram_message_id=telegram_message_id,
                                           telegram_chat_id=telegram_chat_id)
    if status == "awaiting_rework" and action not in {"rework"}:
        if action in {"confirm", "send"}:
            return _reply_on_card(
                "Couldn't send: waiting for what to change.",
                message_id=telegram_message_id,
                chat_id=telegram_chat_id or flow.get("telegram_chat_id"),
                flow=flow,
            )
    if status in {"sending", "unconfirmed"} and flow.get("delivery_key"):
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflowRun
        from distr.core.workflow.telegram_reply_receipts import run_status

        context = flow.get("approval_context") or {}
        with get_session() as db:
            run = db.get(AutoWorkflowRun, context.get("run_id")) if context.get("run_id") else None
            if run and run.ticket_id == flow.get("ticket_id"):
                status = run_status(db, run, flow["delivery_key"]) or status
        if status == "sending":
            return {"text": "Waiting for Telegram to confirm the closing reply."}
        if status == "not_sent":
            return {"text": "Nothing was sent. Open the workflow and review the current recipient before sending."}
    if status == "sent":
        return _reply_on_card(
            f"Sent to {client} on {src}.",
            message_id=telegram_message_id,
            chat_id=telegram_chat_id or flow.get("telegram_chat_id"),
            flow=flow,
        )
    if status in {"sending", "unconfirmed"}:
        return {"text": f"The send to {client} on {src} was not confirmed. Check the original conversation before sending another reply."}
    if flow.get("kind") == "thread_reply":
        return _handle_thread_reply_action(key, flow, action, status, telegram_message_id=telegram_message_id, telegram_chat_id=telegram_chat_id)
    if action == "continue":
        try:
            _thread_steer(flow.get("chat_id"), "Please continue with any remaining work on this ticket.")
        except Exception:
            return {"text": "I couldn't nudge the work along just now. Try again in a moment."}
        save_flow(key, expected_status=status, status="continuing")
        return {"text": "On it, I've asked it to keep going. I'll check back in when there's more to share."}
    if action == "check":
        if not save_flow(key, expected_status=status, status="checking"):
            return _handle_flow_action(key, "check")
        return {"text": _work_status(flow.get("ticket_id")), "reply_markup": work_done_markup(key)}
    if action == "send":
        if not save_flow(key, expected_status=status, status="confirming"):
            return _handle_flow_action(key, "send")
        text = (
            f"Here's the message I'd send:\n\nTo: {client}\nVia: {src}\n\n"
            f"\u201c{client_body(flow.get('body'))}\u201d\n\nIf you send it, {client} gets exactly this on {src} straight away. "
            "Shall I send it?"
        )
        return {"text": text, "reply_markup": confirm_send_markup(key),
                "interaction_token": f"{OC_CARD_PREFIX}{key}"}
    if action == "rework":
        return _begin_rework(key, flow, status)
    if action == "cancel":
        if not save_flow(key, expected_status=status, status="awaiting_decision"):
            return _handle_flow_action(key, "cancel")
        return {"text": "No worries, I'll hold off. Nothing has been sent.", "reply_markup": work_done_markup(key)}
    if action == "confirm":
        if status != "confirming":
            return {"text": "Let's look at the message first. Tap Send message to client to see it."}
        if not save_flow(key, expected_status="confirming", status="sending"):
            return _handle_flow_action(key, "confirm")
        from distr.core.workflow.reply_delivery import deliver_ticket_reply

        ctx = flow.get("approval_context") or None
        # Exactly the text shown in the overview above.
        try:
            outcome = deliver_ticket_reply(int(flow["ticket_id"]), client_body(flow.get("body")), force=True,
                                           approval_context=ctx if ctx and ctx.get("chat_id") else None)
        except Exception:
            logger.warning("Telegram closing reply outcome unavailable", exc_info=True)
            outcome = {"unconfirmed": True}
        delivered = outcome.get("delivered") is True or (
            outcome.get("receipt_matches") is True and outcome.get("delivery_status") == "sent"
        )
        awaiting = outcome.get("delivery_status") == "sending" and outcome.get("delivery_key")
        uncertain = outcome.get("unconfirmed") is True or outcome.get("delivery_status") == "scope_conflict"
        result_status = "sent" if delivered else "sending" if awaiting else "unconfirmed" if uncertain else "send_failed"
        audit("operate_client_reply_confirmed", ticket_id=flow.get("ticket_id"), source=flow.get("source"),
              destination=flow.get("destination"), client=client, body=outcome.get("body") or flow.get("body"),
              delivered=delivered, status="not_sent" if result_status == "send_failed" else result_status)
        save_flow(key, expected_status="sending", status=result_status, delivery_key=outcome.get("delivery_key"))
        if awaiting:
            return {"text": "Waiting for Telegram to confirm the closing reply."}
        if delivered:
            return {"text": f"Sent. {client} has the update on {src}."}
        if uncertain:
            return _handle_flow_action(key, "confirm")
        return {"text": f"I couldn't send that to {client} on {src} just now, so nothing went out. "
                        "You can try again from here.", "reply_markup": work_done_markup(key)}
    return {"text": "I'm not sure what to do with that one."}
