"""Incoming proposals: Tensology is canonical, local triage is deduped against it."""

from __future__ import annotations

from distr.core.incoming import proposals as proposals_mod


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
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda: [])
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **k: {'items': []})
    monkeypatch.setattr(proposals_mod, '_local_proposals', lambda *a: [proposal])

    assert proposals_mod.list_incoming_proposals()['proposals'] == [proposal]
    chat_id = proposals_mod.create_proposal_thread(proposal)
    with get_session() as db:
        assert db.get(Chat, chat_id).project_id == project_id
    assert proposals_mod.list_incoming_proposals()['proposals'] == []

    old_key = proposals_mod._proposal_handoff_key(proposal)
    proposal['message_ids'] = ['message-one', 'message-two']
    assert proposals_mod.list_incoming_proposals()['proposals'] == [proposal]
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
    monkeypatch.setattr('distr.core.incoming.media.proposal_attachments', lambda *a, **k: [])
    monkeypatch.setattr('distr.core.incoming.media.register_thread_attachments',
                        Mock(side_effect=ValueError('Media registration failed')))
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
        "evidence": [{"sent_at": "2026-10-29T07:00:00+00:00"}],
    }


def _incoming_item(source, thread_id, text, label="Client"):
    return {
        "key": f"{source}:{thread_id}",
        "source": source,
        "source_thread_id": thread_id,
        "conversation_label": label,
        "sender": label,
        "text": text,
        "created_at": "2026-10-29T08:00:00+00:00",
    }


def test_tensology_proposals_kept_and_local_deduped(monkeypatch):
    shared = "120363339060471282@g.us"
    monkeypatch.setattr(
        "distr.core.tensology_client.fetch_pending_work_requests",
        lambda: [
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
    dismissed = []
    monkeypatch.setattr("distr.core.tensology_client.decide_work_request", lambda pid, action: dismissed.append((pid, action)))

    first = {"id": "tensology:45", "title": "Check shipping cost", "project_id": 2, "project_name": "Merrypak", "tasks": ["a"]}
    second = {"id": "tensology:46", "title": "Investigate shipping calc", "project_id": 2, "project_name": "Merrypak", "tasks": ["b"]}

    def fake_complete(provider, model, messages, settings):
        return '{"title": "Fix shipping cost calculation", "description": "combined", "tasks": ["a", "b"], "priority": "high"}'

    result = proposals_mod.merge_proposals(first, second, complete=fake_complete)
    assert result["chat_id"] == 501
    assert result["merged"]["title"] == "Fix shipping cost calculation"
    assert result["merged"]["tasks"] == ["a", "b"]
    assert created["kwargs"]["project_id"] == 2
    assert dismissed == [(45, "dismiss"), (46, "dismiss")]


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
