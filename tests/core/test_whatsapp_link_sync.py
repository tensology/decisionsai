import contextlib
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppMessage, WhatsAppPhoneLink
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core.db.whatsapp_links_schema import ensure_whatsapp_board_link_schema
from distr.core.incoming.whatsapp_links import apply_remote_whatsapp_links, canonical_whatsapp_scopes


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'whatsapp-sync.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    monkeypatch.setattr('distr.core.db.get_session', session)
    monkeypatch.setattr('distr.gui.web.routes.kanban.get_session', session)
    monkeypatch.setattr('distr.core.incoming.service.get_session', session)
    with session() as db:
        first, second, local = [Project(name=name) for name in ('First', 'Second', 'Local only')]
        db.add_all([first, second, local])
        db.flush()
        boards = [KanbanBoard(name=p.name, default_project_id=p.id) for p in (first, second, local)]
        db.add_all(boards)
        db.flush()
        for project, board in zip((first, second, local), boards):
            project.kanban_board_id = board.id
        ids = [p.id for p in (first, second, local)]
        board_ids = [b.id for b in boards]
    return session, ids, board_ids


def remote(thread, projects, **extra):
    return {'source': 'whatsapp', 'source_thread_id': thread,
            'projects': [{'decisions_project_id': pid} for pid in projects], **extra}


def test_projection_preserves_other_projects_preferences_and_local_only_boards(store):
    session, projects, boards = store
    jid = '123456@g.us'
    with session() as db:
        db.add_all([WhatsAppPhoneLink(board_id=board, phone_jid=jid, auto_snapshot=True, contact_name='Maintained') for board in boards])
    apply_remote_whatsapp_links([remote(jid, projects[:2])], mapped_project_ids=projects[:2])
    apply_remote_whatsapp_links([remote(jid, [projects[1]])], mapped_project_ids=projects[:2])
    apply_remote_whatsapp_links([remote(jid, [projects[1]])], mapped_project_ids=projects[:2])
    with session() as db:
        rows = db.query(WhatsAppPhoneLink).all()
        assert {row.board_id for row in rows} == set(boards[1:])
        assert all(row.auto_snapshot and row.contact_name == 'Maintained' for row in rows)
    apply_remote_whatsapp_links([remote(jid, [])], mapped_project_ids=projects[:2])
    with session() as db:
        assert [row.board_id for row in db.query(WhatsAppPhoneLink)] == [boards[2]]


def test_remote_addition_materializes_once_per_board(store):
    session, projects, boards = store
    rows = [remote('123456@g.us', projects[:2], label='Customer group')]
    for _ in range(2):
        apply_remote_whatsapp_links(rows, mapped_project_ids=projects[:2])
    with session() as db:
        links = db.query(WhatsAppPhoneLink).all()
        assert {row.board_id for row in links} == set(boards[:2])
        assert all(row.contact_name == 'Customer group' and not row.auto_snapshot for row in links)


def test_one_incoming_refresh_projects_remote_removal_before_serializing_board_pills(store, monkeypatch):
    from distr.core.incoming import service, project_links
    session, projects, boards = store
    jid = '444555@g.us'
    with session() as db:
        db.add_all([WhatsAppPhoneLink(board_id=board, phone_jid=jid) for board in boards[:2]])
        db.add(WhatsAppMessage(message_id='refresh-order-message', jid=jid, chat_type='group', text='Current request'))
    rows = [remote(jid, [projects[1]], link_explicit=True)]
    def snapshot(**kwargs):
        if kwargs.get('refresh'):
            apply_remote_whatsapp_links(rows, mapped_project_ids=projects[:2])
        return rows
    monkeypatch.setattr(project_links, 'project_links', snapshot)
    monkeypatch.setattr(service, '_load_gmail_inbox', lambda **k: ([], {'connected': True}))
    monkeypatch.setattr(service, '_load_mailshot_inbox', lambda **k: ([], {'connected': True}))
    monkeypatch.setattr('distr.core.work_intake.get_work_intake_service', lambda: Mock(list_inbox=lambda **k: []))
    result = service.list_development_incoming(network=True)
    item = next(row for row in result['items'] if row.get('source_message_id') == 'refresh-order-message')
    assert {link['board_id'] for link in item['links']} == {boards[1]}
    assert item['project_ids'] == [projects[1]]


