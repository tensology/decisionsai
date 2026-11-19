"""An explicitly linked email produces reviewable work with its own source identity."""
from unittest.mock import Mock
import pytest

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_sync_regression_links(monkeypatch, tmp_path):
    from distr.core.incoming import project_links
    monkeypatch.setattr(project_links, '_cache_path', lambda: tmp_path / 'links.json')
    monkeypatch.setattr('distr.core.initiative.attention.store_path', lambda: tmp_path / 'attention.json')
    monkeypatch.setattr('distr.core.incoming.proposal_conversation._mail_conversation',
                        lambda source, thread, ids: ([{'source_message_id': ids[0], 'sender': 'client@example.test'}], ids[0]))
    from distr.core.db import IncomingMailProjectLink, get_session
    yield
    with get_session() as db:
        db.query(IncomingMailProjectLink).filter(IncomingMailProjectLink.thread_id.in_(
            ['canonical-assignment', 'remote-conflict', 'remote-success', 'archived-regression'])).delete(synchronize_session=False)


def test_tensology_assignment_and_removal_override_stale_local_link(monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links
    with get_session() as db:
        first, second = Project(name='Local old assignment'), Project(name='Remote changed assignment')
        db.add_all([first, second])
        db.flush()
        old_id, new_id = first.id, second.id
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    project_links.set_mail_project_link('gmail', 'canonical-assignment', 'mail-one', old_id)
    remote = {'source': 'gmail', 'source_thread_id': 'canonical-assignment',
              'projects': [{'decisions_project_id': new_id}]}
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: [remote])
    assert project_links.source_project_index()[('gmail', 'canonical-assignment')] == {new_id}
    remote['projects'] = []
    assert project_links.source_project_index()[('gmail', 'canonical-assignment')] == set()


def test_connected_mail_assignment_never_saves_locally_if_remote_save_fails(monkeypatch):
    import pytest
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links
    from distr.core.tensology_client import TensologyApiError
    with get_session() as db:
        project = Project(name='Sync failure assignment')
        db.add(project)
        db.flush()
        project_id = project.id
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'tensology_enabled': True})
    remote = Mock()
    remote.get.side_effect = lambda path: ([{'id': 91, 'decisions_project_id': project_id}] if path == 'projects' else [])
    remote.patch.side_effect = TensologyApiError('Changed elsewhere', status=409)
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: remote)
    with pytest.raises(TensologyApiError):
        project_links.set_mail_project_link('gmail', 'remote-conflict', 'mail-one', project_id)
    assert ('gmail', 'remote-conflict') not in project_links.mail_project_links()


def test_connected_mail_assignment_confirms_remote_before_local_save(monkeypatch, tmp_path):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links
    with get_session() as db:
        project = Project(name='Sync success assignment')
        db.add(project)
        db.flush()
        project_id = project.id
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'tensology_enabled': True})
    monkeypatch.setattr(project_links, '_cache_path', lambda: tmp_path / 'project-links.json')
    row = {'source': 'gmail', 'source_thread_id': 'remote-success', 'revision': 'old', 'sender_revision': '', 'projects': []}
    remote = Mock()
    remote.get.side_effect = lambda path: ([{'id': 92, 'decisions_project_id': project_id}] if path == 'projects' else [row])
    sender = {'source': 'mail_sender', 'source_thread_id': 'client@example.test', 'revision': 'sender-new',
              'projects': [{'id': 92, 'decisions_project_id': project_id}]}
    remote.patch.return_value = {**row, 'revision': 'new', 'projects': sender['projects'], 'links': [sender, {**row, 'projects': sender['projects']}]}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: remote)
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: [remote.patch.return_value])
    result = project_links.set_mail_project_link('gmail', 'remote-success', 'mail-one', project_id)
    assert result['synced'] is True
    assert project_links.mail_project_links()[('gmail', 'remote-success')] == (project_id, 'mail-one')
    assert remote.patch.call_args.args[1] == {'source': 'gmail', 'source_thread_id': 'remote-success',
                                           'project_ids': [92], 'revision': 'old', 'scope': 'sender', 'action': 'add',
                                           'sender_email': 'client@example.test', 'sender_revision': ''}


