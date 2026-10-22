"""Laya submission API service.

A caller POSTs free text; Laya decides which project and which thread it
belongs to, then the submission is dispatched through the existing Chat agent
pipeline (``signal_manager.web_send_to_agent_requested`` -> agent turn
runtime). No terminal and no IDE driving is involved.

Realtime updates reuse the existing mechanisms:
* EventBus ``ORCHESTRATION_EVENT`` (streamed to the UI by ``GET /api/events/stream``)
* the per-chat websocket (``/api/ws``) via ``publish_chat_event_threadsafe``

State is kept in an in-memory registry (bounded); there is no submissions table.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

EVENT_ACCEPTED = "laya.submission.accepted"
EVENT_ROUTED = "laya.submission.routed"
EVENT_PROGRESS = "laya.submission.progress"
EVENT_DONE = "laya.submission.done"
EVENT_FAILED = "laya.submission.failed"

ORIGIN_SURFACE = "laya_submission"
DEFAULT_PINNED_NAMES = ("decisionsai", "merrypak", "spoortrack")
RECENT_THREAD_WINDOW = timedelta(hours=12)
MAX_REGISTRY = 200
MAX_TEXT = 20000
WATCH_TIMEOUT_S = 900.0
WATCH_INTERVAL_S = 2.0
_WORD = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}")


@dataclass
class ProjectCandidate:
    id: int
    name: str
    description: str = ""
    folder_location: str = ""
    trigger_words: tuple[str, ...] = ()
    pinned: bool = False
    in_use: bool = False
    recent_thread_ids: tuple[int, ...] = ()


@dataclass
class RouteResult:
    project_id: int | None
    project_name: str
    confidence: float
    reason: str
    scores: dict[str, float] = field(default_factory=dict)
    laya: dict[str, Any] = field(default_factory=dict)


@dataclass
class Submission:
    id: str
    text: str
    source: str = ""
    status: str = "accepted"  # accepted|routed|dispatched|done|failed
    created_at: str = ""
    updated_at: str = ""
    hints: dict[str, Any] = field(default_factory=dict)
    project_id: int | None = None
    project_name: str = ""
    thread_id: int | None = None
    thread_created: bool = False
    reason: str = ""
    confidence: float = 0.0
    laya: dict[str, Any] = field(default_factory=dict)
    progress: list[dict[str, Any]] = field(default_factory=list)
    result: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["text_preview"] = self.text[:200]
        data.pop("text", None)
        return data


_registry: dict[str, Submission] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- events

def _event_payload(sub: Submission, kind: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "submission_id": sub.id,
        "status": sub.status,
        "project_id": sub.project_id,
        "project_name": sub.project_name,
        "thread_id": sub.thread_id,
        "reason": sub.reason,
        "confidence": sub.confidence,
        "at": _now(),
    }
    if extra:
        payload.update(extra)
    return payload


def emit(sub: Submission, kind: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Publish one submission event on every existing realtime channel (fail-open)."""
    payload = _event_payload(sub, kind, extra)
    try:
        from distr.core.events import ORCHESTRATION_EVENT, get_event_bus

        get_event_bus().publish(ORCHESTRATION_EVENT, payload)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Laya submission bus publish failed: %s", exc)
    if sub.thread_id is not None:
        try:
            from distr.gui.web.routes.chat import publish_chat_event_threadsafe

            publish_chat_event_threadsafe({"event": "laya_submission", "chat_id": int(sub.thread_id), **payload})
        except Exception as exc:
            logger.debug("Laya submission chat ws publish skipped: %s", exc)
    return payload


# ---------------------------------------------------------------- context

def _pinned_ids(session: Any) -> set[int]:
    try:
        from distr.core.db import Settings

        row = session.query(Settings).first()
        raw = getattr(row, "development_hot_corner_pinned_project_ids", None) or "[]"
        return {int(x) for x in json.loads(raw)}
    except Exception:
        return set()


