"""Retain project history while removing its active delivery scope.

The remote job UUID is a durable receipt: committing locally always precedes
acknowledging remotely, so an offline acknowledgement can be replayed safely.
"""
from __future__ import annotations

import json
import os
import threading
import time
from uuid import UUID, uuid4

from sqlalchemy import or_
from sqlalchemy.orm import aliased

from distr.core.db import Base, Chat, ChatTurnEvent, get_session
from distr.core.db.projects import Project, ProjectRemoval
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketAuditEntry, ProjectExecutionSession
from distr.core.db.schedule_blocks import ScheduleBlock
from distr.core.db.workflow import AutoWorkflowRun, DevelopmentWorkItem
from distr.core.db.orchestrator import ProjectRuntimeSession
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.time import utc_now_naive
from distr.core.tensology_client import configured_tensology_client, TensologyApiError

POLL_SECONDS = 60
_last_attempt = float('-inf')
_lock = threading.Lock()


class RemovalBlocked(ValueError):
    pass


class ProjectBusy(RemovalBlocked):
    pass


def normalized_folder(value):
    value = str(value or '').strip()
    if not value:
        return ''
    if not os.path.isabs(value):
        raise RemovalBlocked('Project source folder must be absolute')
    return os.path.normpath(value)


def _busy(db, project_id, board_ids, ticket_ids):
    from distr.core.project_cli_backends.live_sessions import any_live_session_running
    from distr.core.terminals.thread_terminal import project_terminal_sessions

    if any_live_session_running(project_id) or project_terminal_sessions(project_id):
        return True
    active = ('queued', 'running', 'waiting', 'pending', 'paused')
    if db.query(ProjectExecutionSession.id).filter(or_(ProjectExecutionSession.project_id == project_id, ProjectExecutionSession.ticket_id.in_(ticket_ids)), ProjectExecutionSession.status.in_(active)).first():
        return True
    if db.query(ProjectRuntimeSession.id).filter(ProjectRuntimeSession.project_id == project_id, ProjectRuntimeSession.status.in_(active)).first():
        return True
    if db.query(DevelopmentWorkItem.id).filter(or_(DevelopmentWorkItem.project_id == project_id, DevelopmentWorkItem.local_ticket_id.in_(ticket_ids)), DevelopmentWorkItem.time_started_at.is_not(None)).first():
        return True
    if db.query(ScheduleBlock.id).filter(ScheduleBlock.is_timer_running.is_(True), or_(ScheduleBlock.project_id == project_id, ScheduleBlock.board_id.in_(board_ids), ScheduleBlock.ticket_id.in_(ticket_ids))).first():
        return True
    chat_ids = db.query(Chat.id).filter(Chat.project_id == project_id)
    terminal = aliased(ChatTurnEvent)
    completed_turn = db.query(terminal.id).filter(
        terminal.chat_id == ChatTurnEvent.chat_id,
        terminal.turn_id == ChatTurnEvent.turn_id,
        terminal.event_type.in_(('turn_completed', 'turn_failed', 'turn_cancelled')),
    ).exists()
    if db.query(ChatTurnEvent.id).filter(ChatTurnEvent.chat_id.in_(chat_ids),
                                       ChatTurnEvent.status.in_(active), ~completed_turn).first():
        return True
    if db.query(AutoWorkflowRun.id).filter(or_(AutoWorkflowRun.board_id.in_(board_ids), AutoWorkflowRun.ticket_id.in_(ticket_ids)), AutoWorkflowRun.status.in_(active)).first():
        return True
    automation_ids = db.query(Automation.id).filter(or_(Automation.project_id == project_id, Automation.board_id.in_(board_ids)))
    return bool(db.query(AutomationRun.id).filter(AutomationRun.automation_id.in_(automation_ids), AutomationRun.status.in_(active)).first())