def test_linked_gmail_and_mailshot_are_checked_and_deduplicated(monkeypatch):
    from distr.core.db import IncomingMailProjectLink, get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import mail_check, project_links, proposals
    from distr.core.tensology_client import TensologyApiError
    from distr.gui.web.routes.development.incoming import register_routes

    monkeypatch.setattr(project_links, "project_links", lambda **kwargs: [])
    monkeypatch.setattr("distr.core.incoming.proposal_sync.fetch_remote_proposals", lambda: [])
    monkeypatch.setattr("distr.core.tensology_client.configured_tensology_client",
                        Mock(side_effect=TensologyApiError("No key", code="not_configured")))
    monkeypatch.setattr(mail_check, "_status", {})
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **kwargs: {'items': [], 'channels': {}})
    with get_session() as db:
        first, second = Project(name="Device workflow"), Project(name="Customer portal")
        db.add_all([first, second])
        db.flush()
        device_id, portal_id = first.id, second.id

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    endpoint = "/workflows/studio/incoming"
    # Serve one exact alert and one exact Gmail thread with the same adapter ID.
    # The channels must never share the other's project assignment.
    def read(source, thread, ids):
        title = "[Django] Device Action Task Failed: 2207" if source == "mailshot" else "Portal request"
        body = "An operation failed.\nTraceback (most recent call last):\nRuntimeError: state mismatch" if source == "mailshot" else "Please add a search field to the portal."
        return [{"source": source, "source_thread_id": thread, "source_message_id": ids[0],
                 "sender": "no-reply@example.test" if source == "mailshot" else "client@example.test",
                 "conversation_label": title, "text": body, "created_at": "2026-11-14T12:05:00Z"}], ids[0]

    monkeypatch.setattr("distr.core.incoming.proposal_conversation._mail_conversation", read)
    completed = Mock(return_value='{"is_actionable":true,"title":"Add portal search","tasks":["Add a search field"],"confidence":0.99}')
    monkeypatch.setattr("distr.core.read_response._default_complete", completed)
    mail = {"source": "mailshot", "thread_id": "mail-link-thread", "message_id": "mail-link-id", "project_id": device_id}
    gmail = {**mail, "source": "gmail", "project_id": portal_id}
    assert client.put(endpoint + "/mail/project", json={**mail, "project_id": True}).status_code == 422
    assert client.put(endpoint + "/mail/project", json={**mail, "thread_id": "../other"}).status_code == 422
    try:
        for payload in (mail, gmail):
            assert client.put(endpoint + "/mail/project", json=payload).json()["project_id"] == payload["project_id"]
        inbox = [{"source": row["source"], "source_thread_id": row["thread_id"]} for row in (mail, gmail)]
        inbox.append({"source": "gmail", "source_thread_id": "different-thread", "sender": "client@example.test"})
        assert [row.get("project_id") for row in project_links.apply_project_links(inbox)] == [device_id, portal_id, portal_id]

        class RunOnDemand:
            def __init__(self, target, args, **kwargs):
                self.run = lambda: target(*args)

            def start(self):
                pending.append(self.run)

        pending = []
        monkeypatch.setattr(mail_check, "threading", Mock(Thread=RunOnDemand))
        assert client.get(endpoint + "/check").status_code == 503
        result = client.post(endpoint + "/check").json()
        assert result["checks"]["local_mail"]["source_state"] == "queued"
        assert result["checks"]["tensology"]["source_state"] == "failed"
        assert len(pending) == 1
        pending.pop()()
        result = client.get(endpoint + "/check").json()["checks"]["local_mail"]
        assert result == {"source_state": "complete", "recalibration": {"updated": 2}}
        rows = [row for row in proposals._saved_local_proposals() if row.get("source_thread_id") == mail["thread_id"]]
        assert {(row["source"], row["project_id"]) for row in rows} == {("mailshot", device_id), ("gmail", portal_id)}
        incident = next(row for row in rows if row["source"] == "mailshot")
        assert incident["title"] == "Investigate Device Action Task Failed: 2207"
        assert len(incident["tasks"]) == 3
        assert incident["message_ids"] == [mail["message_id"]]
        completed.assert_called_once()
        assert mail_check.check_linked_mail(project_links.mail_project_links()) == (0, False)
        assert client.put(endpoint + "/mail/project", json={**mail, "project_id": portal_id}).status_code == 200
        assert mail_check.check_linked_mail(project_links.mail_project_links()) == (0, False)
        assert proposals.apply_project_corrections([incident])[0]['project_id'] == device_id
        assert project_links.source_project_index()[('mailshot', mail['thread_id'])] == {device_id, portal_id}
    finally:
        with get_session() as db:
            db.query(IncomingMailProjectLink).filter_by(thread_id=mail["thread_id"]).delete()


