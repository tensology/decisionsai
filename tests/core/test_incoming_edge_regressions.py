"""Proposal boundary regressions. Providers, models and execution stay stubbed."""
import json
from unittest.mock import Mock

import pytest

from distr.core.incoming import proposals as proposals


@pytest.fixture(autouse=True)
def isolate_edge_cache(monkeypatch):
    from distr.core.db import IncomingProposalRestoration, get_session
    monkeypatch.setattr(proposals, '_RESTORED_SNAPSHOTS', {})
    monkeypatch.setattr(proposals, '_TRIAGE_CACHE', {'at':0, 'proposals':[], 'running':False})
    proposals.invalidate_proposals_list_cache()
    yield
    with get_session() as db:
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    proposals.invalidate_proposals_list_cache()


@pytest.fixture
def merge_case(monkeypatch, tmp_path):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    with get_session() as db:
        project = Project(name='Edge sandbox', folder_location=str(tmp_path))
        db.add(project)
        db.commit()
        pid = project.id
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *args, **kwargs: True)
    monkeypatch.setattr('distr.core.incoming.project_links.source_project_index', lambda **kwargs: {})
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args: [])
    monkeypatch.setattr('distr.core.incoming.transcription.prepare_thread_transcripts', lambda *args: None)
    # No model, notices, workflow execution or provider call may escape fixtures.
    monkeypatch.setattr('distr.core.tensology_notify.notify_proposal_created', Mock(side_effect=AssertionError('unexpected notice')))
    monkeypatch.setattr('distr.core.workflow.development_harness.dispatch_development_prompt', Mock(side_effect=AssertionError('unexpected dispatch')))
    a = {'id':f'local:gmail:edge-{pid}-a', 'origin':'local', 'source':'gmail',
         'source_thread_id':f'edge-{pid}-a', 'project_id':pid, 'project_name':'Edge sandbox',
         'title':'First request', 'description':'Do first work', 'tasks':['First acceptance'],
         'message_ids':[f'{pid}-a1'], 'dedupe_key':f'gmail:edge-{pid}-a'}
    b = {**a, 'id':f'local:gmail:edge-{pid}-b', 'source_thread_id':f'edge-{pid}-b',
         'title':'Second request', 'message_ids':[f'{pid}-b1'], 'dedupe_key':f'gmail:edge-{pid}-b'}
    return a, b


def merge(a, b):
    return proposals.merge_proposals(a, b, complete=lambda *args: '{}')


@pytest.fixture
def real_development_dispatch():
    from distr.core.workflow.development_harness import dispatch_development_prompt

    return dispatch_development_prompt


def test_merge_prepares_development_identity_reply_sources_and_replays(merge_case):
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices
    a, b = merge_case
    result = merge(a, b)
    # Reverse order and discard transient caches to simulate a later request.
    proposals.invalidate_proposals_list_cache()
    assert merge(b, a)['chat_id'] == result['chat_id']
    with get_session() as db:
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=result['chat_id']).count() == 1
        params = json.loads(db.get(Chat, result['chat_id']).params)
        assert params['incoming_proposal_handoff']['started'] is False
        assert params['incoming_merge']['pending_originals'] == []
        assert set(params['incoming_merge']['dismissed_original_keys']) == {
            proposals._proposal_handoff_key(a), proposals._proposal_handoff_key(b),
        }
    assert {row['thread_id'] for row in source_choices(proposal_for_thread(result['chat_id']))} == {a['source_thread_id'], b['source_thread_id']}


def _remote_pair(monkeypatch, pair, ids=(990101, 990102)):
    a, b = pair
    canonical = {}
    for wid, row in zip(ids, pair):
        canonical[f'work-requests/{wid}'] = {
            'id':wid, 'title':row['title'], 'description':row['description'], 'tasks':row['tasks'],
            'status':'pending', 'source':'gmail', 'project':{'id':80, 'decisions_project_id':row['project_id'], 'name':row['project_name']},
            'source_conversation':{'thread_id':row['source_thread_id']}, 'source_message_ids':row['message_ids'], 'attachments':[]}
    client = Mock()
    client.get.side_effect = lambda path: canonical[path]
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs:client)
    return {**a, 'id': f'tensology:{ids[0]}'}, {**b, 'id': f'tensology:{ids[1]}'}


def _confirmed_dismissal(proposal_id, action, **kwargs):
    assert action == 'dismiss'
    return {'id': proposal_id, 'status': 'dismissed'}


@pytest.mark.parametrize("conflict,first_id", [("merge_source_changed", 990621), ("invalid_action", 990626)])
def test_versioned_merge_dismissal_retries_only_the_exact_reviewed_revision(monkeypatch, merge_case, conflict, first_id):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.core.db import IncomingProposalDismissal, get_session
    from distr.gui.web.routes.development.incoming import register_routes

    second_id, merged_id = first_id + 1, first_id + 2
    pair = _remote_pair(monkeypatch, merge_case, ids=(first_id, second_id))
    client = tensology_client.configured_tensology_client()
    original_get = client.get.side_effect
    revisions = {f"work-requests/{first_id}": "a" * 64, f"work-requests/{second_id}": "b" * 64}
    client.get.side_effect = lambda path: {**original_get(path), "merge_revision": revisions[path],
                                           "merge_dismissal_revision_supported": True}
    client.post.side_effect = [
        {"id": merged_id}, {"id": first_id, "status": "dismissed"},
        tensology_client.TensologyApiError("Lost confirmation"),
        tensology_client.TensologyApiError("An original proposal changed after it was merged.",
                                          status=409 if conflict == "merge_source_changed" else 400, code=conflict),
        {"id": second_id, "status": "dismissed"},
    ]
    with pytest.raises(tensology_client.TensologyApiError, match="Lost confirmation"):
        merge(*pair)
    recovery = next(row for row in proposals.incoming_merge_recovery({"proposals": list(pair)})["pending_merges"]
                    if row["merge_key"] == proposals._merge_key([f"tensology:{first_id}", f"tensology:{second_id}"]))
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    api = TestClient(app)
    url = f'/workflows/studio/incoming/proposals/merge/{recovery["chat_id"]}/resume'
    body = {"merge_key": recovery["merge_key"]}
    response = api.post(url, json=body)
    assert response.status_code == 409
    assert ("changed after it was merged" if conflict == "merge_source_changed" else "needs the merge dismissal update") in response.json()["error"]
    with get_session() as db:
        assert db.get(IncomingProposalDismissal, f"tensology:{first_id}") is not None
        assert db.get(IncomingProposalDismissal, f"tensology:{second_id}") is None
    response = api.post(url, json=body)
    assert response.status_code == 200 and response.json()["chat_id"] == recovery["chat_id"]
    replacement, first, *second = client.post.call_args_list
    assert replacement.args[1]["source_merge_revisions"] == {str(first_id): "a" * 64, str(second_id): "b" * 64}
    assert first.args == (f"work-requests/{first_id}/action", {"action": "dismiss_merge_source", "merge_revision": "a" * 64})
    assert len(second) == 3 and all(call == second[0] for call in second)
    assert second[0].args == (f"work-requests/{second_id}/action", {"action": "dismiss_merge_source", "merge_revision": "b" * 64})


def test_versioned_merge_waits_for_guarded_dismissal_support_before_creating_replacement(monkeypatch, merge_case):
    from distr.core import tensology_client

    pair = _remote_pair(monkeypatch, merge_case, ids=(990624, 990625))
    client = tensology_client.configured_tensology_client()
    original_get = client.get.side_effect
    client.get.side_effect = lambda path: {**original_get(path), "merge_revision": "a" * 64}
    with pytest.raises(ValueError, match="needs the merge dismissal update"):
        merge(*pair)
    client.post.assert_not_called()
def test_replacement_failure_preserves_originals(monkeypatch, merge_case):
    pair = _remote_pair(monkeypatch, merge_case)
    dismiss = Mock()
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', dismiss)
    monkeypatch.setattr('distr.core.tensology_client.create_merged_work_request', Mock(side_effect=RuntimeError('upstream unavailable')))
    with pytest.raises(RuntimeError, match='upstream unavailable'):
        merge(*pair)
    dismiss.assert_not_called()
    assert proposals._undismissed_proposals(list(pair)) == list(pair)


