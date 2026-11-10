"""Source identities survive recreation; credentials never enter prompts or tool output."""
import json
import os
import sys
from unittest.mock import Mock

from distr.core.external_agent_context import _rank_codex_threads

import pytest

from distr.core.db import Chat, WhatsAppMessage, get_session
from distr.core.db.projects import Project
from distr.core.incoming import source_context as sc
from distr.core.incoming import proposals


@pytest.fixture
def source(tmp_path):
    with get_session() as db:
        project = Project(name="Source handoff", folder_location=str(tmp_path))
        db.add(project)
        db.flush()
        chat = Chat(title="Source handoff", project_id=project.id)
        db.add(chat)
        db.add(WhatsAppMessage(message_id="source-credential", jid="source-fixture@g.us", text="FixturePassword42"))
        db.add(WhatsAppMessage(message_id="foreign-credential", jid="foreign-fixture@g.us", text="OtherFixture42"))
        db.commit()
        cid, pid = int(chat.id), int(project.id)
    proposal = {"source": "whatsapp", "source_thread_id": "source-fixture@g.us",
                "project_id": pid, "title": "Restore quotes", "message_ids": ["source-credential"]}
    sc.register_source_context(cid, proposal)
    yield cid, proposal
    with get_session() as db:
        db.query(WhatsAppMessage).filter(WhatsAppMessage.message_id.in_(["source-credential", "foreign-credential"])).delete(synchronize_session=False)
        db.commit()


def test_snapshot_and_execution_keep_identity_without_copying_value(source):
    cid, proposal = source
    restored = json.loads(proposals.proposal_snapshot_json(proposal))
    assert sc.proposal_source_context(restored) == sc.proposal_source_context(proposal)
    text = proposals._proposal_prompt(restored) + sc.thread_source_instructions(cid)
    assert "source-credential" not in proposals._proposal_prompt(restored)
    assert "source_context" not in proposals._proposal_prompt(restored)
    assert "source-credential" in text and "source-fixture@g.us" in text
    assert "FixturePassword42" not in text
    snapshot = json.loads(proposals.proposal_snapshot_json({**proposal, "evidence": [{"id": "source-credential", "body": "FixturePassword42"}]}))
    assert "FixturePassword42" not in json.dumps(snapshot)
    assert " run" in text and "Status: needs_input" in text
    with get_session() as db:
        assert "FixturePassword42" not in db.get(Chat, cid).params


def test_triage_does_not_transmit_single_value_credential(source):
    conversation = {"source": "whatsapp", "participant": "Fixture", "label": "Fixture",
                    "items": [{"source_message_id": "source-credential", "text": "FixturePassword42"}]}
    prompt = json.dumps(proposals._triage_prompt(conversation, []))
    assert "FixturePassword42" not in prompt and "source-credential" in prompt


def test_failed_proposal_resumes_same_thread_without_duplicate_dispatch(monkeypatch, tmp_path):
    from distr.core.workflow import development_harness as harness
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.workspace_memory import paths
    monkeypatch.setattr(paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    with get_session() as db:
        project = Project(name="Retry source", folder_location=str(tmp_path))
        db.add(project); db.commit(); pid = int(project.id)
    monkeypatch.setattr("distr.core.incoming.project_links.source_project_index",
                        lambda **kwargs: {('whatsapp', 'retry@g.us'): {pid}})
    proposal = {"id": "local:retry-source", "source": "whatsapp", "project_id": pid,
                "source_thread_id": "retry@g.us", "title": "Retry source", "message_ids": ["retry-msg"]}
    monkeypatch.setattr("distr.core.incoming.media.ensure_proposal_attachments", lambda *a, **kw: {"attachments": [], "failures": []})
    monkeypatch.setattr("distr.core.incoming.media.register_thread_attachments", lambda *a: {})
    monkeypatch.setattr(harness, "development_execution_active", lambda cid: False)
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    calls = []
    def dispatch(cid, prompt, **kwargs):
        calls.append(cid)
        assert "retry-msg" not in prompt
        with get_session() as db:
            chat = db.get(Chat, cid); params = json.loads(chat.params)
            assert params["incoming_source_context"]["message_ids"] == ["retry-msg"]
            params["development"]["execution"] = {"status": "running"}
            chat.params = json.dumps(params); db.commit()
        return {"status": "running"}
    monkeypatch.setattr(harness, "dispatch_development_prompt", dispatch)
    cid = proposals.create_proposal_thread(proposal, start_work=True)
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.workflow.development_threads import mark_development_thread
    with get_session() as db:
        board = KanbanBoard(name="Retry existing ticket")
        db.add(board); db.flush()
        lane = KanbanLane(board_id=board.id, name="Todo")
        db.add(lane); db.flush()
        ticket = KanbanTicket(title="Existing incident", lane_id=lane.id, linked_project_id=pid)
        db.add(ticket); db.commit(); ticket_id = int(ticket.id)
    mark_development_thread(cid, ticket_id=ticket_id, source_type="incoming_proposal", source_ref=proposals._proposal_handoff_key(proposal))
    with get_session() as db:
        chat = db.get(Chat, cid); params = json.loads(chat.params)
        params["development"]["execution"] = {"status": "blocked"}
        chat.params = json.dumps(params); chat.is_archived = True; db.commit()
    assert proposals._proposal_handoff_key(proposal) not in proposals._accepted_proposal_keys()
    assert proposals.create_proposal_thread(proposal, start_work=True) == cid
    assert proposals.create_proposal_thread(proposal, start_work=True) == cid
    assert calls == [cid, cid]
    with get_session() as db:
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=cid).count() == 1
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=cid).one().local_ticket_id == ticket_id
        assert not db.get(Chat, cid).is_archived


