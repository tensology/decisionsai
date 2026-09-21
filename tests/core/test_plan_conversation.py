"""Isolated persistence tests; real local model checks require explicit opt-in."""
import json
import os
import sys
from contextlib import contextmanager
from types import ModuleType

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem, PlanWorkspace, PlanItem, PlanItemRevision
from distr.core.planning import conversation as c, service


@pytest.fixture
def store(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    monkeypatch.setattr(c, "get_session", session)
    monkeypatch.setattr(service, "get_session", session)
    monkeypatch.setattr(c, "load_settings_from_db", lambda: {"conversational_llm_provider": "ollama", "conversational_llm_model": "default"})
    assets = ModuleType("distr.core.planning.assets")
    assets.list_assets = lambda wid: []
    assets.context_assets = lambda wid, ids: []
    assets.source_context = lambda wid: {"description": "Project description", "notes": "Project notes", "context_items": [{"content": "Source context"}]}
    monkeypatch.setitem(sys.modules, assets.__name__, assets)
    with session() as db:
        db.add(Project(id=1, name="Synthetic planning project"))
        db.add(PlanWorkspace(id=1, project_id=1, board_key="decisions:1", board_provider="decisions", board_name="Plan"))
        db.add(PlanWorkspace(id=2, board_key="decisions:2", board_provider="decisions", board_name="Other"))
        db.add(PlanItem(id=1, workspace_id=1, item_type="prd", title="Requirements", content="Old", content_format="markdown"))
        db.add(PlanItem(id=2, workspace_id=2, item_type="prd", title="Other", content="Private", content_format="markdown"))
        db.add(PlanItemRevision(item_id=1, revision=1, title="Requirements", content="Old", status="draft"))
    yield session, assets
    engine.dispose()


def respond(monkeypatch, value):
    calls = []
    def stream(provider, model, messages, settings):
        calls.append((provider, model, messages))
        yield value if isinstance(value, str) else json.dumps(value)
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    return calls


def send(**kw):
    return c.send_message(1, message="Improve requirements", provider="openai", model_name="chosen", **kw)


def test_lookup_does_not_create_chat(store):
    result = c.get_conversation(1)
    assert result["messages"] == []
    assert result["chat_id"] is None
    assert result["model_name"] == "default"
    assert result["provider"] == "ollama"
    assert result["provider_routes"]["ollama"]["backend_id"] == "pi"
    assert result["provider_routes"]["openai"]["runtime_id"] == "provider_api"
    with store[0]() as db:
        assert db.query(Chat).count() == 0


def test_planning_contract_covers_feature_impacts_and_real_project_assets(store, monkeypatch):
    calls = respond(monkeypatch, {"reply": "Let's discuss the flow.", "edits": []})
    assert send()["status"] == "complete"
    instruction = calls[0][2][0]["content"]
    assert "Plan features, not isolated pages" in instruction
    assert "update the linked artifacts together" in instruction
    assert "Do not silently invent business rules" in instruction
    assert "uploaded image assets render" in instruction
    assert "Images and charts are placeholders" not in instruction
    assert "Do not add generic dashboard cards" in instruction
    assert "do not copy the example domain" in instruction


def test_build_uses_plain_conversation_copy(monkeypatch):
    observed = {}
    def turn(workspace_id, **kwargs):
        observed.update(kwargs)
        return {"status": "complete"}
    monkeypatch.setattr(c, "_turn", turn)
    c.generate_build(1, provider="ollama", model_name="chosen")
    assert observed["message"] == "Build tasks from this plan."
    assert observed["build"] is True


def test_dead_owner_read_is_nonmutating_and_next_prompt_recovers(store, monkeypatch):
    root_id, old_id = c._claim(1, "Old prompt", "openai", "chosen", [])
    monkeypatch.setattr(c, "owner_is_gone", lambda owner: True)
    observed = c.get_conversation(1)
    assert observed["status"] == "interrupted"
    assert observed["messages"][-1]["content"] == c.INTERRUPTED_REPLY
    with store[0]() as db:
        assert json.loads(db.get(Chat, root_id).params)["plan_status"] == "running"
        assert not db.get(Chat, old_id).response
    respond(monkeypatch, {"reply": "Continued", "edits": []})
    assert send()["status"] == "complete"
    with store[0]() as db:
        old = db.get(Chat, old_id)
        assert json.loads(old.params)["status"] == "interrupted"
        assert old.response == c.INTERRUPTED_REPLY
        assert json.loads(db.get(Chat, root_id).params)["plan_turn_id"] != old_id
    with pytest.raises(ValueError, match="no longer owns"):
        c._finish(root_id, old_id, "Late result", status="complete")


def test_live_owner_remains_locked_even_if_client_disconnected(store):
    c._claim(1, "Still working", "openai", "chosen", [])
    assert c.get_conversation(1)["status"] == "running"
    with pytest.raises(ValueError, match="already running"):
        c._claim(1, "Duplicate", "openai", "chosen", [])


def test_legacy_lock_without_owner_is_not_silently_reclaimed(store):
    root_id, _ = c._claim(1, "Legacy", "openai", "chosen", [])
    with store[0]() as db:
        root = db.get(Chat, root_id)
        root.params = json.dumps({"plan_status": "running"})
    assert c.get_conversation(1)["status"] == "running"
    with pytest.raises(ValueError, match="already running"):
        c._claim(1, "Duplicate", "openai", "chosen", [])


def test_invalid_linked_proposal_reports_reference_and_preserves_plan(store, monkeypatch):
    calls = respond(monkeypatch, {"reply": "Added customer form", "edits": [
        {"item_type": "wireframe", "title": "Customers", "content_format": "wire",
         "content": 'screen "Customers"\n  input "Name" bind=Customer.name'},
        {"id": 1, "item_type": "prd", "title": "Requirements", "content_format": "markdown",
         "content": "## FR-001\nSave a customer", "expected_revision": 1},
    ]})
    result = send()
    assert result["status"] == "error"
    assert "unresolved bind=Customer.name" in result["messages"][-1]["content"]
    persisted = c.get_conversation(1)
    assert persisted["messages"][-1]["content"] == result["messages"][-1]["content"]
    items = service.get_workspace(1)["items"]
    assert len(items) == 1
    assert items[0]["content"] == "Old"
    assert items[0]["revision_count"] == 1
    assert len(calls) == 2  # One repair, never an unbounded model loop.


@pytest.mark.parametrize("repair_kind", ["valid", "questions", "empty", "stale"])
def test_reference_repair_keeps_scope_and_original_snapshot(store, monkeypatch, repair_kind):
    wire = {"item_type": "wireframe", "title": "Customers", "content_format": "wire",
            "content": 'screen Customers\n  input Name bind=Customer.name'}
    broken = {"reply": "Created", "edits": [wire]}
    repaired = {"reply": "Created linked customer screen", "edits": [wire,
        {"item_type": "erd", "title": "Customers", "content_format": "mermaid",
         "content": "erDiagram\n Customer {\n string name\n }"}]}
    if repair_kind == "questions":
        repaired = {"reply": "", "edits": [], "questions": ["Should this customer name be stored?"]}
    if repair_kind == "empty":
        repaired = {"reply": "Fixed", "edits": []}
    calls = []
    def stream(provider, model, messages, settings):
        calls.append((provider, model, messages))
        if len(calls) == 2:
            # The first rejected proposal must not have committed anything.
            assert len(service.get_workspace(1)["items"]) == 1
            if repair_kind == "stale":
                service.update_item(1, content="Concurrent user change", expected_revision=1)
        yield json.dumps(broken if len(calls) == 1 else repaired)
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    result = send()
    assert len(calls) == 2
    assert all(call[:2] == ("openai", "chosen") for call in calls)
    assert calls[1][2][:-2] == calls[0][2]
    assert "Customer.name" in calls[1][2][-1]["content"]
    assert result["status"] == {"valid": "complete", "questions": "needs_input", "empty": "error", "stale": "error"}[repair_kind]
    items = service.get_workspace(1)["items"]
    assert len(items) == (3 if repair_kind == "valid" else 1)
    if repair_kind == "stale":
        assert items[0]["content"] == "Concurrent user change"
    with store[0]() as db:
        child = db.query(Chat).filter(Chat.parent_id.is_not(None)).one()
        assert json.loads(child.params)["proposal_repair"]["attempts"] == 1


@pytest.mark.parametrize('kind,fmt,invalid,valid', [
    ('wireframe', 'wire', 'screen Customers\n  made-up-widget Name', 'screen Customers\n  input Name'),
    ('erd', 'mermaid', 'erDiagram\n Customer { broken }', 'erDiagram\n Customer {\n string name\n }'),
])
@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_artifact_syntax_gets_one_atomic_repair(store, monkeypatch, kind, fmt, invalid, valid, repair_succeeds):
    calls = []
    def stream(provider, model, messages, settings):
        calls.append(messages)
        assert (provider, model) == ('openai', 'chosen')
        current = service.get_workspace(1)['items']
        assert len(current) == 1 and current[0]['content'] == 'Old'
        content = valid if len(calls) == 2 and repair_succeeds else invalid
        yield json.dumps({'reply':'Updated', 'edits':[
            {'item_type':kind, 'title':'Customers', 'content_format':fmt, 'content':content},
            {'id':1, 'item_type':'prd', 'title':'Requirements', 'content_format':'markdown',
             'content':'Updated requirement', 'expected_revision':1},
        ]})
    monkeypatch.setattr(c.llm_factory, 'create_stream', stream)
    result = send()
    assert len(calls) == 2
    assert calls[1][:-2] == calls[0]
    assert 'syntax' in calls[1][-1]['content']
    assert result['status'] == ('complete' if repair_succeeds else 'error')
    items = service.get_workspace(1)['items']
    assert len(items) == (2 if repair_succeeds else 1)
    if not repair_succeeds:
        assert items[0]['content'] == 'Old'
        assert 'Invalid' in result['messages'][-1]['content']


@pytest.mark.parametrize("page_id, valid", [("customers", True), ("removed-page", False)])
def test_selected_wireframe_page_is_validated_and_passed_to_model(store, monkeypatch, page_id, valid):
    with store[0]() as db:
        item = db.get(PlanItem, 1)
        item.item_type, item.content_format = "wireframe", "wire"
        item.content = 'screen "Customers" id=customers\n  input "Name"'
    calls = respond(monkeypatch, {"reply": "Discussing this page", "edits": []})
    result = send(item_id=1, page_id=page_id)
    assert result["status"] == ("complete" if valid else "error")
    assert len(calls) == int(valid)
    if valid:
        assert '"page_id": "customers"' in calls[0][2][-1]["content"]


def test_page_selection_cannot_target_requirements(store, monkeypatch):
    calls = respond(monkeypatch, {"reply": "Must not be called", "edits": []})
    assert send(item_id=1, page_id="customers")["status"] == "error"
    assert not calls


def test_selected_model_and_atomic_edits_persist(store, monkeypatch):
    calls = respond(monkeypatch, {"reply": "Updated", "edits": [{"id": 1, "item_type": "prd", "title": "Requirements", "content": "New", "content_format": "markdown", "expected_revision": 1}]})
    result = send()
    assert result["status"] == "complete"
    assert calls[0][:2] == ("openai", "chosen")
    assert "Private" not in json.dumps(calls[0][2])
    with store[0]() as db:
        assert db.get(PlanItem, 1).content == "New"
        assert db.query(PlanItemRevision).filter_by(item_id=1).count() == 2
        root = db.query(Chat).filter_by(parent_id=None).one()
        assert result["chat_id"] == root.id
        assert root.autonomy_level == "plan"
        assert json.loads(root.params)["development"]["model_route"]["model_name"] == "chosen"
        work = db.query(DevelopmentWorkItem).one()
        assert work.identity_key == "source:plan_workspace:1"
        assert work.workflow_id is None and work.local_ticket_id is None
        child = db.query(Chat).filter_by(parent_id=root.id).one()
        assert child.input == "Improve requirements" and child.response == "Updated"
        assert json.loads(child.params)["artifacts"] == [1]
        assert json.loads(child.params)["expected_revisions"] == {"1": 1}


@pytest.mark.parametrize("response", ["not json", '{"reply":"a","reply":"b","edits":[]}',
    {"reply": "bad", "edits": [{"id": 2, "item_type": "prd", "title": "x", "content": "overwrite", "content_format": "markdown", "expected_revision": 1}]},
    {"reply": "bad", "edits": [{"id": 1, "item_type": "prd", "title": "x", "content": "overwrite", "content_format": "markdown", "expected_revision": 99}]},
    {"reply": "bad", "edits": [], "shell": "echo bad"}])
def test_invalid_response_is_persisted_without_edits(store, monkeypatch, response):
    respond(monkeypatch, response)
    result = send()
    assert result["status"] == "error"
    assert "Planning failed" in result["messages"][-1]["content"]
    with store[0]() as db:
        assert db.get(PlanItem, 1).content == "Old"
        assert db.get(PlanItem, 2).content == "Private"


def test_provider_error_and_recovery_reuse_identity(store, monkeypatch):
    def fail(*args):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(c.llm_factory, "create_stream", fail)
    assert send()["status"] == "error"
    calls = respond(monkeypatch, {"reply": "Recovered", "edits": []})
    assert send()["status"] == "complete"
    assert "provider or planning service" in json.dumps(calls[0][2])
    with store[0]() as db:
        assert db.query(DevelopmentWorkItem).count() == 1
        assert db.query(Chat).count() == 3


def test_concurrent_claim_rejected(store, monkeypatch):
    def stream(*args):
        with pytest.raises(ValueError, match="already running"):
            send()
        yield '{"reply":"done","edits":[]}'
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    assert send()["status"] == "complete"
    with store[0]() as db:
        assert db.query(Chat).count() == 2


def test_cross_scope_selection_never_calls_model(store, monkeypatch):
    calls = respond(monkeypatch, {"reply": "unused", "edits": []})
    assert send(item_id=2)["status"] == "error"
    assert not calls


def test_build_conflict_retains_actionable_explanation():
    from distr.core.planning.build import PlanBuildConflict
    message = "Project file synchronization is pending. Review file changes before building tasks."
    assert c._safe_error(PlanBuildConflict(message)) == "Build needs attention: " + message


def test_image_reaches_selected_local_provider(store, monkeypatch):
    from distr.core.turn_runtime.contracts import TurnResult
    store[1].context_assets = lambda wid, ids: [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
    calls = []
    async def execute(request, *, runtime_id):
        calls.append(request)
        assert runtime_id == "cli_harness"
        return TurnResult(True, runtime_id, "pi", request.route.model, output='{"reply":"Seen","edits":[]}')
    monkeypatch.setattr(c.runtime, "execute_turn", execute)
    result = c.send_message(1, message="See image", provider="ollama", model_name="chosen", attachments=[1])
    assert result["status"] == "complete"
    assert (calls[0].route.provider, calls[0].route.model) == ("ollama", "chosen")
    assert calls[0].route.adapter_options["proposal_images"][0]["type"] == "image_url"
    assert result["messages"][-1]["route"] == {"runtime_id": "cli_harness", "backend_id": "pi", "mode": "proposal_only"}


def test_build_contract_and_persisted_trace(store, monkeypatch):
    from distr.core.planning import build
    task = {"key": "one", "title": "Implement", "description": "Do it", "parent_key": None,
            "depends_on": [], "source_item_ids": [1], "acceptance_criteria": ["Works"],
            "skills": [], "complexity": "low", "role": "implementation"}
    respond(monkeypatch, {"reply": "Build ready", "edits": [], "tasks": [task]})
    def build_tasks(wid, *, tasks, expected_revisions):
        assert wid == 1 and tasks == [task] and expected_revisions == {"1": 1}
        return {"created": 1, "reused": 0}
    monkeypatch.setattr(build, "build_tasks", build_tasks)
    assert c.generate_build(1, provider="openai", model_name="chosen") == {"created": 1, "reused": 0}
    with store[0]() as db:
        child = db.query(Chat).filter(Chat.parent_id.is_not(None)).one()
        assert json.loads(child.params)["tasks"] == [task]
        assert json.loads(child.params)["build_result"]["created"] == 1
    restored = c.get_conversation(1)
    assert restored["messages"][-1]["build_result"] == {"created": 1, "reused": 0}
    assert c.get_conversation(2)["messages"] == []


def test_full_snapshot_change_rejects_all_edits(store, monkeypatch):
    def stream(*args):
        with store[0]() as db:
            db.add(PlanItem(id=3, workspace_id=1, item_type="prd", title="Concurrent", content="Human edit"))
        yield json.dumps({"reply": "Edited", "edits": [{"id": 1, "item_type": "prd", "title": "Requirements",
                         "content": "New", "content_format": "markdown", "expected_revision": 1}]})
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    assert send()["status"] == "error"
    with store[0]() as db:
        assert db.get(PlanItem, 1).content == "Old"
        assert db.get(PlanItem, 3).content == "Human edit"


def test_output_bound_persists_failure(store, monkeypatch):
    monkeypatch.setattr(c, "MAX_OUTPUT", 10)
    respond(monkeypatch, "x" * 11)
    assert send()["status"] == "error"


def test_build_failure_is_persisted(store, monkeypatch):
    respond(monkeypatch, {"reply": "Build", "edits": [], "tasks": [{"source_item_ids": [2]}]})
    result = c.generate_build(1, provider="openai", model_name="chosen")
    assert result["status"] == "error"
    assert c.get_conversation(1)["status"] == "error"


@pytest.mark.parametrize("error", [RuntimeError("sk-secret-token"), ValueError("Bearer sk-secret-token")])
def test_provider_secrets_never_persist(store, monkeypatch, error):
    def stream(*args):
        raise error
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    result = send()
    assert result["status"] == "error"
    assert "sk-secret" not in json.dumps(result)
    with store[0]() as db:
        for row in db.query(Chat):
            assert "sk-secret" not in (row.response or "") + (row.params or "")


def test_unsupported_provider_never_dispatches(store, monkeypatch):
    calls = respond(monkeypatch, {"reply": "unused", "edits": []})
    result = c.send_message(1, message="Plan", provider="not-a-provider", model_name="chosen")
    assert result["status"] == "error" and not calls


def test_project_context_and_prior_explicit_attachments(store, monkeypatch):
    observed = []
    store[1].context_assets = lambda wid, ids: observed.append(list(ids)) or [{"type": "text", "text": "Attached source"}]
    calls = respond(monkeypatch, {"reply": "Noted", "edits": []})
    send(attachments=[7])
    send(attachments=[8])
    assert observed == [[7], [8, 7]]
    assert "Project notes" in json.dumps(calls[-1][2])
    assert "Source context" in json.dumps(calls[-1][2])


def test_retained_attachments_are_bounded(store, monkeypatch):
    observed = []
    store[1].context_assets = lambda wid, ids: observed.append(list(ids)) or []
    respond(monkeypatch, {"reply": "Noted", "edits": []})
    send(attachments=list(range(1, 9)))
    send(attachments=[9])
    assert observed[-1] == [9, *range(1, 8)]


def test_build_cache_and_changed_plan_context(store, monkeypatch):
    from distr.core.planning import build
    task = {"key": "stable", "title": "Implement", "description": "Work", "parent_key": None,
            "depends_on": [], "source_item_ids": [1], "acceptance_criteria": ["Works"],
            "skills": [], "complexity": "low", "role": "implementation"}
    calls = respond(monkeypatch, {"reply": "Ready", "edits": [], "tasks": [task]})
    builds = []
    def materialize(*args, **kwargs):
        builds.append(kwargs)
        return {"created": int(len(builds) == 1), "reused": int(len(builds) > 1)}
    monkeypatch.setattr(build, "build_tasks", materialize)
    first = c.generate_build(1, provider="openai", model_name="chosen")
    assert first == {"created": 1, "reused": 0}
    assert c.generate_build(1, provider="openai", model_name="chosen") == {"created": 0, "reused": 1}
    assert len(calls) == 1 and len(builds) == 2
    with store[0]() as db:
        db.add(PlanItemRevision(item_id=1, revision=2, title="Requirements", content="Changed", status="draft"))
    c.generate_build(1, provider="openai", model_name="chosen")
    assert len(calls) == 2 and len(builds) == 3
    assert '"key": "stable"' in calls[-1][2][-1]["content"]
    assert builds[-1]["expected_revisions"] == {"1": 2}


def test_cached_build_rechecks_ticket_conflicts(store, monkeypatch):
    from distr.core.planning import build
    task = {"key": "stable", "title": "Implement", "description": "Work", "parent_key": None,
            "depends_on": [], "source_item_ids": [1], "acceptance_criteria": ["Works"],
            "skills": [], "complexity": "low", "role": "implementation"}
    calls = respond(monkeypatch, {"reply": "Ready", "edits": [], "tasks": [task]})
    monkeypatch.setattr(build, "build_tasks", lambda *a, **kw: {"created": 1, "reused": 0})
    c.generate_build(1, provider="openai", model_name="chosen")
    def conflict(*args, **kwargs):
        raise ValueError("Ticket was edited after the first build")
    monkeypatch.setattr(build, "build_tasks", conflict)
    assert c.generate_build(1, provider="openai", model_name="chosen")["status"] == "error"
    assert len(calls) == 1
    assert c.get_conversation(1)["status"] == "error"


def test_build_cache_invalidated_by_source_context(store, monkeypatch):
    from distr.core.planning import build
    task = {"key": "stable", "title": "Implement", "description": "Work", "parent_key": None,
            "depends_on": [], "source_item_ids": [1], "acceptance_criteria": ["Works"],
            "skills": [], "complexity": "low", "role": "implementation"}
    calls = respond(monkeypatch, {"reply": "Ready", "edits": [], "tasks": [task]})
    monkeypatch.setattr(build, "build_tasks", lambda *a, **kw: {"created": 0, "reused": 1})
    c.generate_build(1, provider="openai", model_name="chosen")
    store[1].source_context = lambda wid: {"notes": "New deployment constraint"}
    c.generate_build(1, provider="openai", model_name="chosen")
    assert len(calls) == 2
    assert "New deployment constraint" in calls[-1][2][-1]["content"]


@pytest.mark.parametrize("change", ["asset", "discussion"])
def test_build_cache_invalidated_by_asset_or_discussion(store, monkeypatch, change):
    from distr.core.planning import build
    task = {"key": "stable", "title": "Implement", "description": "Work", "parent_key": None,
            "depends_on": [], "source_item_ids": [1], "acceptance_criteria": ["Works"],
            "skills": [], "complexity": "low", "role": "implementation"}
    build_calls = []
    def stream(provider, model, messages, settings):
        is_build = "Generate tasks, with edits empty" in messages[0]["content"]
        if is_build:
            build_calls.append(messages)
        yield json.dumps({"reply": "Noted", "edits": [], "tasks": [task] if is_build else []})
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    monkeypatch.setattr(build, "build_tasks", lambda *a, **kw: {"created": 0, "reused": 1})
    send(attachments=[7])
    c.generate_build(1, provider="openai", model_name="chosen")
    if change == "asset":
        store[1].context_assets = lambda wid, ids: [{"type": "text", "text": "Revised attached requirement"}]
    else:
        send()
    c.generate_build(1, provider="openai", model_name="chosen")
    assert len(build_calls) == 2


def test_build_questions_persist_without_creating_tasks(store, monkeypatch):
    from distr.core.planning import build
    calls = respond(monkeypatch, {"reply": "One missing decision.", "edits": [], "tasks": [],
                                "questions": ["Should customers sign in to save addresses?"]})
    def forbidden(*args, **kwargs):
        pytest.fail("Questions must not materialize tickets")
    monkeypatch.setattr(build, "build_tasks", forbidden)
    result = c.generate_build(1, provider="openai", model_name="chosen")
    assert result["status"] == "needs_input" and result["tasks"] == []
    conversation = c.get_conversation(1)
    assert conversation["status"] == "needs_input"
    assert "sign in" in conversation["messages"][-1]["content"]
    assert "Do not invent missing choices" in calls[0][2][0]["content"]
    respond(monkeypatch, {"reply": "Recorded sign-in requirement.", "edits": [
        {"id": 1, "item_type": "prd", "title": "Requirements", "content": "Sign-in required to save addresses.",
         "content_format": "markdown", "expected_revision": 1}]})
    assert send()["status"] == "complete"
    with store[0]() as db:
        assert "Sign-in required" in db.get(PlanItem, 1).content


@pytest.mark.parametrize("questions", [[""], ["A?"] * 4, "A?", [42]])
def test_invalid_planning_questions_rejected(store, monkeypatch, questions):
    respond(monkeypatch, {"reply": "Question", "edits": [], "tasks": [], "questions": questions})
    assert c.generate_build(1, provider="openai", model_name="chosen")["status"] == "error"


def test_questions_cannot_accompany_mutations(store, monkeypatch):
    respond(monkeypatch, {"reply": "Question", "questions": ["Use this change?"], "edits": [
        {"id": 1, "item_type": "prd", "title": "Requirements", "content": "Unconfirmed choice",
         "content_format": "markdown", "expected_revision": 1}]})
    assert send()["status"] == "error"
    with store[0]() as db:
        assert db.get(PlanItem, 1).content == "Old"


def test_source_material_survives_a_clarifying_question(store, monkeypatch):
    observed = []
    store[1].context_assets = lambda wid, ids: observed.append(list(ids)) or []
    respond(monkeypatch, {"reply": "", "edits": [], "questions": ["Keep the brochure's colours?"]})
    assert send(attachments=[7])["status"] == "needs_input"
    respond(monkeypatch, {"reply": "Noted", "edits": []})
    assert send()["status"] == "complete"
    assert observed == [[7], [7]]


@pytest.mark.parametrize('provider,env_name', [('ollama','PLAN_LOCAL_MODEL_TEST'), ('codex','PLAN_CODEX_MODEL_TEST')])
def test_real_local_model_creates_linked_plan_in_isolated_database(store, monkeypatch, provider, env_name):
    """Uses synthetic source material only; never touches a user's project."""
    from distr.core import orchestration_events
    if not os.environ.get(env_name):
        pytest.skip('Explicit opt-in required for a real model call')
    monkeypatch.setattr(orchestration_events, "emit_orchestration_event", lambda **kwargs: None)
    observed = []
    execute = c.runtime.execute_turn
    async def record(request, **kwargs):
        result = await execute(request, **kwargs)
        observed.append(result.evidence.get("proposal_error"))
        return result
    monkeypatch.setattr(c.runtime, "execute_turn", record)
    result = c.send_message(1, provider=provider, model_name=os.environ[env_name],
        message=("Create a small customer entry screen with Name and Email inputs and a Save button. "
                 "Use a wireframe artifact and a Mermaid ERD with one Customer entity. Update the existing "
                 "requirements document to specify required name and email, email validation and a saved confirmation. "
                 "This is a single-user local Django/PostgreSQL application, server-rendered HTML, no integrations, "
                 "no login and no deployment work. These choices are decided. Create the three linked artifacts now, "
                 "not tasks. Keep each document under 20 lines."))
    assert result["status"] == "complete", (result["messages"][-1]["content"], observed)
    workspace = service.get_workspace(1)
    kinds = {item["item_type"] for item in workspace["items"]}
    assert {"wireframe", "erd", "prd"} <= kinds
    wire = next(item for item in workspace["items"] if item["item_type"] == "wireframe")
    assert wire["content_format"] == "wire"
    assert "email" in wire["content"].lower()
    assert next(item for item in workspace["items"] if item["item_type"] == "prd")["revision_count"] == 2


@pytest.mark.parametrize('provider,env_name', [('ollama','PLAN_LOCAL_MODEL_TEST'), ('codex','PLAN_CODEX_MODEL_TEST')])
def test_real_local_model_builds_dependency_tasks_and_reuses_them(store, monkeypatch, provider, env_name):
    """Real Pi proposal and real materialization, with only synthetic sources."""
    from distr.core import orchestration_events
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
    from distr.core.planning import build

    session, assets = store
    if not os.environ.get(env_name):
        pytest.skip('Explicit opt-in required for a real model call')
    model = os.environ[env_name]
    backend = 'codex' if provider == 'codex' else 'pi'
    monkeypatch.setattr(orchestration_events, "emit_orchestration_event", lambda **kwargs: None)
    monkeypatch.setattr(build, "get_session", session)
    monkeypatch.setattr(build, "_global_complexity_route", lambda level: {
        "backend": backend, "model": model, "model_provider": 'openai' if provider == 'codex' else 'ollama'})
    assets.source_context = lambda wid: {
        "description": "Synthetic customer entry application", "notes": "",
        "context_items": [],
    }
    requirements = (
        "Single-user local Django 5 application, server-rendered HTML and PostgreSQL. "
        "No authentication, third-party integrations, deployment or email sending. "
        "FR-1: A customer has required name and unique valid email. "
        "FR-2: POST /customers validates server-side, persists valid input and redirects "
        "to /customers/saved. Invalid input shows inline errors without creating a record. "
        "FR-3: Save confirmation links back to the form. "
        "Use Django's TestCase and test client, plus a browser check of the two screens. "
        "No extra stack choices are needed. Generate a compact task hierarchy: "
        "one parent for the feature and subtasks for persistence, form behavior, and tests. "
        "Record execution dependencies between subtasks where required. "
        "Keep descriptions under 60 words; at most six tasks."
    )
    with session() as db:
        db.add(KanbanBoard(id=1, name="Synthetic board", default_project_id=1))
        db.add(KanbanLane(id=1, board_id=1, name="Backlog"))
        db.get(PlanItem, 1).content = requirements
        db.query(PlanItemRevision).filter_by(item_id=1).one().content = requirements
        for ident, kind, fmt, content in (
            (3, "wireframe", "wire", 'screen Customers route=/customers\n  heading "New customer"\n  input Name required=true\n  input Email type=email required=true\n  button Save primary\nscreen Saved route=/customers/saved\n  heading "Customer saved"\n  link "Back to customers" route=/customers'),
            (4, "erd", "mermaid", "erDiagram\n  Customer {\n    int id PK\n    string name\n    string email UK\n  }"),
        ):
            db.add(PlanItem(id=ident, workspace_id=1, item_type=kind, title=kind,
                            content=content, content_format=fmt))
            db.add(PlanItemRevision(item_id=ident, revision=1, title=kind, content=content, status="draft"))

    first = c.generate_build(1, provider=provider, model_name=model)
    assert first.get("created", 0) >= 3, first
    tasks = first["tasks"]
    assert any(task["parent_id"] is not None for task in tasks), tasks
    assert any(task["depends_on"] for task in tasks), tasks
    ids = {task["id"] for task in tasks}
    assert all(set(task["depends_on"]) <= ids for task in tasks)
    assert all(task["route"]["model"] == model for task in tasks)
    assert all(task["route"]["backend"] == backend for task in tasks)
    with session() as db:
        assert db.query(KanbanTicket).count() == len(tasks)
        assert db.query(ProjectExecutionSession).count() == 0
        for ticket in db.query(KanbanTicket):
            contract = json.loads(ticket.context_notes)["plan_build"]
            assert contract["task"]["acceptance_criteria"]
            assert set(contract["task"]["source_item_ids"]) <= {1, 3, 4}
            assert not ticket.send_to_cli

    def no_second_model_call(*args, **kwargs):
        raise AssertionError("Unchanged Build should reuse the stored proposal")
    monkeypatch.setattr(c.runtime, "create_stream", no_second_model_call)
    repeated = c.generate_build(1, provider=provider, model_name=model)
    assert repeated["created"] == repeated["updated"] == 0
    assert repeated["reused"] == len(tasks)
    assert {task["id"] for task in repeated["tasks"]} == ids
