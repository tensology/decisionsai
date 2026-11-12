from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core import utils
from distr.core.db import Base, Settings
from distr.core.workflow.development_sidebar import load_pinned_boards, save_pinned_boards
from distr.gui.web.routes.development.thread_controls import register_routes


def test_desktop_board_actions_are_validated_expire_and_are_one_use(monkeypatch):
    from distr.core.workflow import development_sidebar as sidebar
    import pytest
    now = [10.0]
    monkeypatch.setattr(sidebar.time, 'monotonic', lambda: now[0])
    sidebar._desktop_actions.clear()
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as client:
        token = sidebar.queue_desktop_board_action('decisions:13', 'delete')
        path = '/workflows/studio/sidebar/desktop-action'
        assert client.post(path, json={'token': token}).json() == {'board_key': 'decisions:13', 'action': 'delete'}
        assert client.post(path, json={'token': token}).status_code == 400
        assert client.post(path, json={'token': ['invalid']}).status_code == 400
        token = sidebar.queue_desktop_board_action('decisions:13', 'edit')
        now[0] += 121
        assert client.post(path, json={'token': token}).status_code == 400
    with pytest.raises(ValueError):
        sidebar.queue_desktop_board_action('unknown:13', 'edit')
    with pytest.raises(ValueError):
        sidebar.queue_desktop_board_action('decisions:13', 'arbitrary-script')


