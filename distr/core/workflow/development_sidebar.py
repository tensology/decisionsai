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


def load_pinned_boards() -> list[str] | None:
    from distr.core.settings import load_settings_from_db

    value = load_settings_from_db().get(PINNED_BOARDS_SETTING)
    if value is None:
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
    from distr.core.settings import load_settings_from_db

    keys = load_pinned_boards()
    payload = {'board_keys': keys}
    if keys is None:
        legacy = load_settings_from_db().get('development_hot_corner_pinned_project_ids', [])
        if isinstance(legacy, str):
            legacy = json.loads(legacy)
        payload['legacy_project_ids'] = legacy if isinstance(legacy, list) else []
    return payload
