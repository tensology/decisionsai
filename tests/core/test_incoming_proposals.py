"""Incoming proposals: Tensology is canonical, local triage is deduped against it."""

from __future__ import annotations

from distr.core.incoming import proposals as proposals_mod


import pytest
from datetime import datetime, timezone


def _assert_cards(rows, expected):
    assert len(rows) == len(expected)
    for row, source in zip(rows, expected):
        for key, value in source.items():
            assert row.get(key) == value, f"Card field {key}: {row.get(key)!r} != {value!r}"


@pytest.fixture(autouse=True)
def _clear_incoming_proposals_list_cache():
    from distr.core.db import IncomingProposalRestoration, get_session
    with get_session() as db:
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    proposals_mod.invalidate_proposals_list_cache()
    yield
    proposals_mod.invalidate_proposals_list_cache()


def test_dismissed_local_proposal_survives_refresh_and_new_sessions(monkeypatch):
    import json
    import subprocess
    import sys
    from distr.core.db import engine

    proposal = {'id': 'local:gmail:dismiss-persistence', 'origin': 'local', 'source': 'gmail',
                'dedupe_key': 'gmail:dismiss-persistence', 'title': 'Fix dismissal',
                'message_ids': ['dismiss-message-one']}
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda **kwargs: [])
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'proposals', [proposal])
    _assert_cards(proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'], [proposal])
    assert proposals_mod.dismiss_proposal(proposal) is True
    engine.dispose()
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == []
    restarted = subprocess.run(
        [sys.executable, '-c',
         'import json,sys; from distr.core.incoming.proposals import _undismissed_proposals; '
         'assert _undismissed_proposals([json.load(sys.stdin)]) == []'],
        input=json.dumps(proposal), text=True, capture_output=True, timeout=15,
    )
    assert restarted.returncode == 0, restarted.stderr
    # Re-triage can change wording or return a smaller message window.
    proposal['title'] = 'Regenerated title'
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == []
    proposal['message_ids'].append('dismiss-message-two')
    proposals_mod.invalidate_proposals_list_cache()
    _assert_cards(proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'], [proposal])


def test_dismissal_without_message_ids_uses_snapshot_and_is_idempotent():
    proposal = {'id': 'local:gmail:no-message-ids', 'dedupe_key': 'gmail:no-message-ids',
                'source': 'gmail', 'title': 'Original work', 'created_at': '2026-11-02T07:00:00Z'}
    assert proposals_mod.dismiss_proposal(proposal) is True
    assert proposals_mod.dismiss_proposal(proposal) is True
    assert proposals_mod._undismissed_proposals([proposal]) == []
    new_work = {**proposal, 'title': 'Later work', 'created_at': '2026-11-02T09:00:00Z'}
    assert proposals_mod._undismissed_proposals([new_work]) == [new_work]


def test_repeated_source_triage_does_not_revive_or_renotify_dismissed_work(monkeypatch):
    from unittest.mock import Mock

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(proposals_mod.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(proposals_mod, '_triage_model', lambda settings: ('local', 'fixture'))
    monkeypatch.setattr(proposals_mod, '_candidate_projects', lambda: [{'id': 7, 'name': 'Fixture'}])
    monkeypatch.setattr('distr.core.read_response._default_complete', lambda *a:
                        '{"is_actionable":true,"project_id":7,"title":"Fix repeated source work"}')
    notify = Mock()
    monkeypatch.setattr('distr.core.tensology_notify.notify_proposal_created', notify)
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'running', False)
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'at', 0.0)
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'proposals', [])
    item = {**_incoming_item('gmail', 'repeat-triage-dismissal', 'Please fix this bug'),
            'source_message_id': 'triage-original'}
    proposals = proposals_mod._local_proposals([item], {})
    assert len(proposals) == 1
    assert notify.call_count == 1
    assert proposals_mod.dismiss_proposal(proposals[0]) is True
    for _ in range(2):
        monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'at', 0.0)
        assert proposals_mod._local_proposals([item], {}) == []
    assert notify.call_count == 1
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'at', 0.0)
    new_item = {**item, 'source_message_id': 'triage-new', 'created_at': '2026-11-02T10:00:00Z'}
    assert len(proposals_mod._local_proposals([item, new_item], {})) == 1
    assert notify.call_count == 2