def load_project_candidates(session: Any) -> list[ProjectCandidate]:
    from distr.core.db import Chat
    from distr.core.db.projects import Project

    pinned = _pinned_ids(session)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - RECENT_THREAD_WINDOW
    out: list[ProjectCandidate] = []
    for p in session.query(Project).all():
        try:
            words = tuple(str(w).strip().lower() for w in json.loads(p.additional_trigger_words or "[]") if str(w).strip())
        except Exception:
            words = tuple(w.strip().lower() for w in str(p.additional_trigger_words or "").split(",") if w.strip())
        recent = tuple(
            int(r.id)
            for r in session.query(Chat.id)
            .filter(Chat.project_id == p.id, Chat.parent_id == None, Chat.is_archived == False, Chat.modified_date >= cutoff)  # noqa: E711,E712
            .order_by(Chat.modified_date.desc())
            .limit(5)
        )
        name = str(p.name or "")
        out.append(
            ProjectCandidate(
                id=int(p.id),
                name=name,
                description=str(p.description or ""),
                folder_location=str(p.folder_location or ""),
                trigger_words=words,
                pinned=int(p.id) in pinned or name.lower().replace(" ", "") in DEFAULT_PINNED_NAMES,
                in_use=bool(p.in_use),
                recent_thread_ids=recent,
            )
        )
    return out


# ---------------------------------------------------------------- classification

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def score_projects(text: str, candidates: list[ProjectCandidate]) -> dict[int, tuple[float, list[str]]]:
    lowered = text.lower()
    compact = _norm(text)
    tokens = set(_WORD.findall(lowered))
    scores: dict[int, tuple[float, list[str]]] = {}
    for c in candidates:
        score = 0.0
        why: list[str] = []
        n = _norm(c.name)
        if n and len(n) >= 3 and n in compact:
            score += 5.0
            why.append(f"names project '{c.name}'")
        folder = c.folder_location.rstrip("/")
        if folder and folder.lower() in lowered:
            score += 6.0
            why.append("mentions project path")
        elif folder:
            base = _norm(folder.rsplit("/", 1)[-1])
            if base and len(base) >= 4 and base != n and base in compact:
                score += 3.0
                why.append("mentions project folder name")
        hits = [w for w in c.trigger_words if w and (w in tokens or (" " in w and w in lowered))]
        if hits:
            score += 2.0 * min(3, len(hits))
            why.append("keywords: " + ", ".join(hits[:3]))
        desc_hits = tokens & set(_WORD.findall(c.description.lower())) - {"the", "and", "for", "with", "project"}
        if len(desc_hits) >= 2:
            score += 0.5 * min(4, len(desc_hits))
            why.append("description overlap")
        if score > 0:
            if c.pinned:
                score += 0.5
                why.append("pinned")
            if c.recent_thread_ids:
                score += 0.5
                why.append("recent activity")
            if c.in_use:
                score += 0.25
        scores[c.id] = (score, why)
    return scores


def _laya_assessment(text: str) -> dict[str, Any]:
    """Optional Laya model_router assessment (fail-open, only when Laya mode is on)."""
    try:
        from distr.core import laya_runtime

        if laya_runtime.get_mode() == "off":
            return {}
        return laya_runtime.assess_request(text)
    except Exception as exc:
        logger.debug("Laya assessment unavailable: %s", exc)
        return {"error": type(exc).__name__}


def classify(
    text: str,
    candidates: list[ProjectCandidate],
    *,
    project_hint: str | int | None = None,
    assess: Callable[[str], dict[str, Any]] | None = None,
) -> RouteResult:
    laya = (assess or _laya_assessment)(text) or {}
    by_id = {c.id: c for c in candidates}
    if project_hint not in (None, ""):
        hint = str(project_hint).strip()
        match = by_id.get(int(hint)) if hint.isdigit() else None
        if match is None:
            match = next((c for c in candidates if _norm(c.name) == _norm(hint)), None)
        if match is not None:
            return RouteResult(match.id, match.name, 1.0, "caller project hint", laya=laya)
    scores = score_projects(text, candidates)
    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
    if ranked and ranked[0][1][0] > 0:
        best_id, (best, why) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else 0.0
        margin = best - second
        confidence = round(min(0.99, 0.35 + 0.08 * best + 0.05 * margin), 2)
        if margin < 0.5:
            confidence = min(confidence, 0.5)
        return RouteResult(
            best_id, by_id[best_id].name, confidence, "; ".join(why),
            scores={by_id[k].name: v[0] for k, v in ranked[:5] if v[0] > 0}, laya=laya,
        )
    active = next((c for c in candidates if c.in_use), None)
    if active is not None:
        return RouteResult(active.id, active.name, 0.3, "no project signal; using active project", laya=laya)
    return RouteResult(None, "", 0.2, "no project signal; general thread", laya=laya)


