import contextlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from distr.core.db.kanban import KanbanTicket
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.workflow import dispatcher


def _session_for_workflow(run_settings):
    workflow = SimpleNamespace(status='active', run_settings=json.dumps(run_settings))
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = workflow

    @contextlib.contextmanager
    def get_session():
        yield db

    return get_session


def test_completed_successor_prevents_replay_after_queue_changes(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from distr.core.db import Base
    from distr.core.db.workflow import AutoWorkflowStep

    engine = create_engine(f"sqlite:///{tmp_path / 'queue.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dispatcher, "get_session", sessions)
    with sessions.begin() as db:
        workflow = AutoWorkflow(name="Queue")
        db.add(workflow)
        db.flush()
        db.add(AutoWorkflowStep(workflow_id=workflow.id, name="Work",
                                action_type="agent_instruction", instruction="Work the ticket."))
        predecessor = AutoWorkflowRun(workflow_id=workflow.id, status="completed", ticket_id=41)
        db.add(predecessor)
        db.flush()
    monkeypatch.setattr("distr.core.planning.prerequisites.execution_ticket_id", lambda ticket_id, **_: ticket_id)
    monkeypatch.setattr("distr.core.planning.prerequisites.require_plan_prerequisites", lambda *_, **__: None)
    monkeypatch.setattr(dispatcher, "append_ticket_audit_entry", lambda *_, **__: None)
    timer = MagicMock()
    monkeypatch.setattr(dispatcher.threading, "Timer", timer)
    monkeypatch.setattr(dispatcher, "_initializing_runs", set())
    metadata = {"source_type": "workflow_queue", "auto_queued_from_run_id": predecessor.id,
                "ticket_group_items": [{"ticket_id": 41}, {"ticket_id": 42}, {"ticket_id": 43}]}
    started = dispatcher.start_workflow_run(workflow.id, ticket_id=42, dispatch_async=True, run_metadata=metadata)
    assert started["status"] == "started"
    timer.assert_called_once()
    with sessions.begin() as db:
        db.get(AutoWorkflowRun, started["run_id"]).status = "completed"
    timer.reset_mock()
    result = dispatcher.start_workflow_run(workflow.id, ticket_id=43, dispatch_async=True,
                                           run_metadata=metadata)
    assert result.get("status") == "already_dispatched"
    timer.assert_not_called()
    with sessions() as db:
        assert db.query(AutoWorkflowRun).count() == 2
    engine.dispose()


@pytest.mark.parametrize("tied_positions", [False, True])
def test_implicit_queue_skips_completed_and_cancelled_tickets(tmp_path, monkeypatch, tied_positions):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from distr.core.db import Base
    from distr.core.db.kanban import KanbanBoard, KanbanLane

    engine = create_engine(f"sqlite:///{tmp_path / 'queue-status.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dispatcher, "get_session", sessions)
    monkeypatch.setattr("distr.core.workflow.ticket_dispatch.get_session", sessions)
    with sessions.begin() as db:
        workflow = AutoWorkflow(name="Queue", run_settings=json.dumps({"execution_mode": "sequential"}))
        board = KanbanBoard(name="Board")
        db.add_all([workflow, board])
        db.flush()
        lane = KanbanLane(name="Backlog", board_id=board.id)
        complete_lane = KanbanLane(name="Complete", board_id=board.id)
        db.add_all([lane, complete_lane])
        db.flush()
        tickets = [
            KanbanTicket(title=f"Ticket {index}", lane_id=target_lane.id, linked_workflow_id=workflow.id,
                         workflow_queue_position=0 if tied_positions else index, workflow_status=status)
            for index, (target_lane, status) in enumerate([
                (lane, "completed"), (lane, "completed"), (lane, "cancelled"),
                (complete_lane, None), (lane, None),
            ])
        ]
        db.add_all(tickets)
        db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, ticket_id=tickets[0].id,
                              board_id=board.id, status="completed")
        db.add(run)
    dispatch = MagicMock(return_value={"run_id": 502})
    monkeypatch.setattr("distr.core.workflow.work_dispatch.dispatch_work_item", dispatch)
    monkeypatch.setattr("distr.gui.web.kanban_events.increment_kanban_updated", lambda **_: None)

    dispatcher._maybe_auto_start_next_queued_ticket(run.id, workflow.id)

    dispatch.assert_called_once()
    assert dispatch.call_args.kwargs["ticket_id"] == tickets[-1].id
    with sessions.begin() as db:
        selected_ticket = db.get(KanbanTicket, tickets[-1].id)
        if tied_positions:
            selected_ticket.lane_id = complete_lane.id
        else:
            selected_ticket.workflow_status = "cancelled"
    result = dispatcher.start_workflow_run(
        workflow.id, ticket_id=tickets[-1].id, board_id=board.id,
        run_metadata={"source_type": "workflow_queue", "auto_queued_from_run_id": run.id},
    )
    assert "already finished or been cancelled" in result["error"]
    with sessions() as db:
        assert db.query(AutoWorkflowRun).count() == 1
    with sessions.begin() as db:
        selected_ticket = db.get(KanbanTicket, tickets[-1].id)
        selected_ticket.lane_id = lane.id
        selected_ticket.workflow_status = None
        from distr.core.db.workflow import AutoWorkflowStep
        db.add(AutoWorkflowStep(workflow_id=workflow.id, name="Work",
                                action_type="agent_instruction", instruction="Work."))
    monkeypatch.setattr(dispatcher, "append_ticket_audit_entry", lambda *_, **__: None)
    monkeypatch.setattr(dispatcher, "_initializing_runs", set())
    timer = MagicMock()
    monkeypatch.setattr(dispatcher.threading, "Timer", timer)
    started = dispatcher.start_workflow_run(
        workflow.id, ticket_id=tickets[-1].id, board_id=board.id, dispatch_async=True,
        run_metadata={"source_type": "workflow_queue", "auto_queued_from_run_id": run.id},
    )
    assert started["status"] == "started"
    timer.assert_called_once()
    with sessions.begin() as db:
        db.get(AutoWorkflowRun, started["run_id"]).status = "completed"
    repeated = dispatcher.start_workflow_run(
        workflow.id, ticket_id=tickets[-1].id, board_id=board.id, dispatch_async=True,
        run_metadata={"source_type": "workflow_queue", "auto_queued_from_run_id": run.id},
    )
    assert repeated["status"] == "already_dispatched"
    timer.assert_called_once()
    engine.dispose()


@pytest.mark.parametrize("change, late", [
    ("unlinked", False), ("reassigned", False), ("moved", False), ("project", False), ("project", True),
])
def test_stale_implicit_queue_selection_cannot_launch(tmp_path, monkeypatch, change, late):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from distr.core.db import Base
    from distr.core.db.kanban import KanbanBoard, KanbanLane
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflowStep
    from distr.core.workflow.work_dispatch import dispatch_work_item

    engine = create_engine(f"sqlite:///{tmp_path / 'stale-selection.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dispatcher, "get_session", sessions)
    with sessions.begin() as db:
        workflow = AutoWorkflow(name="Queue")
        other_workflow = AutoWorkflow(name="Other workflow")
        board, other_board = KanbanBoard(name="Queue board"), KanbanBoard(name="Other board")
        project = Project(name="New project")
        db.add_all([workflow, other_workflow, board, other_board, project])
        db.flush()
        lane = KanbanLane(name="Backlog", board_id=board.id)
        other_lane = KanbanLane(name="Backlog", board_id=other_board.id)
        db.add_all([lane, other_lane, AutoWorkflowStep(workflow_id=workflow.id, name="Work",
                                                      action_type="agent_instruction", instruction="Work.")])
        db.flush()
        current = KanbanTicket(title="Completed", lane_id=lane.id, linked_workflow_id=workflow.id)
        ticket = KanbanTicket(title="Selected", lane_id=lane.id, linked_workflow_id=workflow.id)
        db.add_all([current, ticket])
        db.flush()
        predecessor = AutoWorkflowRun(workflow_id=workflow.id, ticket_id=current.id,
                                      board_id=board.id, status="completed")
        db.add(predecessor)
    expected = (
        {"unlinked": None, "reassigned": other_workflow.id}.get(change, workflow.id),
        other_lane.id if change == "moved" else lane.id,
        project.id if change == "project" else None,
    )

    def reassign():
        with sessions.begin() as db:
            selected = db.get(KanbanTicket, ticket.id)
            selected.linked_workflow_id, selected.lane_id, selected.linked_project_id = expected
        return 100

    prepare = MagicMock(side_effect=lambda **_: reassign())
    monkeypatch.setattr("distr.core.workflow.development_threads.ensure_development_thread", prepare)
    if not late:
        reassign()

    metadata = {"auto_queued_from_run_id": predecessor.id}
    dispatch = dispatch_work_item(workflow_id=workflow.id, board_id=board.id, ticket_id=ticket.id,
                                  source_type="workflow_queue", run_metadata=metadata, _session_provider=sessions)
    assert dispatch["status"] == "blocked"
    assert prepare.call_count == int(late)
    launch = dispatcher.start_workflow_run(workflow.id, board_id=board.id, ticket_id=ticket.id,
                                          run_metadata={**metadata, "source_type": "workflow_queue"})
    assert launch["status"] == "blocked"
    with sessions() as db:
        selected = db.get(KanbanTicket, ticket.id)
        assert (selected.linked_workflow_id, selected.lane_id, selected.linked_project_id) == expected
        assert selected.source_chat_id is None
        assert db.query(AutoWorkflowRun).count() == 1
    engine.dispose()


def test_sequential_ticket_group_starts_one_and_carries_explicit_group(monkeypatch):
    monkeypatch.setattr(
        dispatcher,
        "get_session",
        _session_for_workflow({"execution_mode": "sequential", "concurrency_scope": "workflow"}),
    )
    calls = []
    hydrated = {
        3: {"ticket_id": 3, "board_id": 8, "context": "Ticket three", "run_metadata": {"project_id": "1"}},
    }
    monkeypatch.setattr(
        "distr.core.workflow.ticket_dispatch.hydrate_ticket_run_ref",
        lambda item, _workflow_id: hydrated[item["ticket_id"]],
    )
    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch.dispatch_work_item",
        lambda *, workflow_id, **kwargs: calls.append((workflow_id, kwargs)) or {"run_id": 701},
    )

    result = dispatcher.start_workflow_ticket_group(
        12,
        [
            {"ticket_id": 3, "board_id": 8, "context": "Ticket three", "run_metadata": {"project_id": "1"}},
            {"ticket_id": 4, "board_id": 8, "context": "Ticket four", "run_metadata": {"project_id": "1"}},
            {"ticket_id": 3, "board_id": 8, "context": "duplicate"},
        ],
        run_metadata={
            "requested_execution_policy": {"read_only": True},
            "qualification_scenario_id": "multi_ticket_delivery",
            "project_id": "99",
            "project_folder": "/repo/unrelated",
        },
    )

    assert result["mode"] == "sequential"
    assert result["ticket_count"] == 2
    assert result["queued_count"] == 1
    assert result["started"] == [{"ticket_id": 3, "run_id": 701}]
    assert len(calls) == 1
    metadata = calls[0][1]["run_metadata"]
    assert metadata["project_id"] == "1"
    assert "project_folder" not in metadata
    assert metadata["ticket_group_size"] == 2
    assert metadata["requested_execution_policy"] == {"read_only": True}
    assert metadata["qualification_scenario_id"] == "multi_ticket_delivery"
    assert metadata["ticket_group_common_metadata"] == {
        "requested_execution_policy": {"read_only": True},
        "qualification_scenario_id": "multi_ticket_delivery",
    }
    assert [item["ticket_id"] for item in metadata["ticket_group_items"]] == [3, 4]
    assert all(set(item) <= {"ticket_id", "board_id"} for item in metadata["ticket_group_items"])


def test_parallel_ticket_group_attempts_each_ticket_and_reports_partial_errors(monkeypatch):
    monkeypatch.setattr(
        dispatcher,
        "get_session",
        _session_for_workflow({"execution_mode": "parallel", "max_parallel_tickets": 3}),
    )
    calls = []
    monkeypatch.setattr(
        "distr.core.workflow.ticket_dispatch.hydrate_ticket_run_ref",
        lambda item, _workflow_id: {
            "ticket_id": item["ticket_id"],
            "board_id": None,
            "context": f"Ticket {item['ticket_id']}",
            "run_metadata": {},
        },
    )

    def start(*, workflow_id, **kwargs):
        calls.append(kwargs)
        if kwargs["ticket_id"] == 9:
            return {"error": "project concurrency guard"}
        return {"run_id": 800 + kwargs["ticket_id"]}

    monkeypatch.setattr("distr.core.workflow.work_dispatch.dispatch_work_item", start)
    result = dispatcher.start_workflow_ticket_group(
        2,
        [{"ticket_id": 8}, {"ticket_id": 9}, {"ticket_id": 10}],
    )

    assert [row["ticket_id"] for row in result["started"]] == [8, 10]
    assert result["errors"] == [{"ticket_id": 9, "error": "project concurrency guard"}]
    assert len(calls) == 3
    assert all(call["run_metadata"]["ticket_group_items"] == [] for call in calls)


def test_append_ticket_group_extends_live_sequential_run_without_restarting(monkeypatch):
    run = SimpleNamespace(
        id=104,
        workflow_id=369,
        board_id=12,
        ticket_id=177,
        status="running",
        run_data=json.dumps({
            "ticket_group_id": "example-group",
            "ticket_group_index": 0,
            "ticket_group_size": 1,
            "ticket_group_items": [{"ticket_id": 177, "board_id": 12, "context": "First", "run_metadata": {}}],
        }),
    )
    workflow = SimpleNamespace(run_settings=json.dumps({"execution_mode": "sequential"}))
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value.first.return_value = run if model is AutoWorkflowRun else workflow
        return chain

    db.query.side_effect = query

    @contextlib.contextmanager
    def get_session():
        yield db

    monkeypatch.setattr(dispatcher, "get_session", get_session)
    result = dispatcher.append_workflow_ticket_group(
        104,
        [
            {"ticket_id": 178, "board_id": 12, "context": "Second", "run_metadata": {}},
            {"ticket_id": 178, "board_id": 12, "context": "duplicate", "run_metadata": {}},
            {"ticket_id": 179, "board_id": 12, "context": "Third", "run_metadata": {}},
        ],
    )

    assert result["appended_ticket_ids"] == [178, 179]
    assert result["ticket_count"] == 3
    assert result["queued_count"] == 2
    stored = json.loads(run.run_data)
    assert [item["ticket_id"] for item in stored["ticket_group_items"]] == [177, 178, 179]
    assert all("context" not in item and "run_metadata" not in item for item in stored["ticket_group_items"])


def test_group_auto_advance_uses_next_selected_ticket_not_global_queue(monkeypatch):
    items = [
        {"ticket_id": 31, "board_id": 4, "context": "First", "run_metadata": {"project_id": "7"}},
        {"ticket_id": 44, "board_id": 5, "context": "Second", "run_metadata": {"project_id": "9"}},
    ]
    run = SimpleNamespace(
        id=501,
        ticket_id=31,
        board_id=4,
        run_data=json.dumps({
            "ticket_group_id": "group-a",
            "ticket_group_index": 0,
            "ticket_group_items": items,
            "ticket_group_common_metadata": {
                "requested_execution_policy": {"read_only": True},
                "qualification_scenario_id": "multi_ticket_delivery",
                "project_id": "7",
                "project_folder": "/repo/first",
                "execution_route": {"cwd": "/repo/first"},
            },
        }),
    )
    workflow = SimpleNamespace(run_settings=json.dumps({"execution_mode": "sequential"}))
    current_ticket = SimpleNamespace(id=31, workflow_queue_position=100)
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        if model is AutoWorkflowRun:
            chain.filter.return_value.first.return_value = run
        elif model is AutoWorkflow:
            chain.filter.return_value.first.return_value = workflow
        elif model is KanbanTicket:
            chain.filter.return_value.first.return_value = current_ticket
        return chain

    db.query.side_effect = query

    @contextlib.contextmanager
    def get_session():
        yield db

    monkeypatch.setattr(dispatcher, "get_session", get_session)
    monkeypatch.setattr(
        "distr.core.workflow.ticket_dispatch.hydrate_ticket_run_ref",
        lambda item, workflow_id: next(row for row in items if row["ticket_id"] == item["ticket_id"]),
    )
    calls = []
    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch.dispatch_work_item",
        lambda *, workflow_id, **kwargs: calls.append((workflow_id, kwargs)) or {"run_id": 502},
    )
    monkeypatch.setattr("distr.gui.web.kanban_events.increment_kanban_updated", lambda **_kwargs: None)

    dispatcher._maybe_auto_start_next_queued_ticket(501, 12)

    assert len(calls) == 1
    assert calls[0][1]["ticket_id"] == 44
    assert calls[0][1]["board_id"] == 5
    assert calls[0][1]["context"] == "Second"
    assert calls[0][1]["run_metadata"]["ticket_group_index"] == 1
    assert calls[0][1]["run_metadata"]["project_id"] == "9"
    assert "project_folder" not in calls[0][1]["run_metadata"]
    assert "execution_route" not in calls[0][1]["run_metadata"]
    assert calls[0][1]["run_metadata"]["requested_execution_policy"] == {"read_only": True}
    assert calls[0][1]["run_metadata"]["qualification_scenario_id"] == "multi_ticket_delivery"


@pytest.mark.parametrize("destination_board", [4, 5])
def test_board_queue_advance_hydrates_the_next_tickets_project(monkeypatch, destination_board):
    run = SimpleNamespace(ticket_id=31, board_id=4, run_data=json.dumps({
        "project_id": 7, "project_name": "First project", "project_folder": "/repo/first",
    }))
    workflow = SimpleNamespace(run_settings=json.dumps({"execution_mode": "sequential"}))
    ticket_query = MagicMock()
    ticket_query.filter.return_value.first.return_value = SimpleNamespace(workflow_queue_position=1)
    ticket_query.join.return_value.filter.return_value.order_by.return_value.first.return_value = (
        SimpleNamespace(id=44, title="Second ticket")
    )
    db = MagicMock()
    queries = {AutoWorkflowRun: MagicMock(), AutoWorkflow: MagicMock(), KanbanTicket: ticket_query}
    queries[AutoWorkflowRun].filter.return_value.first.return_value = run
    queries[AutoWorkflow].filter.return_value.first.return_value = workflow
    db.query.side_effect = queries.__getitem__
    monkeypatch.setattr(dispatcher, "get_session", lambda: contextlib.nullcontext(db))
    target_metadata = {"project_id": "9", "project_name": "Second project", "project_folder": "/repo/second"}
    hydrate = MagicMock(return_value={"ticket_id": 44, "board_id": destination_board, "context": "Second brief",
                                      "run_metadata": target_metadata})
    monkeypatch.setattr("distr.core.workflow.ticket_dispatch.hydrate_ticket_run_ref", hydrate)
    dispatch = MagicMock(return_value={"run_id": 502})
    monkeypatch.setattr("distr.core.workflow.work_dispatch.dispatch_work_item", dispatch)
    monkeypatch.setattr("distr.gui.web.kanban_events.increment_kanban_updated", lambda **kwargs: None)

    dispatcher._maybe_auto_start_next_queued_ticket(501, 12)

    hydrate.assert_called_once_with({"ticket_id": 44, "board_id": 4}, 12)
    if destination_board != 4:
        dispatch.assert_not_called()
        return
    dispatch.assert_called_once()
    assert dispatch.call_args.kwargs["context"] == "Second brief"
    assert dispatch.call_args.kwargs["run_metadata"] == {**target_metadata, "auto_queued_from_run_id": 501}


def test_ticket_scoped_developer_context_replaces_ambient_project_state():
    scoped = dispatcher._scope_developer_context_to_run(
        {
            "runtime": {"cwd": "/repo/decisions"},
            "active_project": {"id": 6, "name": "ThatShirtShow"},
            "active_workflows": [{"id": 99}],
            "active_executions": [{"id": 88}],
            "external_agent_context": {"codex_threads": [{"project": "AuctionNow"}]},
            "user_memory_context": "AuctionNow preferences",
            "workspace": {"projection_path": "/projects/thatshirtshow/.decisions"},
            "board_notes": [{"title": "Todo", "content": "ThatShirtShow"}],
            "ecosystem": {"name_index": {"projects": {"ThatShirtShow": 6}}},
        },
        {
            "project_id": "12",
            "project_name": "Ember & Crust Pizza House",
            "project_folder": "/projects/pizza-house",
            "board_name": "Ember & Crust Delivery",
            "ticket_title": "Define visual direction",
        },
        board_id=9,
        ticket_id=167,
    )

    assert scoped["runtime"]["cwd"] == "/projects/pizza-house"
    assert scoped["active_project"]["id"] == 12
    assert scoped["active_project"]["name"] == "Ember & Crust Pizza House"
    assert scoped["active_board"]["id"] == 9
    assert scoped["active_tickets"] == [{
        "id": 167,
        "title": "Define visual direction",
        "lane": "",
        "priority": "",
        "workflow_status": "running",
        "linked_project_id": 12,
        "linked_workflow_id": None,
        "send_to_cli": False,
    }]
    assert scoped["active_workflows"] == []
    assert scoped["active_executions"] == []
    assert scoped["external_agent_context"] == {}
    assert scoped["user_memory_context"] == ""
    assert scoped["workspace"] == {}
    assert scoped["board_notes"] == []
    assert scoped["ecosystem"] == {}


def test_ticket_scoped_context_can_explicitly_include_ambient_memory():
    scoped = dispatcher._scope_developer_context_to_run(
        {
            "runtime": {"cwd": "/repo/decisions"},
            "user_memory_context": "Shared user preference",
            "workspace": {"projection_path": "/projects/shared/.decisions"},
            "board_notes": [{"title": "Shared note"}],
            "ecosystem": {"name_index": {}},
        },
        {"include_ambient_memory_context": True},
        board_id=None,
        ticket_id=None,
    )

    assert scoped["user_memory_context"] == "Shared user preference"
    assert scoped["workspace"]["projection_path"] == "/projects/shared/.decisions"
    assert scoped["board_notes"] == [{"title": "Shared note"}]
