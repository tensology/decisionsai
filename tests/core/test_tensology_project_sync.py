from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core import tensology_project_sync as sync
from distr.core.tensology_client import TensologyApiError


@pytest.fixture
def isolated_sync(monkeypatch):
    monkeypatch.setattr(sync, '_last_attempt', float('-inf'))
    monkeypatch.setattr(sync, '_last_result', {'projects': [], 'conflicts': []})
    monkeypatch.setattr(sync, '_repository_url', lambda _: '')
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    yield sessions
    engine.dispose()


def test_collects_pinned_board_projects_with_project_identity_not_board_id(isolated_sync):
    with isolated_sync() as db:
        first = Project(id=91, name='Local', folder_location='/local/project/', description='Description')
        external = Project(id=92, name='External', folder_location='/local/external', startup_instructions='start')
        ignored = Project(id=93, name='Not pinned', folder_location='/local/ignored')
        db.add_all([first, external, ignored])
        db.add_all([
            KanbanBoard(id=5, name='Local board', source='database', default_project_id=91),
            KanbanBoard(id=6, name='Jira board', source='jira', external_board_id='35', default_project_id=92),
            KanbanBoard(id=7, name='Ignored board', source='database', default_project_id=93),
        ])
        db.commit()
    rows = sync.collect_pinned_projects(isolated_sync, pinned_keys=['decisions:5', 'jira:35'])
    assert [row['decisions_project_id'] for row in rows] == [91, 92]
    assert rows[1]['board_key'] == 'jira:35'
    assert rows[1]['start_commands'] == 'start'
    assert not any(row['decisions_project_id'] == 5 for row in rows)


def test_ambiguous_board_does_not_guess_project(isolated_sync):
    with isolated_sync() as db:
        db.add_all([Project(id=1, name='One', folder_location='/one', kanban_board_id=5), Project(id=2, name='Two', folder_location='/two', kanban_board_id=5)])
        db.add(KanbanBoard(id=5, name='Ambiguous', source='database'))
        db.commit()
    with pytest.raises(ValueError, match='exactly one'):
        sync.collect_pinned_projects(isolated_sync, pinned_keys=['decisions:5'])


def test_invalid_pinned_folder_and_missing_board_are_reported(isolated_sync):
    with isolated_sync() as db:
        db.add(Project(id=1, name='No folder'))
        db.add(KanbanBoard(id=5, name='Board', source='database', default_project_id=1))
        db.commit()
    with pytest.raises(ValueError, match='absolute local folder'):
        sync.collect_pinned_projects(isolated_sync, pinned_keys=['decisions:5'])
    with pytest.raises(ValueError, match='no local project mapping'):
        sync.collect_pinned_projects(isolated_sync, pinned_keys=['decisions:999'])


def test_sync_throttles_wakes_after_pin_change_and_never_selects_billing(monkeypatch, isolated_sync):
    client = Mock()
    client.post.return_value = {'projects': [{'project_id': 190, 'decisions_project_id': 9, 'is_billable': False}], 'pending': [], 'conflicts': []}
    monkeypatch.setattr(sync, 'configured_tensology_client', lambda **_: client)
    monkeypatch.setattr(sync, 'collect_pinned_projects', lambda: [{'decisions_project_id': 9}])
    assert sync.sync_pinned_projects()['projects'][0]['project_id'] == 190
    assert sync.sync_pinned_projects()['projects'][0]['is_billable'] is False
    assert client.post.call_count == 1
    body = client.post.call_args.args[1]
    assert body['new_projects_non_billable'] is False
    assert 'is_billable' not in body['projects'][0]
    sync.invalidate_pinned_project_sync()
    sync.sync_pinned_projects()
    assert client.post.call_count == 2
    sync.sync_pinned_projects(force=True)
    assert client.post.call_count == 3


def test_offline_failure_is_visible_and_retries_on_next_interval(monkeypatch, isolated_sync):
    now = [100.0]
    monkeypatch.setattr(sync.time, 'monotonic', lambda: now[0])
    client = Mock()
    client.post.side_effect = TensologyApiError('Offline', code='unavailable')
    monkeypatch.setattr(sync, 'configured_tensology_client', lambda **_: client)
    monkeypatch.setattr(sync, 'collect_pinned_projects', lambda: [])
    assert sync.sync_pinned_projects()['error'] == 'Offline'
    sync.sync_pinned_projects()
    assert client.post.call_count == 1
    now[0] += sync.SYNC_SECONDS
    client.post.side_effect = None
    client.post.return_value = {'projects': [], 'pending': [], 'conflicts': []}
    assert 'error' not in sync.sync_pinned_projects()
    assert client.post.call_count == 2


def test_pin_save_wakes_heartbeat_without_network(monkeypatch):
    from distr.core.workflow.development_sidebar import save_pinned_boards
    save, wake = Mock(), Mock()
    monkeypatch.setattr('distr.core.utils.save_settings_to_db', save)
    monkeypatch.setattr(sync, 'invalidate_pinned_project_sync', wake)
    assert save_pinned_boards(['decisions:5']) == ['decisions:5']
    save.assert_called_once()
    wake.assert_called_once()
