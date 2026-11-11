"""Read-only execution gate for the explicit dependencies of generated Plan tasks.

The gate grants no execution authority and imposes no source-revision policy.
Dependencies finish at the existing human-accepted Complete lane (including
its legacy aliases), not at a worker's successful exit or the QA output lane.
An accepted dependency may supersede an old failed/cancelled worker attempt;
an execution that is still active cannot be superseded by a stale lane label.
"""
from __future__ import annotations

import hashlib
import json
import re

from distr.core.db import Chat, get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflowRun, DevelopmentWorkItem, PlanWorkspace
from distr.core.kanban.lifecycle import DELIVERY_DONE_LANE, _LEGACY_DELIVERY_LANE_ALIASES

_IDENTITY = re.compile(r'^plan:([1-9][0-9]*):task:(.+)$')


class PlanPrerequisiteError(ValueError):
    """A generated task is not ready for execution; normal work is untouched."""


def execution_ticket_id(ticket_id=None, *, chat_id=None, run_id=None, scope_ticket_ids=(), session=None):
    """Resolve durable ownership and reject mismatched generated-task scope."""
    def ident(value):
        if (type(value) is not int and not (isinstance(value, str) and value.isascii() and value.isdecimal())) or int(value) <= 0:
            raise PlanPrerequisiteError('The execution ticket scope is invalid. Review its thread/workflow assignment.')
        return int(value)
    explicit = ident(ticket_id) if ticket_id is not None else None
    scoped_ids = [ident(value) for value in scope_ticket_ids if value is not None]
    if chat_id is None and run_id is None and not scoped_ids:
        return explicit
    if session is None:
        with get_session() as db:
            return execution_ticket_id(explicit, chat_id=chat_id, run_id=run_id, scope_ticket_ids=scoped_ids, session=db)
    owners = list(scoped_ids)
    chats = [chat_id] if chat_id is not None else []
    if run_id is not None:
        run = session.get(AutoWorkflowRun, int(run_id))
        if run is not None:
            try:
                metadata = json.loads(run.run_data or '{}')
                scoped_ticket = run.ticket_id if run.ticket_id is not None else metadata.get('ticket_id')
                if scoped_ticket is not None:
                    owners.append(int(scoped_ticket))
                if metadata.get('chat_id') is not None:
                    chats.append(metadata['chat_id'])
            except (ValueError, TypeError, AttributeError):
                raise PlanPrerequisiteError('The workflow ticket scope is invalid. Review its execution context.')
    for scoped_chat in set(chats):
        chat = session.get(Chat, int(scoped_chat))
        if chat is None:
            continue
        root_id = chat.parent_id or chat.id
        item = session.query(DevelopmentWorkItem).filter_by(chat_id=root_id).first()
        if item is not None and item.local_ticket_id is not None:
            owners.append(int(item.local_ticket_id))
            continue
        root = session.get(Chat, root_id)
        try:
            metadata = (json.loads(root.params or '{}').get('development') or {}) if root else {}
            if metadata.get('ticket_id') is not None:
                owners.append(int(metadata['ticket_id']))
        except (ValueError, TypeError, AttributeError):
            raise PlanPrerequisiteError('The Development ticket scope is invalid. Review its thread context.')
    identities = set(owners + ([explicit] if explicit is not None else []))
    if len(identities) > 1:
        rows = session.query(KanbanTicket).filter(KanbanTicket.id.in_(identities)).all()
        if any(row.source_provider == 'plan' and _IDENTITY.fullmatch(str(row.source_external_id or '')) for row in rows):
            raise PlanPrerequisiteError('The generated Plan task does not match its thread/workflow ticket scope. Review the assignment.')
    return explicit if explicit is not None else (owners[0] if owners else None)


