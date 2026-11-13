"""Editable queued submissions and their explicitly attached files."""
import json
import logging
import time
from uuid import uuid4
from pathlib import Path

from distr.core.chat_turns import redact_text
from distr.core.db import Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflowRun, DevelopmentCommand
from distr.core.db.time import utc_now_naive

logger = logging.getLogger(__name__)


def submission_metadata(chat_id, metadata):
    from . import development_control as control
    from .development_harness import _safe_turn_attachments
    raw_metadata = dict(metadata or {})
    result = control.redact_metadata({key: value for key, value in raw_metadata.items() if key not in {'composer', 'attachments'}})
    with control.get_session() as db:
        chat = control._require_development_root(db, chat_id)
        project = db.get(Project, chat.project_id) if chat.project_id else None
        folder = project.folder_location if project else ''
    raw_files = raw_metadata.get('attachments') or []
    files = _safe_turn_attachments(raw_files, project_folder=folder or '')
    if len(files) != len(raw_files):
        raise ValueError('An attached file is missing or outside this project. Reattach it before submitting.')
    result['attachments'] = files
    draft = raw_metadata.get('composer')
    if isinstance(draft, dict):
        items = []
        for raw in (draft.get('attachments') or [])[:80]:
            if not isinstance(raw, dict):
                continue
            item = {key: raw[key] for key in ('kind', 'key', 'label', 'reference', 'mime_type', 'size', 'source', 'sourceLabel', 'skill_id', 'suppress_prompt_context', 'hidden_chip') if key in raw}
            item['text'] = redact_text(raw.get('text') or '', limit=48000, preserve_paths=True)
            if raw.get('transcript'):
                item['transcript'] = redact_text(raw['transcript'], limit=48000, preserve_paths=True)
            if item.get('kind') == 'file' and not any(row['path'] == str(Path(str(item.get('reference') or '')).expanduser().resolve()) for row in files):
                raise ValueError('The composer file does not match an attached file.')
            items.append(item)
        result['composer'] = {'text': redact_text(draft.get('text') or '', limit=48000, preserve_paths=True),
                              'attachments': items, 'use_playwright': bool(draft.get('use_playwright'))}
    return result


def command_file(command_id, index):
    from . import development_control as control
    command = control.list_commands_for_id(command_id)
    if not command:
        raise FileNotFoundError('Queued item not found.')
    metadata = submission_metadata(command['chat_id'], command['metadata'])
    files = metadata['attachments']
    if index < 0 or index >= len(files):
        raise FileNotFoundError('Attachment not found.')
    return files[index]


def record_submission_message(chat_id, command_id, content):
    """A retry starts the same queued turn without duplicating its user message."""
    from . import development_control as control
    from distr.core.chat import ChatService
    with control.get_session() as db:
        messages = db.query(Chat).filter(Chat.parent_id == chat_id).all()
        if any(control._json(row.params, {}).get('development_turn', {}).get('queued_command_id') == command_id for row in messages):
            return
    ChatService.add_user_message(chat_id, content, metadata={'queued_command_id': command_id})


def start_next_followup(chat_id: int, command_id: int, revision: str, *, after_completion=None) -> dict:
    """Start the queue head only when the Development thread can accept a new turn."""
    from . import development_control as control
    from . import development_harness as harness

    with control.get_session() as db:
        root = control._require_development_root(db, chat_id)
        command = db.get(DevelopmentCommand, command_id)
        if command is None or command.chat_id != chat_id or command.command_type != "followup":
            raise LookupError("Queued follow-up not found.")
        if command.status == "delivered":
            target = control._json(command.metadata_json, {}).get("started_chat_id") or chat_id
            return {"chat_id": target, "command_id": command_id, "already_started": True}
        if command.status == "dispatching":
            execution = control._json(root.params, {}).get("development", {}).get("execution") or {}
            claim = execution.get("followup_start")
            if isinstance(claim, dict) and claim.get("command_id") == command_id:
                return {"chat_id": chat_id, "command_id": command_id, "turn_id": execution.get("turn_id"), "starting": True}
        if command.status != "queued" or command.modified_at.isoformat() != revision:
            raise ValueError("The queued item changed. Refresh the queue.")
        harness._require_saved_followup_scope(command, root)
        head = control._ordered_pending_commands(db, root).first()
        if head is None or head.id != command.id:
            raise ValueError("The next queued item changed. Refresh the queue.")
        development = control._json(root.params, {}).get("development") or {}
        if (development.get("workflow_id") or development.get("proactive_dispatch")
                or str(root.autonomy_level or "").lower() == "approval"):
            raise ValueError("This thread needs the desktop workflow or approval controls. The item remains queued.")
        content, metadata = command.content, control._json(command.metadata_json, {})
    if metadata.get("retry_new_thread") is True:
        if after_completion is not None:
            raise ValueError("This follow-up needs a new thread. It remains queued for review.")
        return {"command_id": command_id, **run_submission(command_id, new_thread=True, revision=revision)}
    metadata = submission_metadata(chat_id, metadata)
    execution = harness.dispatch_development_prompt(
        chat_id, content, save_prompt=True, attachments=metadata["attachments"],
        skill_ids=metadata.get("skill_ids") or [], use_playwright=bool(metadata.get("composer", {}).get("use_playwright")),
        queued_followup={"next_followup": True, "command_id": command_id, "modified_at": revision,
                         **({"after_completion": after_completion} if after_completion is not None else {})},
    )
    receipt = {"chat_id": chat_id, "command_id": command_id, "turn_id": execution.get("turn_id")}
    with control.get_session() as db:
        command = db.get(DevelopmentCommand, command_id)
        if command and command.status == "delivered":
            return {**receipt, "started": True}
        if command and command.status == "queued" and control._json(command.metadata_json, {}).get("review_after_startup_failure"):
            raise ValueError(command.result_summary)
    return {**receipt, "starting": True}