def test_partial_merge_dismissal_retries_same_replacement_and_thread(monkeypatch, merge_case):
    from distr.core.chat import _chat_params
    from distr.core.db import get_session

    pair = _remote_pair(monkeypatch, merge_case)
    replacement = Mock(return_value={'id':990103})
    monkeypatch.setattr('distr.core.tensology_client.create_merged_work_request', replacement)
    dismiss = Mock(side_effect=[
        _confirmed_dismissal(990101, 'dismiss'), RuntimeError('dismiss temporarily unavailable'),
        _confirmed_dismissal(990102, 'dismiss'),
    ])
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', dismiss)
    with pytest.raises(RuntimeError, match='dismiss temporarily unavailable'):
        merge(*pair)
    with get_session() as db:
        chat, receipt = next((chat, receipt) for chat, receipt in proposals._saved_merge_receipts(db)
                             if receipt.get('tensology') == {'id': 990103})
        chat_id = chat.id
        assert _chat_params(chat.params)['incoming_proposal_handoff']['key'] == 'tensology:990103'
        assert [row['id'] for row in receipt['pending_originals']] == ['tensology:990102']
        assert receipt['dismissed_original_keys'] == ['tensology:990101']
    result = merge(*pair)
    assert result['merged']['id'] == 'tensology:990103'
    assert result['chat_id'] == chat_id
    assert replacement.call_count == 1
    assert merge(*pair)['chat_id'] == result['chat_id']
    assert replacement.call_count == 1
    assert [call.args[0] for call in dismiss.call_args_list] == [990101, 990102, 990102]


@pytest.mark.parametrize("problem", ["missing_receipt", "wrong_status", "wrong_proposal"])
def test_merge_requires_confirmation_for_the_exact_remote_original(monkeypatch, merge_case, problem):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.core.db import IncomingProposalDismissal, get_session
    from distr.gui.web.routes.development.incoming import register_routes

    first_id, second_id, merged_id = {
        "missing_receipt": (990601, 990602, 990603), "wrong_status": (990604, 990605, 990606),
        "wrong_proposal": (990607, 990608, 990609),
    }[problem]
    confirmation = {
        "missing_receipt": {}, "wrong_status": {"id": second_id, "status": "sent_to_decisions"},
        "wrong_proposal": {"id": 990699, "status": "dismissed"},
    }[problem]
    pair = _remote_pair(monkeypatch, merge_case, ids=(first_id, second_id))
    client = tensology_client.configured_tensology_client()
    client.post.side_effect = [
        {"id": merged_id},
        {"id": first_id, "status": "dismissed"}, confirmation, {"id": second_id, "status": "dismissed"},
    ]
    monkeypatch.setattr("distr.core.read_response._default_complete", lambda *_: "{}")
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    api = TestClient(app)
    path = "/workflows/studio/incoming/proposals/merge"
    body = {"first": pair[0], "second": pair[1]}

    response = api.post(path, json=body)
    assert response.status_code == 409
    assert "could not confirm dismissal" in response.json()["error"]
    with get_session() as db:
        chat, receipt = next((chat, receipt) for chat, receipt in proposals._saved_merge_receipts(db)
                             if receipt.get("tensology") == {"id": merged_id})
        chat_id = chat.id
        merge_key = receipt["key"]
        assert receipt["dismissed_original_keys"] == [f"tensology:{first_id}"]
        assert [row["id"] for row in receipt["pending_originals"]] == [f"tensology:{second_id}"]
        assert db.get(IncomingProposalDismissal, f"tensology:{second_id}") is None
        with pytest.raises(proposals.MergeHandoffPending):
            proposals.require_ready_merge_handoff(json.loads(chat.params), chat.project_id, db=db)

    # A refreshed Incoming list no longer has the dismissed first card.
    fresh = {**merge_case[0], "id": "local:gmail:unrelated", "message_ids": ["new evidence"]}
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **_: {"proposals": [pair[1], fresh]})
    listing = api.get("/workflows/studio/incoming/proposals").json()
    assert [row["id"] for row in listing["proposals"]] == [fresh["id"]]
    assert [(row["chat_id"], row["merge_key"]) for row in listing["pending_merges"]] == [(chat_id, merge_key)]
    resume_url = f"{path}/{chat_id}/resume"
    assert api.post(resume_url, json={"merge_key": "merge:" + "a" * 64}).status_code == 409
    with monkeypatch.context() as patch:
        patch.setattr("distr.core.incoming.project_links.proposal_in_scope", lambda *args: False)
        assert api.post(resume_url, json={"merge_key": merge_key}).status_code == 409
    assert client.post.call_count == 3

    response = api.post(resume_url, json={"merge_key": merge_key})
    assert response.status_code == 200 and response.json()["chat_id"] == chat_id
    assert len(response.json()["review_key"]) == 64
    assert api.get("/workflows/studio/incoming/proposals").json()["pending_merges"] == []
    assert api.post(resume_url, json={"merge_key": merge_key}).json()["chat_id"] == chat_id
    replacement, *dismissals = client.post.call_args_list
    assert replacement.args[0] == "work-requests"
    assert [call.args for call in dismissals] == [
        (f"work-requests/{proposal_id}/action", {"action": "dismiss"})
        for proposal_id in (first_id, second_id, second_id)
    ]
    assert dismissals[1].kwargs["idempotency_key"] == dismissals[2].kwargs["idempotency_key"]


@pytest.mark.parametrize('legacy', [False, True])
def test_merged_originals_cannot_be_restored_from_dismissal_history(merge_case, legacy):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, IncomingProposalDismissal, get_session
    from distr.gui.web.routes.development.incoming import register_routes

    a, b = merge_case
    result = merge(a, b)
    if legacy:
        with get_session() as db:
            chat = db.get(Chat, result['chat_id'])
            params = _chat_params(chat.params)
            params['incoming_merge'].pop('original_keys', None)
            params['incoming_merge'].pop('dismissed_original_keys', None)
            chat.params = json.dumps(params)
            db.commit()
    newer = {**a, 'message_ids': ['fresh evidence for the same conversation']}
    proposals.dismiss_proposal(newer)
    key = proposals._proposal_handoff_key(a)
    fresh_key = proposals._proposal_handoff_key(newer)
    history_keys = {row['key'] for row in proposals.list_dismissed_proposals()}
    assert fresh_key in history_keys
    assert not history_keys.intersection({key, proposals._proposal_handoff_key(b)})
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    response = TestClient(app).post('/workflows/studio/incoming/proposals/restore', json={'key': key})
    assert response.status_code == 409
    assert 'already merged' in response.json()['error']
    with get_session() as db:
        assert db.get(IncomingProposalDismissal, key) is not None
    assert proposals.undismiss_proposal(fresh_key)['restored']
    assert merge(b, a)['chat_id'] == result['chat_id']


def test_merge_rejects_a_locally_dismissed_original(merge_case):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.incoming import register_routes

    a, b = merge_case
    proposals.dismiss_proposal(a)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    response = TestClient(app).post('/workflows/studio/incoming/proposals/merge', json={'first': a, 'second': b})
    assert response.status_code == 409
    assert "no longer pending" in response.json()['error']
    with pytest.raises(ValueError, match="no longer pending"):
        proposals.create_proposal_thread(a, start_work=True)
    assert proposals._undismissed_proposals([a, b]) == [b]


