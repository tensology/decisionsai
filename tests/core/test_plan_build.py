from __future__ import annotations

import copy
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem, PlanFileWrite, PlanWorkspace, PlanItem, PlanItemRevision
from distr.core.planning import build


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'build.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session():
        with factory() as db:
            yield db

    monkeypatch.setattr(build, "get_session", session)
    monkeypatch.setattr(build, "_global_complexity_route", lambda level: {
        "backend": "pi", "model": f"local-{level}", "model_provider": "ollama"})
    with session() as db:
        for number in (1, 2):
            db.add(Project(id=number, name=f"Project {number}"))
            db.add(KanbanBoard(id=number, name=f"Board {number}", default_project_id=number))
            db.add(KanbanLane(id=number, board_id=number, name="Backlog"))
            db.add(PlanWorkspace(id=number, board_key=f"decisions:{number}", project_id=number))
            db.add(PlanItem(id=number, workspace_id=number, title="Wireframe"))
            db.add(PlanItemRevision(item_id=number, revision=1, title="Wireframe"))
        db.commit()
    yield session
    engine.dispose()


def task(key="parent", **changes):
    return {"key": key, "title": key.title(), "description": "Implement the wireframe",
            "parent_key": None, "depends_on": [], "source_item_ids": [1],
            "acceptance_criteria": ["Matches the approved wireframe"], "skills": ["ui-validation"],
            "complexity": "medium", "role": "implementation", **changes}


def run(tasks, **kwargs):
    return build.build_tasks(1, tasks=tasks, expected_revisions=kwargs.get("revisions", {"1": 1}))


def test_build_rejects_unresolved_wireframe_references_even_when_other_project_defines_them(store):
    with store() as db:
        wire = db.get(PlanItem, 1)
        wire.content_format = "wire"
        wire.item_type = "wireframe"
        wire.content = 'screen "Customers"\n  button "Save" requirement=FR-001'
        other = db.get(PlanItem, 2)
        other.item_type = "frac"
        other.content_format = "markdown"
        other.content = "## FR-001\nSave the customer"
        db.commit()
    with pytest.raises(ValueError, match="unresolved requirement=FR-001"):
        run([task()])
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


@pytest.mark.parametrize("chat_project", [None, 1, 2])
def test_ticket_source_uses_canonical_project_conversation_not_workspace_id(store, chat_project):
    if chat_project is not None:
        with store() as db:
            db.add(Chat(id=42, title="Planning conversation", project_id=chat_project))
            db.add(DevelopmentWorkItem(chat_id=42, identity_key="source:plan_workspace:1"))
            db.commit()
    result = run([task()])
    with store() as db:
        ticket = db.get(KanbanTicket, result["tasks"][0]["id"])
        assert ticket.source_thread_id == ("42" if chat_project == 1 else None)
        assert ticket.source_url == "/development/boards/decisions/1/plan/"
        ticket.source_thread_id = "1"  # Legacy workspace-as-chat projection.
        db.commit()
    assert run([task()])["reused"] == 1
    with store() as db:
        assert db.get(KanbanTicket, result["tasks"][0]["id"]).source_thread_id == ("42" if chat_project == 1 else None)


@pytest.mark.parametrize("already_built", [False, True])
def test_pending_file_projection_blocks_new_and_repeated_builds(store, already_built):
    if already_built:
        run([task()])
    with store() as db:
        db.add(PlanFileWrite(item_id=1, target_path="/unused/plan.md", desired_hash="pending"))
        db.commit()
    with pytest.raises(build.PlanBuildConflict, match="synchronization is pending"):
        run([task()])
    with store() as db:
        assert db.query(KanbanTicket).count() == int(already_built)
        # Build cannot overwrite a project file to resolve its own blocker.
        assert db.get(PlanFileWrite, 1) is not None
        db.delete(db.get(PlanFileWrite, 1))
        db.commit()
    result = run([task()])
    assert result["reused"] == int(already_built)
    assert result["created"] == int(not already_built)


def test_other_workspace_pending_files_do_not_block_build(store):
    with store() as db:
        db.add(PlanFileWrite(item_id=2, target_path="/unused/other.md", desired_hash="pending"))
        db.commit()
    assert run([task()])["created"] == 1