def recover_completed_followup(chat_id: int) -> None:
    """Resume only the queue item saved by this direct turn's completion."""
    from . import development_control as control, development_harness as harness
    from distr.core.chat_turns import complete_turn

    with control.get_session() as db:
        root = db.get(Chat, chat_id)
        if root is None or root.parent_id is not None or root.is_archived:
            return
        development = control._json(root.params, {}).get("development") or {}
        execution = development.get("execution") or {}
        completion = execution.get("followup_completion_pending")
        if (not isinstance(completion, dict) or type(completion.get("command_id")) is not int
                or not isinstance(completion.get("modified_at"), str)
                or not harness._completed_followup_matches(db, root, execution, completion)
                or not harness._direct_record_owns_scope(db, root, development, execution)):
            return
        turn = db.get(Chat, completion["turn_id"])
        project_id, response = root.project_id, turn.response if turn is not None else None
    if not response:
        return
    event = complete_turn(chat_id, turn_id=completion["turn_id"], display_text=response,
                          expected_root_scope=(chat_id, project_id))
    if event is None or event["status"] != "completed":
        return
    try:
        start_next_followup(chat_id, completion["command_id"], completion["modified_at"], after_completion=completion)
    except (ValueError, LookupError):
        # Scope or queue changes require the existing explicit review controls.
        return


def recover_completed_followups() -> None:
    from sqlalchemy import case, func
    from . import development_control as control

    with control.get_session() as db:
        data = case((func.json_valid(Chat.params) == 1, Chat.params), else_="{}")
        chats = db.query(Chat.id).filter(
            Chat.parent_id.is_(None),
            func.json_type(data, "$.development.execution.followup_completion_pending") == "object",
        ).all()
    for (chat_id,) in chats:
        try:
            recover_completed_followup(chat_id)
        except Exception:
            logger.exception("Could not recover the queued follow-up for thread %s", chat_id)


def _manual_start_matches(command, target, token):
    if command is None or command.status != "dispatching":
        return False
    from . import development_control as control

    metadata = control._json(command.metadata_json, {})
    claim = metadata.get("manual_start")
    return isinstance(claim, dict) and claim.get("token") == token and (
        metadata.get("started_chat_id") if claim.get("new_thread") else command.chat_id
    ) == target.id


def _require_manual_source(db, command, claim):
    from . import development_control as control, development_harness as harness

    source = control._require_development_root(db, command.chat_id)
    harness._require_direct_dispatch_scope(db, source, tuple(claim["scope"]), claim["board_id"])
    harness._require_saved_followup_scope(command, source)
    return source


def _release_manual_start(command):
    from . import development_control as control

    metadata = control._json(command.metadata_json, {})
    claim = metadata.pop("manual_start")
    metadata.update(review_after_startup_failure=True, retry_new_thread=claim["new_thread"])
    command.metadata_json = json.dumps(metadata, ensure_ascii=False)
    command.status = "queued"
    command.delivered_at = None
    command.result_summary = "Startup stopped. The follow-up remains queued for review."