# ---------------------------------------------------------------- threads

def select_or_create_thread(
    session: Any,
    route: RouteResult,
    text: str,
    *,
    thread_hint: int | None = None,
    new_thread: bool = False,
) -> tuple[int, bool, str]:
    from distr.core.db import Chat
    from distr.core.workflow.development_threads import is_development_thread

    if thread_hint is not None:
        chat = session.get(Chat, int(thread_hint))
        if chat is None:
            raise LookupError("thread_id not found")
        while chat.parent_id is not None:
            chat = session.get(Chat, int(chat.parent_id))
        if is_development_thread(session, chat):
            raise PermissionError("Development tasks cannot receive Chat agent messages")
        return int(chat.id), False, "caller thread hint"
    if not new_thread:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - RECENT_THREAD_WINDOW
        q = session.query(Chat).filter(
            Chat.parent_id == None, Chat.is_archived == False, Chat.is_hidden == False,  # noqa: E711,E712
            Chat.modified_date >= cutoff,
        )
        q = q.filter(Chat.project_id == route.project_id) if route.project_id is not None else q.filter(Chat.project_id == None)  # noqa: E711
        for chat in q.order_by(Chat.modified_date.desc()).limit(10):
            if not is_development_thread(session, chat):
                return int(chat.id), False, "recent project thread"
    settings_provider = settings_model = None
    try:
        from distr.core.db import Settings

        s = session.query(Settings).first()
        settings_provider = getattr(s, "llm_provider", None) or getattr(s, "agent_provider", None)
        settings_model = getattr(s, "llm_model", None) or getattr(s, "agent_model", None)
    except Exception:
        pass
    title = (text.strip().splitlines() or ["Laya submission"])[0][:60] or "Laya submission"
    chat = Chat(
        title=title, input="", response="", project_id=route.project_id, is_new=True,
        provider=settings_provider or "Ollama", model_name=settings_model or "",
    )
    session.add(chat)
    session.commit()
    return int(chat.id), True, "new thread"


# ---------------------------------------------------------------- dispatch

def _default_dispatch(sub: Submission) -> None:
    from distr.core.signals import signal_manager

    signal_manager.web_send_to_agent_requested.emit(
        int(sub.thread_id), sub.text, False, None, None,
        {"origin_surface": ORIGIN_SURFACE, "origin_request_id": sub.id, "input_type": "text"},
    )


def _latest_row_id(session: Any, thread_id: int) -> int:
    from distr.core.db import Chat

    row = session.query(Chat.id).filter((Chat.id == thread_id) | (Chat.parent_id == thread_id)).order_by(Chat.id.desc()).first()
    return int(row[0]) if row else int(thread_id)


def _thread_title(session: Any, thread_id: int) -> str:
    """Title for live thread-list insertion; fail-open."""
    try:
        from distr.core.db import Chat

        chat = session.get(Chat, int(thread_id))
        return str(getattr(chat, "title", "") or "")[:120]
    except Exception:
        return ""


def _find_response(session: Any, thread_id: int, after_id: int) -> str | None:
    from distr.core.db import Chat

    rows = (
        session.query(Chat)
        .filter((Chat.id == thread_id) | (Chat.parent_id == thread_id), Chat.id > after_id)
        .order_by(Chat.id.desc())
        .limit(5)
        .all()
    )
    for row in rows:
        if (row.response or "").strip():
            return str(row.response)
    return None