def test_graph_materializes_parents_dependencies_metadata_and_reuses_across_lanes(store, monkeypatch):
    tasks = [task("child", parent_key="parent", depends_on=["parent"], role="testing"), task()]
    first = run(tasks)
    child, parent = first["tasks"]
    assert (first["created"], first["reused"]) == (2, 0)
    assert child["parent_id"] == parent["id"]
    assert child["depends_on"] == [parent["id"]]
    assert child["route"]["step_role"] == "testing"
    assert child["route"]["model"] == "local-medium"
    with store() as db:
        db.add(KanbanLane(id=3, board_id=1, name="Done"))
        ticket = db.get(KanbanTicket, child["id"])
        ticket.lane_id = 3
        meta = json.loads(ticket.context_notes)["plan_build"]
        assert meta["task"]["acceptance_criteria"] == tasks[0]["acceptance_criteria"]
        assert meta["task"]["skills"] == ["ui-validation"]
        assert meta["expected_revisions"] == {"1": 1}
        assert ticket.linked_project_id == 1
        assert ticket.source_external_id == "plan:1:task:child"
        assert not ticket.send_to_cli and ticket.workflow_status is None
        assert ticket.linked_workflow_id is None
        assert db.query(ProjectExecutionSession).count() == 0
        db.commit()
    monkeypatch.setattr(build, "_global_complexity_route", lambda _: pytest.fail("Reuse must preserve routing"))
    second = run(list(reversed(tasks)))
    assert (second["created"], second["reused"]) == (0, 2)
    assert {r["key"]: r for r in first["tasks"]} == {r["key"]: r for r in second["tasks"]}


@pytest.mark.parametrize("tasks", [
    [task(), task()], [task(depends_on=["unknown"])], [task(parent_key="unknown")],
    [task(depends_on=["parent"])], [task(parent_key="parent")],
    [task(depends_on=["b"]), task("b", depends_on=["parent"])],
    [task(parent_key="b"), task("b", parent_key="parent")],
    [task(parent_key="b"), task("b", depends_on=["parent"])],
    [task(source_item_ids=[2])], [task(source_item_ids=[])],
    [task(source_item_ids=[True])], [task(role="deployment")],
    [task(complexity="auto")], [task(skills="ui-validation")],
    [task(depends_on=["parent", "parent"])], [task(provider="")],
    [task(acceptance_criteria=[1])], [task(title=" ")],
])
def test_invalid_graph_is_atomic(store, tasks):
    with pytest.raises(ValueError):
        run(tasks)
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


@pytest.mark.parametrize("revisions", [{}, {"1": 0}, {"1": 2}, {"1": True}, {1: 1}, {"01": 1}, {"1": 1, "2": 1}])
def test_revision_validation(store, revisions):
    with pytest.raises(ValueError):
        run([task()], revisions=revisions)
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


def test_added_requirement_invalidates_full_build_snapshot(store):
    with store() as db:
        db.add(PlanItem(id=3, workspace_id=1, title="New requirement"))
        db.commit()
    with pytest.raises(ValueError, match="source membership"):
        run([task()])
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


def test_omitted_existing_task_requires_explicit_replanning(store):
    run([task(), task("second")])
    with pytest.raises(ValueError, match="omitted"):
        run([task()])
    with store() as db:
        assert db.query(KanbanTicket).count() == 2


def test_cross_workspace_sources_with_expected_revision_are_rejected(store):
    with pytest.raises(ValueError, match="outside workspace"):
        run([task(source_item_ids=[2])], revisions={"2": 1})


@pytest.mark.parametrize("change", ["project", "source", "key", "lane", "missing_project"])
def test_board_consistency(store, change):
    with store() as db:
        if change == "project":
            db.get(KanbanBoard, 1).default_project_id = 2
        elif change == "source":
            db.get(KanbanBoard, 1).source = "trello"
        elif change == "key":
            db.get(PlanWorkspace, 1).board_key = "trello:1"
        elif change == "missing_project":
            db.get(PlanWorkspace, 1).project_id = None
        else:
            db.delete(db.get(KanbanLane, 1))
        db.commit()
    with pytest.raises(ValueError):
        run([task()])
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


@pytest.mark.parametrize("field,value", [("title", "Authored title"), ("description", "Authored text"),
    ("context_notes", "Authored notes"), ("linked_project_id", 2), ("lane_id", 2),
    ("parent_ticket_id", 999), ("complexity", "high")])
def test_authored_conflict_preserves_content_and_prevents_partial_build(store, field, value):
    ticket_id = run([task()])["tasks"][0]["id"]
    with store() as db:
        setattr(db.get(KanbanTicket, ticket_id), field, value)
        db.commit()
    with pytest.raises(build.PlanBuildConflict):
        run([task("new"), task()])
    with store() as db:
        assert db.query(KanbanTicket).count() == 1
        assert getattr(db.get(KanbanTicket, ticket_id), field) == value


def test_proposal_change_updates_and_workspace_keys_are_isolated(store):
    first = run([task()])
    revised = run([task(acceptance_criteria=["Different acceptance"])])
    assert revised["updated"] == 1 and revised["created"] == 0
    assert revised["tasks"][0]["id"] == first["tasks"][0]["id"]
    second = build.build_tasks(2, tasks=[task(source_item_ids=[2])], expected_revisions={"2": 1})
    assert second["created"] == 1
    assert first["tasks"][0]["id"] != second["tasks"][0]["id"]