def test_credential_reaches_consumer_but_output_and_parent_environment_stay_clean(source, capfd):
    cid, _ = source
    before = os.environ.get("EUROLUX_PASSWORD")
    result = sc.run_with_source_credential(cid, "source-credential", "EUROLUX_PASSWORD", [sys.executable, "-c",
        "import os,sys; value=os.environ['EUROLUX_PASSWORD']; print(value); print(value,file=sys.stderr); sys.exit(0 if value=='FixturePassword42' else 5)"])
    assert result == {"status": "completed", "exit_code": 0}
    assert "FixturePassword42" not in "".join(capfd.readouterr())
    assert os.environ.get("EUROLUX_PASSWORD") == before


def test_failed_consumer_does_not_leak_credentials(source, capfd):
    cid, _ = source
    result = sc.run_with_source_credential(cid, "source-credential", "EUROLUX_PASSWORD", [sys.executable, "-c",
        "import os,sys; print(os.environ['EUROLUX_PASSWORD']); sys.exit(7)"])
    assert result == {"status": "failed", "exit_code": 7}
    assert "FixturePassword42" not in "".join(capfd.readouterr())


def test_foreign_message_is_rejected_even_if_manifest_is_tampered(source):
    cid, proposal = source
    sc.register_source_context(cid, {**proposal, "message_ids": ["foreign-credential"]})
    with pytest.raises(ValueError, match="inbound source"):
        sc.run_with_source_credential(cid, "foreign-credential", "EUROLUX_PASSWORD", ["false"])


