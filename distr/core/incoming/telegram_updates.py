"""Owner-facing execution updates for proposals explicitly approved in Telegram."""
from __future__ import annotations

import json
import hashlib
import threading

from sqlalchemy import case, func, update

from distr.core.chat import _chat_params
from distr.core.db import Chat, get_session

_lock = threading.RLock()
_KEY = "telegram_proposal_update"
_STAGES = {
    "development": "Work started", "testing": "Running tests", "refinement": "Refining the change",
    "retesting": "Retesting", "verification": "Checking the result", "blocked": "Needs your attention",
    "done": "Completed", "failed": "Work could not be completed",
}


def _write_receipt(db, chat_id, receipt):
    # Execution can advance while Telegram enqueue is in progress. Patch only
    # our JSON member in the local SQLite store rather than replacing a stale
    # copy of all chat parameters (which also holds the execution state).
    params = case((func.json_valid(Chat.params) == 1, Chat.params), else_="{}")
    db.execute(
        update(Chat).where(Chat.id == int(chat_id)).values(
            params=func.json_set(params, f"$.{_KEY}", func.json(json.dumps(receipt)))
        ).execution_options(synchronize_session=False)
    )
    db.commit()


def _manager():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    return getattr(app, "telegram_manager", None) if app else None


def queue_execution_update(ticket, chat_id, execution, status):
    if (ticket.get("source_metadata") or {}).get("approval_source") != "telegram":
        return
    if status not in _STAGES:
        return
    job = str(execution.get("job_id") or execution.get("id") or "run")
    key = f"{job}:{status}"
    header = f"{ticket.get('project_name') or 'Project'}: {ticket.get('reference') or ticket.get('title') or 'Ticket'}"
    message = f"{header}\n{_STAGES[status]}."
    if status in {"done", "failed", "blocked"}:
        packet = execution.get("output_packet") or {}
        public_result = packet.get("human_summary") if status == "done" else ""
        response = str(public_result or packet.get("output") or packet.get("summary") or execution.get("worker_question") or execution.get("summary") or execution.get("error") or "").strip()
        if status == "failed" and str(execution.get("status") or "") in {"completed", "completed_noop"}:
            message += " Final verification evidence is missing. Review the result before retrying."
        if response:
            message += f"\n\n{response}"
    with _lock, get_session() as db:
        chat = db.get(Chat, int(chat_id))
        if chat is None:
            return
        params = _chat_params(chat.params)
        receipt = params.get(_KEY) or {}
        if receipt.get("sent") == key or (receipt.get("pending") or {}).get("key") == key:
            return
        # A later stage supersedes an undelivered intermediate status. A final
        # response remains durable here even after the ticket leaves the live list.
        receipt["pending"] = {"key": key, "text": message, "offset": 0}
        _write_receipt(db, chat_id, receipt)
    flush_execution_updates()


def flush_execution_updates():
    manager = _manager()
    if manager is None or not getattr(manager, "telegram_user_id", None):
        return
    with _lock, get_session() as db:
        rows = db.query(Chat).filter(Chat.parent_id.is_(None), Chat.params.contains(f'"{_KEY}"')).all()
        for chat in rows:
            params = _chat_params(chat.params)
            receipt = params.get(_KEY) or {}
            pending = receipt.get("pending")
            if not pending:
                continue
            text = pending["text"]
            offset = int(pending.get("offset") or 0)
            while offset < len(text):
                # Queue acceptance is explicit. Failed/disconnected enqueue is
                # retried by the normal local state-sync tick, including after restart.
                try:
                    receipt_id = f"proposal-update:{chat.id}:{hashlib.sha256(pending['key'].encode()).hexdigest()[:12]}:{offset}"
                    accepted = manager.send_to_telegram(
                        text[offset:offset + 3500], interaction_token=receipt_id,
                    ) is True
                except Exception:
                    accepted = False
                if not accepted:
                    break
                offset += 3500
                pending["offset"] = offset
                _write_receipt(db, chat.id, receipt)
            if offset >= len(text):
                _write_receipt(db, chat.id, {"sent": pending["key"], "pending": None})