def test_link_summary_uses_observed_lid_without_duplicate_phone_conversation(store, monkeypatch):
    from distr.core.incoming import service, project_links
    session, projects, boards = store
    phone, lid = '123456789@s.whatsapp.net', '987654321@lid'
    with session() as db:
        db.add(WhatsAppPhoneLink(board_id=boards[0], phone_jid=phone, phone_number='123456789'))
        db.add(WhatsAppMessage(message_id='observed-lid-message', jid=lid, jid_phone='123456789', text='Private request'))
    monkeypatch.setattr(project_links, 'project_links', lambda **k: [])
    result = service.list_development_incoming(network=False)
    assert result['links'][0]['source_thread_id'] == lid
    item = next(row for row in result['items'] if row.get('source_message_id') == 'observed-lid-message')
    assert {link['board_id'] for link in item['links']} == {boards[0]}


def test_never_assigned_empty_source_preserves_local_links(store):
    session, projects, boards = store
    with session() as db:
        db.add(WhatsAppPhoneLink(board_id=boards[0], phone_jid='123456@g.us'))
    apply_remote_whatsapp_links([remote('123456@g.us', [], link_explicit=False)], mapped_project_ids=projects[:2])
    with session() as db:
        assert db.query(WhatsAppPhoneLink).count() == 1


def test_projection_does_not_delete_shared_board_owned_by_unmapped_project(store):
    session, projects, boards = store
    with session() as db:
        db.get(Project, projects[2]).kanban_board_id = boards[0]
        db.add(WhatsAppPhoneLink(board_id=boards[0], phone_jid='group@g.us'))
    apply_remote_whatsapp_links([remote('group@g.us', [])], mapped_project_ids=projects[:2])
    with session() as db:
        assert db.query(WhatsAppPhoneLink).count() == 1


def test_group_membership_never_inherits_private_sender_scope(store):
    session, projects, boards = store
    with session() as db:
        db.add(WhatsAppMessage(message_id='group-message', jid='group@g.us', jid_phone='27123456789@s.whatsapp.net'))
    apply_remote_whatsapp_links([remote('27123456789@s.whatsapp.net', [projects[0]])], mapped_project_ids=projects[:2])
    with session() as db:
        assert [row.phone_jid for row in db.query(WhatsAppPhoneLink)] == ['27123456789@s.whatsapp.net']


def test_group_and_private_jid_with_same_numeric_part_are_not_the_same_link(store):
    from distr.core.kanban.whatsapp_jid import upsert_whatsapp_phone_link
    session, _, boards = store
    with session() as db:
        private, _ = upsert_whatsapp_phone_link(db, board_id=boards[0], phone_jid='123@s.whatsapp.net', board_scoped=True)
        group, created = upsert_whatsapp_phone_link(db, board_id=boards[0], phone_jid='123@g.us', board_scoped=True)
        assert created and private.id != group.id
        assert private.phone_jid == '123@s.whatsapp.net'


def test_private_alias_removal_cannot_resurrect_from_old_local_row(store):
    session, projects, boards = store
    lid, phone = '998877@lid', '27123456789@s.whatsapp.net'
    with session() as db:
        db.add(WhatsAppMessage(message_id='alias-observation', jid=lid, jid_phone=phone))
        db.add(WhatsAppPhoneLink(board_id=boards[0], phone_jid=lid, phone_number='27123456789'))
    rows = [remote(lid, [projects[0]]), remote(phone, [])]
    assert canonical_whatsapp_scopes(rows, alias_pairs=[(lid, phone)])[lid] == set()
    apply_remote_whatsapp_links(rows, mapped_project_ids=projects[:2])
    with session() as db:
        assert db.query(WhatsAppPhoneLink).count() == 0