def remove_project(db, project_id, *, removal_id=None, source_name=None, local_folder_path=None):
    """Apply locally in the caller's transaction. Never remove files or history."""
    project = db.get(Project, int(project_id))
    if project is None:
        raise RemovalBlocked('Project source ID no longer exists')
    if removal_id is None and project.deleted_at is not None:
        receipt = db.query(ProjectRemoval).filter(ProjectRemoval.project_id == project.id).order_by(ProjectRemoval.completed_at.desc()).first()
        if receipt is not None:
            return receipt
    request_id = str(UUID(str(removal_id))) if removal_id else str(uuid4())
    name = project.name if source_name is None else source_name
    folder = normalized_folder(project.folder_location if local_folder_path is None else local_folder_path)
    receipt = db.get(ProjectRemoval, request_id)
    if receipt is not None:
        if receipt.project_id != project.id or receipt.source_name != name or receipt.local_folder_path != folder:
            raise RemovalBlocked('Removal UUID does not match this project source')
        return receipt
    if name != project.name or folder != normalized_folder(project.folder_location):
        raise RemovalBlocked('Project source name or folder changed; review the removal request')
    if project.deleted_at is not None:
        # Another delivery of the same source can acknowledge an already removed project.
        receipt = ProjectRemoval(id=request_id, project_id=project.id, source_name=name, local_folder_path=folder)
        db.add(receipt)
        db.flush()
        return receipt
    boards = db.query(KanbanBoard).filter(or_(KanbanBoard.default_project_id == project.id, KanbanBoard.id == project.kanban_board_id)).all()
    board_ids = [b.id for b in boards]
    # A stale explicit link must never delete another project's board.
    if any(b.default_project_id not in (None, project.id) for b in boards):
        raise RemovalBlocked('Project board belongs to another project')
    if board_ids and db.query(Project.id).filter(Project.id != project.id, Project.kanban_board_id.in_(board_ids)).first():
        raise RemovalBlocked('Project board is shared with another project')
    lanes = db.query(KanbanLane.id).filter(KanbanLane.board_id.in_(board_ids))
    tickets = db.query(KanbanTicket).filter(or_(KanbanTicket.lane_id.in_(lanes), KanbanTicket.linked_project_id == project.id)).all()
    ticket_ids = [t.id for t in tickets]
    if _busy(db, project.id, board_ids, ticket_ids):
        raise ProjectBusy('Project has an active execution, terminal or timer; removal deferred')
    automations = db.query(Automation).filter(or_(Automation.project_id == project.id, Automation.board_id.in_(board_ids))).all()
    audits = db.query(KanbanTicketAuditEntry).filter(KanbanTicketAuditEntry.ticket_id.in_(ticket_ids)).all()
    def values(row):
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}
    snapshot = {'boards': [values(b) for b in boards],
                'tickets': [{**values(t), 'files': [values(f) for f in t.files],
                             'links': [values(link) for link in t.links],
                             'todos': [values(todo) for todo in t.todos]} for t in tickets],
                'legacy_columns': [{**values(c), 'tickets': [values(t) for t in c.tickets]} for c in project.board_columns],
                'ticket_audits': [{c.name: getattr(a, c.name) for c in a.__table__.columns} for a in audits]}
    receipt = ProjectRemoval(id=request_id, project_id=project.id, source_name=name, local_folder_path=folder,
                             audit_snapshot=json.dumps(snapshot, default=str))
    db.add(receipt)
    for audit in audits:
        db.delete(audit)
    db.flush()
    # History tables keep their rows and project identity; only invalid delivery FKs clear.
    for mapper in tuple(Base.registry.mappers):
        model = mapper.class_
        for column in mapper.local_table.columns:
            targets = {fk.target_fullname for fk in column.foreign_keys}
            ids = ticket_ids if 'kanban_tickets.id' in targets else board_ids if 'kanban_boards.id' in targets else None
            if ids and column.nullable:
                # Detaching delivery identity is not new historical activity.
                # Explicit self-assignments suppress SQLAlchemy's client onupdate
                # timestamps (some reports use execution.updated_at as work end).
                updates = {c: c for c in mapper.local_table.columns
                           if c.onupdate is not None or c.server_onupdate is not None}
                updates[column] = None
                db.query(model).filter(column.in_(ids)).update(updates, synchronize_session='fetch')
    for automation in automations:
        automation.status = 'paused'
    for ticket in tickets:
        db.delete(ticket)
    db.flush()
    for board in boards:
        db.delete(board)
    # Retire the older per-project board too, without touching context or files.
    project.board_columns.clear()
    project.kanban_board_id = None
    project.in_use = False
    project.deleted_at = utc_now_naive()
    db.flush()
    return receipt


def process_project_removals(*, force=False, session_factory=None):
    global _last_attempt
    if not _lock.acquire(blocking=False):
        return {'deferred': True}
    try:
        now = time.monotonic()
        if not force and now - _last_attempt < POLL_SECONDS:
            return {'throttled': True}
        _last_attempt = now
        try:
            client = configured_tensology_client(source='decisions-project-removals')
            data = client.get('projects/removals')
        except TensologyApiError as exc:
            return {'error': str(exc)}
        if not isinstance(data, dict) or not isinstance(data.get('removals'), list):
            return {'error': 'Invalid project removal response'}
        results = []
        for job in data['removals'][:100]:
            try:
                request_id = str(UUID(str(job['id'])))
                project_id = job['decisions_project_id']
                if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
                    raise ValueError('Invalid project source ID')
                if not isinstance(job['source_name'], str) or not isinstance(job['local_folder_path'], str):
                    raise ValueError('Invalid project source identity')
            except (KeyError, TypeError, ValueError):
                results.append({'status': 'invalid'})
                continue
            status, error = 'completed', ''
            try:
                with (session_factory or get_session)() as db:
                    remove_project(db, project_id, removal_id=request_id, source_name=job['source_name'], local_folder_path=job['local_folder_path'])
                    db.commit()
            except ProjectBusy:
                results.append({'id': request_id, 'status': 'deferred'})
                continue
            except RemovalBlocked as exc:
                status, error = 'blocked', str(exc)
            except Exception:
                # Database/transport failures are retryable, never terminally blocked.
                results.append({'id': request_id, 'status': 'retry'})
                continue
            try:
                client.post(f'projects/removals/{request_id}/ack', {'status': status, 'error': error},
                            idempotency_key=f'project-removal-{request_id}-{status}')
                if status == 'completed':
                    with (session_factory or get_session)() as db:
                        receipt = db.get(ProjectRemoval, request_id)
                        receipt.acknowledged_at = utc_now_naive()
                        db.commit()
                    from distr.core.tensology_project_sync import invalidate_project_sync
                    invalidate_project_sync()
                results.append({'id': request_id, 'status': status})
            except Exception:
                results.append({'id': request_id, 'status': 'retry_ack'})
        return {'removals': results}
    finally:
        _lock.release()
