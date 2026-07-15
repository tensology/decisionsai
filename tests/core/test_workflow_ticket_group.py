import contextlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.db.kanban import KanbanTicket
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.workflow import dispatcher


def _session_for_workflow(run_settings):
    workflow = SimpleNamespace(run_settings=json.dumps(run_settings))
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = workflow

    @contextlib.contextmanager
    def get_session():
        yield db

    return get_session


def test_sequential_ticket_group_starts_one_and_carries_explicit_group(monkeypatch):
    monkeypatch.setattr(
        dispatcher,
        "get_session",
        _session_for_workflow({"execution_mode": "sequential", "concurrency_scope": "workflow"}),
    )
    calls = []
    monkeypatch.setattr(dispatcher, "start_workflow_run", lambda workflow_id, **kwargs: calls.append((workflow_id, kwargs)) or {"run_id": 701})

    result = dispatcher.start_workflow_ticket_group(
        12,
        [
            {"ticket_id": 3, "board_id": 8, "context": "Ticket three", "run_metadata": {"project_id": "1"}},
            {"ticket_id": 4, "board_id": 8, "context": "Ticket four", "run_metadata": {"project_id": "1"}},
            {"ticket_id": 3, "board_id": 8, "context": "duplicate"},
        ],
    )

    assert result["mode"] == "sequential"
    assert result["ticket_count"] == 2
    assert result["queued_count"] == 1
    assert result["started"] == [{"ticket_id": 3, "run_id": 701}]
    assert len(calls) == 1
    metadata = calls[0][1]["run_metadata"]
    assert metadata["ticket_group_size"] == 2
    assert [item["ticket_id"] for item in metadata["ticket_group_items"]] == [3, 4]


def test_parallel_ticket_group_attempts_each_ticket_and_reports_partial_errors(monkeypatch):
    monkeypatch.setattr(
        dispatcher,
        "get_session",
        _session_for_workflow({"execution_mode": "parallel", "max_parallel_tickets": 3}),
    )
    calls = []

    def start(_workflow_id, **kwargs):
        calls.append(kwargs)
        if kwargs["ticket_id"] == 9:
            return {"error": "project concurrency guard"}
        return {"run_id": 800 + kwargs["ticket_id"]}

    monkeypatch.setattr(dispatcher, "start_workflow_run", start)
    result = dispatcher.start_workflow_ticket_group(
        2,
        [{"ticket_id": 8}, {"ticket_id": 9}, {"ticket_id": 10}],
    )

    assert [row["ticket_id"] for row in result["started"]] == [8, 10]
    assert result["errors"] == [{"ticket_id": 9, "error": "project concurrency guard"}]
    assert len(calls) == 3
    assert all(call["run_metadata"]["ticket_group_items"] == [] for call in calls)


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
    calls = []
    monkeypatch.setattr(dispatcher, "start_workflow_run", lambda workflow_id, **kwargs: calls.append((workflow_id, kwargs)) or {"run_id": 502})
    monkeypatch.setattr("distr.gui.web.kanban_events.increment_kanban_updated", lambda **_kwargs: None)

    dispatcher._maybe_auto_start_next_queued_ticket(501, 12)

    assert len(calls) == 1
    assert calls[0][1]["ticket_id"] == 44
    assert calls[0][1]["board_id"] == 5
    assert calls[0][1]["context"] == "Second"
    assert calls[0][1]["run_metadata"]["ticket_group_index"] == 1


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