def test_check_discovers_new_alert_for_remembered_sender(monkeypatch, tmp_path):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import mail_check, project_links, proposals
    from distr.core.initiative import attention

    monkeypatch.setattr(attention, "store_path", lambda: tmp_path / "attention.json")
    monkeypatch.setattr(project_links, "project_links", lambda **kwargs: [])
    monkeypatch.setattr("distr.core.incoming.proposal_sync.fetch_remote_proposals", lambda: [])
    monkeypatch.setattr(mail_check, "_status", {})
    with get_session() as db:
        project = Project(name="Spoortrack sender test")
        db.add(project)
        db.flush()
        project_id = project.id
    attention.link_address("developers@spoortrack.example", project_id)
    alert = {"source": "mailshot", "source_thread_id": "next-alert", "source_message_id": "new-mail",
             "conversation_label": "[Django] Device Action Task Failed",
             "text": "Traceback (most recent call last):\nRuntimeError: state mismatch",
             "sender": "Spoortrack Developers <DEVELOPERS@spoortrack.example>", "processed": True}
    unrelated = {**alert, "source_thread_id": "other-thread", "source_message_id": "other-mail",
                 "sender": '"developers@spoortrack.example" <sales@spoortrack.example>',
                 "recipient": "developers@spoortrack.example"}
    monkeypatch.setattr("distr.core.incoming.service.list_development_incoming",
                        lambda **kwargs: {"items": project_links.apply_project_links([dict(alert), dict(unrelated)])})
    read = Mock(return_value=([alert], alert["source_message_id"]))
    monkeypatch.setattr("distr.core.incoming.proposal_conversation._mail_conversation", read)
    pending = []
    thread = Mock(side_effect=lambda target, args, **kwargs: Mock(start=lambda: pending.append(lambda: target(*args))))
    monkeypatch.setattr(mail_check, "threading", Mock(Thread=thread))
    assert mail_check.start_mail_check()["source_state"] == "queued"
    pending.pop()()
    assert mail_check.mail_check_status() == {"source_state": "complete", "recalibration": {"updated": 1}}
    read.assert_called_once_with("mailshot", "next-alert", ["new-mail"])
    rows = [row for row in proposals._saved_local_proposals() if row.get("source_thread_id") == "next-alert"]
    assert len(rows) == 1 and rows[0]["project_id"] == project_id
    assert mail_check.start_mail_check()["source_state"] == "queued"
    pending.pop()()
    assert mail_check.mail_check_status()["recalibration"]["updated"] == 0


def test_sender_cache_updates_existing_future_and_removal_without_resurrecting_local(monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links
    from distr.core.initiative import attention
    with get_session() as db:
        project = Project(name='Sender canonical scope')
        db.add(project)
        db.flush()
        pid = project.id
    attention.link_address('client@example.test', pid)
    attention.learn_thread_link('gmail', 'learned-sender', [pid], 'email:client@example.test')
    sender = {'source': 'mail_sender', 'source_thread_id': 'client@example.test',
              'projects': [{'decisions_project_id': pid}]}
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: [sender])
    items = [{'source': 'gmail', 'source_thread_id': thread, 'sender': address}
             for thread, address in [('future-thread', 'Client <client@example.test>'),
                                     ('impostor', 'client@example.test <other@example.test>')]]
    assert [row['project_ids'] for row in project_links.apply_project_links(items)] == [[pid], []]
    assert project_links.source_project_index()[('gmail', 'future-thread')] == {pid}
    archived = {'source': 'gmail', 'source_thread_id': 'archived-thread', 'sender_email': 'client@example.test',
                'direct_projects': [{'decisions_project_id': pid}], 'projects': [], 'is_archived': True}
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: [sender, archived])
    assert project_links.source_project_index()[('gmail', 'archived-thread')] == set()
    assert project_links.apply_project_links([{'source': 'gmail', 'source_thread_id': 'archived-thread',
                                             'sender': 'client@example.test'}])[0]['project_ids'] == []
    sender['projects'] = []
    assert project_links.source_project_index()[('gmail', 'future-thread')] == set()
    assert project_links.source_project_index()[('gmail', 'learned-sender')] == set()
    assert project_links.apply_project_links(items)[0]['project_ids'] == []