def test_shared_board_pins_round_trip_in_isolated_settings_database(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(utils, 'Session', sessions)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', utils.load_settings_from_db)
    with sessions() as db:
        db.add(Settings(development_hot_corner_pinned_project_ids='[9]'))
        db.commit()
    monkeypatch.setattr('distr.core.db.get_session', sessions)
    # Legacy project 9 no longer exists, so migration records an empty shared list.
    assert load_pinned_boards() == []
    assert save_pinned_boards(['jira:ABC', 'decisions:13', 'jira:ABC']) == ['jira:ABC', 'decisions:13']
    assert load_pinned_boards() == ['jira:ABC', 'decisions:13']
    assert utils.load_settings_from_db()['development_hot_corner_pinned_project_ids'] == [9]
    save_pinned_boards([])
    assert load_pinned_boards() == []


def test_sidebar_pin_boundary_rejects_invalid_keys_and_returns_shared_state(monkeypatch):
    saved = []
    monkeypatch.setattr('distr.core.workflow.development_sidebar.load_pinned_boards', lambda: ['decisions:13'])
    def save(keys):
        from distr.core.workflow.development_sidebar import normalize_board_keys
        normalized = normalize_board_keys(keys)
        saved.append(normalized)
        return normalized
    monkeypatch.setattr('distr.core.workflow.development_sidebar.save_pinned_boards', save)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/workflows/studio/sidebar/pins').json() == {'board_keys': ['decisions:13']}
        for invalid in (None, 'jira:ABC', ['unknown:9'], ['jira:'], [42], ['decisions:9'] * 501):
            assert client.put('/workflows/studio/sidebar/pins', json={'board_keys': invalid}).status_code == 400
        assert saved == []
        response = client.put('/workflows/studio/sidebar/pins', json={'board_keys': ['trello:ABC']})
        assert response.json() == {'board_keys': ['trello:ABC']}
        assert saved == [['trello:ABC']]


def test_pin_operations_merge_across_clients_and_stale_seed_is_ignored(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    engine = create_engine(f'sqlite:///{tmp_path / "board-pins.db"}')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(utils, 'Session', sessions)
    monkeypatch.setattr('distr.core.db.get_session', sessions)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', utils.load_settings_from_db)
    monkeypatch.setattr('distr.core.tensology_project_sync.invalidate_pinned_project_sync', lambda: None)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    path = '/workflows/studio/sidebar/pins'
    with TestClient(app) as client:
        client.put(path, json={'board_keys': ['decisions:13']}).raise_for_status()
        snapshot = client.get(path).json()['board_keys']
        client.put(path, json={'board_keys': [*snapshot, 'jira:ABC']}).raise_for_status()
        client.put(path, json={'board_keys': [*snapshot, 'trello:XYZ']}).raise_for_status()
        assert client.get(path).json() == {'board_keys': ['decisions:13', 'trello:XYZ']}
        client.put(path, json={'board_keys': snapshot}).raise_for_status()

    gate = Barrier(2, timeout=5)

    def pin(key):
        with TestClient(app) as client:
            assert client.get(path).json() == {'board_keys': ['decisions:13']}
            gate.wait()
            return client.patch(path, json={'board_key': key, 'pinned': True}).raise_for_status().json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        jira, trello = pool.map(pin, ['jira:ABC', 'trello:XYZ'])
    assert 'jira:ABC' in jira['board_keys'] and 'trello:XYZ' in trello['board_keys']
    with TestClient(app) as client:
        assert client.patch(path, json={'board_key': 'jira:ABC', 'pinned': 'false'}).status_code == 400
        assert client.patch(path, json={'board_key': 'unknown:ABC', 'pinned': True}).status_code == 400
        expected = client.get(path).json()['board_keys']
        assert set(expected) == {'decisions:13', 'jira:ABC', 'trello:XYZ'}
        assert client.put(path, json={'board_keys': [], 'only_if_unset': 'true'}).status_code == 400
        assert client.put(path, json={'board_keys': ['decisions:99'], 'only_if_unset': True}).json() == {'board_keys': expected}
        assert client.patch(path, json={'board_key': 'jira:ABC', 'pinned': True}).json() == {'board_keys': expected}
        for _ in range(2):
            assert client.patch(path, json={'board_key': 'jira:ABC', 'pinned': False}).json() == {
                'board_keys': ['decisions:13', 'trello:XYZ'],
            }


def _isolated_pin_db(monkeypatch, legacy):
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(utils, 'Session', sessions)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', utils.load_settings_from_db)
    monkeypatch.setattr('distr.core.db.get_session', sessions)
    with sessions() as db:
        db.add(Settings(development_hot_corner_pinned_project_ids=legacy))
        local, external = Project(name='Local'), Project(name='External')
        unpinned = Project(name='Unpinned')
        db.add_all([local, external, unpinned])
        db.flush()
        board = KanbanBoard(name='Jira', source='jira', external_board_id='ABC', default_project_id=external.id)
        db.add(board)
        db.flush()
        external.kanban_board_id = board.id
        db.commit()
        return sessions, int(local.id), int(external.id)


def test_legacy_hover_project_pins_migrate_to_board_keys_once(monkeypatch):
    from distr.core.workflow import development_sidebar as sidebar
    sessions, local_id, external_id = _isolated_pin_db(monkeypatch, None)
    utils.save_settings_to_db({'development_hot_corner_pinned_project_ids': [external_id, local_id, 999]})
    keys = load_pinned_boards()
    local_key = sidebar.project_board_keys(sessions)[local_id]
    assert local_key.startswith('decisions:')
    # Order follows sorted project ids; unknown project 999 is skipped.
    assert keys == [local_key, 'jira:ABC']
    # Shared setting is now authoritative; legacy setting is untouched (archived).
    settings = utils.load_settings_from_db()
    assert settings['development_hot_corner_pinned_project_ids'] == [external_id, local_id, 999]
    # Idempotent: later reads never re-merge legacy pins, so unpinning sticks.
    save_pinned_boards(['jira:ABC'])
    assert load_pinned_boards() == ['jira:ABC']
    assert sidebar.migrate_legacy_project_pins(sessions) is None
    assert sidebar.sidebar_pin_preferences() == {'board_keys': ['jira:ABC']}


def test_migration_is_noop_without_legacy_pins(monkeypatch):
    from distr.core.workflow import development_sidebar as sidebar
    _isolated_pin_db(monkeypatch, None)
    assert load_pinned_boards() is None
    assert sidebar.sidebar_pin_preferences() == {'board_keys': None}
    assert utils.load_settings_from_db().get('development_pinned_board_keys') is None


def test_hover_panel_and_web_share_board_key_format():
    from types import SimpleNamespace
    from distr.core.workflow.development_sidebar import board_key_for
    assert board_key_for(SimpleNamespace(id=5, source='database', external_board_id=None)) == 'decisions:5'
    assert board_key_for(SimpleNamespace(id=5, source=None, external_board_id=None)) == 'decisions:5'
    assert board_key_for(SimpleNamespace(id=5, source='trello', external_board_id='T1')) == 'trello:T1'
    assert board_key_for(SimpleNamespace(id=5, source='jira', external_board_id=None)) is None
    assert board_key_for(None) is None