def test_dismissed_tensology_proposal_does_not_return_as_local(monkeypatch):
    row = _tensology_row(91045, 'dismiss-upstream', 'gmail', 'Dismiss upstream')
    row['evidence'][0]['id'] = 'upstream-message'
    proposal = proposals_mod._from_tensology(row)
    local = {**proposal, 'id': 'local:gmail:dismiss-upstream', 'origin': 'local'}
    pending = [row]
    writes = []
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda **kwargs: pending)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *a: writes.append(a))
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'proposals', [local])
    assert proposals_mod.dismiss_proposal(proposal) is True
    assert writes == [(91045, 'dismiss')]
    # Even a stale upstream response cannot restore the dismissed record.
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == []
    pending.clear()
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == []
    local['message_ids'] = ['upstream-message', 'new-request']
    proposals_mod.invalidate_proposals_list_cache()
    _assert_cards(proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'], [local])


def test_failed_upstream_dismissal_keeps_local_receipt(monkeypatch):
    """Local dismiss must stick even when Tensology is unreachable."""
    from unittest.mock import Mock

    row = _tensology_row(91046, 'dismiss-failed', 'gmail', 'Keep failed dismissal')
    proposal = proposals_mod._from_tensology(row)
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda **kwargs: [row])
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', Mock(side_effect=ValueError('Offline')))
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'proposals', [])
    assert proposals_mod.dismiss_proposal(proposal) is True
    # Upstream failed, but the local ledger hides it (and covers the conversation messages).
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == []
    assert proposal.get('_upstream_dismiss_error')


def test_remote_tensology_dismissal_is_honoured_on_intake(monkeypatch):
    row = _tensology_row(91047, 'remote-dismiss', 'whatsapp', 'Remote dismiss')
    row['evidence'][0]['id'] = 'wa-msg-1'
    local = {
        'id': 'local:whatsapp:remote-dismiss', 'origin': 'local', 'source': 'whatsapp',
        'dedupe_key': 'whatsapp:remote-dismiss', 'title': 'Remote dismiss',
        'message_ids': ['wa-msg-1'], 'source_thread_id': 'remote-dismiss',
    }
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda **kwargs: [])
    monkeypatch.setitem(proposals_mod._TRIAGE_CACHE, 'proposals', [local])
    _assert_cards(proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'], [local])
    assert proposals_mod.record_remote_proposal_decision({
        'request_id': 91047,
        'status': 'dismissed',
        'source': 'whatsapp',
        'thread_id': 'remote-dismiss',
        'message_ids': ['wa-msg-1'],
        'title': 'Remote dismiss',
    }) is True
    assert proposals_mod.list_incoming_proposals(refresh_local=False)['proposals'] == []


def test_accepted_local_proposal_stays_out_after_refresh_but_new_work_surfaces(monkeypatch, tmp_path):
    from distr.core.db import Chat, get_session
    from distr.core.db.projects import Project
    from distr.core.db.workflow import DevelopmentWorkItem

    with get_session() as db:
        project = Project(name="Proposal handoff test", folder_location=str(tmp_path))
        db.add(project)
        db.commit()
        project_id = int(project.id)

    created = []

    def create(**kwargs):
        with get_session() as db:
            chat = Chat(title=kwargs['title'], project_id=kwargs['project_id'], params='{}')
            db.add(chat)
            db.flush()
            db.add(DevelopmentWorkItem(chat_id=chat.id, project_id=project_id,
                                      source_type='incoming_proposal',
                                      identity_key='source:incoming_proposal:' + kwargs['source_ref']))
            db.commit()
            created.append(kwargs)
            return int(chat.id)

    proposal = {'id': 'local:gmail:receipt-test', 'origin': 'local', 'source': 'gmail',
                'dedupe_key': 'gmail:receipt-test', 'project_id': project_id,
                'title': 'Review test work', 'message_ids': ['message-one']}
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', create)
    monkeypatch.setattr('distr.core.incoming.media.proposal_attachments', lambda *a, **k: [])
    monkeypatch.setattr('distr.core.incoming.media.register_thread_attachments', lambda *a: None)
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda **kwargs: [])
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **k: {'items': []})
    monkeypatch.setattr(proposals_mod, '_local_proposals', lambda *a: [proposal])

    _assert_cards(proposals_mod.list_incoming_proposals()['proposals'], [proposal])
    chat_id = proposals_mod.create_proposal_thread(proposal)
    with get_session() as db:
        assert db.get(Chat, chat_id).project_id == project_id
    assert proposals_mod.list_incoming_proposals()['proposals'] == []

    old_key = proposals_mod._proposal_handoff_key(proposal)
    proposal['message_ids'] = ['message-one', 'message-two']
    proposals_mod.invalidate_proposals_list_cache()
    _assert_cards(proposals_mod.list_incoming_proposals()['proposals'], [proposal])
    assert proposals_mod._proposal_handoff_key(proposal) != old_key
    assert created[0]['project_id'] == project_id