@pytest.mark.parametrize("damage", [
    "null_keys", "foreign_pending", "unknown_pending", "wrong_fingerprint", "missing_dismissal", "missing_progress",
])
def test_damaged_merge_receipt_keeps_originals_protected_and_other_proposals_usable(merge_case, monkeypatch, damage):
    from distr.core.db import Chat, IncomingProposalDismissal, get_session
    from distr.core.workflow import development_harness as harness
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow.dispatcher import start_workflow_run

    a, b = merge_case
    merged = merge(a, b)
    unrelated = {**a, "id": a["id"] + "-new", "message_ids": ["new message"]}
    proposals.dismiss_proposal(unrelated)
    unrelated_key = proposals._proposal_handoff_key(unrelated)
    with get_session() as db:
        chat = db.get(Chat, merged["chat_id"])
        params = json.loads(chat.params)
        receipt = params["incoming_merge"]
        if damage == "null_keys":
            receipt["original_keys"] = None
        elif damage == "foreign_pending":
            receipt["pending_originals"] = [unrelated]
        elif damage == "unknown_pending":
            receipt["pending_originals"] = None
        elif damage == "wrong_fingerprint":
            receipt["key"] = "merge:damaged"
        elif damage == "missing_dismissal":
            db.delete(db.get(IncomingProposalDismissal, proposals._proposal_handoff_key(b)))
            assert receipt["pending_originals"] == []
        else:
            receipt["dismissed_original_keys"].remove(proposals._proposal_handoff_key(b))
        scope = harness.development_scope_identity(chat.project_id, harness.development_thread_metadata(chat),
                                                   chat_id=chat.id)
        chat.params = json.dumps(params)
        workflow = AutoWorkflow(name="Merge execution fixture")
        db.add(workflow)
        db.flush()
        workflow_id = workflow.id
        db.add(AutoWorkflowStep(workflow_id=workflow_id, name="Review", instruction="Review saved evidence"))
        db.commit()

    assert unrelated_key in {row["key"] for row in proposals.list_dismissed_proposals()}
    with pytest.raises(ValueError, match="already merged"):
        proposals.undismiss_proposal(proposals._proposal_handoff_key(a))
    assert proposals.undismiss_proposal(unrelated_key)["restored"]
    if damage == "null_keys":
        assert merge(a, b)["chat_id"] == merged["chat_id"]
        execution = harness._update_execution(merged["chat_id"], expected_scope=scope, job_id="verified",
                                              status="initializing")
        assert execution["job_id"] == "verified"
    else:
        dismiss = Mock()
        monkeypatch.setattr(proposals, "_persist_proposal_dismissal", dismiss)
        with pytest.raises(ValueError):
            merge(a, b)
        with pytest.raises(proposals.MergeHandoffPending):
            proposals.create_proposal_thread(merged["merged"], start_work=True)
        if damage == "missing_dismissal":
            from fastapi import APIRouter, FastAPI
            from fastapi.testclient import TestClient
            from distr.gui.web.routes.development.incoming import register_routes

            app, router = FastAPI(), APIRouter()
            register_routes(router, None)
            app.include_router(router)
            response = TestClient(app).post("/workflows/studio/incoming/proposals/thread",
                                            json={"proposal": merged["merged"]})
            assert response.status_code == 409
            assert response.json()["error"] == str(proposals.MergeHandoffPending())
        with pytest.raises(proposals.MergeHandoffPending):
            harness._update_execution(merged["chat_id"], expected_scope=scope, job_id="unexpected",
                                      status="initializing")
        blocked = start_workflow_run(workflow_id, run_metadata={"chat_id": merged["chat_id"]})
        assert blocked["status"] == "blocked" and "Finish merging" in blocked["error"]
        with get_session() as db:
            assert db.query(AutoWorkflowRun).filter_by(workflow_id=workflow_id).count() == 0
        dismiss.assert_not_called()


def test_partial_merge_reserves_original_against_a_different_pair(monkeypatch, merge_case):
    a, b = merge_case
    c = {**b, 'id': b['id'] + '-c', 'source_thread_id': b['source_thread_id'] + '-c',
         'dedupe_key': b['dedupe_key'] + '-c', 'message_ids': [b['message_ids'][0] + '-c']}
    with monkeypatch.context() as patch:
        patch.setattr(proposals, '_persist_proposal_dismissal', Mock(side_effect=RuntimeError('receipt write failed')))
        with pytest.raises(RuntimeError, match='receipt write failed'):
            merge(a, b)
    with pytest.raises(ValueError, match="another merge"):
        merge(a, c)
    with pytest.raises(ValueError, match="another merge"):
        proposals.create_proposal_thread(a, start_work=True)
    resumed = merge(b, a)
    assert merge(a, b)['chat_id'] == resumed['chat_id']
    assert proposals._undismissed_proposals([a, b, c]) == [c]


def test_merge_rejects_an_original_with_an_existing_development_thread(merge_case):
    a, b = merge_case
    chat_id = proposals.create_proposal_thread(a)
    with pytest.raises(ValueError, match="no longer pending"):
        merge(a, b)
    assert proposals.create_proposal_thread(a) == chat_id


def test_starting_remote_merge_preserves_original_reply_destinations(monkeypatch, merge_case):
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices
    from distr.core.workflow import development_harness
    from distr.core import tensology_client

    a, b = merge_case
    pair = _remote_pair(monkeypatch, merge_case, ids=(990106, 990107))
    client = tensology_client.configured_tensology_client()
    get_original = client.get.side_effect
    client.get.side_effect = lambda path: get_original(path) if path != 'work-requests/990105' else {
        **get_original('work-requests/990106'), 'id': 990105,
        'requires_merge_receipt': True,
        'title': 'Updated replacement brief', 'source_message_ids': a['message_ids'] + b['message_ids'],
    }
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', Mock(return_value={'id': 990105}))
    monkeypatch.setattr(tensology_client, 'decide_work_request', Mock(side_effect=_confirmed_dismissal))
    monkeypatch.setattr(development_harness, 'development_execution_active', lambda *_: False)
    monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda *_: {})
    dispatch = Mock(return_value={'status': 'running'})
    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', dispatch)

    failure = {'name': 'missing.pdf', 'error': 'original source offline'}
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args: [failure])
    result = merge(*pair)
    chat_id = proposals.create_proposal_thread(result['merged'], start_work=True)
    assert chat_id == result['chat_id']
    assert dispatch.call_args.args[1].startswith('Updated replacement brief')
    assert {row['thread_id']: row['message_ids'] for row in source_choices(proposal_for_thread(chat_id))} == {
        a['source_thread_id']: a['message_ids'], b['source_thread_id']: b['message_ids'],
    }
    assert proposals.proposal_thread_attachment_report(chat_id)['failed_attachments'] == [failure]
    assert client.post.call_args.args[1] == {'action': 'acknowledge_thread', 'thread_id': chat_id}


@pytest.mark.parametrize('start_work', [False, True])
def test_reserved_remote_original_cannot_create_a_thread(monkeypatch, merge_case, start_work):
    from distr.core import tensology_client
    from distr.core.workflow import development_threads

    first, _ = _remote_pair(monkeypatch, merge_case, ids=(990243, 990244))
    client = tensology_client.configured_tensology_client()
    original = client.get.side_effect
    client.get.side_effect = lambda path: {**original(path), 'merge_source_reserved': True}
    ensure = Mock()
    monkeypatch.setattr(development_threads, 'ensure_development_thread', ensure)
    with pytest.raises(ValueError, match='reserved for a merge'):
        proposals.create_proposal_thread(first, start_work=start_work)
    ensure.assert_not_called()
    client.post.assert_not_called()


def test_remote_replacement_waits_for_local_merge_preparation(monkeypatch, merge_case):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.core.incoming.proposal_sync import reconcile_decisions
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices
    from distr.core.workflow import development_harness
    from distr.gui.web.routes.development.incoming import register_routes

    pair = _remote_pair(monkeypatch, merge_case, ids=(990230, 990231))
    client = tensology_client.configured_tensology_client()
    original = client.get.side_effect
    replacement = {
        **original('work-requests/990230'), 'id': 990232, 'requires_merge_receipt': True,
        'source_conversation': {'thread_id': 'merged:replacement-fixture'},
    }
    client.get.side_effect = lambda path: replacement if path == 'work-requests/990232' else original(path)
    create = Mock(return_value=replacement)
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', create)
    dismiss = Mock(side_effect=OSError('Remote dismissal interrupted'))
    monkeypatch.setattr(tensology_client, 'decide_work_request', dismiss)
    dispatch = Mock()
    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', dispatch)
    stale = {**pair[0], 'id': 'tensology:990232'}

    with monkeypatch.context() as patch:
        patch.setattr(proposals, '_record_proposal_handoff', Mock(side_effect=OSError('Local receipt interrupted')))
        with pytest.raises(OSError, match='Local receipt interrupted'):
            merge(*pair)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    response = TestClient(app).post('/workflows/studio/incoming/proposals/thread', json={'proposal': stale})
    assert response.status_code == 409
    assert 'Finish merging' in response.json()['error']
    dismiss.assert_not_called()

    # A stale card need not include the authoritative replacement marker.
    replacement_card = {key: value for key, value in stale.items() if key != 'source_thread_id'}
    monkeypatch.setattr('distr.core.read_response._default_complete', lambda *_: '{}')
    response = TestClient(app).post('/workflows/studio/incoming/proposals/merge',
                                    json={'first': replacement_card, 'second': pair[1]})
    assert response.status_code == 409
    assert 'Finish merging' in response.json()['error']
    assert create.call_count == 1
    dismiss.assert_not_called()

    with pytest.raises(OSError, match='Remote dismissal interrupted'):
        merge(*pair)
    with pytest.raises(ValueError, match='Finish merging'):
        proposals.create_proposal_thread(stale, start_work=True)
    assert reconcile_decisions([replacement]) == ([replacement], [])
    client.post.assert_not_called()
    dispatch.assert_not_called()

    dismiss.side_effect = _confirmed_dismissal
    result = merge(*pair)
    assert create.call_count == 2
    assert {row['thread_id']: row['message_ids'] for row in source_choices(proposal_for_thread(result['chat_id']))} == {
        row['source_thread_id']: row['message_ids'] for row in pair
    }
    assert reconcile_decisions([replacement]) == ([replacement], [])
    client.post.assert_not_called()

    client.post.side_effect = OSError('Acknowledgement interrupted')
    assert proposals.create_proposal_thread(stale) == result['chat_id']
    client.post.side_effect = None
    client.post.return_value = {'status': 'sent_to_decisions'}
    with monkeypatch.context() as patch:
        patch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *_: False)
        assert reconcile_decisions([replacement]) == ([replacement], [])
        assert client.post.call_count == 1
    rows, errors = reconcile_decisions([replacement])
    assert not errors and rows[0]['status'] == 'sent_to_decisions'
    assert client.post.call_args.args[1] == {'action': 'acknowledge_thread', 'thread_id': result['chat_id']}
    dispatch.assert_not_called()