def recover_manual_starts(db, chat_id):
    from . import development_control as control, development_harness as harness

    pending = db.query(DevelopmentCommand).filter_by(chat_id=chat_id, status="dispatching")

    def interrupted(row):
        metadata = control._json(row.metadata_json, {})
        claim = metadata.get("manual_start") if isinstance(metadata, dict) else None
        return isinstance(claim, dict) and claim.get("process") != harness._FOLLOWUP_STARTUP_PROCESS

    if not any(interrupted(row) for row in pending):
        return
    if db.get_bind().dialect.name == "sqlite":
        db.rollback()
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    for row in pending.populate_existing():
        if interrupted(row):
            _release_manual_start(row)
    db.commit()


_UNREVIEWED_STOP = object()
_STOP_FIELDS = {
    "execution_session_id": "expected_session_id",
    "workflow_run_id": "expected_workflow_run_id",
    "turn_id": "expected_turn_id",
}


def _steer_run_active(state, harness):
    return state.get("status") in harness.ACTIVE_EXECUTION_STATUSES | {"paused"} and not state.get("completed_at")


def _steer_stop_options(chat_id, expected_stop, active_workflows):
    from . import development_harness as harness
    from distr.core.turn_runtime import active_turn_registered

    state = harness.development_execution_state(chat_id)
    target = harness.exact_development_stop_target(state)
    if expected_stop is not _UNREVIEWED_STOP:
        if expected_stop is not None and (
            not isinstance(expected_stop, dict) or len(expected_stop) != 1
            or any(key not in _STOP_FIELDS or type(value) is not int or value <= 0
                   for key, value in expected_stop.items())
        ):
            raise ValueError("Choose a valid run before steering. The item remains queued.")
        if expected_stop != target:
            raise ValueError("The run changed. Review the current run before steering. The item remains queued.")
    if active_workflows and (target is None or active_workflows != [target.get("workflow_run_id")]):
        raise ValueError("The active workflow could not be identified for Stop. The item remains queued.")
    if target:
        return {_STOP_FIELDS[key]: value for key, value in target.items()}
    if _steer_run_active(state, harness) or active_turn_registered(chat_id):
        raise ValueError("The active worker could not be identified for Stop. The item remains queued.")
    return {}


def _wait_for_steer_stop(chat_id, options):
    from . import development_harness as harness
    from distr.core.turn_runtime import active_turn_registered

    field, expected = next(iter(options.items()))
    state_field = {
        "expected_session_id": "execution_session_id",
        "expected_workflow_run_id": "id",
        "expected_turn_id": "turn_id",
    }[field]
    deadline = time.monotonic() + 10
    while True:
        state = harness.development_execution_state(chat_id)
        active = _steer_run_active(state, harness)
        if active and (state.get(state_field) != expected or (state.get("direct") is False) != (field == "expected_workflow_run_id")):
            raise ValueError("A replacement run started. Review it before steering. The item remains queued.")
        if not active and not active_turn_registered(chat_id):
            return
        if time.monotonic() >= deadline:
            raise ValueError("The worker is still stopping. Your item remains queued; try Steer again shortly.")
        time.sleep(.05)