def test_explicit_and_board_routes(store):
    with store() as db:
        db.get(KanbanBoard, 1).orchestrator_policy = json.dumps({"complexity_routing": {
            "high": {"backend": "codex", "model": "chosen-model"}}})
        db.commit()
    result = run([task(complexity="high", role="review"),
                  task("explicit", provider="ollama", model="chosen-local", role="polish")])
    route, explicit = [row["route"] for row in result["tasks"]]
    assert route["backend"] == "codex" and route["model"] == "chosen-model"
    assert "model_provider" not in route
    assert route["task_profile"]["intent"] == "review"
    assert explicit["backend"] == "pi" and explicit["model_provider"] == "ollama"
    assert explicit["model"] == "chosen-local" and explicit["source"] == "plan_explicit"


def test_failure_during_insert_rolls_back_whole_graph(store):
    def fail_second(mapper, connection, target):
        if target.title == "Child":
            raise RuntimeError("insert failure")

    event.listen(KanbanTicket, "before_insert", fail_second)
    try:
        with pytest.raises(RuntimeError, match="insert failure"):
            run([task(), task("child", parent_key="parent")])
    finally:
        event.remove(KanbanTicket, "before_insert", fail_second)
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


def test_inputs_not_mutated_and_empty_graph(store):
    assert run([]) == {"created": 0, "updated": 0, "reused": 0, "tasks": []}
    tasks = [task()]
    original = copy.deepcopy(tasks)
    run(tasks)
    assert tasks == original
    with pytest.raises(build.PlanBuildConflict, match="omitted"):
        run([])


def revise_source(store):
    with store() as db:
        db.get(PlanItem, 1).content = "Revised blueprint"
        db.add(PlanItemRevision(item_id=1, revision=2, title="Revised wireframe"))
        db.commit()


def test_revised_blueprint_updates_same_ticket_and_repeat_reuses(store, monkeypatch):
    original = run([task()])["tasks"][0]
    revise_source(store)
    monkeypatch.setattr(build, "_global_complexity_route", lambda _: pytest.fail("Preserve selected route"))
    proposal = [task(title="Revised task", description="New behavior", complexity="high", role="review")]
    result = run(proposal, revisions={"1": 2})
    assert (result["created"], result["updated"], result["reused"]) == (0, 1, 0)
    assert result["tasks"][0]["id"] == original["id"]
    assert result["tasks"][0]["route"]["model"] == original["route"]["model"]
    assert result["tasks"][0]["route"]["step_role"] == "review"
    repeat = run(proposal, revisions={"1": 2})
    assert (repeat["created"], repeat["updated"], repeat["reused"]) == (0, 0, 1)
    assert result["tasks"] == repeat["tasks"]
    with store() as db:
        ticket = db.get(KanbanTicket, original["id"])
        assert ticket.description == "New behavior"
        assert json.loads(ticket.context_notes)["plan_build"]["expected_revisions"] == {"1": 2}
        assert db.query(KanbanTicket).count() == 1


@pytest.mark.parametrize("edit", ["title", "description", "context", "structured_context", "extra_context"])
def test_revised_source_does_not_overwrite_authored_content(store, edit):
    ticket_id = run([task()])["tasks"][0]["id"]
    revise_source(store)
    with store() as db:
        ticket = db.get(KanbanTicket, ticket_id)
        if edit in ("title", "description"):
            setattr(ticket, edit, "Human edit")
        elif edit == "context":
            ticket.context_notes += "\nHuman notes"
        else:
            notes = json.loads(ticket.context_notes)
            if edit == "structured_context":
                notes["plan_build"]["task"]["acceptance_criteria"] = ["Human acceptance"]
            else:
                notes["human_notes"] = "Keep this"
            ticket.context_notes = json.dumps(notes, sort_keys=True)
        before = (ticket.title, ticket.description, ticket.context_notes)
        db.commit()
    with pytest.raises(build.PlanBuildConflict, match="authored"):
        run([task(title="Revised task"), task("new")], revisions={"1": 2})
    with store() as db:
        ticket = db.get(KanbanTicket, ticket_id)
        assert (ticket.title, ticket.description, ticket.context_notes) == before
        assert db.query(KanbanTicket).count() == 1


