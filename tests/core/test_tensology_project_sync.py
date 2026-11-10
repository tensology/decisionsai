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


def test_sync_throttles_wakes_after_pin_change_and_only_defaults_new_projects_nonbillable(monkeypatch, isolated_sync):
    client = Mock()
    client.post.return_value = {'scope': 'all', 'new_projects_non_billable': True, 'projects': [{'project_id': 190, 'decisions_project_id': 9, 'is_billable': False}], 'pending': [], 'conflicts': []}
    monkeypatch.setattr(sync, 'configured_tensology_client', lambda **_: client)
    monkeypatch.setattr(sync, 'collect_projects', lambda: [{'decisions_project_id': 9, 'name': 'Test project'}])
    assert sync.sync_pinned_projects()['projects'][0]['project_id'] == 190
    assert sync.sync_pinned_projects()['projects'][0]['is_billable'] is False
    assert client.post.call_count == 1
    body = client.post.call_args.args[1]
    assert body['new_projects_non_billable'] is True
    assert body['scope']=='all'
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
    monkeypatch.setattr(sync, 'collect_projects', lambda: [])
    assert sync.sync_pinned_projects()['error'] == 'Offline'
    sync.sync_pinned_projects()
    assert client.post.call_count == 1
    now[0] += sync.SYNC_SECONDS
    client.post.side_effect = None
    client.post.return_value = {'scope':'all', 'new_projects_non_billable': True, 'projects': [], 'pending': [], 'conflicts': []}
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


def test_all_projects_include_unpinned_and_folderless_without_board_dependencies(isolated_sync):
    with isolated_sync() as db:
        db.add_all([Project(id=1,name='Pinned',folder_location='/pinned/'),
                    Project(id=2,name='Not pinned',folder_location='/other'),
                    Project(id=3,name='No folder',folder_location='')])
        db.add(KanbanBoard(id=10,name='Pinned board',source='database',default_project_id=1))
        db.commit()
    rows=sync.collect_projects(isolated_sync,pinned_keys=['decisions:10','decisions:999'])
    assert [p['decisions_project_id'] for p in rows]==[1,2,3]
    assert [p['is_pinned'] for p in rows]==[True,False,False]
    assert rows[0]['local_folder_path'] == '/pinned'
    assert rows[2]['local_folder_path']=='' and rows[2]['repo_url']==''


@pytest.mark.parametrize('folder', ['../other', '/', '//'])
def test_invalid_folder_does_not_prevent_project_identity_sync(isolated_sync, folder):
    with isolated_sync() as db:
        db.add(Project(id=1,name='Incomplete setup',folder_location=folder))
        db.commit()
    row=sync.collect_projects(isolated_sync,pinned_keys=[])[0]
    assert row['decisions_project_id']==1 and row['local_folder_path']==''


def test_invalid_saved_pins_do_not_block_any_project(monkeypatch, isolated_sync):
    with isolated_sync() as db:
        db.add(Project(id=1, name='Still synchronized', folder_location=''))
        db.commit()
    monkeypatch.setattr('distr.core.workflow.development_sidebar.load_pinned_boards',
                        Mock(side_effect=ValueError('Invalid saved pin data')))
    rows = sync.collect_projects(isolated_sync)
    assert len(rows) == 1
    assert rows[0]['decisions_project_id'] == 1 and rows[0]['is_pinned'] is False


def test_periodic_sync_recovers_projects_created_outside_the_ui(monkeypatch,isolated_sync):
    now=[100.0];client=Mock()
    original_collect=sync.collect_projects
    monkeypatch.setattr(sync.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(sync,'configured_tensology_client',lambda **_:client)
    monkeypatch.setattr(sync,'collect_projects',lambda:original_collect(isolated_sync,pinned_keys=[]))
    client.post.return_value={'scope':'all','new_projects_non_billable':True,'projects':[],'conflicts':[],'pending':[]}
    sync.sync_projects()
    assert client.post.call_args.args[1]['projects']==[]
    with isolated_sync() as db:
        db.add(Project(id=1,name='CLI created',folder_location=''))
        db.commit()
    now[0]+=sync.SYNC_SECONDS
    sync.sync_projects()
    assert client.post.call_args.args[1]['projects'][0]['name']=='CLI created'


@pytest.mark.parametrize('settings', [
    {'tensology_enabled': False, 'tensology_key': 'test-key'},
    {'tensology_enabled': True, 'tensology_key': ''},
])
def test_no_configuration_makes_no_network_or_project_collection(monkeypatch,isolated_sync,settings):
    collect=Mock()
    constructor = Mock()
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: settings)
    monkeypatch.setattr('distr.core.tensology_client.TensologyClient', constructor)
    monkeypatch.setattr(sync,'collect_projects',collect)
    result=sync.sync_projects(force=True)
    assert result['code']=='not_configured'
    collect.assert_not_called()
    constructor.assert_not_called()


