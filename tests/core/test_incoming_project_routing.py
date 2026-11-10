import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core.incoming import proposals
from distr.core.incoming import media


@pytest.fixture
def projects(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr('distr.core.db.get_session', sessions)
    with sessions() as db:
        db.add_all([
            Project(id=3, name='Correct project', kanban_board_id=16),
            Project(id=16, name='Unrelated project whose id equals the board id'),
            Project(id=7, name='Another project'),
        ])
        db.add(KanbanBoard(id=16, name='Linked board', source='database', default_project_id=3))
        db.commit()
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [
        {'id': 3, 'name': 'Correct project'}, {'id': 16, 'name': 'Unrelated project'}, {'id': 7, 'name': 'Another project'},
    ])
    monkeypatch.setattr(proposals, '_triage_model', lambda _: ('fixture', 'fixture'))
    monkeypatch.setattr(proposals, '_dismissed_message_coverage', lambda: {})
    monkeypatch.setattr(media, 'list_proposal_attachment_manifest', lambda _: [])
    yield sessions
    engine.dispose()


def item(**changes):
    return {'source': 'whatsapp', 'source_thread_id': 'fixture-thread', 'source_message_id': 'message-1',
            'text': 'Please investigate the missing catalogue item.', 'sender': 'Client', 'direction': 'inbound',
            'created_at': '2026-11-09T08:00:00Z', **changes}


def triage(row, project_id=None):
    return proposals._triage_conversations([row], {}, complete=lambda *_: json.dumps({
        'is_actionable': True, 'project_id': project_id, 'title': 'Investigate catalogue item',
    }))


def test_board_id_resolves_to_project_identity_even_when_same_number_is_another_project(projects):
    grouped = proposals._group_conversations([item(board_id=16)])
    assert grouped[0]['project_id'] == 3
    assert triage(item(board_id=16))[0]['project_id'] == 3


def test_valid_explicit_project_identity_is_not_reinterpreted_as_board(projects):
    assert triage(item(project_id=16, board_id=16))[0]['project_id'] == 16


def test_missing_board_mapping_never_invents_project_with_board_id(projects, caplog):
    with projects() as db:
        db.add(KanbanBoard(id=7, name='Unmapped board', source='database'))
        db.commit()
    assert triage(item(board_id=7)) == []
    # An unmapped source must not reach semantic classification at all.
    assert triage(item(board_id=7), project_id=3) == []


def test_conflicting_board_default_and_project_link_do_not_guess(projects):
    with projects() as db:
        db.get(Project, 3).kanban_board_id = None
        db.get(Project, 7).kanban_board_id = 16
        db.commit()
    grouped = proposals._group_conversations([item(board_id=16)])
    assert grouped[0]['project_id'] is None
    assert grouped[0]['routing_error']
    assert triage(item(board_id=16)) == []


def test_project_link_can_resolve_board_without_default(projects):
    with projects() as db:
        db.get(KanbanBoard, 16).default_project_id = None
        db.commit()
    assert triage(item(board_id=16))[0]['project_id'] == 3


def test_invalid_or_missing_project_identity_is_not_invented(projects):
    for supplied in (999, True, 'not-a-project', 7.5):
        assert triage(item(project_id=supplied)) == []
    for suggested in (999, True, 7.5, 'not-a-project'):
        assert triage(item(), project_id=suggested) == []


def test_conflicting_conversation_project_links_disable_fallback(projects):
    rows = [item(project_id=3), item(project_id=7, source_message_id='message-2')]
    grouped = proposals._group_conversations(rows)
    assert grouped[0]['project_id'] is None
    assert grouped[0]['routing_error'] == 'This conversation has conflicting project links.'
