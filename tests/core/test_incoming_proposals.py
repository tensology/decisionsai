"""Incoming proposals: Tensology is canonical, local triage is deduped against it."""

from __future__ import annotations

from distr.core.incoming import proposals as proposals_mod


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