def test_failed_media_handoff_keeps_proposal_pending(monkeypatch):
    import pytest
    from unittest.mock import Mock

    proposal = {'id': 'tensology:89', 'title': 'Attachment handoff',
                'project': {'name': 'Project'}, 'attachments': []}
    client = Mock()
    client.get.return_value = {**proposal, 'id': 89}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **k: client)
    monkeypatch.setattr('distr.core.tensology_client._resolve_tensology_project', lambda p: {'id': 7})
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', lambda **k: 123)
    monkeypatch.setattr(
        'distr.core.incoming.media.ensure_proposal_attachments',
        Mock(side_effect=ValueError('Media registration failed')),
    )
    receipt, action = Mock(), Mock()
    monkeypatch.setattr(proposals_mod, '_record_proposal_handoff', receipt)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', action)
    with pytest.raises(ValueError, match='Media registration failed'):
        proposals_mod.create_proposal_thread(proposal)
    receipt.assert_not_called()
    action.assert_not_called()


def test_unlinked_local_proposal_cannot_create_unscoped_thread(monkeypatch):
    import pytest
    from unittest.mock import Mock

    create = Mock()
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', create)
    with pytest.raises(ValueError, match='Link the proposal'):
        proposals_mod.create_proposal_thread({'id': 'local:test', 'title': 'Needs project'})
    create.assert_not_called()


def _tensology_row(pid, thread_id, source, title):
    return {
        "id": pid,
        "status": "pending",
        "source": source,
        "title": title,
        "project": {"id": 1, "name": "Merrypak"},
        "source_conversation": {"thread_id": thread_id},
        "evidence": [{"sent_at": datetime.now(timezone.utc).isoformat()}],
    }


