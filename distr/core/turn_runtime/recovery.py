"""Durable receipts for resuming a native Development turn."""

from hashlib import sha256
import json
from pathlib import Path

from distr.core.db import get_session
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.turn_runtime.contracts import POTENTIALLY_MUTATING_PROJECT_TOOLS


class RecoveryJournal:
    def __init__(self, session_id, project_folder, completed=None):
        self.session_id = session_id
        self.root = Path(project_folder).resolve()
        self.completed = dict(completed or {})

    @classmethod
    def load(cls, session_id, *, scope, turn_id):
        with get_session() as db:
            row = db.get(ProjectExecutionSession, session_id)
            packet = json.loads(row.input_packet or "{}") if row else {}
            if (
                row is None or row.route_type != "native_turn" or row.origin != "development"
                or row.status not in {"queued", "running", "waiting", "interrupted"}
                or not isinstance(packet, dict)
                or (row.project_id, row.ticket_id) != (scope.project_id, scope.ticket_id)
                or packet.get("chat_id") != scope.chat_id or packet.get("turn_id") != turn_id
                or packet.get("board_id") != scope.board_id
                or packet.get("project_folder") != scope.project_folder
                or packet.get("recovery_version") != 1
            ):
                raise ValueError("No matching recoverable native execution was recorded for this turn.")
            events = db.query(ProjectExecutionEvent).filter_by(session_id=session_id).filter(
                ProjectExecutionEvent.event_type.in_(("recovery_seed", "mutation_started", "mutation_finished"))
            ).order_by(ProjectExecutionEvent.id).all()
            completed = {}
            pending = set()
            for event in events:
                receipt = json.loads(event.payload)
                if event.event_type == "recovery_seed":
                    completed.update(receipt)
                elif event.event_type == "mutation_started":
                    if receipt["tool"] not in {"write_file", "replace_text"}:
                        raise ValueError("The interrupted turn used a shell or automation tool. Inspect its effects before restarting.")
                    pending.add(receipt["key"])
                elif receipt["success"]:
                    pending.discard(receipt["key"])
                    completed[receipt["key"]] = receipt["summary"]
            if pending:
                raise ValueError("An interrupted write has no successful receipt. Inspect the project before restarting.")
            return completed

    def _record(self, event_type, payload):
        with get_session() as db:
            if db.get(ProjectExecutionSession, self.session_id) is None:
                raise RuntimeError("The execution session is missing. No write can be safely started.")
            db.add(ProjectExecutionEvent(
                session_id=self.session_id, event_type=event_type, payload=json.dumps(payload),
            ))
            db.commit()

    def seed(self):
        if self.completed:
            self._record("recovery_seed", self.completed)

    def key(self, name, arguments):
        if name not in POTENTIALLY_MUTATING_PROJECT_TOOLS:
            return None
        if name == "automation_control" and str(arguments.get("action") or "get").strip().lower() == "get":
            return None
        if name in {"write_file", "replace_text"}:
            path = str((self.root / str(arguments.get("path") or ".")).resolve())
            if name == "write_file":
                arguments = {"path": path, "content": str(arguments.get("content") or "")}
            else:
                arguments = {
                    "path": path,
                    "old_text": str(arguments.get("old_text") or ""),
                    "new_text": str(arguments.get("new_text") or ""),
                    "expected_replacements": max(1, int(arguments.get("expected_replacements") or 1)),
                }
        payload = json.dumps([name, arguments], sort_keys=True)
        return sha256(payload.encode()).hexdigest()

    def start(self, name, key):
        self._record("mutation_started", {"key": key, "tool": name})

    def finish(self, key, observation):
        # Persist the outcome before forwarding it to the model or UI.
        from distr.core.chat_turns import redact_text

        summary = redact_text(observation.summary, limit=500, preserve_paths=True)
        self._record("mutation_finished", {
            "key": key, "success": observation.status == "success", "summary": summary,
        })