def test_incoming_serializes_all_board_links_for_aliases_without_group_inheritance(store):
    from distr.core.incoming.service import list_development_incoming
    session, _, boards = store
    lid, phone = '998877@lid', '27123456789@s.whatsapp.net'
    with session() as db:
        db.add_all([WhatsAppPhoneLink(board_id=boards[0], phone_jid=lid),
                    WhatsAppPhoneLink(board_id=boards[1], phone_jid=phone),
                    WhatsAppMessage(message_id='alias-reader', jid=lid, jid_phone=phone, text='Private request'),
                    WhatsAppMessage(message_id='older-alias-reader', jid=lid, text='Earlier private request'),
                    WhatsAppMessage(message_id='group-reader', jid='group@g.us', jid_phone=phone, chat_type='group', text='Separate group')])
    result = list_development_incoming(network=False)
    rows = {row['source_message_id']: row for row in result['items'] if row['source'] == 'whatsapp'}
    assert {link['board_id'] for link in rows['alias-reader']['links']} == set(boards[:2])
    assert {link['board_id'] for link in rows['older-alias-reader']['links']} == set(boards[:2])
    assert rows['group-reader']['links'] == []


def test_routes_confirm_remote_before_local_link_and_remove(store, monkeypatch):
    session, projects, boards = store
    from distr.gui.web.routes.kanban import create_routes
    from distr.core.incoming import project_links
    sync = Mock(return_value=True)
    monkeypatch.setattr(project_links, 'sync_whatsapp_project_link', sync, raising=False)
    app = FastAPI()
    app.include_router(create_routes())
    client = TestClient(app)
    path = f'/tickets/boards/{boards[0]}/whatsapp-links'
    added = client.post(path, json={'phone_jid': 'group@g.us', 'auto_snapshot': True})
    assert added.status_code == 200, added.text
    assert added.json()['synced'] is True
    sync.assert_called_once_with(boards[0], 'group@g.us')
    # The same conversation can remain linked to another project.
    second = client.post(f'/tickets/boards/{boards[1]}/whatsapp-links', json={'phone_jid': 'group@g.us'})
    assert second.status_code == 200, second.text
    removed = client.delete(path + f"/{added.json()['id']}")
    assert removed.status_code == 200 and removed.json()['synced'] is True
    assert sync.call_args.args == (boards[0], 'group@g.us')
    assert sync.call_args.kwargs == {'remove': True}
    with session() as db:
        assert [row.board_id for row in db.query(WhatsAppPhoneLink)] == [boards[1]]


def test_remote_failure_does_not_mutate_local_link(store, monkeypatch):
    session, _, boards = store
    from distr.core.tensology_client import TensologyApiError
    from distr.gui.web.routes.kanban import create_routes
    from distr.core.incoming import project_links
    sync = Mock(side_effect=TensologyApiError('Changed elsewhere', status=409))
    monkeypatch.setattr(project_links, 'sync_whatsapp_project_link', sync, raising=False)
    app = FastAPI()
    app.include_router(create_routes())
    client = TestClient(app)
    path = f'/tickets/boards/{boards[0]}/whatsapp-links'
    assert client.post(path, json={'phone_jid': 'group@g.us'}).status_code == 409
    with session() as db:
        assert db.query(WhatsAppPhoneLink).count() == 0
        link = WhatsAppPhoneLink(board_id=boards[0], phone_jid='group@g.us', auto_snapshot=True)
        db.add(link)
        db.flush()
        link_id = link.id
    assert client.delete(path + f'/{link_id}').status_code == 409
    with session() as db:
        assert db.get(WhatsAppPhoneLink, link_id).auto_snapshot is True


