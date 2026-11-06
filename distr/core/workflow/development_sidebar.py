"""Shared board pins for the Development web and desktop sidebars."""

import json
import secrets
import time

DESKTOP_BOARD_ACTIONS = {
    'new-ticket', 'new-chat', 'plan', 'kanban', 'list', 'refresh', 'edit',
    'activate', 'open-folder', 'terminals', 'terminal-toggle', 'archive', 'delete',
}
_desktop_actions = {}


def queue_desktop_board_action(board_key: str, action: str) -> str:
    if action not in DESKTOP_BOARD_ACTIONS:
        raise ValueError('Unknown board action.')
    key = normalize_board_keys([board_key])[0]
    now = time.monotonic()
    for token, entry in list(_desktop_actions.items()):
        if entry['expires'] <= now:
            _desktop_actions.pop(token, None)
    token = secrets.token_urlsafe(24)
    _desktop_actions[token] = {'board_key': key, 'action': action, 'expires': now + 120}
    return token


def consume_desktop_board_action(token: str) -> dict:
    entry = _desktop_actions.pop(token, None)
    if entry is None or entry['expires'] <= time.monotonic():
        raise ValueError('Desktop action expired. Open it again from the sidebar.')
    return {'board_key': entry['board_key'], 'action': entry['action']}

PINNED_BOARDS_SETTING = 'development_pinned_board_keys'
# Archived: the native hover panel used to pin by project id. It is read once to
# migrate into board keys and is never written or deleted afterwards.
LEGACY_PROJECT_PINS_SETTING = 'development_hot_corner_pinned_project_ids'
_LOCAL_BOARD_SOURCES = {None, 'database', 'decisions', 'local'}


def board_key_for(board) -> str | None:
    """Return the shared sidebar key for a Kanban board (same format as the web UI)."""
    if board is None:
        return None
    source = getattr(board, 'source', None)
    if source in _LOCAL_BOARD_SOURCES:
        return f'decisions:{int(board.id)}' if getattr(board, 'id', None) is not None else None
    identity = getattr(board, 'external_board_id', None)
    return f'{source}:{identity}' if source and identity else None


def project_board_keys(session_provider=None) -> dict[int, str]:
    """Map project id -> board key from the one board source both panels use."""
    from distr.core.kanban.lifecycle import ensure_project_boards

    if session_provider is None:
        from distr.core.db import get_session as session_provider
    with session_provider() as db:
        boards = ensure_project_boards(db)
        keys = {int(pid): board_key_for(board) for pid, board in boards.items()}
        db.commit()
    return {pid: key for pid, key in keys.items() if key}


def _legacy_project_ids(settings: dict) -> list[int]:
    value = settings.get(LEGACY_PROJECT_PINS_SETTING)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    ids = []
    for item in value:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(ids))


def migrate_legacy_project_pins(session_provider=None, settings: dict | None = None) -> list[str] | None:
    """Convert hover-panel project pins into shared board keys, once.

    Runs only while the shared setting is unset, so it is idempotent: after the
    first save the shared list is authoritative. The legacy setting is left as is.
    Returns the migrated keys, or None when there was nothing to migrate.
    """
    if settings is None:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
    if settings.get(PINNED_BOARDS_SETTING) is not None:
        return None
    legacy_ids = _legacy_project_ids(settings)
    if not legacy_ids:
        return None
    mapping = project_board_keys(session_provider)
    keys = [mapping[pid] for pid in legacy_ids if pid in mapping]
    return save_pinned_boards(keys)


def load_pinned_boards(migrate: bool = True) -> list[str] | None:
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    value = settings.get(PINNED_BOARDS_SETTING)
    if value is None:
        if not migrate:
            return None
        try:
            return migrate_legacy_project_pins(settings=settings)
        except Exception:
            import logging
            logging.getLogger(__name__).warning('Board pin migration failed', exc_info=True)
            return None
    if isinstance(value, str):
        value = json.loads(value)
    return normalize_board_keys(value)


def normalize_board_keys(keys) -> list[str]:
    if not isinstance(keys, list) or len(keys) > 500:
        raise ValueError('Board pins must be a list of at most 500 board keys.')
    result = []
    for key in keys:
        if not isinstance(key, str) or len(key) > 200 or ':' not in key:
            raise ValueError('Invalid board key.')
        provider, identity = key.split(':', 1)
        if provider not in {'decisions', 'jira', 'trello'} or not identity.strip():
            raise ValueError('Invalid board key.')
        if key not in result:
            result.append(key)
    return result


def save_pinned_boards(keys) -> list[str]:
    from distr.core.utils import save_settings_to_db

    normalized = normalize_board_keys(keys)
    save_settings_to_db({PINNED_BOARDS_SETTING: json.dumps(normalized)})
    return normalized


def sidebar_pin_preferences() -> dict:
    """Shared pins for both panels; legacy project pins are migrated server-side."""
    return {'board_keys': load_pinned_boards()}