def test_stale_dismiss_cannot_bypass_a_merge_reservation(monkeypatch, merge_case):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.gui.web.routes.development.incoming import register_routes

    pair = _remote_pair(monkeypatch, merge_case, ids=(990233, 990234))
    create = Mock(return_value={'id': 990235})
    dismiss = Mock(side_effect=OSError('Upstream unavailable'))
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', create)
    monkeypatch.setattr(tensology_client, 'decide_work_request', dismiss)
    with pytest.raises(OSError, match='Upstream unavailable'):
        merge(*pair)

    key = proposals._merge_key([proposals._proposal_handoff_key(row) for row in pair])
    recovery = next(row for row in proposals.incoming_merge_recovery({'proposals': []})['pending_merges']
                    if row['merge_key'] == key)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    response = client.post('/workflows/studio/incoming/proposals/dismiss', json={'proposal': pair[0]})
    assert response.status_code == 409
    assert 'Resume merge' in response.json()['error']
    assert proposals._undismissed_proposals(list(pair)) == list(pair)
    replacement = {**pair[0], 'id': 'tensology:990235'}
    response = client.post('/workflows/studio/incoming/proposals/dismiss', json={'proposal': replacement})
    assert response.status_code == 409
    assert 'Resume merge' in response.json()['error']
    assert proposals._undismissed_proposals([replacement]) == [replacement]
    assert dismiss.call_count == 1

    dismiss.side_effect = _confirmed_dismissal
    response = client.post(f"/workflows/studio/incoming/proposals/merge/{recovery['chat_id']}/resume",
                           json={'merge_key': recovery['merge_key']})
    assert response.status_code == 200
    assert response.json()['chat_id'] == recovery['chat_id']
    assert create.call_count == 1
    assert dismiss.call_count == 3


def test_merge_persists_missing_attachment_failure(monkeypatch, merge_case):
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args:[{'name':'missing.pdf', 'error':'offline'}])
    result = merge(*merge_case)
    assert proposals.proposal_thread_attachment_report(result['chat_id'])['failed_attachments'] == [{'name':'missing.pdf', 'error':'offline'}]
    assert merge(*merge_case)['merged']['attachments'][0]['error'] == 'offline'


def test_recovered_remote_attachment_clears_warning_only_with_matching_evidence():
    recovered_link = {'name': 'report.pdf', 'api_path': 'source-1'}
    recovered_digest = {'name': 'report.pdf', 'api_path': 'source-2', 'sha256': 'found'}
    wrong_digest = {'name': 'report.pdf', 'api_path': 'source-1', 'sha256': 'missing'}
    unrelated = {'name': 'report.pdf'}
    merge = {'file_failures': [recovered_link, recovered_digest, wrong_digest, unrelated]}
    files = [{'name': 'report.pdf', 'path': '/managed/file', 'api_path': 'source-1', 'sha256': 'found'}]

    assert proposals._unresolved_merge_file_failures(merge, files) == [wrong_digest, unrelated]


def test_starting_merged_proposal_preserves_recorded_attachment_failure(monkeypatch, merge_case):
    from distr.core.workflow import development_harness

    failure = {'name': 'missing.pdf', 'error': 'original source offline'}
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args: [failure])
    result = merge(*merge_case)
    chat_id = result['chat_id']
    monkeypatch.setattr(development_harness, 'development_execution_active', lambda *_: False)
    monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda *_: {})
    dispatch = Mock(return_value={'status': 'running'})
    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', dispatch)

    assert proposals.create_proposal_thread(result['merged'], start_work=True) == chat_id
    dispatch.assert_called_once()
    assert proposals.proposal_thread_attachment_report(chat_id)['failed_attachments'] == [failure]


@pytest.mark.parametrize(('first_id', 'scenario'), [(990700, 'matching'), (990710, 'changed'), (990720, 'moved')])
@pytest.mark.parametrize('entry', ['incoming', 'thread'])
def test_starting_completed_merge_checks_the_registered_original(real_development_dispatch, merge_case, monkeypatch,
                                                                tmp_path, first_id, scenario, entry):
    from pathlib import Path
    from threading import Event
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    from distr.core.incoming import media
    from distr.core.workflow import development_harness
    from distr.core.workflow.dispatcher import start_workflow_run
    from distr.gui.web.routes.development.incoming import register_routes
    from distr.gui.web.routes.development.threads import register_routes as register_thread_routes

    if entry == 'thread':
        first_id += 100
    pair = _remote_pair(monkeypatch, merge_case, ids=(first_id, first_id + 1))
    replacement_id = first_id + 2
    client = tensology_client.configured_tensology_client()
    get_original = client.get.side_effect
    proposal_ref = f'tensology:{first_id}'
    original_path = f'work-requests/{first_id}'
    location = f'{original_path}/attachment?snapshot_id=8&attachment_index=0'
    replacement_path = f'work-requests/{replacement_id}'
    project_id = pair[0]['project_id']
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    identity = f'{proposal_ref}:{location}'
    path = media._destination(media._root(project_id, proposal_ref), identity, 'scope.txt')
    path.write_bytes(b'Reviewed bytes')
    record = media._record(path, 'scope.txt', 'text/plain', identity, api_path=location)
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *_: [record])
    create = Mock(return_value={'id': replacement_id})
    dismiss = Mock(side_effect=_confirmed_dismissal)
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', create)
    monkeypatch.setattr(tensology_client, 'decide_work_request', dismiss)
    result = merge(*pair)
    chat_id = result['chat_id']
    path.unlink()

    def get(request):
        if request == replacement_path:
            return {**get_original(original_path), 'id': replacement_id, 'requires_merge_receipt': True,
                    'source_message_ids': pair[0]['message_ids'] + pair[1]['message_ids']}
        assert request == original_path
        canonical = get_original(request)
        project = canonical['project'] if scenario != 'moved' else {
            **canonical['project'], 'decisions_project_id': project_id + 100,
        }
        return {**canonical, 'status': 'dismissed', 'project': project,
                'attachments': [{'name': 'scope.txt', 'api_path': location}]}

    altered = scenario == 'changed'

    def download(link, destination, *, max_bytes):
        assert link == location and destination != path and max_bytes == len(b'Reviewed bytes')
        destination.write_bytes(b'Altered! bytes' if altered else b'Reviewed bytes')

    def launch(thread_id, *_args, **_kwargs):
        assert thread_id == chat_id
        [attachment] = media.required_thread_attachments(thread_id)
        assert Path(attachment['path']).read_bytes() == b'Reviewed bytes'
        return {'status': 'running'}

    client.get.reset_mock()
    client.get.side_effect = get
    client.download.side_effect = download
    monkeypatch.setattr(development_harness, 'development_execution_active', lambda *_: False)
    monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda *_: {})
    dispatch = Mock(side_effect=launch)
    launched = Event()
    worker = Mock()

    async def run(**kwargs):
        worker(**kwargs)
        launched.set()

    if entry == 'thread':
        monkeypatch.setattr(development_harness, 'dispatch_development_prompt', real_development_dispatch)
        monkeypatch.setattr(development_harness, '_run_execution', run)
        monkeypatch.setattr(development_harness, '_route', lambda *a, **kw: ('codex', 'fixture', {}, 'medium'))
    else:
        monkeypatch.setattr(development_harness, 'dispatch_development_prompt', dispatch)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    register_thread_routes(router, None)
    app.include_router(router)
    api = TestClient(app)
    endpoint = '/workflows/studio/incoming/proposals/thread'
    body = {'proposal': result['merged']}
    expected_reads = [((replacement_path,), {}), ((original_path,), {})]
    if entry == 'thread':
        endpoint = f'/workflows/studio/tasks/{chat_id}/messages'
        body = {'message': 'Work from the reviewed attachment.'}
        expected_reads = [((original_path,), {})]
    with get_session() as db:
        original_turns = db.query(Chat).filter_by(parent_id=chat_id).count()
    response = api.post(endpoint, json=body)

    def check_started(reply):
        assert reply.status_code == 200, reply.text
        if entry == 'incoming':
            assert reply.json()['chat_id'] == chat_id
            dispatch.assert_called_once()
            client.post.assert_called_once()
        else:
            assert reply.json()['started'] is True
            assert launched.wait(timeout=5)
            worker.assert_called_once()
            assert worker.call_args.kwargs['chat_id'] == chat_id
            [saved] = [item for item in worker.call_args.kwargs['attachments'] if item.get('name') == 'scope.txt']
            assert Path(saved['path']).read_bytes() == b'Reviewed bytes'
            client.post.assert_not_called()

    if scenario == 'matching':
        check_started(response)
        assert proposals.proposal_thread_attachment_report(chat_id)['failed_attachments'] == []
    else:
        assert response.status_code == 409
        assert response.json()['code'] == 'proposal_attachment_unavailable'
        assert 'scope.txt' in response.json().get('error', response.json().get('detail', ''))
        assert not path.exists()
        dispatch.assert_not_called()
        worker.assert_not_called()
        client.post.assert_not_called()
        with get_session() as db:
            assert db.query(Chat).filter_by(parent_id=chat_id).count() == original_turns
            workflow = AutoWorkflow(name='Attachment-dependent workflow', status='active')
            db.add(workflow)
            db.commit()
            workflow_id = workflow.id
        held_run = start_workflow_run(workflow_id, run_metadata={'chat_id': chat_id})
        assert held_run['code'] == 'proposal_attachment_unavailable'
        assert 'scope.txt' in held_run['error']
        with get_session() as db:
            assert db.query(AutoWorkflowRun).filter_by(workflow_id=workflow_id).count() == 0
    assert client.get.call_args_list == expected_reads
    assert client.download.call_count == (0 if scenario == 'moved' else 1)
    if scenario == 'changed':
        altered = False
        retry = api.post(endpoint, json=body)
        check_started(retry)
        assert client.download.call_count == 2
    create.assert_called_once()
    assert dismiss.call_count == 2