def test_cli_lists_candidates_without_message_bodies(source, capsys):
    cid, _ = source
    assert sc.main(["--chat-id", str(cid), "list"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == {"messages": [{"id": "source-credential", "credential_candidate": True}]}


def test_missing_source_credential_is_explicit_needs_input(source, capsys):
    cid, _ = source
    assert sc.main(["--chat-id", str(cid), "run", "--message-id", "missing", "--env", "EUROLUX_PASSWORD", "--", "false"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "needs_input"
    assert "FixturePassword42" not in json.dumps(result)


@pytest.mark.parametrize("text", ["Please use this new password tomorrow", "two values 42 43", "", "hello"])
def test_ambiguous_credentials_are_not_guessed(text):
    with pytest.raises(ValueError):
        sc._credential_value(text)


def test_ticket_context_keeps_human_notes_and_references_on_repeated_sync(source):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    cid, _ = source
    with get_session() as db:
        board = KanbanBoard(name="Source board")
        db.add(board); db.flush()
        lane = KanbanLane(board_id=board.id, name="Todo")
        db.add(lane); db.flush()
        ticket = KanbanTicket(title="Source ticket", lane_id=lane.id, source_chat_id=cid, context_notes="Human notes")
        db.add(ticket); db.commit(); tid = int(ticket.id)
    sc.sync_ticket_source_context(cid)
    sc.sync_ticket_source_context(cid)
    with get_session() as db:
        notes = db.get(KanbanTicket, tid).context_notes
        assert notes.startswith("Human notes") and notes.count("source-credential") == 1
        assert "FixturePassword42" not in notes


# Voice and Incoming context regressions, 2026-11-10.
def test_codex_query_cannot_cross_explicit_project_scope():
    wrong = {'id': 'spoortrack', 'cwd': '/repo/SPOORTRACK', 'title': 'Correct carrier bag size details'}
    right = {'id': 'merrypak', 'cwd': '/repo/www.merrypak.co.za', 'title': 'Catalogue import'}
    assert _rank_codex_threads([wrong, right], query='Correct carrier bag size details', project='Merrypak') == [right]


def test_ordinary_actionable_result_with_empty_optional_proposals_is_retained(monkeypatch):
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [{'id': 10, 'name': 'Shop'}])
    monkeypatch.setattr(proposals, '_dismissed_message_coverage', lambda: {})
    monkeypatch.setattr(proposals, '_latest_conversation_context', lambda conversation: [])
    monkeypatch.setattr('distr.core.incoming.routing_context.earlier_context', lambda conversation: [])
    item = {'source': 'whatsapp', 'source_thread_id': 'empty-wrapper', 'source_message_id': 'new',
            'project_id': 10, 'sender': 'Client', 'text': 'Please fix checkout'}
    result = {'is_actionable': True, 'project_id': 10, 'title': 'Fix checkout', 'proposals': []}
    rows = proposals._triage_conversations([item], {}, complete=lambda *args: json.dumps(result))
    assert len(rows) == 1
    assert rows[0]['message_ids'] == ['new']


def test_missing_codex_id_never_falls_back_to_keyword_match():
    row = {'id': 'other', 'cwd': '/repo/merrypak', 'title': 'Deploy production'}
    assert _rank_codex_threads([row], thread_id='missing', query='Deploy production', project='Merrypak') == []


def test_emoji_acknowledgement_is_not_new_project_work():
    from distr.core.incoming.proposals import _worth_triaging
    assert not _worth_triaging({'items': [
        {'sender': 'Client', 'text': 'Please fix quotes', 'created_at': '1'},
        {'sender': 'Developer', 'text': 'Fixed and working', 'created_at': '2', 'from_me': True},
        {'sender': 'Client', 'text': '👍🏽', 'created_at': '3'},
    ]})
    assert _worth_triaging({'items': [
        {'sender': 'Client', 'text': 'Thanks, but it is broken again. Please check.', 'created_at': '4'},
    ]})


def test_development_reference_keeps_latest_deployment_evidence(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import distr.core.db as db
    from distr.core.chat import ChatService
    from distr.core.agent.tools.system.ide_thread import _read_development_thread

    chat = SimpleNamespace(title='Merrypak', project_id=2, params=json.dumps({'development': {'execution': {'status': 'completed'}}}))
    monkeypatch.setattr(db, 'get_session', lambda: nullcontext(SimpleNamespace(get=lambda *args: chat)))
    old = [{'role': 'assistant', 'content': 'Old pending deployment report. ' * 100} for _ in range(20)]
    current = [{'role': 'user', 'content': 'Deploy the fix; import separately.'},
               {'role': 'assistant', 'content': 'Production deployment verified. Data import remains separate.'}]
    monkeypatch.setattr(ChatService, 'get_chat_history', lambda chat_id: old + current)
    output = _read_development_thread(947, action='read', limit_messages=30)
    reference = output.split('REFERENCE:\n', 1)[1]
    payload = json.loads(reference)
    assert payload['messages'][-2:] == current
    assert len(reference) < 7000


def test_latest_resolution_context_is_exact_conversation_only(monkeypatch):
    from distr.core.db import WhatsAppMessage, get_session
    from distr.core.incoming import proposals
    with get_session() as db:
        rows = [WhatsAppMessage(jid='context-resolution', message_id='request', text='Please fix quotes', whatsapp_timestamp=1),
                WhatsAppMessage(jid='context-resolution', message_id='fixed', text='Fixed and working', from_me=True, whatsapp_timestamp=2),
                WhatsAppMessage(jid='context-resolution', message_id='ack', text='Perfect thanks', whatsapp_timestamp=3),
                WhatsAppMessage(jid='foreign-resolution', message_id='foreign', text='Other project', whatsapp_timestamp=4)]
        db.add_all(rows)
        db.commit()
    try:
        context = proposals._latest_conversation_context({'source': 'whatsapp', 'thread_id': 'context-resolution'})
        assert [row['id'] for row in context] == ['request', 'fixed', 'ack']
        assert context[1]['direction'] == 'outbound'
        assert all(row['context_only'] for row in context)
        assert 'fresh request after a fix' in proposals._TRIAGE_SYSTEM
    finally:
        with get_session() as db:
            db.query(WhatsAppMessage).filter(WhatsAppMessage.jid.in_(['context-resolution', 'foreign-resolution'])).delete(synchronize_session=False)
            db.commit()


def test_current_development_read_does_not_use_external_ide(monkeypatch):
    import distr.core.agent.tools.system.ide_thread as module
    from distr.core.agent.services.llm import oracle_context

    monkeypatch.setattr(oracle_context, 'get_current_development_thread', lambda: {
        'thread_id': 947, 'project_id': 2, 'project_name': 'Merrypak', 'title': 'Correct carrier bag size details',
    })
    monkeypatch.setattr(module, '_is_development_thread', lambda value: value == 947)
    read = Mock(return_value='Verified Merrypak deployment')
    monkeypatch.setattr(module, '_read_development_thread', read, raising=False)
    external = Mock(return_value={'success': True, 'found': True, 'messages': [{'role': 'assistant', 'text': 'Wrong GPS test'}]})
    monkeypatch.setattr('distr.core.ide_threads.ide_thread_action', external)
    result = module.IdeThreadTool()._run(action='read', project_id=2, project='Merrypak', query='Correct carrier bag size details')
    assert result == 'Verified Merrypak deployment'
    read.assert_called_once_with(947, action='read', limit_messages=12)
    external.assert_not_called()


@pytest.mark.parametrize('action', ['read', 'status'])
def test_explicit_development_read_and_status_use_same_thread(monkeypatch, action):
    import distr.core.agent.tools.system.ide_thread as module

    monkeypatch.setattr(module, '_is_development_thread', lambda value: value == 947)
    read = Mock(return_value='Merrypak result')
    monkeypatch.setattr(module, '_read_development_thread', read, raising=False)
    monkeypatch.setattr('distr.core.ide_threads.ide_thread_action', Mock(side_effect=AssertionError('External IDE must not run')))
    assert module.IdeThreadTool()._run(action=action, development_thread_id=947) == 'Merrypak result'
    read.assert_called_once_with(947, action=action, limit_messages=12)


def test_explicit_external_surface_is_preserved(monkeypatch):
    import distr.core.agent.tools.system.ide_thread as module
    external = Mock(return_value={'success': True, 'found': True, 'messages': [{'role': 'assistant', 'text': 'Explicit Codex'}]})
    monkeypatch.setattr('distr.core.ide_threads.ide_thread_action', external)
    assert 'Explicit Codex' in module.IdeThreadTool()._run(action='read', surface='codex', thread_id='explicit')
    external.assert_called_once()


def test_strict_incoming_reader_exposes_upstream_failure(monkeypatch):
    import distr.core.tensology_client as client
    remote = Mock()
    remote.get.side_effect = client.TensologyApiError('Temporarily unavailable', status=503)
    monkeypatch.setattr(client, 'configured_tensology_client', lambda **kwargs: remote)
    with pytest.raises(client.TensologyApiError):
        client.fetch_pending_work_requests(include_decided=True, strict=True)
    assert client.fetch_pending_work_requests() == []  # Day-plan best effort stays compatible.


def test_failed_list_read_is_visible_and_retries_after_recovery(monkeypatch):
    from distr.core.incoming import proposals as incoming
    import distr.core.tensology_client as client

    row = {'id': 99, 'status': 'pending', 'source': 'gmail', 'title': 'Billing request',
           'project': {'name': 'Spoortrack'}, 'source_conversation': {'thread_id': 'client'}, 'source_message_ids': ['message']}
    fetch = Mock(side_effect=[client.TensologyApiError('Temporarily unavailable', status=503), [row]])
    monkeypatch.setattr(client, 'fetch_pending_work_requests', fetch)
    monkeypatch.setattr('distr.core.incoming.proposal_sync.reconcile_decisions', lambda rows: (rows, []))
    monkeypatch.setattr(incoming, '_project_map', lambda: {'spoortrack': 10})
    monkeypatch.setattr(incoming, '_scoped_proposals', lambda rows: rows)
    monkeypatch.setattr(incoming, '_undismissed_proposals', lambda rows: rows)
    monkeypatch.setattr(incoming, '_accepted_proposal_keys', lambda: set())
    monkeypatch.setattr(incoming, '_saved_local_proposals', lambda: [])
    monkeypatch.setattr(incoming, '_record_local_proposals', lambda rows: None)
    monkeypatch.setattr(incoming, '_with_restored_snapshots', lambda rows: rows)
    monkeypatch.setattr(incoming, 'apply_project_corrections', lambda rows: rows)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    incoming.invalidate_proposals_list_cache()
    try:
        failed = incoming.list_incoming_proposals(refresh_local=False)
        assert failed['tensology']['error']
        assert failed['sync']['state'] == 'partial'
        recovered = incoming.list_incoming_proposals(refresh_local=False)
        assert [row['id'] for row in recovered['proposals']] == ['tensology:99']
        assert fetch.call_count == 2
        assert fetch.call_args.kwargs['strict'] is True
    finally:
        incoming.invalidate_proposals_list_cache()
