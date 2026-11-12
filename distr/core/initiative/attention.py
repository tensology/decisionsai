"""Durable attention state for Initiative: who and what to set aside, and what still needs Paul.

Why this exists (Initiative audit, 12 Nov 2026):

* "Ignore this project" was only a checkbox (``projects.agent_ignored``) and "don't
  worry about that client" had no store at all. A note in MEMORY.md drops out of the
  agent's context after five newer sections and no code path ever read it.
* Ignoring work silently hid clients who were still waiting for a reply.

This module is the one place the code reads for that state. It is a small JSON file
next to the other Initiative state (``$DECISIONS_DB_DIR`` or ``~/.decisions``), so it
survives restarts and new sessions and never ages out of a context window.

Rules every caller follows:

* Ignored = no proactive work, no automatic replies, no nudges about routine items.
* A client who is *waiting* on an ignored project or an ignored client is never
  dropped silently: :func:`waiting_tensions` turns it into a "you asked me to set this
  aside, but X is waiting" item that recommendations, Own intake and automations surface.
* Email address links (``link_address``) route future mail for that address to a
  project locally. They never override a non-empty link from the Tensology server.
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
from typing import Any, Iterable

logger = logging.getLogger("distr.core.initiative.attention")

_LOCK = threading.RLock()
CLIENT_SOURCES = {"whatsapp", "gmail", "email", "mailshot", "telegram", "jira", "trello", "incoming_proposal", "tensology"}
DONE_WORDS = ("done", "complete", "closed", "shipped", "released", "archive")
ASK_REPEAT_WINDOW = timedelta(days=3)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    base = os.environ.get("DECISIONS_DB_DIR") or os.path.expanduser("~/.decisions")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_path() -> Path:
    return _data_dir() / "initiative_attention.json"


def _empty() -> dict[str, Any]:
    return {"version": 1, "clients": {}, "projects": {}, "focus": {}, "address_links": {},
            "learned_thread_links": {}, "asks": {}, "link_flags": {}}


def load() -> dict[str, Any]:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    base = _empty()
    base.update({k: v for k, v in data.items() if isinstance(v, type(base.get(k, v)))})
    return base


def _save(data: dict[str, Any]) -> None:
    path = store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _audit(event: str, **payload: Any) -> None:
    try:
        from distr.core.initiative.outcome_flow import audit

        audit(event, **payload)
    except Exception:
        logger.debug("attention audit unavailable", exc_info=True)


# ---------------------------------------------------------------------------
# Client identity
# ---------------------------------------------------------------------------

def client_keys(*values: Any, source: str = "") -> set[str]:
    """Stable identifiers for a client from any mix of name, email, phone, JID or chat id."""
    keys: set[str] = set()
    src = str(source or "").strip().lower()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        low = text.lower()
        if low.endswith("@g.us"):
            keys.add(f"wagroup:{low[:-5]}")
            continue
        if low.endswith("@s.whatsapp.net") or low.endswith("@lid"):
            user = low.split("@", 1)[0].split(":", 1)[0]
            keys.add(f"wa:{re.sub(r'[^0-9]', '', user)}" if low.endswith("@s.whatsapp.net") else f"walid:{user}")
            continue
        emails = _EMAIL_RE.findall(text)
        for email in emails:
            keys.add(f"email:{email.lower()}")
            keys.add(f"domain:{email.lower().split('@', 1)[1]}")
        if src == "telegram" and re.fullmatch(r"-?\d{3,20}", low):
            keys.add(f"tg:{low}")
            continue
        digits = re.sub(r"\D", "", low)
        if len(digits) >= 8 and re.fullmatch(r"[+\d\s()\-]+", low):
            keys.add(f"wa:{digits}")
            continue
        name = _EMAIL_RE.sub("", re.sub(r"<.*?>", "", low))
        name = re.sub(r"\s+", " ", name).strip(" \"'<>,")
        if name and re.search(r"[a-z]", name) and name not in {"the client", "unknown sender", "client"}:
            keys.add(f"name:{name}")
    # A domain is only a key when the caller ignores a whole domain explicitly.
    return keys


def _entry_keys(entry: dict[str, Any]) -> set[str]:
    return set(entry.get("keys") or [])


def ignore_client(identifier: str, *, label: str = "", note: str = "", project_ids: Iterable[int] = (),
                  whole_domain: bool = False, source: str = "") -> dict[str, Any]:
    """Persist "don't worry about this client". Returns the stored entry."""
    keys = client_keys(identifier, label, source=source)
    if not whole_domain:
        keys = {k for k in keys if not k.startswith("domain:")}
    if not keys:
        raise ValueError("I need a name, email address, phone number or chat to recognise that client.")
    entry_id = hashlib.sha256("|".join(sorted(keys)).encode()).hexdigest()[:12]
    with _LOCK:
        data = load()
        # Merge with any entry that already shares a key.
        for cid, existing in list(data["clients"].items()):
            if _entry_keys(existing) & keys:
                keys |= _entry_keys(existing)
                entry_id = cid
        entry = {
            "id": entry_id, "label": label or str(identifier), "keys": sorted(keys), "note": note,
            "project_ids": sorted({int(p) for p in project_ids}), "created": _now_iso(),
        }
        data["clients"][entry_id] = entry
        _save(data)
    _audit("attention_client_ignored", client=entry["label"], keys=entry["keys"], note=note, status="ignored")
    return entry


def unignore_client(identifier: str) -> bool:
    keys = client_keys(identifier) | {f"name:{str(identifier).strip().lower()}"}
    removed = False
    with _LOCK:
        data = load()
        for cid, entry in list(data["clients"].items()):
            if cid == identifier or _entry_keys(entry) & keys or str(entry.get("label", "")).lower() == str(identifier).lower():
                data["clients"].pop(cid)
                removed = True
        if removed:
            _save(data)
    if removed:
        _audit("attention_client_unignored", client=str(identifier), status="unignored")
    return removed


def ignored_clients() -> list[dict[str, Any]]:
    return list(load()["clients"].values())


def client_ignored(*values: Any, source: str = "") -> dict[str, Any] | None:
    keys = client_keys(*values, source=source)
    if not keys:
        return None
    for entry in load()["clients"].values():
        if _entry_keys(entry) & keys:
            return entry
    return None


# ---------------------------------------------------------------------------
# Projects (the flag lives on projects.agent_ignored; notes live here)
# ---------------------------------------------------------------------------

def _find_project(db, project: Any):
    from distr.core.db.projects import Project

    if isinstance(project, int) or str(project).strip().isdigit():
        return db.get(Project, int(project))
    name = str(project or "").strip().lower()
    rows = [p for p in db.query(Project).filter(Project.deleted_at.is_(None)).all() if str(p.name or "").strip().lower() == name]
    return rows[0] if len(rows) == 1 else None


def set_project_ignored(project: Any, ignored: bool = True, *, note: str = "") -> dict[str, Any]:
    """Set projects.agent_ignored (the flag every scan already reads) and keep a durable note."""
    from distr.core.db import get_session

    with get_session() as db:
        row = _find_project(db, project)
        if row is None:
            raise ValueError(f"I couldn't find exactly one project called {project!r}.")
        row.agent_ignored = bool(ignored)
        db.commit()
        pid, name = int(row.id), str(row.name or "")
    with _LOCK:
        data = load()
        if ignored:
            data["projects"][str(pid)] = {"id": pid, "name": name, "note": note, "created": _now_iso()}
        else:
            data["projects"].pop(str(pid), None)
        _save(data)
    _audit("attention_project_ignored" if ignored else "attention_project_unignored", project=name, project_id=pid,
           note=note, status="ignored" if ignored else "unignored")
    return {"project_id": pid, "name": name, "ignored": bool(ignored)}


def ignored_project_ids() -> set[int]:
    try:
        from distr.core.initiative.outcome_flow import ignored_project_ids as _ids

        return set(_ids())
    except Exception:
        return set()


def project_ignored(project_id: Any = None, project_name: Any = None) -> bool:
    try:
        if project_id is not None and str(project_id).strip().isdigit() and int(project_id) in ignored_project_ids():
            return True
    except Exception:
        pass
    if project_name:
        try:
            from distr.core.initiative.outcome_flow import ignored_project_names

            return str(project_name).strip().lower() in ignored_project_names()
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Focus (durable; fed by explicit instructions and by the memory palace)
# ---------------------------------------------------------------------------

def set_focus(subject: str, *, weight: int = 2, note: str = "") -> dict[str, Any]:
    key = str(subject or "").strip().lower()
    if not key:
        raise ValueError("Tell me what to focus on.")
    with _LOCK:
        data = load()
        data["focus"][key] = {"subject": subject, "weight": int(weight), "note": note, "created": _now_iso()}
        _save(data)
    return data["focus"][key]


def clear_focus(subject: str) -> bool:
    with _LOCK:
        data = load()
        hit = data["focus"].pop(str(subject or "").strip().lower(), None) is not None
        if hit:
            _save(data)
    return hit


_NEG_RE = re.compile(r"\b(ignore|don'?t worry about|not a priority|deprioriti[sz]e|on hold|paused|park(?:ed)?|set aside|stop (?:working on|following))\b", re.I)
_POS_RE = re.compile(r"\b(priorit(?:y|ies|ise|ize)|focus|launch|deadline|urgent|important|key client|main client|biggest)\b", re.I)


def attention_profile(hints: Iterable[str], project_names: Iterable[str], client_names: Iterable[str] = ()) -> dict[str, Any]:
    """Turn memory-palace notes + durable focus into what to boost and what to quiet.

    Returns {"boost": {name: weight}, "quiet": {name}, "focus_words": set, "reasons": {name: text}}.
    A name is matched as a whole word in a hint sentence; a sentence with a negative cue
    quiets the names in it, one with a positive cue boosts them.
    """
    names = {str(n).strip(): str(n).strip().lower() for n in [*project_names, *client_names] if str(n or "").strip()}
    boost: dict[str, int] = {}
    quiet: set[str] = set()
    reasons: dict[str, str] = {}
    for hint in hints:
        for sentence in re.split(r"(?<=[.!?;])\s+|\n", str(hint or "")):
            low = sentence.lower()
            hits = [orig for orig, ln in names.items() if ln and re.search(rf"\b{re.escape(ln)}\b", low)]
            if not hits:
                continue
            if _NEG_RE.search(sentence):
                for name in hits:
                    quiet.add(name)
                    reasons[name] = sentence.strip()[:200]
            elif _POS_RE.search(sentence):
                for name in hits:
                    boost[name] = boost.get(name, 0) + 2
                    reasons.setdefault(name, sentence.strip()[:200])
    for key, entry in load()["focus"].items():
        for orig, ln in names.items():
            if ln == key or key in ln:
                boost[orig] = boost.get(orig, 0) + int(entry.get("weight") or 2)
                reasons.setdefault(orig, f"You asked me to focus on {entry.get('subject')}.")
    return {"boost": boost, "quiet": quiet, "reasons": reasons}


# ---------------------------------------------------------------------------
# Waiting clients vs. ignore: never silent
# ---------------------------------------------------------------------------

def _lane_done(name: str) -> bool:
    low = str(name or "").lower()
    return any(w in low for w in DONE_WORDS)


def waiting_tensions(*, now: datetime | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Open client work that is hidden by an ignore. Each item says who is waiting and why it's hidden.

    A ticket counts when it came from a client channel, is not in a done lane, and either
    its project is ignored or its client is ignored. Pending client-reply holds in the
    outcome flow count too.
    """
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    now = now or datetime.now()
    ignored = ignored_project_ids()
    clients = ignored_clients()
    if not ignored and not clients:
        return []
    out: list[dict[str, Any]] = []
    with get_session() as db:
        projects = {int(p.id): p for p in db.query(Project).all()}
        boards = {int(b.id): b for b in db.query(KanbanBoard).all()}
        lanes = {int(l.id): l for l in db.query(KanbanLane).all()}
        for t in db.query(KanbanTicket).all():
            provider = str(getattr(t, "source_provider", "") or "").lower()
            if provider not in CLIENT_SOURCES:
                continue
            lane = lanes.get(int(t.lane_id)) if t.lane_id is not None else None
            if lane is None or _lane_done(lane.name) or "backlog" in str(lane.name or "").lower():
                continue
            board = boards.get(int(lane.board_id)) if lane.board_id is not None else None
            pid = t.linked_project_id if t.linked_project_id is not None else (board.default_project_id if board else None)
            pname = str(getattr(projects.get(int(pid)) if pid is not None else None, "name", "") or "")
            contact = str(t.source_contact or "").strip()
            thread = str(t.source_thread_id or "").strip()
            if provider == "incoming_proposal":
                m = re.match(r"^local:([a-z]+):(.+):[0-9a-f]{12,}$", str(t.source_external_id or ""))
                if m:
                    thread = thread or m.group(2)
            client_entry = None
            for entry in clients:
                if _entry_keys(entry) & client_keys(contact, thread, source=provider):
                    client_entry = entry
                    break
            project_hidden = pid is not None and int(pid) in ignored
            if not project_hidden and client_entry is None:
                continue
            modified = getattr(t, "modified_date", None) or now
            days = max(0, (now - modified).days) if isinstance(modified, datetime) else 0
            who = (client_entry or {}).get("label") or _first_name(contact) or "the client"
            why = (f"you asked me to set {pname or 'that project'} aside" if project_hidden
                   else f"you asked me not to worry about {who}")
            out.append({
                "ticket_id": int(t.id), "title": str(t.title or ""), "project": pname, "project_id": pid,
                "client": who, "source": provider, "lane": str(lane.name or ""), "days": days,
                "reason": "project_ignored" if project_hidden else "client_ignored", "why": why,
            })
    # Client replies held by the outcome flow for these tickets are part of the same tension.
    try:
        from distr.core.initiative.outcome_flow import _load_state

        held = {str(f.get("ticket_id")) for f in (_load_state().get("flows") or {}).values()
                if f.get("kind") == "reply" and f.get("status") in {"awaiting_decision", "held_for_approval", "blocked",
                                                                     "notification_failed", "skipped_source_off"}}
        for item in out:
            item["reply_held"] = str(item["ticket_id"]) in held
    except Exception:
        pass
    out.sort(key=lambda i: (-i["days"], i["ticket_id"]))
    return out[:limit]