def require_plan_prerequisites(ticket_id=None, *, project_id=None, board_id=None, chat_id=None, run_id=None, session=None):
    """Validate a generated task's retained dependency contract before dispatch.

    Returns None for unscoped work and ordinary/legacy artifact tickets. Raises
    PlanPrerequisiteError for broken generated contracts or incomplete sources.
    Callers may pass their existing read transaction to avoid nested sessions.
    """
    if ticket_id is None and chat_id is None and run_id is None:
        return
    if session is None:
        with get_session() as db:
            return require_plan_prerequisites(ticket_id, project_id=project_id, board_id=board_id,
                                               chat_id=chat_id, run_id=run_id, session=db)
    db = session
    ticket_id = execution_ticket_id(ticket_id, chat_id=chat_id, run_id=run_id, session=db)
    if ticket_id is None:
        return
    ticket = db.get(KanbanTicket, int(ticket_id))
    if ticket is None or ticket.source_provider != 'plan':
        return
    identity = _IDENTITY.fullmatch(str(ticket.source_external_id or ''))
    if identity is None:
        return  # Existing one-artifact approvals use a different stable identity.
    workspace_id, task_key = int(identity.group(1)), identity.group(2)

    def reject(reason):
        raise PlanPrerequisiteError(f'Plan task #{ticket.id} cannot start: {reason}')

    try:
        notes = json.loads(ticket.context_notes or '{}')
        contract = notes['plan_build']
        task = contract['task']
        digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
        if ((notes.get('original_digest') is not None and notes['original_digest'] != digest) or type(contract.get('version')) is not int
                or contract['version'] != 1 or type(contract.get('workspace_id')) is not int
                or contract['workspace_id'] != workspace_id
                or task.get('key') != task_key):
            reject('its generated dependency contract changed or is unsupported. Review/rebuild this task.')
        dependencies, dependency_keys = contract['depends_on'], task['depends_on']
        if (not isinstance(dependencies, list) or not isinstance(dependency_keys, list)
                or len(dependencies) > 200 or len(dependency_keys) > 200
                or len(dependencies) != len(set(dependencies))
                or len(dependency_keys) != len(set(dependency_keys))
                or len(dependencies) != len(dependency_keys)
                or any(type(value) is not int or value <= 0 or value == ticket.id for value in dependencies)
                or any(not isinstance(key, str) or not key or len(key) > 128 for key in dependency_keys)):
            reject('its dependency references are invalid. Review/rebuild this task.')
        expected_project = contract['project_id']
        if type(expected_project) is not int or expected_project <= 0:
            reject('its project identity is unavailable.')
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, PlanPrerequisiteError):
            raise
        reject('its generated dependency contract is missing or invalid. Review/rebuild this task.')

    workspace = db.get(PlanWorkspace, workspace_id)
    lane = db.get(KanbanLane, ticket.lane_id)
    board = db.get(KanbanBoard, lane.board_id) if lane else None
    project = db.get(Project, expected_project)
    effective_project = ticket.linked_project_id if ticket.linked_project_id is not None else (board.default_project_id if board else None)
    if (workspace is None or workspace.project_id != expected_project or project is None or project.deleted_at is not None
            or board is None or board.archived or board.default_project_id != expected_project
            or workspace.board_key != f'decisions:{board.id}' or workspace.board_provider != 'decisions'
            or effective_project != expected_project
            or (project_id is not None and str(project_id) != str(expected_project))
            or (board_id is not None and str(board_id) != str(board.id))):
        reject('its project or board no longer matches the plan. Review the assignment.')

    accepted = {DELIVERY_DONE_LANE.casefold(), *[name.casefold() for name in _LEGACY_DELIVERY_LANE_ALIASES[DELIVERY_DONE_LANE]]}
    if not dependencies:
        return
    expected_identities = {f'plan:{workspace_id}:task:{key}' for key in dependency_keys}
    found_identities = set()
    blockers = []
    dependency_rows = {row.id:(row, dep_lane) for row, dep_lane in db.query(KanbanTicket, KanbanLane).outerjoin(
        KanbanLane, KanbanLane.id == KanbanTicket.lane_id).filter(KanbanTicket.id.in_(dependencies)).all()} if dependencies else {}
    active_ids = {ident for (ident,) in db.query(ProjectExecutionSession.ticket_id).filter(
        ProjectExecutionSession.ticket_id.in_(dependencies),
        ProjectExecutionSession.status.in_(['initializing', 'queued', 'running', 'waiting'])).distinct()} if dependencies else set()
    active_ids.update(ident for (ident,) in db.query(AutoWorkflowRun.ticket_id).filter(
        AutoWorkflowRun.ticket_id.in_(dependencies), AutoWorkflowRun.status.in_(['initializing', 'queued', 'running', 'waiting'])).distinct())
    # Canonical native ownership is DevelopmentWorkItem; its execution state is
    # persisted at Chat.params.development.execution (development_harness.py).
    native_roots = db.query(DevelopmentWorkItem.local_ticket_id, Chat.params).join(
        Chat, Chat.id == DevelopmentWorkItem.chat_id).filter(
        DevelopmentWorkItem.local_ticket_id.in_(dependencies)).all()
    owned_ids = {ident for ident, _ in native_roots}
    legacy_chat_ids = {row.source_chat_id:row.id for row, _ in dependency_rows.values()
                       if row.id not in owned_ids and row.source_chat_id is not None}
    for root in db.query(Chat).filter(Chat.id.in_(legacy_chat_ids)).all() if legacy_chat_ids else []:
        try:
            metadata = json.loads(root.params or '{}').get('development') or {}
            if str(metadata.get('ticket_id')) == str(legacy_chat_ids[root.id]):
                native_roots.append((legacy_chat_ids[root.id], root.params))
        except (ValueError, TypeError, AttributeError):
            blockers.append(f'#{legacy_chat_ids[root.id]} has unreadable execution ownership')
    for ident, params in native_roots:
        try:
            execution = (json.loads(params or '{}').get('development') or {}).get('execution') or {}
            if str(execution.get('status') or '').lower() in {'initializing', 'queued', 'running', 'waiting'}:
                active_ids.add(ident)
        except (ValueError, TypeError, AttributeError):
            blockers.append(f'#{ident} has unreadable execution state')
    for ident in dependencies:
        dependency, dep_lane = dependency_rows.get(ident, (None, None))
        dep_project = (dependency.linked_project_id if dependency and dependency.linked_project_id is not None
                       else board.default_project_id)
        if (dependency is None or dep_lane is None or dep_lane.board_id != board.id
                or dep_project != expected_project or dependency.source_provider != 'plan'
                or dependency.source_external_id not in expected_identities):
            blockers.append(f'#{ident} is missing or belongs to another plan/project/board')
            continue
        found_identities.add(dependency.source_external_id)
        active = str(dependency.workflow_status or '').lower() in {'queued', 'running', 'waiting'} or ident in active_ids
        if active:
            blockers.append(f'#{ident} still has active work')
        elif str(dep_lane.name or '').strip().casefold() not in accepted:
            blockers.append(f'#{ident} has not reached accepted {DELIVERY_DONE_LANE}')
    if blockers or found_identities != expected_identities:
        reject('prerequisites are incomplete: ' + '; '.join(blockers or ['dependency identities do not match the plan']))