def test_old_server_cannot_silently_leave_automatic_projects_pending(monkeypatch,isolated_sync):
    client=Mock();client.post.return_value={'projects':[],'pending':[{'decisions_project_id':1}],'conflicts':[]}
    monkeypatch.setattr(sync,'configured_tensology_client',lambda **_:client)
    monkeypatch.setattr(sync,'collect_projects',lambda:[])
    assert sync.sync_projects()['code']=='upgrade_required'


def test_concurrent_sync_does_not_send_a_duplicate_request(monkeypatch,isolated_sync):
    client=Mock();monkeypatch.setattr(sync,'configured_tensology_client',lambda **_:client)
    assert sync._sync_lock.acquire(blocking=False)
    try:
        assert sync.sync_projects(force=True)['syncing'] is True
        client.post.assert_not_called()
    finally:sync._sync_lock.release()


def test_repository_lookup_does_not_take_the_parent_repository(monkeypatch,tmp_path):
    from types import SimpleNamespace
    child=tmp_path/'child';child.mkdir()
    run=Mock(return_value=SimpleNamespace(returncode=0,stdout=str(tmp_path)))
    monkeypatch.setattr(sync.subprocess,'run',run)
    assert sync._repository_url(str(child))==''
    assert run.call_count==1


def test_more_than_500_projects_are_all_sent_in_bounded_batches(monkeypatch, isolated_sync):
    rows = [{'decisions_project_id': i, 'name': f'Project {i}'} for i in range(1, 502)]
    client = Mock()
    client.post.side_effect = lambda _, body, **kwargs: {
        'scope': 'all', 'new_projects_non_billable': True,
        'projects': [{'decisions_project_id': row['decisions_project_id']} for row in body['projects']],
        'pending': [], 'conflicts': [],
    }
    monkeypatch.setattr(sync, 'configured_tensology_client', lambda **_: client)
    monkeypatch.setattr(sync, 'collect_projects', lambda: rows)
    result = sync.sync_projects(force=True)
    assert [len(call.args[1]['projects']) for call in client.post.call_args_list] == [500, 1]
    assert [row['decisions_project_id'] for row in result['projects']] == list(range(1, 502))


@pytest.mark.parametrize('field,value', [
    ('name', 'n' * 201), ('description', 'd' * 100001),
    ('start_commands', 's' * 100001), ('repo_url', 'r' * 501), ('name', 'bad\x00name'),
])
def test_invalid_project_is_reported_without_blocking_other_projects(monkeypatch, isolated_sync, field, value):
    bad = {'decisions_project_id': 1, 'name': 'Invalid', field: value}
    good = {'decisions_project_id': 2, 'name': 'Valid'}
    client = Mock()
    client.post.return_value = {'scope': 'all', 'new_projects_non_billable': True, 'projects': [good], 'pending': [], 'conflicts': []}
    monkeypatch.setattr(sync, 'configured_tensology_client', lambda **_: client)
    monkeypatch.setattr(sync, 'collect_projects', lambda: [bad, good])
    result = sync.sync_projects(force=True)
    assert client.post.call_args.args[1]['projects'] == [good]
    assert result['projects'] == [good]
    assert result['conflicts'][0]['decisions_project_id'] == 1
    assert field in result['conflicts'][0]['reason']


def test_duplicate_source_folders_do_not_block_unrelated_projects():
    good = {'decisions_project_id': 3, 'name': 'Independent', 'local_folder_path': '/other'}
    valid, conflicts = sync._validated_manifest([
        {'decisions_project_id': 1, 'name': 'One', 'local_folder_path': '/same'},
        {'decisions_project_id': 2, 'name': 'Two', 'local_folder_path': '/same'}, good,
    ])
    assert valid == [good]
    assert [row['decisions_project_id'] for row in conflicts] == [1, 2]


def test_remote_removal_suppression_is_retained_in_sync_result(monkeypatch, isolated_sync):
    client = Mock()
    tombstone = {'decisions_project_id': 1, 'removal_id': 'retained-removal'}
    client.post.return_value = {'scope': 'all', 'new_projects_non_billable': True, 'suppressed': [tombstone]}
    monkeypatch.setattr(sync, 'configured_tensology_client', lambda **_: client)
    monkeypatch.setattr(sync, 'collect_projects', lambda: [{'decisions_project_id': 1, 'name': 'Kept in Decisions'}])
    result = sync.sync_projects(force=True)
    assert result['suppressed'] == [tombstone]
    assert result['projects'] == []