def _first_name(contact: Any) -> str:
    text = str(contact or "").strip()
    if not text or text.isdigit() or "@" in text:
        return ""
    return text.split()[0].strip(",.") if text else ""


def tension_text(item: dict[str, Any]) -> str:
    waited = f" for {item['days']} day{'s' if item['days'] != 1 else ''}" if item.get("days") else ""
    held = " I'm holding a reply for them." if item.get("reply_held") else ""
    where = f" in {item['project']}" if item.get("project") else ""
    return (f"Heads up: {item['why']}, but {item['client']} is still waiting{waited} on "
            f"\u201c{item['title'][:80]}\u201d{where}.{held} Want me to reply, hand it to someone, "
            "or let them know it's on hold?")


# ---------------------------------------------------------------------------
# Repeated approval asks (don't nag; remember that we already asked)
# ---------------------------------------------------------------------------

def recent_ask(fingerprint: str, *, now: datetime | None = None, window: timedelta = ASK_REPEAT_WINDOW) -> dict[str, Any] | None:
    entry = load()["asks"].get(str(fingerprint))
    if not entry:
        return None
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    try:
        last = datetime.fromisoformat(entry["last"])
        if last.tzinfo is None:
            last = last.astimezone()
    except Exception:
        return None
    return entry if now - last < window else None