def _incoming_item(source, thread_id, text, label="Client"):
    return {
        "key": f"{source}:{thread_id}",
        "source": source,
        "source_thread_id": thread_id,
        "conversation_label": label,
        "sender": label,
        "text": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def test_tensology_proposals_kept_and_local_deduped(monkeypatch):
    shared = "120363339060471282@g.us"
    monkeypatch.setattr(
        "distr.core.tensology_client.fetch_pending_work_requests",
        lambda **kwargs: [
            _tensology_row(45, shared, "whatsapp", "Check the first shipping cost"),
            _tensology_row(46, shared, "whatsapp", "Investigate incorrect shipping cost"),
            _tensology_row(47, "email-abc", "mailshot", "Reconnect GitLab"),
        ],
    )
    monkeypatch.setattr(
        "distr.core.incoming.service.list_development_incoming",
        lambda limit=100, **kwargs: {"items": []},
    )
    # A local proposal on the same thread must be suppressed; a new thread must survive.
    monkeypatch.setattr(
        "distr.core.incoming.proposals._local_proposals",
        lambda items, settings: [
            {"id": f"local:whatsapp:{shared}", "origin": "local", "dedupe_key": f"whatsapp:{shared}",
             "source": "whatsapp", "source_thread_id": shared, "title": "duplicate"},
            {"id": "local:gmail:thread-new", "origin": "local", "dedupe_key": "gmail:thread-new",
             "source": "gmail", "source_thread_id": "thread-new", "title": "new work"},
        ],
    )

    result = proposals_mod.list_incoming_proposals(limit=50)
    tensology = [p for p in result["proposals"] if p["origin"] == "tensology"]
    local = [p for p in result["proposals"] if p["origin"] == "local"]

    assert len(tensology) == 3  # never deduped among themselves
    assert [p["source_thread_id"] for p in local] == ["thread-new"]  # shared thread suppressed


def test_triage_builds_proposal_from_llm(monkeypatch):
    monkeypatch.setattr(
        "distr.core.incoming.proposals._candidate_projects",
        lambda limit=50: [{"id": 7, "name": "Merrypak", "description": "", "open_tickets": []}],
    )
    items = [_incoming_item("whatsapp", "t1", "Please fix the shipping cost", label="Carmen")]

    def fake_complete(provider, model, messages, settings):
        return (
            '```json\n{"is_actionable": true, "project_id": 7, "title": "Fix shipping cost", '
            '"description": "The first shipping cost is wrong.", "tasks": ["a", "b"], '
            '"priority": "high", "estimated_minutes": 30, "confidence": 0.9, "rationale": "client asks"}\n```'
        )

    out = proposals_mod._triage_conversations(items, {}, complete=fake_complete)
    assert len(out) == 1
    proposal = out[0]
    assert proposal["origin"] == "local" and proposal["project_id"] == 7
    assert proposal["title"] == "Fix shipping cost"
    assert proposal["tasks"] == ["a", "b"] and proposal["priority"] == "high"
    assert proposal["estimated_minutes"] == 30


def test_triage_skips_non_actionable_and_unknown_project(monkeypatch):
    monkeypatch.setattr(
        "distr.core.incoming.proposals._candidate_projects",
        lambda limit=50: [{"id": 7, "name": "Merrypak", "description": "", "open_tickets": []}],
    )
    items = [_incoming_item("whatsapp", "t1", "Please fix it", label="Client")]

    assert proposals_mod._triage_conversations(items, {}, complete=lambda *a, **k: '{"is_actionable": false}') == []
    assert proposals_mod._triage_conversations(
        items, {}, complete=lambda *a, **k: '{"is_actionable": true, "project_id": 999, "title": "x"}'
    ) == []


def test_triage_skips_own_messages_before_calling_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(
        "distr.core.incoming.proposals._candidate_projects",
        lambda limit=50: [{"id": 7, "name": "X", "description": "", "open_tickets": []}],
    )
    items = [dict(_incoming_item("whatsapp", "t1", "ok", label="Paul Hoft"), from_me=True)]
    out = proposals_mod._triage_conversations(items, {}, complete=lambda *a, **k: called.append(1) or "{}")
    assert out == [] and called == []


def test_proposal_thread_preserves_project_prompt_and_actions_upstream(monkeypatch):
    calls = []
    writes = []
    receipts = []
    monkeypatch.setattr(proposals_mod, '_record_proposal_handoff', lambda cid, proposal, **kwargs: receipts.append(cid))
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {
        'conversational_llm_provider': 'ollama', 'conversational_llm_model': 'local-test'})
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', lambda **kwargs: (calls.append(kwargs) or 123))
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *args: writes.append(args))
    proposal = {'id': 'tensology:45', 'title': 'Review launch copy', 'description': 'Review the final menu.',
                'tasks': ['Check spelling'], 'project_id': 7, 'source': 'gmail', 'participant': 'Client'}
    from unittest.mock import Mock
    client = Mock()
    client.get.return_value = {**proposal, 'id': 45, 'project': {'id': 70, 'name': 'Merrypak'}, 'attachments': []}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: client)
    monkeypatch.setattr('distr.core.tensology_client._resolve_tensology_project', lambda payload: {'id': 7})
    assert proposals_mod.create_proposal_thread(proposal) == 123
    assert calls[0]['project_id'] == 7
    assert calls[0]['source_type'] == 'incoming_proposal'
    assert calls[0]['source_ref'] == 'tensology:45'
    assert calls[0]['title'] == proposal['title']
    assert proposal['description'] in calls[0]['starting_question']
    assert 'Check spelling' in calls[0]['starting_question']
    assert writes == [(45, 'send_to_decisions')]
    assert receipts == [123]


def test_missing_authoritative_evidence_stops_before_thread_or_source_action(monkeypatch):
    from unittest.mock import Mock
    import pytest
    from distr.core.tensology_client import TensologyApiError

    client = Mock()
    client.get.side_effect = TensologyApiError('Restore source messages.', status=409, code='proposal_evidence_missing')
    create, action = Mock(), Mock()
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: client)
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', create)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', action)
    with pytest.raises(TensologyApiError, match='Restore source messages'):
        proposals_mod.create_proposal_thread({'id': 'tensology:45', 'title': 'Missing original evidence'})
    create.assert_not_called()
    action.assert_not_called()