@pytest.mark.parametrize('constraint', ['CONSTRAINT uq_old_jid UNIQUE(phone_jid)', 'UNIQUE(phone_jid)', 'inline'])
def test_migration_preserves_ids_metadata_extra_columns_indexes_triggers_and_sequence(tmp_path, constraint):
    engine = create_engine(f"sqlite:///{tmp_path / 'old-links.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        jid_definition = "phone_jid VARCHAR NOT NULL DEFAULT 'phone_jid UNIQUE literal' UNIQUE" if constraint == 'inline' else 'phone_jid VARCHAR NOT NULL'
        final_constraint = '' if constraint == 'inline' else ', ' + constraint
        conn.exec_driver_sql(f'''CREATE TABLE whatsapp_phone_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, board_id INTEGER NOT NULL,
            {jid_definition}, phone_number VARCHAR, contact_name VARCHAR,
            auto_snapshot BOOLEAN DEFAULT 0, created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            maintained_extra TEXT DEFAULT 'phone_jid UNIQUE, untouched (text)'{final_constraint})''')
        conn.exec_driver_sql('CREATE INDEX maintained_link_board ON whatsapp_phone_links(board_id)')
        conn.exec_driver_sql('CREATE TABLE link_audit (link_id INTEGER)')
        conn.exec_driver_sql('CREATE TRIGGER maintained_link_update AFTER UPDATE ON whatsapp_phone_links BEGIN INSERT INTO link_audit VALUES(NEW.id); END')
        conn.exec_driver_sql("INSERT INTO whatsapp_phone_links(id,board_id,phone_jid,phone_number,contact_name,auto_snapshot,maintained_extra) VALUES(37,7,'group@g.us','group','Maintained',1,'Exact value')")
        conn.exec_driver_sql('CREATE TABLE link_children(link_id INTEGER REFERENCES whatsapp_phone_links(id))')
        conn.exec_driver_sql('INSERT INTO link_children VALUES(37)')
        conn.exec_driver_sql("INSERT INTO whatsapp_phone_links(id,board_id,phone_jid) VALUES(99,9,'deleted@g.us')")
        conn.exec_driver_sql('DELETE FROM whatsapp_phone_links WHERE id=99')
    for _ in range(2):
        ensure_whatsapp_board_link_schema(engine)
    with engine.begin() as conn:
        row = conn.execute(text('SELECT * FROM whatsapp_phone_links WHERE id=37')).mappings().one()
        assert row['contact_name'] == 'Maintained' and row['auto_snapshot'] == 1 and row['maintained_extra'] == 'Exact value'
        assert conn.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        assert conn.exec_driver_sql('SELECT link_id FROM link_children').all() == [(37,)]
        conn.exec_driver_sql("INSERT INTO whatsapp_phone_links(board_id,phone_jid) VALUES(8,'group@g.us')")
        assert conn.exec_driver_sql('SELECT MAX(id) FROM whatsapp_phone_links').scalar_one() == 100
        assert conn.exec_driver_sql('SELECT maintained_extra FROM whatsapp_phone_links WHERE id=100').scalar_one() == 'phone_jid UNIQUE, untouched (text)'
        if constraint == 'inline':
            defaults = {row[1]: row[4] for row in conn.exec_driver_sql('PRAGMA table_info(whatsapp_phone_links)')}
            assert defaults['phone_jid'] == "'phone_jid UNIQUE literal'"
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE name='maintained_link_board'").scalar_one() == 1
        conn.exec_driver_sql("UPDATE whatsapp_phone_links SET contact_name='Updated' WHERE id=37")
        assert conn.exec_driver_sql('SELECT link_id FROM link_audit').all() == [(37,)]
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO whatsapp_phone_links(board_id,phone_jid) VALUES(7,'group@g.us')")


def test_index_only_migration_keeps_existing_table_and_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old-index-links.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE whatsapp_phone_links(id INTEGER PRIMARY KEY, board_id INTEGER, phone_jid TEXT, extra TEXT)')
        conn.exec_driver_sql('CREATE UNIQUE INDEX uq_old_jid ON whatsapp_phone_links(phone_jid)')
        conn.exec_driver_sql("INSERT INTO whatsapp_phone_links VALUES(7,1,'group@g.us','Keep')")
    ensure_whatsapp_board_link_schema(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO whatsapp_phone_links VALUES(8,2,'group@g.us','Also keep')")
        assert conn.exec_driver_sql('SELECT extra FROM whatsapp_phone_links ORDER BY id').all() == [('Keep',), ('Also keep',)]