def run_submission(command_id, *, new_thread=False, revision=None, expected_stop=_UNREVIEWED_STOP):
    """Reserve the command while stopping or preparing a thread, then claim the exact startup."""
    from . import development_control as control, development_harness as harness
    from .development_threads import development_scope_identity, development_thread_metadata, ensure_development_thread
    from distr.core.turn_runtime import active_turn_registered

    with control.get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(DevelopmentCommand, command_id)
        if row is None:
            raise LookupError("Queued item not found.")
        metadata = control._json(row.metadata_json, {})
        if not isinstance(metadata, dict):
            metadata = {}
        if row.status == "delivered":
            return {"chat_id": metadata.get("started_chat_id") or row.chat_id, "already_started": True}
        if row.status == "dispatching":
            claim = metadata.get("manual_start")
            if isinstance(claim, dict) and claim.get("new_thread") == new_thread:
                if claim.get("process") != harness._FOLLOWUP_STARTUP_PROCESS:
                    _release_manual_start(row)
                    db.commit()
                    raise ValueError("Startup stopped. Review the queued item and retry.")
                target = metadata.get("started_chat_id") if new_thread else row.chat_id
                return {"chat_id": target, "starting": True}
        if row.status != "queued" or (revision is not None and row.modified_at.isoformat() != revision):
            raise ValueError("This item is being edited or has already changed. Refresh the queue.")
        chat_id, content = row.chat_id, row.content
        source = control._require_development_root(db, chat_id)
        source_settings = development_thread_metadata(source)
        if not new_thread and source_settings.get("proactive_dispatch"):
            raise ValueError("This item stays queued while the approved work is unresolved.")
        harness._require_saved_followup_scope(row, source)
        scope = development_scope_identity(source.project_id, source_settings, chat_id=chat_id)
        board = harness._direct_scope_board(db, source, source_settings)
        claim = {"token": uuid4().hex, "process": harness._FOLLOWUP_STARTUP_PROCESS,
                 "scope": list(scope), "board_id": board.id if board else None, "new_thread": new_thread}
        if metadata.get("started_chat_id") and (
            not new_thread or metadata.get("started_from_scope", list(scope)) != list(scope)
        ):
            raise ValueError("This item already has a reserved new thread. Review its project before retrying.")
        row.status = "dispatching"
        row.result_summary = "Preparing to start."
        row.metadata_json = json.dumps({**metadata, "manual_start": claim}, ensure_ascii=False)
        project_id = source.project_id
        preferences = {key: getattr(source, key) for key in ("route_mode", "execution_profile", "autonomy_level", "provider", "model_name")}
        inherited_settings = {key: source_settings[key] for key in ("model_route", "permission_profile") if key in source_settings}
        active_workflows = [run_id for (run_id,) in db.query(AutoWorkflowRun.id).filter_by(chat_id=chat_id).filter(
            AutoWorkflowRun.status.in_(["initializing", "queued", "running", "waiting", "paused"]),
        )] if not new_thread else []
        db.commit()
    try:
        stop_options = {} if new_thread else _steer_stop_options(chat_id, expected_stop, active_workflows)
        metadata = submission_metadata(chat_id, metadata)
        target = chat_id
        if new_thread:
            target = ensure_development_thread(
                title=content.split("\n")[0][:120], project_id=project_id,
                source_type="queued_instruction", source_ref=str(command_id), _session_provider=control.get_session,
            )
            with control.get_session() as db:
                if db.get_bind().dialect.name == "sqlite":
                    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                row = db.get(DevelopmentCommand, command_id)
                if row is None or row.status != "dispatching" or control._json(row.metadata_json, {}).get("manual_start") != claim:
                    raise ValueError("The queued item changed before startup.")
                _require_manual_source(db, row, claim)
                fork = db.get(Chat, target)
                fork_settings = development_thread_metadata(fork)
                if (metadata.get("started_chat_id") not in (None, target) or fork.project_id != project_id
                        or fork_settings.get("source_type") != "queued_instruction"
                        or fork_settings.get("source_ref") != str(command_id)):
                    raise ValueError("The reserved new thread changed. The item remains queued.")
                if not metadata.get("started_chat_id"):
                    for key, value in preferences.items():
                        setattr(fork, key, value)
                    params = control._json(fork.params, {})
                    params["development"] = {**fork_settings, **inherited_settings}
                    fork.params = json.dumps(params)
                row.metadata_json = json.dumps({**metadata, "manual_start": claim,
                    "started_chat_id": target, "started_from_scope": list(scope)}, ensure_ascii=False)
                db.commit()
        elif stop_options:
            with control.get_session() as db:
                _require_manual_source(db, db.get(DevelopmentCommand, command_id), claim)
            result = harness.stop_development_execution(chat_id, **stop_options)
            if not (result.get("stop_requested") or result.get("stopped")):
                raise ValueError("The run changed or could not be stopped. Review the current run. The item remains queued.")
            _wait_for_steer_stop(chat_id, stop_options)
        if _steer_run_active(harness.development_execution_state(target), harness) or active_turn_registered(target):
            raise ValueError("A worker is still active. Your item remains queued.")
        execution = harness.dispatch_development_prompt(
            target, content, save_prompt=True, attachments=metadata["attachments"],
            skill_ids=metadata.get("skill_ids") or [], use_playwright=bool(metadata.get("composer", {}).get("use_playwright")),
            queued_followup={"command_id": command_id, "manual_token": claim["token"]},
        )
        with control.get_session() as db:
            row = db.get(DevelopmentCommand, command_id)
            if row.status == "queued":
                raise ValueError(row.result_summary)
            return {"chat_id": target, "turn_id": execution.get("turn_id"),
                    "started" if row.status == "delivered" else "starting": True}
    except Exception:
        with control.get_session() as db:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = db.get(DevelopmentCommand, command_id)
            if row is not None and row.status == "dispatching":
                current = control._json(row.metadata_json, {}).get("manual_start") or {}
                if current.get("token") == claim["token"]:
                    _release_manual_start(row)
                    db.commit()
        raise