def record_ask(fingerprint: str, summary: str = "", *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    stamp = (now if now.tzinfo else now.astimezone()).isoformat(timespec="seconds")
    with _LOCK:
        data = load()
        entry = dict(data["asks"].get(str(fingerprint)) or {"first": stamp, "count": 0, "summary": summary})
        entry["last"] = stamp
        entry["count"] = int(entry.get("count") or 0) + 1
        data["asks"][str(fingerprint)] = entry
        cutoff = (datetime.now().astimezone() - timedelta(days=30)).isoformat()
        data["asks"] = {k: v for k, v in data["asks"].items() if str(v.get("last", "")) >= cutoff}
        _save(data)
    return entry


# ---------------------------------------------------------------------------
# Email address -> project links (local; never overrides a non-empty server link)
# ---------------------------------------------------------------------------

def _address_key(address: str) -> str:
    raw = str(address or "").strip().lower()
    emails = _EMAIL_RE.findall(raw)
    if emails:
        return f"email:{emails[0].lower()}"
    if raw.startswith("@") and "." in raw:
        return f"domain:{raw[1:]}"
    if re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", raw):
        return f"domain:{raw}"
    raise ValueError("That doesn't look like an email address or domain (use name@example.com or @example.com).")


def link_address(address: str, project: Any, *, note: str = "") -> dict[str, Any]:
    """Route future mail from or to this address (or @domain) to a project."""
    from distr.core.db import get_session

    key = _address_key(address)
    with get_session() as db:
        row = _find_project(db, project)
        if row is None:
            raise ValueError(f"I couldn't find exactly one project called {project!r}.")
        pid, name = int(row.id), str(row.name or "")
    with _LOCK:
        data = load()
        entry = dict(data["address_links"].get(key) or {"address": key.split(":", 1)[1], "project_ids": [], "created": _now_iso()})
        entry["project_ids"] = sorted({*entry.get("project_ids", []), pid})
        entry["note"] = note or entry.get("note", "")
        data["address_links"][key] = entry
        _save(data)
    _audit("attention_address_linked", address=key, project=name, project_id=pid, status="linked",
           conflict=len(entry["project_ids"]) > 1)
    return {**entry, "key": key, "project": name, "conflict": len(entry["project_ids"]) > 1}


def unlink_address(address: str, project: Any = None) -> bool:
    key = _address_key(address)
    with _LOCK:
        data = load()
        entry = data["address_links"].get(key)
        if not entry:
            return False
        if project is None:
            data["address_links"].pop(key)
        else:
            from distr.core.db import get_session

            with get_session() as db:
                row = _find_project(db, project)
            pid = int(row.id) if row is not None else None
            entry["project_ids"] = [p for p in entry.get("project_ids", []) if p != pid]
            if not entry["project_ids"]:
                data["address_links"].pop(key)
        # Learned thread links that came from this address are re-derived on the next pass.
        data["learned_thread_links"] = {k: v for k, v in data["learned_thread_links"].items() if v.get("via") != key}
        _save(data)
    return True


def address_links() -> dict[str, dict[str, Any]]:
    return dict(load()["address_links"])


def address_project_ids(*addresses: Any) -> tuple[set[int], str]:
    """Project ids linked to any of these addresses (exact address wins over domain)."""
    links = load()["address_links"]
    exact: set[int] = set()
    domain: set[int] = set()
    via = ""
    for value in addresses:
        for email in _EMAIL_RE.findall(str(value or "")):
            e = email.lower()
            hit = links.get(f"email:{e}")
            if hit:
                exact |= set(hit.get("project_ids") or [])
                via = via or f"email:{e}"
            dhit = links.get(f"domain:{e.split('@', 1)[1]}")
            if dhit:
                domain |= set(dhit.get("project_ids") or [])
                via = via or f"domain:{e.split('@', 1)[1]}"
    return (exact or domain), via


def learn_thread_link(source: str, thread_id: str, project_ids: Iterable[int], via: str) -> None:
    key = f"{source}|{thread_id}"
    ids = sorted({int(p) for p in project_ids})
    with _LOCK:
        data = load()
        current = data["learned_thread_links"].get(key)
        if current and current.get("project_ids") == ids and current.get("via") == via:
            return
        data["learned_thread_links"][key] = {"project_ids": ids, "via": via, "created": _now_iso()}
        _save(data)


def learned_thread_links() -> dict[tuple[str, str], set[int]]:
    out: dict[tuple[str, str], set[int]] = {}
    for key, entry in load()["learned_thread_links"].items():
        source, _, thread = key.partition("|")
        out[(source, thread)] = set(entry.get("project_ids") or [])
    return out


def ticket_hold_reason(ticket_id: Any) -> str:
    """Why automatic work on this ticket is held (ignored project or client), or ''. Never raises."""
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

        with get_session() as db:
            t = db.get(KanbanTicket, int(ticket_id))
            if t is None:
                return ""
            pid = t.linked_project_id
            if pid is None and t.lane_id is not None:
                lane = db.get(KanbanLane, int(t.lane_id))
                board = db.get(KanbanBoard, int(lane.board_id)) if lane and lane.board_id is not None else None
                pid = board.default_project_id if board else None
            contact, thread, provider = t.source_contact, t.source_thread_id, str(t.source_provider or "")
        if pid is not None and int(pid) in ignored_project_ids():
            return "its project is set aside"
        entry = client_ignored(contact, thread, source=provider) if provider.lower() in CLIENT_SOURCES else None
        if entry:
            return f"you asked me not to worry about {entry.get('label') or 'that client'}"
    except Exception:
        logger.debug("ticket hold check failed", exc_info=True)
    return ""


def record_link_flags(flags: Iterable[dict[str, Any]], *, keep: int = 30) -> None:
    """Mail that needs a project decision (conflict, topic drift, unlinked forward). Surfaced, never dropped."""
    stamp = _now_iso()
    with _LOCK:
        data = load()
        current = dict(data.get("link_flags") or {})
        changed = False
        for flag in flags:
            key = f"{flag.get('kind')}|{flag.get('source')}|{flag.get('thread')}"
            if key in current and current[key].get("project_ids") == flag.get("project_ids"):
                continue
            current[key] = {**flag, "seen": stamp}
            changed = True
        if changed:
            data["link_flags"] = dict(sorted(current.items(), key=lambda kv: kv[1].get("seen", ""))[-keep:])
            _save(data)


def link_flags() -> list[dict[str, Any]]:
    return list((load().get("link_flags") or {}).values())


def clear_link_flag(kind: str, source: str, thread: str) -> bool:
    with _LOCK:
        data = load()
        hit = data["link_flags"].pop(f"{kind}|{source}|{thread}", None) is not None
        if hit:
            _save(data)
    return hit


def snapshot() -> dict[str, Any]:
    """Everything a person (or the agent) needs to see what's set aside and why."""
    data = load()
    return {
        "ignored_clients": list(data["clients"].values()),
        "ignored_project_notes": list(data["projects"].values()),
        "ignored_project_ids": sorted(ignored_project_ids()),
        "focus": list(data["focus"].values()),
        "address_links": data["address_links"],
        "learned_thread_links": data["learned_thread_links"],
        "link_flags": list(data["link_flags"].values()),
        "store": str(store_path()),
    }
