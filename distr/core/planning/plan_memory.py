"""Plan chat memory (N2 item 8), local-first and opt-in per chat. No schema change.

* Switch: root chat params.memory = "on" | "off" (default off = never stores, never retrieves).
* Write: after a finished turn, one redacted record (user message + reply + changed item titles) is
  kept on that turn's params.memory_record. Redaction runs BEFORE storing (mempalace redact():
  keys/tokens/passwords/JWTs/card-like numbers/emails/phones/hex/blobs, plus .env lines). If redaction
  changed more than half the text, nothing is stored.
* Retrieval: same project only, other live chats with memory on, deterministic token-overlap ranking,
  top 5 within 4,000 characters, fed to the model as reference data (not instructions).
* Forget: per chat or per project, removes the records (the visible messages stay). Archived chats
  are excluded from retrieval until restored.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from distr.core.db import Chat

MAX_RECORD = 2000
MAX_RESULTS = 5
MAX_CONTEXT_CHARS = 4000
_ENV_LINE = re.compile(r"(?m)^[A-Z][A-Z0-9_]{2,}=\S+")
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "have", "are", "was", "you", "your", "can", "not"}


def _conv():
    from distr.core.planning import conversation
    return conversation


def _params(chat):
    return json.loads(chat.params or "{}")


def redact_record(text: str) -> str | None:
    from distr.core.mempalace.selfimprove.redact import redact
    raw = str(text or "")
    if not raw.strip():
        return None
    out = redact(_ENV_LINE.sub("[REDACTED:env]", raw))
    removed = len(raw) - len(re.sub(r"\[REDACTED[^\]]*\]", "", out))
    if removed > len(raw) / 2:
        return None
    return out[:MAX_RECORD]


def enabled(root) -> bool:
    return _params(root).get("memory") == "on"


def set_enabled(workspace_id: int, chat_id: int, on: bool) -> dict:
    with _conv().get_session() as db:
        root = _conv()._root(db, workspace_id, chat_id)
        params = _params(root)
        params["memory"] = "on" if on else "off"
        params["chat_revision"] = int(params.get("chat_revision", 0)) + 1
        root.params = json.dumps(params)
        db.commit()
    return memory_view(workspace_id, chat_id)


def record_turn(root_id: int, child_id: int, titles: list[str]) -> bool:
    """Store the redacted record on the turn when the chat's memory is on. Never raises into the turn."""
    try:
        with _conv().get_session() as db:
            root, child = db.get(Chat, root_id), db.get(Chat, child_id)
            if root is None or child is None or not enabled(root):
                return False
            body = f"You: {child.input or ''}\nAssistant: {child.response or ''}"
            if titles:
                body += "\nChanged: " + ", ".join(t for t in titles if t)[:300]
            text = redact_record(body)
            if text is None:
                return False
            params = _params(child)
            params["memory_record"] = {"text": text, "at": datetime.now(timezone.utc).isoformat()}
            child.params = json.dumps(params)
            db.commit()
            return True
    except Exception:  # noqa: BLE001 - memory is best effort
        return False


def _records(db, roots):
    for root in roots:
        for child in db.query(Chat).filter(Chat.parent_id == root.id).order_by(Chat.id):
            record = _params(child).get("memory_record")
            if record and record.get("text"):
                yield root, child, record


def _tokens(text):
    return {w for w in _WORD.findall(str(text).lower()) if w not in _STOP}


def recall(workspace_id: int, root_id: int, message: str) -> list[dict]:
    """Records from OTHER live chats of the same project (any workspace of it) with memory on."""
    with _conv().get_session() as db:
        root = db.get(Chat, root_id)
        if root is None or not enabled(root) or not root.project_id:
            return []
        candidates = (db.query(Chat).filter(Chat.parent_id.is_(None), Chat.project_id == root.project_id,
                                            Chat.autonomy_level == "plan", Chat.id != root.id).all())
        candidates = [c for c in candidates if not c.is_archived and enabled(c)]
        query = _tokens(message)
        scored = []
        for chat, child, record in _records(db, candidates):
            score = len(query & _tokens(record["text"]))
            if score:
                scored.append((score, child.id, chat, record))
        scored.sort(key=lambda row: (-row[0], -row[1]))
        out, size = [], 0
        for score, turn_id, chat, record in scored[:MAX_RESULTS]:
            if size + len(record["text"]) > MAX_CONTEXT_CHARS:
                break
            size += len(record["text"])
            out.append({"chat_id": chat.id, "chat_title": chat.title, "turn_id": turn_id, "text": record["text"]})
        return out


def memory_view(workspace_id: int, chat_id: int) -> dict:
    with _conv().get_session() as db:
        root = _conv()._root(db, workspace_id, chat_id)
        items = [{"turn_id": child.id, "text": record["text"], "at": record.get("at")}
                 for _, child, record in _records(db, [root])]
        return {"chat_id": root.id, "enabled": enabled(root), "records": items[-50:], "count": len(items)}


def forget(workspace_id: int, chat_id: int, *, scope: str = "chat") -> dict:
    """scope "chat": this chat's records; "project": every plan chat of the same project."""
    if scope not in {"chat", "project"}:
        raise ValueError("Forget scope is chat or project.")
    with _conv().get_session() as db:
        root = _conv()._root(db, workspace_id, chat_id)
        roots = [root]
        if scope == "project" and root.project_id:
            roots = db.query(Chat).filter(Chat.parent_id.is_(None), Chat.project_id == root.project_id,
                                          Chat.autonomy_level == "plan").all()
        removed = 0
        for _, child, _record in list(_records(db, roots)):
            params = _params(child)
            params.pop("memory_record", None)
            child.params = json.dumps(params)
            removed += 1
        db.commit()
    return {"removed": removed, **memory_view(workspace_id, chat_id)}