def _watch(sub: Submission, session_factory: Callable[[], Any], baseline: int, timeout_s: float, interval_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(interval_s)
        try:
            with session_factory() as session:
                response = _find_response(session, int(sub.thread_id), baseline)
        except Exception as exc:
            logger.debug("Laya submission watch error: %s", exc)
            continue
        if response is not None:
            _update(sub, status="done", result=response[:4000])
            emit(sub, EVENT_DONE, {"result_preview": response[:500]})
            return
    _update(sub, status="failed", error="timed out waiting for agent response")
    emit(sub, EVENT_FAILED, {"error": sub.error})


def _update(sub: Submission, **changes: Any) -> None:
    with _lock:
        for k, v in changes.items():
            setattr(sub, k, v)
        sub.updated_at = _now()


def report_progress(submission_id: str, message: str, **extra: Any) -> bool:
    """Record a progress note and emit ``laya.submission.progress``."""
    sub = get_submission(submission_id)
    if sub is None:
        return False
    entry = {"message": str(message)[:500], "at": _now(), **extra}
    with _lock:
        sub.progress.append(entry)
        del sub.progress[:-50]
    emit(sub, EVENT_PROGRESS, {"message": entry["message"], **extra})
    return True


def _store(sub: Submission) -> None:
    with _lock:
        _registry[sub.id] = sub
        if len(_registry) > MAX_REGISTRY:
            for key in list(_registry)[: len(_registry) - MAX_REGISTRY]:
                _registry.pop(key, None)


def get_submission(submission_id: str) -> Submission | None:
    with _lock:
        return _registry.get(submission_id)


def submit(
    text: str,
    *,
    source: str = "",
    project: str | int | None = None,
    thread_id: int | None = None,
    new_thread: bool = False,
    session_factory: Callable[[], Any] | None = None,
    dispatch: Callable[[Submission], None] | None = None,
    assess: Callable[[str], dict[str, Any]] | None = None,
    watch: bool = True,
    watch_timeout_s: float = WATCH_TIMEOUT_S,
    watch_interval_s: float = WATCH_INTERVAL_S,
) -> Submission:
    """Accept, route, and dispatch a submission. Returns the stored record."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("text is required")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text exceeds {MAX_TEXT} characters")
    if session_factory is None:
        from distr.core.db import get_session as session_factory  # type: ignore[assignment]
    now = _now()
    sub = Submission(
        id=uuid.uuid4().hex, text=text, source=str(source or "")[:80], created_at=now, updated_at=now,
        hints={k: v for k, v in {"project": project, "thread_id": thread_id, "new_thread": new_thread or None}.items() if v not in (None, "")},
    )
    _store(sub)
    emit(sub, EVENT_ACCEPTED, {"source": sub.source})
    try:
        with session_factory() as session:
            candidates = load_project_candidates(session)
            route = classify(text, candidates, project_hint=project, assess=assess)
            tid, created, thread_reason = select_or_create_thread(
                session, route, text, thread_hint=thread_id, new_thread=new_thread
            )
            baseline = _latest_row_id(session, tid)
            thread_title = _thread_title(session, tid)
        _update(
            sub, status="routed", project_id=route.project_id, project_name=route.project_name,
            thread_id=tid, thread_created=created, reason=f"{route.reason} | thread: {thread_reason}",
            confidence=route.confidence, laya=route.laya,
        )
        emit(sub, EVENT_ROUTED, {"thread_created": created, "thread_title": thread_title, "scores": route.scores})
        (dispatch or _default_dispatch)(sub)
        _update(sub, status="dispatched")
        report_progress(sub.id, "dispatched to agent turn runtime")
    except Exception as exc:
        logger.warning("Laya submission %s failed: %s", sub.id, exc)
        _update(sub, status="failed", error=str(exc)[:500])
        emit(sub, EVENT_FAILED, {"error": sub.error})
        return sub
    if watch:
        threading.Thread(
            target=_watch, args=(sub, session_factory, baseline, watch_timeout_s, watch_interval_s),
            name=f"laya-submission-{sub.id[:8]}", daemon=True,
        ).start()
    return sub