def test_merge_rebuilds_two_proposals_into_one(monkeypatch):
    created = {}
    monkeypatch.setattr("distr.core.chat.ChatService.create_new_chat", lambda **kwargs: (created.setdefault("kwargs", kwargs) and 0) or (501, None))
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    monkeypatch.setattr("distr.core.incoming.media.register_thread_attachments", lambda *a, **k: None)
    monkeypatch.setattr(proposals_mod, "_merge_proposal_attachments", lambda *a, **k: [])
    notices = []
    monkeypatch.setattr("distr.core.tensology_notify.notify_proposal_created", lambda event: notices.append(event))
    dismissed = []
    monkeypatch.setattr("distr.core.tensology_client.decide_work_request", lambda pid, action: dismissed.append((pid, action)))

    first = {
        "id": "tensology:45", "title": "Check shipping cost", "project_id": 2, "project_name": "Merrypak",
        "description": "Req A", "tasks": ["a", "shared"], "rationale": "Why A", "message_ids": ["m1"],
        "evidence": [{"id": "m1", "body": "hello"}],
    }
    second = {
        "id": "tensology:46", "title": "Investigate shipping calc", "project_id": 2, "project_name": "Merrypak",
        "description": "Req B", "tasks": ["shared", "b"], "rationale": "Why B", "message_ids": ["m2"],
        "evidence": [{"id": "m2", "body": "there"}],
    }

    def fake_complete(provider, model, messages, settings):
        # Title polish only — body/tasks come from combined FRAC sections.
        return '{"title": "Fix shipping cost calculation"}'

    result = proposals_mod.merge_proposals(first, second, complete=fake_complete)
    assert result["chat_id"] == 501
    merged = result["merged"]
    assert merged["title"] == "Fix shipping cost calculation"
    assert merged["tasks"] == ["a", "shared", "b"]
    assert "Req A" in merged["description"] and "Req B" in merged["description"]
    assert "Why A" in merged["rationale"] and "Why B" in merged["rationale"]
    assert merged["message_ids"] == ["m1", "m2"]
    assert created["kwargs"]["project_id"] == 2
    assert dismissed == [(45, "dismiss"), (46, "dismiss")]
    assert notices and notices[0].get("title") == "Fix shipping cost calculation"


def test_merge_dedupes_duplicate_attachments_via_shared_storage_path(monkeypatch):
    created = {}
    monkeypatch.setattr("distr.core.chat.ChatService.create_new_chat", lambda **kwargs: (created.setdefault("kwargs", kwargs) and 0) or (777, None))
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    registered = []
    monkeypatch.setattr("distr.core.incoming.media.register_thread_attachments", lambda chat_id, rows: registered.append((chat_id, rows)))
    monkeypatch.setattr("distr.core.tensology_notify.notify_proposal_created", lambda event: None)
    monkeypatch.setattr("distr.core.tensology_client.decide_work_request", lambda *a, **k: None)

    shared = {
        "name": "invoice.pdf", "mime_type": "application/pdf", "size": 12,
        "sha256": "abc123", "source_message_id": "msg-1", "path": "/tmp/invoice.pdf", "id": "att-1",
    }
    duplicate = dict(shared)  # same content hash
    other = {
        "name": "photo.jpg", "mime_type": "image/jpeg", "size": 99,
        "sha256": "def456", "source_message_id": "msg-2", "path": "/tmp/photo.jpg", "id": "att-2",
    }

    def fake_ensure(proposal, project_id, client=None, chat_id=None, strict=True):
        if proposal["id"] == "tensology:1":
            return {"attachments": [shared, other], "failures": []}
        return {"attachments": [duplicate], "failures": []}  # duplicate content hash

    monkeypatch.setattr("distr.core.incoming.media.ensure_proposal_attachments", fake_ensure)

    first = {"id": "tensology:1", "title": "A", "project_id": 2, "project_name": "Merrypak", "tasks": ["t1"]}
    second = {"id": "tensology:2", "title": "B", "project_id": 2, "project_name": "Merrypak", "tasks": ["t2"]}
    result = proposals_mod.merge_proposals(first, second, complete=lambda *a, **k: '{"title": "A + B"}')
    names = [row["name"] for row in result["merged"]["attachments"]]
    assert names == ["invoice.pdf", "photo.jpg"]
    assert registered and registered[0][0] == 777
    assert len(registered[0][1]) == 2


