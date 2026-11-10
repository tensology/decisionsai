"""Keep every local project discoverable in Tensology without changing portal billing."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from distr.core.tensology_client import TensologyApiError, configured_tensology_client

logger = logging.getLogger(__name__)
SYNC_SECONDS = 60
_last_attempt = float('-inf')
_sync_lock = threading.Lock()
_last_result = {'projects': [], 'conflicts': []}


@lru_cache(maxsize=512)
def _repository_url(folder: str) -> str:
    if not folder or not Path(folder).is_dir():
        return ''
    try:
        root = subprocess.run(['git', '-C', folder, 'rev-parse', '--show-toplevel'],
                              capture_output=True, text=True, timeout=3, check=False)
        if root.returncode or Path(root.stdout.strip()).resolve() != Path(folder).resolve():
            return ''
    except (OSError, subprocess.TimeoutExpired):
        return ''
    for remote in ('origin', 'production'):
        try:
            result = subprocess.run(
                ['git', '-C', folder, 'config', '--get', f'remote.{remote}.url'],
                capture_output=True, text=True, timeout=3, check=False,
            )
            value = result.stdout.strip() if result.returncode == 0 else ''
            if value:
                parsed = urlsplit(value)
                # Never transmit credentials embedded in an HTTP remote.
                if parsed.password or parsed.query or parsed.fragment or (parsed.scheme in {'http', 'https'} and parsed.username):
                    return ''
                return value
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return ''
    return ''


def collect_pinned_projects(session_provider=None, *, pinned_keys=None) -> list[dict]:
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    from distr.core.workflow.development_sidebar import board_key_for, load_pinned_boards

    keys = set(load_pinned_boards(migrate=False) or []) if pinned_keys is None else set(pinned_keys)
    provider = session_provider or get_session
    rows, seen = [], set()
    with provider() as db:
        boards = {}
        for board in db.query(KanbanBoard).all():
            key = board_key_for(board)
            if key in keys and key in boards:
                raise ValueError(f'Pinned board {key} has multiple local mappings. Review its project link.')
            boards[key] = board
        projects = db.query(Project).filter(Project.deleted_at.is_(None)).order_by(Project.id).all()
        for key in sorted(keys):
            board = boards.get(key)
            if board is None:
                raise ValueError(f'Pinned board {key} has no local project mapping.')
            matches = [p for p in projects if p.id == board.default_project_id or p.kanban_board_id == board.id]
            if len(matches) != 1:
                raise ValueError(f'Pinned board {key} must link to exactly one local project.')
            project = matches[0]
            if project.id in seen:
                continue
            folder = str(project.folder_location or '').strip()
            if not folder.startswith('/') or '..' in Path(folder).parts:
                raise ValueError(f'Pinned project {project.name} needs an absolute local folder before sync.')
            seen.add(project.id)
            rows.append({
                'decisions_project_id': int(project.id), 'board_key': key,
                'name': project.name, 'description': project.description or '',
                'local_folder_path': folder, 'start_commands': project.startup_instructions or '',
                'repo_url': _repository_url(folder),
            })
    return rows


def collect_projects(session_provider=None, *, pinned_keys=None) -> list[dict]:
    """Collect the durable project list; pins only annotate it, never restrict it."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    from distr.core.workflow.development_sidebar import board_key_for, load_pinned_boards

    try:
        keys = set(load_pinned_boards(migrate=False) or []) if pinned_keys is None else set(pinned_keys)
    except (TypeError, ValueError):
        logger.warning('Saved project pins are invalid; synchronizing all projects without pin annotations')
        keys = set()
    with (session_provider or get_session)() as db:
        pinned_boards = [board for board in db.query(KanbanBoard).all() if board_key_for(board) in keys]
        pinned_ids = {board.default_project_id for board in pinned_boards if board.default_project_id}
        board_ids = {board.id for board in pinned_boards}
        rows = []
        for project in db.query(Project).filter(Project.deleted_at.is_(None)).order_by(Project.id).all():
            folder = str(project.folder_location or '').strip()
            if folder and (not folder.startswith('/') or not folder.rstrip('/') or '..' in Path(folder).parts or '\x00' in folder or len(folder) > 1000):
                logger.warning('Project %s has no valid absolute folder; synchronizing its identity without a folder', project.id)
                folder = ''
            elif folder:
                folder = str(Path(folder)).rstrip('/')
            rows.append({
                'decisions_project_id': int(project.id), 'name': project.name,
                'description': project.description or '', 'local_folder_path': folder,
                'start_commands': project.startup_instructions or '', 'repo_url': _repository_url(folder),
                'is_pinned': project.id in pinned_ids or project.kanban_board_id in board_ids,
            })
    return rows


