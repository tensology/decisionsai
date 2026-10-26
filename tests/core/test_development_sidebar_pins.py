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
    assert load_pinned_boards() is None
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