def test_sender_assignment_rejects_outgoing_and_invalid_original(monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links
    with get_session() as db:
        project = Project(name='Verified sender scope')
        db.add(project)
        db.flush()
        pid = project.id
    for original in [{'sender': 'client@example.test', 'from_me': True}, {'sender': 'not an email'}]:
        monkeypatch.setattr('distr.core.incoming.proposal_conversation._mail_conversation',
                            lambda *args: ([dict(original, source_message_id='message')], 'message'))
        with pytest.raises(ValueError, match='received message'):
            project_links.set_mail_project_link('gmail', 'verified-sender', 'message', pid)


def test_unlink_sender_preserves_other_projects_and_cannot_revive_old_threads(monkeypatch):
    from distr.core.db import get_session, IncomingMailProjectLink
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links
    from distr.core.initiative import attention
    with get_session() as db:
        first, second = Project(name='Remove sender project'), Project(name='Retain sender project')
        db.add_all([first, second])
        db.flush()
        removed, retained = first.id, second.id
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    project_links.set_mail_project_link('gmail', 'sender-unlink-one', 'mail-one', removed)
    project_links.set_mail_project_link('mailshot', 'sender-unlink-two', 'mail-two', removed)
    attention.link_address('client@example.test', retained)
    result = project_links.set_mail_project_link('gmail', 'sender-unlink-one', 'mail-one', removed, remove=True)
    assert result['project_ids'] == [retained]
    items = [{'source': source, 'source_thread_id': thread, 'sender': 'client@example.test'}
             for source, thread in [('gmail', 'sender-unlink-one'), ('mailshot', 'sender-unlink-two'), ('gmail', 'future-unlink')]]
    assert [row['project_ids'] for row in project_links.apply_project_links(items)] == [[retained]] * 3
    with get_session() as db:
        assert db.query(IncomingMailProjectLink).filter_by(project_id=removed).count() == 0
    project_links.set_mail_project_link('gmail', 'sender-unlink-one', 'mail-one', retained, remove=True)
    assert [row['project_ids'] for row in project_links.apply_project_links(items)] == [[], [], []]


def test_connected_unlink_confirms_sender_tombstone_before_removing_local_link(monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links
    from distr.core.tensology_client import TensologyApiError
    from distr.core.initiative import attention
    with get_session() as db:
        project = Project(name='Connected sender unlink')
        db.add(project)
        db.flush()
        pid = project.id
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    project_links.set_mail_project_link('gmail', 'connected-unlink', 'mail-one', pid)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'tensology_enabled': True})
    sender = {'source': 'mail_sender', 'source_thread_id': 'client@example.test', 'revision': 'old',
              'projects': [{'id': 91, 'decisions_project_id': pid}]}
    row = {'source': 'gmail', 'source_thread_id': 'connected-unlink', 'revision': 'conversation',
           'sender_revision': 'old', 'projects': sender['projects']}
    client = Mock()
    client.get.side_effect = lambda path: [{'id': 91, 'decisions_project_id': pid}] if path == 'projects' else [sender, row]
    client.patch.side_effect = TensologyApiError('Changed elsewhere', status=409)
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **k: client)
    with pytest.raises(TensologyApiError):
        project_links.set_mail_project_link('gmail', 'connected-unlink', 'mail-one', pid, remove=True)
    assert attention.address_project_ids('client@example.test')[0] == {pid}
    client.patch.side_effect = None
    client.patch.return_value = {**row, 'projects': [], 'links': [{**row, 'projects': []}, {**sender, 'projects': [], 'link_explicit': True}]}
    result = project_links.set_mail_project_link('gmail', 'connected-unlink', 'mail-one', pid, remove=True)
    assert result['synced'] and result['removed']
    assert client.patch.call_args.args[1]['action'] == 'remove'
    assert attention.address_project_ids('client@example.test')[0] == set()
    assert ('gmail', 'connected-unlink') not in project_links.mail_project_links()


@pytest.mark.parametrize('remove', [False, True])
def test_address_controls_confirm_remote_before_changing_local_state(monkeypatch, remove):
    from distr.core.initiative import attention
    from distr.core.incoming import project_links
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.tensology_client import TensologyApiError
    with get_session() as db:
        project = Project(name='Address sync regression')
        db.add(project); db.flush(); pid = project.id
    attention.set_sender_projects('customer@example.test', [pid])
    before = attention.load()
    remote = Mock(side_effect=TensologyApiError('Changed elsewhere', status=409))
    monkeypatch.setattr(project_links, 'sync_address_projects', remote)
    with pytest.raises(TensologyApiError, match='Changed elsewhere'):
        (attention.unlink_address if remove else attention.link_address)('customer@example.test', pid)
    assert attention.load() == before
    assert remote.call_args.kwargs == ({'remove': True} if remove else {})


def test_address_unlink_all_is_one_atomic_sender_delta(monkeypatch):
    from distr.core.initiative import attention
    from distr.core.incoming import project_links, link_exchange
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'tensology_enabled': True})
    attention.set_sender_projects('customer@example.test', [2])
    rows = [{'source': 'gmail', 'source_thread_id': 'known-thread', 'sender_email': 'customer@example.test'},
            {'source': 'mail_sender', 'source_thread_id': 'customer@example.test',
             'projects': [{'decisions_project_id': 2}, {'decisions_project_id': 3}]}]
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: rows)
    delta = Mock(return_value={'synced': True, 'project_ids': []})
    monkeypatch.setattr(link_exchange, 'sync_project_deltas', delta)
    assert attention.unlink_address('customer@example.test')
    delta.assert_called_once_with('gmail', 'known-thread', [2, 3], sender='customer@example.test', remove=True)
    assert attention.address_links()['email:customer@example.test']['explicit_empty']
    assert attention.address_project_ids('customer@example.test')[0] == set()