def _validated_manifest(rows):
    """Keep an invalid local row from blocking unrelated projects; never truncate it."""
    valid, conflicts = [], []
    paths = {}
    for row in rows:
        path = row.get('local_folder_path', '')
        if path:
            paths[path] = paths.get(path, 0) + 1
    for row in rows:
        reason = ''
        for key, maximum in [('name', 200), ('description', 100000), ('repo_url', 500), ('start_commands', 100000)]:
            value = row.get(key, '')
            if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
                reason = f'Invalid project {key}: use text of at most {maximum} characters without null characters.'
                break
        if not reason and not row.get('name', '').strip():
            reason = 'Each project needs a name.'
        if not reason and paths.get(row.get('local_folder_path', ''), 0) > 1:
            reason = 'Multiple Decisions projects use this local folder. Review their identities before syncing.'
        if reason:
            conflicts.append({'decisions_project_id': row['decisions_project_id'], 'name': row.get('name', ''), 'reason': reason})
        else:
            valid.append(row)
    return valid, conflicts


def invalidate_project_sync() -> None:
    """Wake the next heartbeat after local edits; never do network I/O in a save."""
    global _last_attempt
    _last_attempt = float('-inf')


def invalidate_pinned_project_sync() -> None:
    """Compatibility with the sidebar's pin-change notification."""
    invalidate_project_sync()


def sync_projects(*, force=False) -> dict:
    global _last_attempt, _last_result
    if not _sync_lock.acquire(blocking=False):
        return {**_last_result, 'syncing': True}
    try:
        now = time.monotonic()
        if not force and now - _last_attempt < SYNC_SECONDS:
            return dict(_last_result)
        _last_attempt = now
        client = configured_tensology_client(source='decisions-project-sync')
        rows, conflicts = _validated_manifest(collect_projects())
        # Automatic creation never authorizes a client charge. Existing portal
        # billing choices and edited production fields remain authoritative.
        combined = {'scope': 'all', 'new_projects_non_billable': True, 'projects': [], 'pending': [], 'conflicts': conflicts, 'suppressed': []}
        # All-scope requests never unpin omitted rows, so bounded batches are safe.
        for offset in range(0, max(len(rows), 1), 500):
            result = client.post(
                'projects/pinned-sync',
                {'projects': rows[offset:offset + 500], 'scope': 'all', 'new_projects_non_billable': True},
                idempotency_key=f'project-sync:{uuid.uuid4()}',
            )
            if not isinstance(result, dict) or result.get('scope') != 'all' or result.get('new_projects_non_billable') is not True:
                raise TensologyApiError('Tensology does not support automatic all-project synchronization yet.', code='upgrade_required')
            for key in ('projects', 'pending', 'conflicts', 'suppressed'):
                combined[key].extend(result.get(key) or [])
        _last_result = combined
        if _last_result.get('conflicts'):
            logger.warning('Tensology project sync needs review for %s project(s)', len(_last_result['conflicts']))
        return dict(_last_result)
    except (TensologyApiError, ValueError) as exc:
        _last_result = {'projects': [], 'conflicts': [], 'error': str(exc), 'code': getattr(exc, 'code', 'invalid_project')}
        logger.debug('Tensology project sync unavailable: %s', exc)
        return dict(_last_result)
    finally:
        _sync_lock.release()


def sync_pinned_projects(*, force=False) -> dict:
    """Compatibility entry point: all callers now synchronize the full project list."""
    return sync_projects(force=force)