def test_create_and_start_uses_project_flash_route_retries_failure_and_starts_once(monkeypatch, tmp_path):
    import pytest
    from distr.core.db import Chat, get_session
    from distr.core.db.projects import Project
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.workflow import development_harness
    from distr.core.workspace_memory import paths

    monkeypatch.setattr(paths, 'WORKSPACES_ROOT', tmp_path/'workspaces')
    with get_session() as db:
        project=Project(name='Proposal execution fixture', folder_location=str(tmp_path))
        db.add(project)
        db.commit()
        project_id=int(project.id)
    proposal={'id':'local:execution-proof', 'origin':'local', 'source':'gmail',
              'project_id':project_id, 'title':'Verify proposal work', 'message_ids':['proof-message']}
    monkeypatch.setattr('distr.core.incoming.media.proposal_attachments', lambda *a, **k: [])
    monkeypatch.setattr('distr.core.incoming.media.register_thread_attachments', lambda *a: None)
    monkeypatch.setattr(development_harness, 'development_execution_active', lambda cid:False)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {
        'automation_cli_backend':'pi', 'automation_cli_model_provider':'ollama',
        'automation_cli_model':'deepseek-v4.1-flash:cloud'})
    calls=[]
    def dispatch(cid, prompt, **kwargs):
        with get_session() as db:
            assert db.get(Chat,cid).project_id == project_id
        calls.append((cid,prompt,kwargs))
        return {'status':'failed','error':'Executor unavailable'} if len(calls)==1 else {'status':'running'}
    monkeypatch.setattr(development_harness,'dispatch_development_prompt',dispatch)
    with pytest.raises(ValueError, match='Executor unavailable'):
        proposals_mod.create_proposal_thread(proposal,start_work=True)
    assert proposals_mod._proposal_handoff_key(proposal) not in proposals_mod._accepted_proposal_keys()
    cid=proposals_mod.create_proposal_thread(proposal,start_work=True)
    assert proposals_mod.create_proposal_thread(proposal,start_work=True)==cid
    assert len(calls)==2
    assert calls[0][0]==calls[1][0]==cid
    assert calls[-1][2]['routing_assessment']['route']['model']=='deepseek-v4.1-flash:cloud'
    assert proposals_mod._proposal_handoff_key(proposal) in proposals_mod._accepted_proposal_keys()
    with get_session() as db:
        item=db.query(DevelopmentWorkItem).filter_by(chat_id=cid).one()
        assert item.project_id==project_id
    from distr.core.workflow.development_threads import thread_execution_route
    pinned = thread_execution_route(cid)
    assert pinned['route_mode'] == 'manual'
    assert pinned['model'] == 'deepseek-v4.1-flash:cloud'
    assert pinned['model_provider'] == 'ollama'


def test_proposals_list_cache_serves_until_invalidated(monkeypatch):
    """Page refresh must not re-hit Tensology until dismiss/create/WS/Resync."""
    calls = {'n': 0}

    def fetch(**kwargs):
        calls['n'] += 1
        return [{
            'id': 501,
            'title': 'Cached proposal',
            'status': 'pending',
            'source': 'gmail',
            'project': {'name': 'CacheProj'},
            'source_conversation': {'channel': 'gmail', 'thread_id': 'cache-thread'},
            'source_message_ids': ['m1'],
        }]

    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', fetch)
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **k: {'items': []})
    monkeypatch.setattr(proposals_mod, '_local_proposals', lambda *a, **k: [])
    monkeypatch.setattr(proposals_mod, '_project_map', lambda: {})
    monkeypatch.setattr(proposals_mod, 'apply_project_corrections', lambda rows: rows)
    monkeypatch.setattr(proposals_mod, '_accepted_proposal_keys', lambda: set())
    monkeypatch.setattr(proposals_mod, '_undismissed_proposals', lambda rows: rows)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'incoming_proposals_auto_accept': False})
    proposals_mod.invalidate_proposals_list_cache()

    first = proposals_mod.list_incoming_proposals(refresh_local=False)
    second = proposals_mod.list_incoming_proposals(refresh_local=False)
    assert calls['n'] == 1
    assert first['proposals'] and second['proposals']
    assert first['proposals'][0]['id'] == second['proposals'][0]['id']

    proposals_mod.drop_cached_proposals(request_ids=[501])
    third = proposals_mod.list_incoming_proposals(refresh_local=False)
    assert calls['n'] == 1  # surgical drop, no refetch
    assert third['proposals'] == []

    proposals_mod.invalidate_proposals_list_cache()
    proposals_mod.list_incoming_proposals(refresh_local=False, force=True)
    assert calls['n'] == 2