@pytest.mark.parametrize('cache_state', ['matching', 'changed', 'foreign_chat', 'moved_project', 'foreign_destination'])
def test_completed_local_merge_recovers_only_the_exact_whatsapp_cache(merge_case, monkeypatch, tmp_path, cache_state):
    from pathlib import Path
    from distr.core.db import Chat, WhatsAppMessage, get_session
    from distr.core.incoming import media

    first, second = merge_case
    conversation = f'cache-{first["project_id"]}@g.us'
    message_id = f'whatsapp-{first["project_id"]}-original'
    first.update(id=f'local:whatsapp:{conversation}', source='whatsapp', source_thread_id=conversation,
                 dedupe_key=f'whatsapp:{conversation}', message_ids=[message_id])
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    download = Mock(side_effect=AssertionError('Unexpected provider download'))
    monkeypatch.setattr(media, '_download_whatsapp', download)
    cache = tmp_path / 'relay-original'
    cache.write_bytes(b'Reviewed bytes')
    with get_session() as db:
        row = WhatsAppMessage(jid=conversation, message_id=message_id, media_type='document',
                              media_filename='scope.txt', media_local_path=str(cache))
        db.add(row)
        db.commit()
        row_id = row.id
    [record] = media.ensure_proposal_attachments(first, first['project_id'])['attachments']
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *_: [record])
    result = merge(first, second)
    destination = Path(record['path'])
    destination.unlink()
    if cache_state == 'changed':
        cache.write_bytes(b'Modified bytes')
        destination.write_bytes(b'Corrupted copy')
    elif cache_state == 'foreign_chat':
        with get_session() as db:
            db.get(WhatsAppMessage, row_id).jid = 'other-conversation@g.us'
            db.commit()
    elif cache_state == 'moved_project':
        monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope',
                            lambda original: original.get('source_thread_id') != conversation)
    elif cache_state == 'foreign_destination':
        foreign = tmp_path / '.decisions/workspaces/projects/999999/scope.txt'
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(b'Foreign bytes')
        with get_session() as db:
            chat = db.get(Chat, result['chat_id'])
            params = json.loads(chat.params)
            params['incoming_attachments'][0]['path'] = str(foreign.resolve())
            chat.params = json.dumps(params)
            db.commit()

    if cache_state == 'matching':
        proposals.restore_reviewed_thread_merge_attachments(result['chat_id'], first['project_id'])
        assert destination.read_bytes() == b'Reviewed bytes'
        assert proposals.proposal_thread_attachment_report(result['chat_id'])['failed_attachments'] == []
        destination.unlink()
        monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda *_: {})

        def dispatch(chat_id, *_args, **_kwargs):
            assert chat_id == result['chat_id']
            assert destination.read_bytes() == b'Reviewed bytes'
            return {'status': 'running'}

        worker = Mock(side_effect=dispatch)
        monkeypatch.setattr('distr.core.workflow.development_harness.dispatch_development_prompt', worker)
        assert proposals.create_proposal_thread(result['merged'], start_work=True) == result['chat_id']
        worker.assert_called_once()
    else:
        with pytest.raises(proposals.ReviewedMergeAttachmentUnavailable, match='scope.txt'):
            proposals.restore_reviewed_thread_merge_attachments(result['chat_id'], first['project_id'])
        if cache_state == 'changed':
            assert destination.read_bytes() == b'Corrupted copy'
        else:
            assert not destination.exists()
        if cache_state == 'foreign_destination':
            assert foreign.read_bytes() == b'Foreign bytes'
    download.assert_not_called()


@pytest.mark.parametrize(('provider', 'destination_inbox', 'cache_state'), [
    ('gmail', False, 'matching'),
    ('mailshot', True, 'matching'),
    ('gmail', False, 'changed'),
    ('gmail', False, 'foreign_conversation'),
    ('gmail', False, 'foreign_manifest'),
    ('gmail', False, 'malformed_manifest'),
    ('mailshot', True, 'moved_project'),
])
def test_completed_local_merge_recovers_only_verified_mail_copy(merge_case, monkeypatch, tmp_path,
                                                                provider, destination_inbox, cache_state):
    from pathlib import Path
    from distr.core.incoming.mail_attachments import store_mail_attachment

    first, second = merge_case
    project_id = first['project_id']
    conversation = f'cache-{project_id}'
    reference = f'local:{provider}:{conversation}'
    first.update(id=reference, source=provider, source_thread_id=conversation,
                 dedupe_key=f'{provider}:{conversation}', message_ids=[f'mail-{project_id}'])
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    download = Mock(return_value=(b'Reviewed bytes', 'text/plain', 'reviewed.txt'))
    item = {'provider': provider, 'message_id': first['message_ids'][0], 'attachment_id': 'part-1', 'name': 'scope.txt'}
    record = store_mail_attachment(item, reference=reference, downloader=download,
                                   project_id=None if destination_inbox else project_id)
    cached_reference = f'{reference}-other' if cache_state == 'foreign_conversation' else reference
    cached = store_mail_attachment(item, reference=cached_reference, downloader=download,
                                   project_id=project_id if destination_inbox else None)
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *_: [record])
    result = merge(first, second)
    destination, source = Path(record['path']), Path(cached['path'])
    destination.unlink()
    if cache_state == 'matching':
        destination.with_name(destination.name + '.record.json').unlink()
    elif cache_state == 'changed':
        source.write_bytes(b'Modified bytes')
        destination.write_bytes(b'Corrupted copy')
    elif cache_state == 'foreign_manifest':
        manifest = source.with_name(source.name + '.record.json')
        manifest.write_text(json.dumps({**cached, 'source_message_id': 'other-message'}))
    elif cache_state == 'malformed_manifest':
        source.with_name(source.name + '.record.json').write_text('[]')
    elif cache_state == 'moved_project':
        monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope',
                            lambda original: original.get('source_thread_id') != conversation)

    if cache_state == 'matching':
        proposals.restore_reviewed_thread_merge_attachments(result['chat_id'], project_id)
        assert destination.read_bytes() == source.read_bytes() == b'Reviewed bytes'
        assert proposals.proposal_thread_attachment_report(result['chat_id'])['failed_attachments'] == []
        reused = store_mail_attachment(item, reference=reference, downloader=download,
                                       project_id=None if destination_inbox else project_id)
        assert reused['sha256'] == record['sha256']
    else:
        with pytest.raises(proposals.ReviewedMergeAttachmentUnavailable, match='reviewed.txt'):
            proposals.restore_reviewed_thread_merge_attachments(result['chat_id'], project_id)
        if cache_state == 'changed':
            assert destination.read_bytes() == b'Corrupted copy'
        else:
            assert not destination.exists()
    assert download.call_count == 2


