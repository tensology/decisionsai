"""Plan chats per workspace (N3 item 9, Option A: no schema change).

Chat #1 is the legacy root linked through the workspace's DevelopmentWorkItem; further chats are root
``chats`` rows with autonomy_level="plan", execution_profile="design" and params.plan_workspace_id.
Delete is a soft archive (is_archived=1), never a row delete. Rename / archive / restore / compact take
``expected_revision`` (params.chat_revision) and raise ValueError on a stale value (409 at the route).
Compaction is extractive and deterministic (no model call): older turns become params.summary and stop
being replayed as history; the messages stay visible.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from distr.core.db import Chat
from distr.core.db.workflow import PlanWorkspace

MAX_TITLE = 120
SUMMARY_CHARS = 8000          # ~2k tokens (N3 default)
TURN_USER_CHARS = 240
TURN_REPLY_CHARS = 360
KEEP_RECENT_TURNS = 4         # auto-compaction keeps the newest turns verbatim
_DONE = {"complete", "needs_input", "sync_pending"}


def _params(chat):
    return json.loads(chat.params or "{}")


def is_plan_chat(chat) -> bool:
    """True for a Plan-area chat sharing the chats table.

    Plan chats are a different product from Chat history and Development threads.
    Match any marker written by plan creation: autonomy_level, plan_workspace_id,
    or source_type. Callers must skip these rows. Do not use is_hidden for this.
    """
    if str(getattr(chat, "autonomy_level", None) or "") == "plan":
        return True
    raw = getattr(chat, "params", None)
    if isinstance(raw, dict):
        params = raw
    else:
        try:
            params = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return False
    if not isinstance(params, dict):
        return False
    if params.get("plan_workspace_id") not in (None, ""):
        return True
    return params.get("source_type") == "plan_workspace"


def _conv():
    from distr.core.planning import conversation
    return conversation


def _write_lock(db):
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def _chat_row(chat, params, counts):
    summary = params.get("summary") or {}
    return {"id": chat.id, "title": chat.title or "Chat", "archived": bool(chat.is_archived),
            "revision": int(params.get("chat_revision", 0)), "status": params.get("plan_status", "idle"),
            "provider": str(chat.provider or "").strip().lower(), "model_name": chat.model_name,
            "messages": counts.get(chat.id, 0), "compacted_upto": summary.get("upto_turn_id"),
            "memory": params.get("memory") == "on",
            "created_at": chat.created_date.isoformat() if getattr(chat, "created_date", None) else None}


def list_chats(workspace_id):
    """All chats of the workspace, oldest first (archived ones flagged, never dropped)."""
    with _conv().get_session() as db:
        roots = _conv()._workspace_roots(db, workspace_id)
        counts = {}
        if roots:
            rows = db.execute(text(
                "SELECT parent_id, COUNT(*) FROM chats WHERE parent_id IN (%s) "
                "GROUP BY parent_id" % ",".join(str(int(r.id)) for r in roots))).fetchall()
            counts = {int(pid): int(n) for pid, n in rows}
        return [_chat_row(chat, _params(chat), counts) for chat in roots]


def new_root(db, workspace, *, provider=None, model=None, title=None):
    """Create an extra root chat inside the caller's transaction (no commit)."""
    count = len(_conv()._workspace_roots(db, workspace.id))
    chat = Chat(title=(title or f"Chat {count + 1}")[:MAX_TITLE], project_id=workspace.project_id,
                autonomy_level="plan", execution_profile="design", route_mode="manual",
                provider=provider, model_name=model,
                params=json.dumps({"plan_workspace_id": int(workspace.id), "chat_revision": 0,
                                   "source_type": "plan_workspace"}))
    db.add(chat)
    db.flush()
    return chat


def _title(value):
    value = " ".join(str(value or "").split())
    if not value or len(value) > MAX_TITLE:
        raise ValueError(f"A chat name needs 1-{MAX_TITLE} characters.")
    return value


def default_route():
    """Server-side chat default for NEW chats: the user's own conversational provider/model, read through
    the existing settings path (llm_factory.resolve_settings_keys). Nothing is hardcoded; no key is read
    here. Returns (None, None) when the setting is missing, unsupported or "auto"."""
    conversation = _conv()
    try:
        provider, model = conversation.llm_factory.resolve_settings_keys(conversation.load_settings_from_db())
    except Exception:  # noqa: BLE001 - settings unreadable: no default
        return None, None
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    if provider not in conversation.SUPPORTED_PROVIDERS or not model or model.lower() == "auto":
        return None, None
    return provider, model


def create_chat(workspace_id, title=None):
    """New empty chat on the server default route; falls back to the newest chat's route."""
    title = _title(title) if title is not None else None
    with _conv().get_session() as db:
        _write_lock(db)
        workspace = db.get(PlanWorkspace, int(workspace_id))
        if workspace is None:
            raise LookupError("Plan workspace not found.")
        provider, model = default_route()
        if provider is None:
            latest = _conv()._root(db, workspace_id)
            provider, model = (latest.provider, latest.model_name) if latest else (None, None)
        chat = new_root(db, workspace, provider=provider, model=model, title=title)
        chat_id = chat.id
        db.commit()
    return {"chat_id": chat_id, "chats": list_chats(workspace_id)}