def test_address_remove_preserves_canonical_other_projects(monkeypatch):
    from distr.core.initiative import attention
    from distr.core.incoming import project_links
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    with get_session() as db:
        project = Project(name='Address selected removal'); db.add(project); db.flush(); pid = project.id
    attention.set_sender_projects('customer@example.test', [pid])
    attention.learn_thread_link('mailshot', 'known', [pid], 'email:customer@example.test')
    monkeypatch.setattr(project_links, 'sync_address_projects', Mock(return_value={'synced': True, 'project_ids': [991]}))
    attention.unlink_address('customer@example.test', pid)
    assert attention.address_project_ids('customer@example.test')[0] == {991}
    assert attention.learned_thread_links()[('mailshot', 'known')] == {991}


def test_address_control_without_known_sender_does_not_invent_conversation(monkeypatch):
    from distr.core.incoming import project_links, link_exchange
    from distr.core.db import IncomingMailProjectLink, get_session
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'tensology_enabled': True})
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: [])
    monkeypatch.setattr(project_links, 'verified_mail_sender', lambda *args: 'other@example.test')
    delta = Mock(); monkeypatch.setattr(link_exchange, 'sync_project_deltas', delta)
    with pytest.raises(ValueError, match='received Gmail or Mailshot'):
        project_links.sync_address_projects('unknown@example.test', [2])
    delta.assert_not_called()


@pytest.mark.parametrize('method', ['post', 'delete'])
def test_address_routes_surface_remote_conflict_without_claiming_success(monkeypatch, method):
    from distr.core.initiative import attention
    from distr.core.tensology_client import TensologyApiError
    from distr.gui.web.routes.settings.initiative_attention import register_routes
    app, router = FastAPI(), APIRouter(); register_routes(router, None); app.include_router(router)
    failure = Mock(side_effect=TensologyApiError('Changed elsewhere', status=409))
    monkeypatch.setattr(attention, 'link_address' if method == 'post' else 'unlink_address', failure)
    client = TestClient(app)
    response = (client.post('/initiative/attention/email-links', json={'address': 'customer@example.test', 'project': '2'})
                if method == 'post' else client.delete('/initiative/attention/email-links?address=customer@example.test&project=2'))
    assert response.status_code == 409
    assert response.json() == {'success': False, 'error': 'Changed elsewhere'}


@pytest.mark.parametrize('action', ['link_email', 'unlink_email'])
def test_agent_address_tool_surfaces_failed_sync_without_success_claim(monkeypatch, action):
    from distr.core.initiative import attention
    from distr.core.tensology_client import TensologyApiError
    from distr.core.agent.tools.system.initiative_attention_tool import InitiativeAttentionTool
    target = 'link_address' if action == 'link_email' else 'unlink_address'
    monkeypatch.setattr(attention, target, Mock(side_effect=TensologyApiError('Project link changed elsewhere', status=409)))
    assert InitiativeAttentionTool()._run(action=action, email='customer@example.test', project='2') == 'Project link changed elsewhere'


def test_archived_canonical_mail_denies_stale_local_assignment_even_without_explicit_marker(monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.incoming import project_links, proposals
    from distr.core.initiative import attention
    with get_session() as db:
        project = Project(name='Archived canonical denial'); db.add(project); db.flush(); pid = project.id
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    project_links.set_mail_project_link('mailshot', 'archived-regression', 'archived-message', pid)
    # Legacy ingestion could archive a source before explicit unlink markers existed.
    canonical = {'source': 'mailshot', 'source_thread_id': 'archived-regression',
                 'is_archived': True, 'link_explicit': False, 'projects': [],
                 'direct_projects': [], 'sender_email': 'client@example.test'}
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: [canonical])
    assert project_links.source_project_index()[('mailshot', 'archived-regression')] == set()
    assert ('mailshot', 'archived-regression') not in project_links.mail_project_links()
    row = {'id': 'local:mailshot:archived-regression', 'origin': 'local', 'source': 'mailshot',
           'source_thread_id': 'archived-regression', 'message_ids': ['archived-message'], 'project_id': pid}
    assert not project_links.proposal_in_scope(row, allow_review=True)
