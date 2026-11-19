"""Customer questions amend related work, preserving identity across retries."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest

from distr.core.incoming.followup_policy import rank_followups
from distr.core.incoming import proposals, followups


def source(**changes):
    return dict({'id': 'local:whatsapp:group', 'source': 'whatsapp', 'source_thread_id': 'group',
                 'project_id': 7, 'title': 'Investigate incorrect DELIVERY3 charge',
                 'description': 'Customer asks why the delivery charge was incorrect.', 'message_ids': ['original']}, **changes)


def test_same_policy_is_used_in_both_products():
    portal = Path(__file__).resolve().parents[4] / 'DONE/www.tensology.com/backend/webapp/apps/main/incoming_followup_policy.py'
    if not portal.is_file():
        pytest.skip('The sibling Tensology checkout is required for cross-product policy parity.')
    assert portal.read_bytes() == Path(followups.__file__).with_name('followup_policy.py').read_bytes()


def test_best_match_prefers_topic_over_recency_and_never_crosses_scope():
    question = source(title='Explain the incorrect DELIVERY3 charge', description='Any idea how this happened?', message_ids=['question'])
    previous = source(thread_id=12, created_at='2026-11-17')
    other = source(thread_id=15, title='Update the homepage logo', description='Replace the logo', created_at='2026-11-18')
    wrong_project = dict(previous, project_id=8, thread_id=20)
    wrong_conversation = dict(previous, source_thread_id='other', thread_id=21)
    wrong_channel = dict(previous, source='gmail', thread_id=22)
    ranked = rank_followups(question, [other, wrong_project, wrong_conversation, wrong_channel, previous])
    assert [row['thread_id'] for row in ranked] == [12, 15]
    assert rank_followups(source(title='Add stock export', description='Implement a CSV stock export', message_ids=['new']), [other]) == []


def test_ambiguous_followup_uses_most_recent_best_match():
    question = source(title='Customer follow-up', description='Any idea how this happened?', message_ids=['new'])
    candidates = [source(thread_id=1, created_at='2026-11-16'), source(thread_id=2, created_at='2026-11-17')]
    assert rank_followups(question, candidates)[0]['thread_id'] == 2
    question['source_context_message_ids'] = ['older-evidence']
    candidates[0]['message_ids'] = ['older-evidence']
    assert rank_followups(question, candidates)[0]['thread_id'] == 1


@pytest.fixture
def work():
    from distr.core.db import Chat, get_session
    from distr.core.db.projects import Project
    from distr.core.db.workflow import DevelopmentWorkItem
    with get_session() as db:
        project = Project(name='Incoming followup fixture')
        db.add(project)
        db.flush()
        original = source(project_id=project.id, id=f'local:whatsapp:group-{project.id}', source_thread_id=f'group-{project.id}')
        root = Chat(title=original['title'], project_id=project.id, params=json.dumps({
            'development': {'execution': {'status': 'completed'}},
            'incoming_proposal_handoff': {'key': proposals._proposal_handoff_key(original), 'proposal': original, 'started': True}}))
        db.add(root)
        db.flush()
        db.add(DevelopmentWorkItem(chat_id=root.id, project_id=project.id, identity_key=f'followup-fixture:{root.id}'))
        return root.id, original


def test_followup_retains_original_receipt_and_old_retry_identity(work):
    chat_id, original = work
    new = dict(original, message_ids=['question'], title='Explain incorrect DELIVERY3 charge', description='Any idea how this happened?')
    assert followups.followup_thread(new)['thread_id'] == chat_id
    proposals._record_proposal_handoff(chat_id, new, project_id=original['project_id'], started=True, followup=True)
    assert proposals._existing_proposal_thread(original, original['project_id']) == chat_id
    assert proposals._existing_proposal_thread(new, original['project_id']) == chat_id
    assert {proposals._proposal_handoff_key(original), proposals._proposal_handoff_key(new)} <= proposals._accepted_proposal_keys(include_terminal=True)
    assert followups.followup_thread(new) is None
    assert followups.followup_thread(new, include_accepted=True)['thread_id'] == chat_id
    assert proposals.resolve_proposal_review(proposals.proposal_review_key(original))['chat_id'] == chat_id


def test_cancelled_work_and_explicit_merges_are_not_implicitly_reopened(work):
    from distr.core.db import Chat, get_session
    chat_id, original = work
    question = dict(original, message_ids=['question'], description='Any idea how this happened?')
    assert followups.followup_thread(dict(question, requires_merge_receipt=True)) is None
    with get_session() as db:
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params['development']['execution']['status'] = 'cancelled'
        root.params = json.dumps(params)
    assert followups.followup_thread(question) is None
    with pytest.raises(ValueError, match='cancelled work'):
        followups.append_followup(chat_id, question, start_work=True)


def test_append_is_durable_and_idempotent_and_reopens_completed_thread(work, monkeypatch):
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import DevelopmentCommand
    chat_id, original = work
    new = dict(original, message_ids=['question'], title='Customer follow-up', description='Any idea how this happened?')
    with get_session() as db:
        db.get(Chat, chat_id).is_archived = True
    launch = Mock()
    monkeypatch.setattr('distr.core.workflow.queued_submissions.start_next_followup', launch)
    monkeypatch.setattr('distr.core.workflow.development_harness.development_execution_active', lambda _: False)
    first = followups.append_followup(chat_id, new, start_work=True)
    # Simulate the durable queue claim made by the actual launcher.
    with get_session() as db:
        db.get(DevelopmentCommand, first['id']).status = 'delivered'
    again = followups.append_followup(chat_id, new, start_work=True)
    assert first['id'] == again['id']
    assert launch.call_count == 1
    with get_session() as db:
        assert db.get(Chat, chat_id).is_archived is False
        assert db.query(DevelopmentCommand).filter_by(chat_id=chat_id).count() == 1
        children = db.query(Chat).filter_by(parent_id=chat_id).all()
        assert len(children) == 1
        assert 'Any idea how this happened?' in children[0].input


def test_active_work_queues_followup_without_starting_second_worker(work, monkeypatch):
    chat_id, original = work
    monkeypatch.setattr('distr.core.workflow.development_harness.development_execution_active', lambda _: True)
    launch = Mock()
    monkeypatch.setattr('distr.core.workflow.queued_submissions.start_next_followup', launch)
    command = followups.append_followup(chat_id, dict(original, message_ids=['active-question']), start_work=True)
    assert command['status'] == 'queued'
    launch.assert_not_called()


def test_long_source_identifiers_keep_retry_identity(work, monkeypatch):
    chat_id, original = work
    monkeypatch.setattr('distr.core.workflow.development_harness.development_execution_active', lambda _: True)
    question = dict(original, id='local:gmail:' + 'a' * 255, source='gmail',
                    source_thread_id='a' * 255, message_ids=['long-question'])
    first = followups.append_followup(chat_id, question, start_work=True)
    assert followups.append_followup(chat_id, question, start_work=True)['id'] == first['id']


def test_accept_followup_reuses_root_and_card_and_preserves_original(work, monkeypatch):
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import DevelopmentCommand
    chat_id, original = work
    new = dict(original, message_ids=['accepted-question'], title='Explain incorrect DELIVERY3 charge',
               description='Any idea how this happened?')
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *a, **k: True)
    monkeypatch.setattr('distr.core.workflow.development_harness.development_execution_active', lambda _: False)
    create = Mock(side_effect=AssertionError('A follow-up must reuse its thread'))
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', create)
    def deliver(root, command, revision):
        with get_session() as db:
            db.get(DevelopmentCommand, command).status = 'delivered'
    monkeypatch.setattr('distr.core.workflow.queued_submissions.start_next_followup', deliver)
    assert proposals.create_proposal_thread(new, start_work=True) == chat_id
    assert proposals.create_proposal_thread(new, start_work=True) == chat_id
    assert proposals._existing_proposal_thread(original, original['project_id']) == chat_id
    with get_session() as db:
        assert db.query(DevelopmentCommand).filter_by(chat_id=chat_id).count() == 1
        assert db.get(Chat, chat_id).title == original['title']
        assert len(json.loads(db.get(Chat, chat_id).params)['incoming_proposal_history']) == 1


def test_server_ticket_amendments_use_same_queue_once(work, monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.workflow import DevelopmentCommand
    chat_id, original = work
    monkeypatch.setattr('distr.core.workflow.development_harness.development_execution_active', lambda _: True)
    ticket = {'id': 941, 'incoming_followups': [{'id': 62, 'dispatched': True, 'title': 'Customer follow-up',
               'description': 'Any idea how this happened?', 'message_ids': ['server-question'], 'discussion_message_id': 15}],
               'discussion': [{'id': 15, 'content': 'Exact received question: Any idea how this happened?'}]}
    followups.append_ticket_followups(ticket, chat_id, original['project_id'])
    followups.append_ticket_followups(ticket, chat_id, original['project_id'])
    with get_session() as db:
        assert db.query(DevelopmentCommand).filter_by(chat_id=chat_id).count() == 1
        assert 'Exact received question:' in db.query(DevelopmentCommand).filter_by(chat_id=chat_id).one().content


def test_foreign_customer_owner_rejected_before_local_thread_mutation(monkeypatch):
    from distr.core.tensology_client import TensologyApiError
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *a, **k: True)
    client = Mock()
    client.get.return_value = {'followup': {'connection_id': 'other-customer'}, 'attachments': []}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **k: client)
    with pytest.raises(ValueError, match='owning machine'):
        proposals.create_proposal_thread({'id': 'tensology:992', 'project_id': 7})
    client.post.assert_not_called()


def test_auto_accept_uses_existing_thread_and_never_repeats_snapshot(work, monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.workflow import DevelopmentCommand
    chat_id, original = work
    question = dict(original, origin='local', project_name='Incoming followup fixture',
                    message_ids=['automatic-question'], title='Explain delivery code', description='Any idea how this happened?')
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {
        'incoming_proposals_auto_accept': True, 'incoming_proposals_auto_accept_projects': ['Incoming followup fixture']})
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *a, **k: True)
    monkeypatch.setattr('distr.core.workflow.development_harness.development_execution_active', lambda _: True)
    assert proposals.auto_accept_proposals([question]) == [proposals._proposal_handoff_key(question)]
    assert proposals.auto_accept_proposals([question]) == []
    with get_session() as db:
        assert db.query(DevelopmentCommand).filter_by(chat_id=chat_id).count() == 1


def test_direct_acceptance_and_later_poll_share_canonical_snapshot_key(work, monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.workflow import DevelopmentCommand
    chat_id, original = work
    accepted = dict(original, id='tensology:887')
    proposals._record_proposal_handoff(chat_id, accepted, project_id=original['project_id'], started=True, followup=True)
    monkeypatch.setattr('distr.core.workflow.development_harness.development_execution_active', lambda _: True)
    ticket = {'id': 541, 'incoming_followups': [{'id': 887, 'dispatched': True, 'title': 'Previously accepted'},
                                            {'id': 888, 'dispatched': False, 'title': 'Queued only'}]}
    followups.append_ticket_followups(ticket, chat_id, original['project_id'])
    with get_session() as db:
        assert db.query(DevelopmentCommand).filter_by(chat_id=chat_id).count() == 0


def test_new_machine_does_not_execute_followups_already_in_its_initial_prompt(work):
    from distr.core.db import get_session
    from distr.core.db.workflow import DevelopmentCommand
    chat_id, original = work
    ticket = {'id': 501, 'incoming_followups': [{'id': 899, 'dispatched': True, 'title': 'Follow-up',
                 'discussion_message_id': 19}], 'discussion': [{'id': 19, 'content': 'Original customer question'}]}
    followups.append_ticket_followups(ticket, chat_id, original['project_id'], included_in_initial_prompt=True)
    assert 'tensology:899' in proposals._accepted_proposal_keys(include_terminal=True)
    with get_session() as db:
        assert db.query(DevelopmentCommand).filter_by(chat_id=chat_id).count() == 0
    from distr.core.tensology_client import _execution_prompt
    prompt = _execution_prompt(ticket, 'low')
    assert 'Continue the existing work' in prompt
    assert '"discussion_message_id": 19' in prompt