def _locked(db, workspace_id, chat_id, expected_revision):
    _write_lock(db)
    chat = _conv()._root(db, workspace_id, chat_id)
    params = _params(chat)
    if expected_revision is not None and int(expected_revision) != int(params.get("chat_revision", 0)):
        raise ValueError("This chat changed in another window. Reload the chat list and try again.")
    return chat, params


def _save(chat, params):
    params["chat_revision"] = int(params.get("chat_revision", 0)) + 1
    chat.params = json.dumps(params)


def _not_running(params):
    from distr.core.planning.ownership import owner_is_gone
    if params.get("plan_status") == "running" and not owner_is_gone(params.get("plan_owner")):
        raise ValueError("Wait for the running turn in this chat to finish first.")


def rename_chat(workspace_id, chat_id, *, title, expected_revision=None):
    title = _title(title)
    with _conv().get_session() as db:
        chat, params = _locked(db, workspace_id, chat_id, expected_revision)
        chat.title = title
        _save(chat, params)
        db.commit()
    return {"chat_id": chat_id, "chats": list_chats(workspace_id)}


def archive_chat(workspace_id, chat_id, *, expected_revision=None):
    """Soft delete: hidden from the switcher's live list, kept in history, restorable."""
    with _conv().get_session() as db:
        chat, params = _locked(db, workspace_id, chat_id, expected_revision)
        _not_running(params)
        chat.is_archived = True
        _save(chat, params)
        db.commit()
    return {"chat_id": chat_id, "chats": list_chats(workspace_id)}


def restore_chat(workspace_id, chat_id, *, expected_revision=None):
    with _conv().get_session() as db:
        chat, params = _locked(db, workspace_id, chat_id, expected_revision)
        chat.is_archived = False
        _save(chat, params)
        db.commit()
    return {"chat_id": chat_id, "chats": list_chats(workspace_id)}


def clear_chat(workspace_id, chat_id, *, expected_revision=None):
    """Clear = archive this conversation into history and start a fresh chat (undo: restore it)."""
    with _conv().get_session() as db:
        chat, params = _locked(db, workspace_id, chat_id, expected_revision)
        _not_running(params)
        workspace = db.get(PlanWorkspace, int(workspace_id))
        chat.is_archived = True
        _save(chat, params)
        fresh = new_root(db, workspace, provider=chat.provider, model=chat.model_name)
        ids = chat.id, fresh.id
        db.commit()
    return {"archived_chat_id": ids[0], "chat_id": ids[1], "chats": list_chats(workspace_id)}


def _summarise(previous, turns):
    lines = [previous] if previous else []
    for child in turns:
        user = " ".join(str(child.input or "").split())[:TURN_USER_CHARS]
        reply = " ".join(str(child.response or "").split())[:TURN_REPLY_CHARS]
        if user:
            lines.append(f"- You: {user}")
        if reply:
            lines.append(f"  Assistant: {reply}")
    out = "\n".join(lines)
    return out if len(out) <= SUMMARY_CHARS else "…" + out[-(SUMMARY_CHARS - 1):]


def _compact(db, chat, params, *, upto_turn_id):
    summary = params.get("summary") or {}
    after = summary.get("upto_turn_id") or 0
    turns = (db.query(Chat).filter(Chat.parent_id == chat.id, Chat.id > after, Chat.id <= upto_turn_id)
             .order_by(Chat.id).all())
    turns = [t for t in turns if _params(t).get("status") in _DONE]
    if not turns:
        return False
    params["summary"] = {"text": _summarise(summary.get("text"), turns), "upto_turn_id": turns[-1].id,
                         "turns": int(summary.get("turns", 0)) + len(turns),
                         "created_at": datetime.now(timezone.utc).isoformat()}
    return True


def compact_chat(workspace_id, chat_id, *, expected_revision=None):
    """Manual compaction: every finished turn so far becomes the summary."""
    with _conv().get_session() as db:
        chat, params = _locked(db, workspace_id, chat_id, expected_revision)
        _not_running(params)
        last = db.query(Chat).filter(Chat.parent_id == chat.id).order_by(Chat.id.desc()).first()
        if last is None or not _compact(db, chat, params, upto_turn_id=last.id):
            raise ValueError("Nothing to compact yet.")
        _save(chat, params)
        db.commit()
    return {"chat_id": chat_id, "chats": list_chats(workspace_id)}


def auto_compact(workspace_id, root_id, *, before_turn_id, limit):
    """Called inside a running turn: when the replayed history exceeds ``limit`` characters, fold all but
    the newest KEEP_RECENT_TURNS finished turns into the summary. Returns the summary text (or None)."""
    with _conv().get_session() as db:
        chat = db.get(Chat, root_id)
        params = _params(chat)
        upto = (params.get("summary") or {}).get("upto_turn_id") or 0
        children = (db.query(Chat).filter(Chat.parent_id == root_id, Chat.id > upto, Chat.id < before_turn_id)
                    .order_by(Chat.id).all())
        size = sum(len(c.input or "") + len(c.response or "") for c in children)
        if size > limit and len(children) > KEEP_RECENT_TURNS:
            if _compact(db, chat, params, upto_turn_id=children[-KEEP_RECENT_TURNS - 1].id):
                params["chat_revision"] = int(params.get("chat_revision", 0)) + 1
                chat.params = json.dumps(params)
                db.commit()
        return (params.get("summary") or {}).get("text") or None