def test_merge_reconciles_recovered_attachment_without_hiding_distinct_failure(monkeypatch):
    first = {'source': 'whatsapp', 'source_thread_id': 'same-chat'}
    second = {**first, 'id': 'second'}
    failure = {'name': 'report.pdf', 'source_message_id': 'message-1', 'error': 'offline'}
    distinct = {**failure, 'source_message_id': 'message-2'}
    recovered = {'name': 'report.pdf', 'source_message_id': 'message-1',
                 'path': '/managed/report.pdf', 'sha256': 'verified', 'size': 10}
    ensure = Mock(side_effect=[
        {'attachments': [], 'failures': [failure, distinct]},
        {'attachments': [recovered], 'failures': []},
    ])
    monkeypatch.setattr('distr.core.incoming.media.ensure_proposal_attachments', ensure)
    monkeypatch.setattr('distr.core.incoming.transcription.with_transcript', lambda row: row)

    assert proposals._merge_proposal_attachments(first, second, 1) == [distinct, recovered]


@pytest.mark.parametrize('status', ['dismissed', 'sent_to_decisions', 'approved', None])
def test_dismissed_authoritative_proposal_never_dispatches(monkeypatch, merge_case, status):
    a = merge_case[0]
    client = Mock()
    client.get.return_value = {'id':990104, 'status':status, 'attachments':[],
                              'project': {'id': 123, 'decisions_project_id': a['project_id'], 'name': a['project_name']}}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs:client)
    with pytest.raises(ValueError, match='no longer pending'):
        proposals.create_proposal_thread({**a, 'id':'tensology:990104'}, start_work=True)


@pytest.mark.parametrize('text', ['Thanks, add a dark mode toggle.', 'Great, update the logo.', 'Please fix the failing E2E tests.', 'Please test E2E login.'])
def test_normal_requests_survive_prefilter(text):
    assert proposals._worth_triaging({'items':[{'text':text, 'sender':'Client', 'direction':'inbound'}], 'label':'Work'})


@pytest.mark.parametrize('text', ['Thanks!', '👍', 'Simulated E2E test please fix fixture'])
def test_acknowledgements_and_synthetic_messages_stay_filtered(text):
    assert not proposals._worth_triaging({'items':[{'text':text, 'sender':'Client', 'direction':'inbound'}], 'label':'Work'})


def test_full_durable_brief_survives_storage_and_restore(merge_case):
    a = merge_case[0]
    brief = {**a, 'description':'Detailed requirement. '*800,
             'tasks':[{'title':f'Task {index}', 'acceptance_criteria':'Required acceptance. '*250} for index in range(220)],
             'evidence':[{'id':str(index), 'body':'Original request paragraph. '*250} for index in range(220)]}
    proposals._record_local_proposals([brief])
    restored = next(row for row in proposals._saved_local_proposals() if row['id'] == a['id'])
    for field in ('description', 'tasks', 'evidence'):
        assert restored[field] == brief[field]
    proposals._persist_proposal_dismissal(brief)
    restored = proposals.undismiss_proposal(proposals._proposal_handoff_key(brief))['proposal']
    for field in ('description', 'tasks', 'evidence'):
        assert restored[field] == brief[field]
    assert len(proposals.proposal_snapshot_json(brief).encode()) <= 20000


@pytest.mark.parametrize('payload', [
    {'estimated_amount':5000}, {'evidence':[{'transcript':'Deploy to production'}]},
    {'attachments':[{'caption':'delete the production database'}]},
    {'context_evidence':[{'body':'Buy a paid subscription'}]},
])
def test_auto_accept_checks_all_retained_risk_fields(payload):
    assert proposals.auto_accept_skip_reason({'origin':'local', 'project_id':9, 'project_name':'DecisionsAI', 'title':'Update app', **payload}, {})


def test_canonical_context_remains_context_only():
    row = proposals._from_tensology({'id':990105, 'context_evidence':[{'id':'old', 'body':'Old request'}]})
    assert row['evidence'][0]['context_only'] is True


def test_synthetic_merged_source_authorizes_every_original():
    from distr.core.incoming.project_links import proposal_in_scope
    proposal = {'project_id':9, 'source':'whatsapp', 'source_thread_id':'merged:99',
                'source_scope':[{'source':'whatsapp', 'source_thread_id':'first'}, {'source':'gmail', 'source_thread_id':'second'}]}
    index = {('whatsapp','first'):{9}, ('gmail','second'):{9}}
    assert proposal_in_scope(proposal, index)
    assert not proposal_in_scope(proposal, {('whatsapp','first'):{9}})
    assert not proposal_in_scope(proposal, {('whatsapp','first'):{9}, ('gmail','second'):{10}})
    assert not proposal_in_scope({**proposal, 'source_scope':[]}, {('whatsapp','merged:99'):{9}})
    hidden = {**proposal, 'source_scope':proposal['source_scope'][:1],
              'evidence':[{'id':'other', 'original_source':{'channel':'gmail', 'source_thread_id':'unlinked'}}]}
    assert not proposal_in_scope(hidden, index)


@pytest.mark.parametrize('failure', ['lost_response', 'lost_receipt', 'rejected'])
def test_mixed_merge_can_resume_without_either_original_card(monkeypatch, merge_case, failure):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.core.db import IncomingMixedMergeIntent, get_session
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices
    from distr.gui.web.routes.development.incoming import register_routes

    base_id = {'lost_response': 990280, 'lost_receipt': 990284, 'rejected': 990288}[failure]
    remote, _ = _remote_pair(monkeypatch, merge_case, ids=(base_id, base_id + 1))
    local = merge_case[1]
    client = tensology_client.configured_tensology_client()
    original = client.get.side_effect
    client.get.side_effect = lambda path: {**original(path), 'merge_revision': 'a' * 64,
                                           'merge_dismissal_revision_supported': True}
    key = proposals._merge_key([proposals._proposal_handoff_key(row) for row in (remote, local)])
    replacement = {'id': base_id + 2, 'title': 'Saved mixed merge'}
    calls = []

    def create(body, *, idempotency_key):
        assert idempotency_key == key
        with get_session() as db:
            assert db.get(IncomingMixedMergeIntent, key) is not None
        calls.append(body)
        if failure == 'rejected':
            raise tensology_client.TensologyApiError('Sources changed', status=409, code='merge_source_changed')
        if len(calls) == 1 and failure == 'lost_response':
            raise OSError('Interrupted before receipt')
        return replacement

    dismiss = Mock(side_effect=_confirmed_dismissal)
    model = Mock(return_value='{"title":"Saved mixed merge"}')
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', create)
    monkeypatch.setattr(tensology_client, 'decide_work_request', dismiss)
    monkeypatch.setattr('distr.core.read_response._default_complete', model)
    if failure == 'rejected':
        with pytest.raises(tensology_client.TensologyApiError, match='Sources changed'):
            proposals.merge_proposals(remote, local)
        assert proposals.incoming_merge_recovery({'proposals': [remote, local]})['proposals'] == [remote, local]
        with get_session() as db:
            assert db.get(IncomingMixedMergeIntent, key) is None
        dismiss.assert_not_called()
        return
    with monkeypatch.context() as patch:
        if failure == 'lost_receipt':
            patch.setattr(proposals, '_record_proposal_handoff', Mock(side_effect=OSError('Interrupted before receipt')))
        with pytest.raises(OSError, match='Interrupted'):
            proposals.merge_proposals(remote, local)
    dismiss.assert_not_called()
    assert proposals.incoming_merge_recovery({'proposals': [remote, local]})['proposals'] == []
    assert any(row['merge_key'] == key and row.get('local_recovery')
               for row in proposals.incoming_merge_recovery({'proposals': []})['pending_merges'])
    with pytest.raises(ValueError, match='reserved'):
        proposals.create_proposal_thread(local, start_work=True)

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    api = TestClient(app)
    endpoint = '/workflows/studio/incoming/proposals/merge'
    result = api.post(endpoint, json={'local_recovery': key})
    assert result.status_code == 200, result.text
    chat_id = result.json()['chat_id']
    assert {choice['thread_id']: choice['message_ids'] for choice in source_choices(proposal_for_thread(chat_id))} == {
        row['source_thread_id']: row['message_ids'] for row in (remote, local)
    }
    assert calls[1].get('resume_replacement_id') == (replacement['id'] if failure == 'lost_receipt' else None)
    assert calls[0]['title'] == calls[1]['title'] == replacement['title']
    model.assert_called_once()
    assert api.post(endpoint, json={'local_recovery': key}).json()['chat_id'] == chat_id
    assert len(calls) == 2 and dismiss.call_count == 1
    with get_session() as db:
        assert db.get(IncomingMixedMergeIntent, key) is None