@pytest.mark.parametrize("protection", ["running", "completed", "waiting", "done_lane", "active_lane", "execution", "legacy"])
def test_started_work_requires_replanning_but_identical_build_reuses(store, protection):
    ticket_id = run([task()])["tasks"][0]["id"]
    with store() as db:
        ticket = db.get(KanbanTicket, ticket_id)
        if protection.endswith("lane"):
            db.get(KanbanLane, 1).name = "Done" if protection == "done_lane" else "In Progress"
        elif protection == "execution":
            db.add(ProjectExecutionSession(ticket_id=ticket_id, project_id=1, status="completed"))
        elif protection == "legacy":
            ticket.context_notes = json.dumps({"plan_build": json.loads(ticket.context_notes)["plan_build"]}, sort_keys=True)
        else:
            ticket.workflow_status = protection
        before = ticket.context_notes
        db.commit()
    assert run([task()])["reused"] == 1
    revise_source(store)
    with pytest.raises(build.PlanBuildConflict, match="needs_replanning"):
        run([task(title="Revised")], revisions={"1": 2})
    with store() as db:
        assert db.get(KanbanTicket, ticket_id).context_notes == before


def test_update_rewires_parent_and_dependencies_without_duplicate(store):
    first = run([task(), task("child", parent_key="parent", depends_on=["parent"])])
    child_id = first["tasks"][1]["id"]
    revised = run([task("child", parent_key="new", depends_on=["new"]), task("new"), task()])
    child, parent, retained = revised["tasks"]
    assert (revised["created"], revised["updated"]) == (1, 1)
    assert child["id"] == child_id
    assert child["parent_id"] == parent["id"] and child["depends_on"] == [parent["id"]]
    assert run([task("child", parent_key="new", depends_on=["new"]), task("new"), task()])["reused"] == 3
    with store() as db:
        assert db.query(KanbanTicket).count() == 3  # Old parent is explicitly retained.


def test_conflict_rolls_back_other_pending_updates(store):
    result = run([task(), task("blocked")])
    with store() as db:
        db.get(KanbanTicket, result["tasks"][1]["id"]).workflow_status = "completed"
        db.commit()
    with pytest.raises(build.PlanBuildConflict):
        run([task(title="New title"), task("blocked", title="Also changed")])
    with store() as db:
        assert db.get(KanbanTicket, result["tasks"][0]["id"]).title == "Parent"


@pytest.mark.parametrize("change", [
    {"acceptance_criteria": []}, {"key": "k" * 129}, {"title": "t" * 301},
    {"description": "d" * 20001}, {"parent_key": "p" * 129},
])
def test_untrusted_task_bounds(store, change):
    with pytest.raises(ValueError):
        run([task(**change)])
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


def test_task_count_limit(store):
    with pytest.raises(ValueError, match="200"):
        run([task(str(i)) for i in range(201)])
    result = run([task(str(i)) for i in range(200)])
    assert result["created"] == 200


def test_revision_only_update_and_none_string_key(store):
    initial = run([task("None")])
    assert run([task("None")])["reused"] == 1
    revise_source(store)
    result = run([task("None")], revisions={"1": 2})
    assert result["updated"] == 1
    assert result["tasks"] == initial["tasks"]
    assert run([task("None")], revisions={"1": 2})["reused"] == 1


def test_insert_failure_rolls_back_preceding_update(store):
    ticket_id = run([task()])["tasks"][0]["id"]

    def fail(mapper, connection, target):
        raise RuntimeError("insert failure after update")

    event.listen(KanbanTicket, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError):
            run([task(title="Updated"), task("child", parent_key="parent")])
    finally:
        event.remove(KanbanTicket, "before_insert", fail)
    with store() as db:
        assert db.get(KanbanTicket, ticket_id).title == "Parent"
        assert db.query(KanbanTicket).count() == 1


def test_configured_policy_without_backend_probes_or_dispatch(store, monkeypatch):
    from distr.core import settings
    from distr.core.kanban import ticket_policy
    from distr.core import project_cli_backends

    monkeypatch.setattr(settings, "load_settings_from_db", lambda: {
        "project_cli_medium_backend": "pi", "project_cli_medium_model": "configured-local",
        "project_cli_medium_model_provider": "ollama"})
    monkeypatch.setattr(build, "_global_complexity_route", ticket_policy._global_complexity_route)
    monkeypatch.setattr(project_cli_backends, "get_backend", lambda *a, **k: pytest.fail("Backend probe"))
    monkeypatch.setattr(project_cli_backends, "run_project_task", lambda *a, **k: pytest.fail("Dispatch"))
    route = run([task()])["tasks"][0]["route"]
    assert route["model"] == "configured-local"
    assert route["model_provider"] == "ollama"


def test_concurrent_builds_reuse_one_graph(store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run([task(), task("child", parent_key="parent")]), range(2)))
    assert sorted(result["created"] for result in results) == [0, 2]
    assert results[0]["tasks"] == results[1]["tasks"]
    with store() as db:
        assert db.query(KanbanTicket).count() == 2
