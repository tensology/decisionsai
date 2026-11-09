"""Push pinned local project identities to Tensology, preserving portal billing choices."""

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
                if parsed.scheme in {'http', 'https'} and (parsed.username or parsed.password):
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
        projects = db.query(Project).order_by(Project.id).all()
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


def invalidate_pinned_project_sync() -> None:
    """Wake the next heartbeat after pin edits, without delaying the pin UI on the network."""
    global _last_attempt
    _last_attempt = float('-inf')


def sync_pinned_projects(*, force=False) -> dict:
    global _last_attempt, _last_result
    if not _sync_lock.acquire(blocking=False):
        return {**_last_result, 'syncing': True}
    try:
        now = time.monotonic()
        if not force and now - _last_attempt < SYNC_SECONDS:
            return dict(_last_result)
        _last_attempt = now
        client = configured_tensology_client(source='decisions-pinned-projects')
        rows = collect_pinned_projects()
        # Discovery never chooses billing. New projects wait for portal review.
        result = client.post(
            'projects/pinned-sync',
            {'projects': rows, 'new_projects_non_billable': False},
            idempotency_key=f'pinned-projects:{uuid.uuid4()}',
        )
        _last_result = result if isinstance(result, dict) else {'projects': [], 'conflicts': []}
        if _last_result.get('conflicts'):
            logger.warning('Tensology pinned project sync needs review for %s project(s)', len(_last_result['conflicts']))
        return dict(_last_result)
    except (TensologyApiError, ValueError) as exc:
        _last_result = {'projects': [], 'conflicts': [], 'error': str(exc)}
        logger.debug('Tensology pinned project sync unavailable: %s', exc)
        return dict(_last_result)
    finally:
        _sync_lock.release()