@pytest.mark.parametrize(('source_id', 'scenario'), enumerate(
    ['intact', 'local-changed', 'refetch', 'changed-source', 'offline', 'unlisted', 'unsafe-path'], start=990292,
), ids=['intact', 'local-changed', 'refetch', 'changed-source', 'offline', 'unlisted', 'unsafe-path'])
def test_fresh_process_recovers_only_verified_mixed_merge_media(monkeypatch, merge_case, tmp_path, source_id, scenario):
    import subprocess
    import sys
    from pathlib import Path
    from distr.core import tensology_client
    from distr.core.incoming import media

    remote, _ = _remote_pair(monkeypatch, merge_case, ids=(source_id, source_id + 10))
    local = merge_case[1]
    key = proposals._merge_key([proposals._proposal_handoff_key(row) for row in (remote, local)])
    upstream = tensology_client.configured_tensology_client()
    canonical = {**upstream.get(f'work-requests/{source_id}'), 'merge_revision': 'a' * 64,
                 'merge_dismissal_revision_supported': True}
    upstream.get.side_effect = None
    upstream.get.return_value = canonical
    location = f'work-requests/{source_id}/attachment?message_id=1234567890123456&index=0'
    if scenario in {'intact', 'local-changed'}:
        path = tmp_path / '.decisions/workspaces/projects' / str(local['project_id']) / 'attachments' / 'scope.txt'
        path.parent.mkdir(parents=True)
        identity, metadata = 'restart-fixture', {}
    else:
        identity = f'tensology:{source_id}:{location}'
        metadata = {'api_path': location}
        monkeypatch.setattr(Path, 'home', lambda: tmp_path)
        path = media._destination(media._root(local['project_id'], f'tensology:{source_id}'), identity, 'scope.txt')
        if scenario == 'unsafe-path':
            path = tmp_path / 'scope.txt'
        if scenario != 'unlisted':
            canonical['attachments'] = [{'name': 'scope.txt', 'api_path': location}]
    path.write_bytes(b'Original proposal scope')
    record = media._record(path, 'scope.txt', 'text/plain', identity, **metadata)
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *_: [record])
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', Mock(side_effect=OSError('Response lost')))
    with pytest.raises(OSError, match='Response lost'):
        merge(remote, local)
    if scenario == 'refetch':
        path.unlink()
        path.parent.rmdir()
    elif scenario != 'intact':
        path.write_bytes(b'Changed proposal bytes!')

    script = """
import json, runpy, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

runpy.run_path('tests/conftest.py')
from distr.core import tensology_client
from distr.core.db import Chat, get_session
from distr.core.incoming import proposals
from distr.core.incoming.media import resolve_thread_attachment
from distr.core.incoming.source_reply import proposal_for_thread, source_choices

key, canonical, root, attachment_id, scenario, expected_path, location = sys.argv[1:]
canonical = json.loads(canonical)
calls = []
downloads = []

def download(link, target, *, max_bytes):
    assert link == location and Path(target) != Path(expected_path)
    assert max_bytes == len(b'Original proposal scope')
    downloads.append(link)
    if scenario == 'offline':
        raise tensology_client.TensologyApiError('Offline')
    target.write_bytes(b'Changed proposal bytes!' if scenario == 'changed-source' else b'Original proposal scope')

def create(body, *, idempotency_key):
    assert idempotency_key == key
    calls.append(body)
    return {'id': canonical['id'] + 100, 'title': 'Recovered media'}

with patch.object(Path, 'home', return_value=Path(root)), \
     patch.object(tensology_client, 'configured_tensology_client', return_value=SimpleNamespace(get=lambda _: canonical, download=download)), \
     patch.object(tensology_client, 'create_merged_work_request', side_effect=create), \
     patch.object(tensology_client, 'decide_work_request', side_effect=lambda proposal_id, *_args, **_kwargs: {'id': proposal_id, 'status': 'dismissed'}), \
     patch('distr.core.incoming.project_links.proposal_in_scope', return_value=True), \
     patch('distr.core.incoming.project_links.source_project_index', return_value={}), \
     patch('distr.core.incoming.transcription.prepare_thread_transcripts'), \
     patch('distr.core.incoming.events.publish_incoming_change'), \
     patch('distr.core.incoming.mail_attachments._download_mail_bytes', side_effect=AssertionError('mailbox is offline')):
    recovery = proposals.incoming_merge_recovery({'proposals': []})
    assert any(row['merge_key'] == key and row['local_recovery'] for row in recovery['pending_merges'])
    result = proposals.resume_mixed_proposal_merge(key)
    chat_id = result['chat_id']
    assert proposals.resume_mixed_proposal_merge(key)['chat_id'] == chat_id and len(calls) == 1
    assert len(source_choices(proposal_for_thread(chat_id))) == 2
    assert downloads == ([location] if scenario in {'refetch', 'changed-source', 'offline'} else [])
    assert not list(Path(expected_path).parent.glob('.merge-media-*'))
    with get_session() as db:
        unavailable = proposals.unavailable_thread_attachment_names(db.get(Chat, chat_id))
    if scenario not in {'intact', 'refetch'}:
        report = proposals.proposal_thread_attachment_report(chat_id)
        assert any(row['name'] == 'scope.txt' and row.get('error') for row in report['failed_attachments']), report
        assert result['failed_attachments'] == [{'name': 'scope.txt'}]
        assert unavailable == ['scope.txt']
        assert not any(row.get('path') for row in result['merged']['attachments'])
        assert Path(expected_path).read_bytes() == b'Changed proposal bytes!'
    else:
        source, _ = resolve_thread_attachment(chat_id, attachment_id)
        assert source == Path(expected_path)
        assert source.read_bytes() == b'Original proposal scope'
        assert result['failed_attachments'] == []
        assert unavailable == []
        source.unlink()
        reopened = proposals.resume_mixed_proposal_merge(key)
        assert reopened['chat_id'] == chat_id and len(calls) == 1
        assert reopened['failed_attachments'] == [{'name': 'scope.txt'}]
        assert all(not row.get('path') for row in reopened['merged']['attachments'])
        source.write_bytes(b'Original proposal scope')
        assert proposals.resume_mixed_proposal_merge(key)['failed_attachments'] == []
"""
    fresh = subprocess.run([sys.executable, '-c', script, key, json.dumps(canonical), str(tmp_path),
                            record['id'], scenario, str(path.resolve()), location], capture_output=True, text=True, timeout=20)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr


def test_mixed_merge_replays_exact_pair_and_rejects_consumed_original(monkeypatch, merge_case):
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices

    remote, _ = _remote_pair(monkeypatch, merge_case, ids=(990211, 990212))
    local = merge_case[1]
    upstream_evidence = {990211: remote['message_ids'], 990213: ['new-remote-message']}

    def create(body, **kwargs):
        assert len(body['merged_from']) == 1
        source_id = body['merged_from'][0]
        assert body['source_message_ids'] == upstream_evidence[source_id]
        return {'id': source_id + 20}

    replacement = Mock(side_effect=create)
    monkeypatch.setattr('distr.core.tensology_client.create_merged_work_request', replacement)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', _confirmed_dismissal)
    first = merge(remote, local)
    assert {row['thread_id']: row['message_ids'] for row in source_choices(proposal_for_thread(first['chat_id']))} == {
        remote['source_thread_id']: remote['message_ids'], local['source_thread_id']: local['message_ids'],
    }
    assert merge(local, remote)['chat_id'] == first['chat_id']
    other = {**local, 'id':local['id'] + '-next', 'message_ids':['new-source-message']}
    with pytest.raises(ValueError, match="no longer pending"):
        merge(remote, other)
    newer = {**merge_case[0], 'message_ids': ['new-remote-message']}
    fresh_remote, _ = _remote_pair(monkeypatch, (newer, other), ids=(990213, 990214))
    second = merge(fresh_remote, other)
    assert first['chat_id'] != second['chat_id']
    assert replacement.call_count == 2
    assert replacement.call_args_list[0].kwargs['idempotency_key'] != replacement.call_args_list[1].kwargs['idempotency_key']


def test_merge_route_keeps_upstream_revision_conflict(monkeypatch, merge_case):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.gui.web.routes.development.incoming import register_routes

    a, b = _remote_pair(monkeypatch, merge_case, ids=(990226, 990227))
    error = 'A proposal or its evidence changed. Refresh Incoming before merging.'
    monkeypatch.setattr('distr.core.read_response._default_complete', lambda *_: '{}')
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', Mock(
        side_effect=tensology_client.TensologyApiError(error, status=409, code='merge_source_changed'),
    ))
    dismiss = Mock()
    monkeypatch.setattr(tensology_client, 'decide_work_request', dismiss)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)

    response = TestClient(app).post('/workflows/studio/incoming/proposals/merge', json={'first': a, 'second': b})

    assert response.status_code == 409
    assert response.json() == {'success': False, 'error': error}
    dismiss.assert_not_called()


@pytest.mark.parametrize('changed', ['project', 'identity', 'messages', 'conversation', 'edited', 'revision'])
def test_merge_rejects_refetched_original_that_changed_scope(monkeypatch, merge_case, changed):
    from distr.core import tensology_client

    a, b = _remote_pair(monkeypatch, merge_case, ids=(990215, 990216))
    if changed == 'edited':
        a['details_edited_at'] = b['details_edited_at'] = None
    if changed == 'revision':
        a['merge_revision'] = b['merge_revision'] = 'a' * 64
    client = tensology_client.configured_tensology_client()
    original = client.get.side_effect

    def get(path):
        row = original(path)
        if changed == 'identity':
            return {**row, 'id': 990217}
        if changed == 'messages':
            return {**row, 'source_message_ids': ['new-message']}
        if changed == 'conversation':
            return {**row, 'source_conversation': {'thread_id': 'new-thread'}}
        if changed == 'edited':
            return {**row, 'details_edited_at': '2026-11-11T15:00:00Z'}
        if changed == 'revision':
            return {**row, 'merge_revision': 'b' * 64}
        return {**row, 'project': {**row['project'], 'decisions_project_id': a['project_id'] + 1}}

    client.get.side_effect = get
    replacement = Mock()
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', replacement)
    with pytest.raises(ValueError, match="changed"):
        merge(a, b)
    replacement.assert_not_called()


@pytest.mark.parametrize('problem', [None, 'partner', 'replacement'])
def test_retry_upstream_merge_uses_exact_reserved_sources_and_never_regenerates_title(monkeypatch, merge_case, problem):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import tensology_client
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices
    from distr.gui.web.routes.development.incoming import register_routes

    first = {None: 990260, 'partner': 990264, 'replacement': 990268}[problem]
    pair = _remote_pair(monkeypatch, merge_case, ids=(first, first + 1))
    client = tensology_client.configured_tensology_client()
    original = client.get.side_effect
    retry = {'replacement_id': first + 2, 'original_ids': [first, first + 1]}
    client.get.side_effect = lambda path: {
        **original(path), 'merge_revision': 'a' * 64, 'merge_dismissal_revision_supported': True,
        'merge_source_reserved': True,
        'merge_retry': None if problem == 'partner' and path.endswith(str(first + 1)) else retry,
    }
    create = Mock(return_value={'id': first + (3 if problem == 'replacement' else 2), 'title': 'Saved merge'})
    dismiss = Mock(side_effect=_confirmed_dismissal)
    model = Mock()
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', create)
    monkeypatch.setattr(tensology_client, 'decide_work_request', dismiss)
    monkeypatch.setattr('distr.core.read_response._default_complete', model)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    api = TestClient(app)
    endpoint = '/workflows/studio/incoming/proposals/merge'
    response = api.post(endpoint, json={'retry': retry})
    model.assert_not_called()
    if problem:
        assert response.status_code == 409
        assert create.call_count == (problem == 'replacement')
        dismiss.assert_not_called()
        return
    assert response.status_code == 200
    chat_id = response.json()['chat_id']
    assert {choice['thread_id'] for choice in source_choices(proposal_for_thread(chat_id))} == {
        row['source_thread_id'] for row in pair
    }
    assert create.call_args.args[0]['resume_replacement_id'] == retry['replacement_id']
    assert dismiss.call_count == 2
    assert api.post(endpoint, json={'retry': retry}).json()['chat_id'] == chat_id
    assert create.call_count == 1 and dismiss.call_count == 2


def test_merge_retry_forwards_revisions_and_keeps_committed_title(monkeypatch, merge_case):
    from distr.core import tensology_client

    pair = _remote_pair(monkeypatch, merge_case, ids=(990221, 990222))
    client = tensology_client.configured_tensology_client()
    original = client.get.side_effect
    revisions = {'990221': 'a' * 64, '990222': 'b' * 64}
    client.get.side_effect = lambda path: {**original(path), 'merge_revision': revisions[path.split('/')[-1]],
                                           'merge_dismissal_revision_supported': True}
    replacement = Mock(side_effect=[TimeoutError('response lost'), {'id': 990223, 'title': 'Committed title'}])
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', replacement)
    dismiss = Mock(side_effect=_confirmed_dismissal)
    monkeypatch.setattr(tensology_client, 'decide_work_request', dismiss)

    with pytest.raises(TimeoutError, match='response lost'):
        proposals.merge_proposals(*pair, complete=lambda *_: '{"title":"Committed title"}')
    dismiss.assert_not_called()
    result = proposals.merge_proposals(*reversed(pair), complete=lambda *_: '{"title":"New model title"}')

    first, replay = replacement.call_args_list
    assert first.kwargs['idempotency_key'] == replay.kwargs['idempotency_key']
    assert first.args[0]['source_merge_revisions'] == replay.args[0]['source_merge_revisions'] == revisions
    assert replay.args[0]['title'] == 'New model title'
    assert result['merged']['title'] == 'Committed title'
    assert dismiss.call_count == 2


def test_merge_rejects_partial_revision_support_before_preparing_work(monkeypatch, merge_case):
    from distr.core import tensology_client

    pair = _remote_pair(monkeypatch, merge_case, ids=(990224, 990225))
    client = tensology_client.configured_tensology_client()
    original = client.get.side_effect
    client.get.side_effect = lambda path: {**original(path), 'merge_revision': 'a' * 64 if path.endswith('224') else None}
    replacement = Mock()
    complete = Mock()
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', replacement)

    with pytest.raises(ValueError, match='incomplete proposal revisions'):
        proposals.merge_proposals(*pair, complete=complete)
    replacement.assert_not_called()
    complete.assert_not_called()


def test_merge_transport_preserves_full_idempotency_key(monkeypatch):
    from distr.core.tensology_client import create_merged_work_request
    client = Mock()
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs:client)
    key = 'merge:' + 'a' * 64
    create_merged_work_request({'merged_from':[123]}, idempotency_key=key)
    assert client.post.call_args.kwargs['idempotency_key'] == key
