from __future__ import annotations

import pytest

from distr.core.chat import ChatService
from distr.core.db import Chat, Settings, get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem
from distr.core.workflow.development_threads import (
    DevelopmentThreadConflict,
    development_thread_metadata,
    ensure_development_thread,
    repair_development_ownership_integrity,
    rebind_development_thread,
    update_development_thread_settings,
    update_development_model_route,
)


def test_integrity_repair_removes_orphans_detaches_links_and_backfills_legacy_rows():
    with get_session() as db:
        board = KanbanBoard(name="Repair board", source="local")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Orphan link",
            source_chat_id=999991,
        )
        db.add(ticket)
        db.add(DevelopmentWorkItem(chat_id=999992, identity_key="chat:999992"))
        legacy = Chat(title="Legacy Development", params='{"development":{"source_type":"prompt"}}')
        db.add(legacy)
        db.commit()
        ticket_id = ticket.id
        legacy_id = legacy.id

    result = repair_development_ownership_integrity()

    assert result["orphan_work_items_removed"] == 1
    assert result["ticket_links_detached"] == 1
    assert result["work_items_backfilled"] >= 1
    with get_session() as db:
        assert db.get(KanbanTicket, ticket_id).source_chat_id is None
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=999992).first() is None
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=legacy_id).one()


def test_ticket_development_thread_is_stable_and_keeps_workflow_identity():
    first = ensure_development_thread(
        workflow_id=44,
        title="DEV-42",
        project_id=None,
        ticket_id=42,
        starting_question="Implement DEV-42",
    )
    reused = ensure_development_thread(
        workflow_id=99,
        title="DEV-42 again",
        project_id=None,
        ticket_id=42,
        starting_question="Retry DEV-42",
    )
    other = ensure_development_thread(
        workflow_id=44,
        title="DEV-43",
        project_id=None,
        ticket_id=43,
        starting_question="Implement DEV-43",
    )

    assert reused == first
    assert other != first
    with get_session() as db:
        metadata = development_thread_metadata(db.get(Chat, int(first)))
    assert metadata == {"workflow_id": 44, "ticket_id": 42}


def test_development_thread_does_not_replace_active_conversational_chat():
    conversational_chat_id, _ = ChatService.create_new_chat(title="Conversational chat")

    development_chat_id = ensure_development_thread(
        title="Development task",
        starting_question="Implement the task",
    )

    assert development_chat_id != conversational_chat_id
    with get_session() as db:
        settings = db.query(Settings).first()
        assert settings.last_chat_id == conversational_chat_id
        assert settings.agent_current_chat_id == conversational_chat_id


def test_external_ticket_identity_is_scoped_by_provider_and_board():
    jira = ensure_development_thread(
        workflow_id=44,
        title="DEV-42",
        board_provider="jira",
        board_key="engineering",
        board_ticket_key="DEV-42",
    )
    jira_reused = ensure_development_thread(
        workflow_id=99,
        title="DEV-42 retry",
        board_provider="jira",
        board_key="engineering",
        board_ticket_key="DEV-42",
    )
    trello = ensure_development_thread(
        workflow_id=44,
        title="DEV-42 Trello",
        board_provider="trello",
        board_key="engineering",
        board_ticket_key="DEV-42",
    )
    other_jira_board = ensure_development_thread(
        workflow_id=44,
        title="DEV-42 other board",
        board_provider="jira",
        board_key="operations",
        board_ticket_key="DEV-42",
    )

    assert jira_reused == jira
    assert len({jira, trello, other_jira_board}) == 3


def test_open_ended_work_does_not_reuse_a_thread_by_workflow():
    first = ensure_development_thread(workflow_id=44, title="First open-ended task")
    second = ensure_development_thread(workflow_id=44, title="Second open-ended task")

    assert first != second
    with get_session() as db:
        keys = {
            row.identity_key
            for row in db.query(DevelopmentWorkItem)
            .filter(DevelopmentWorkItem.chat_id.in_([first, second]))
            .all()
        }
    assert keys == {f"chat:{first}", f"chat:{second}"}


def test_rebind_updates_authoritative_and_legacy_scope_atomically():
    chat_id = ensure_development_thread(
        workflow_id=44,
        title="Move me",
        project_id=None,
        board_provider="jira",
        board_key="old-board",
        board_ticket_key="DEV-42",
    )

    result = rebind_development_thread(
        chat_id,
        project_id=None,
        board_provider="trello",
        board_key="new-board",
        board_ticket_key="card-9",
        board_ticket_title="New card",
        board_ticket_lane="Doing",
    )

    assert result["identity_key"] == "ticket:trello:new-board:card-9"
    with get_session() as db:
        chat = db.get(Chat, chat_id)
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        metadata = development_thread_metadata(chat)
        item_scope = (item.board_provider, item.board_key, item.ticket_key)
    assert item_scope == ("trello", "new-board", "card-9")
    assert metadata["board_provider"] == "trello"
    assert metadata["board_key"] == "new-board"
    assert metadata["board_ticket_key"] == "card-9"

    cleared = rebind_development_thread(
        chat_id,
        project_id=None,
        board_provider=None,
        board_key=None,
    )
    assert cleared["identity_key"] == f"chat:{chat_id}"
    with get_session() as db:
        metadata = development_thread_metadata(db.get(Chat, chat_id))
    assert "board_provider" not in metadata
    assert "board_key" not in metadata
    assert "board_ticket_key" not in metadata


def test_rebind_refuses_to_take_another_threads_work_item():
    owner = ensure_development_thread(
        workflow_id=44,
        title="Owner",
        ticket_id=42,
    )
    other = ensure_development_thread(workflow_id=44, title="Other")

    with pytest.raises(DevelopmentThreadConflict, match=f"thread #{owner}"):
        rebind_development_thread(
            other,
            project_id=None,
            board_provider=None,
            board_key=None,
            ticket_id=42,
        )


def test_rebind_moves_a_thread_from_its_old_ticket_to_an_unowned_ticket():
    with get_session() as db:
        board = KanbanBoard(name="Reassignment board")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="In Progress", position=0)
        db.add(lane)
        db.flush()
        old_ticket = KanbanTicket(lane_id=lane.id, title="Old ticket")
        next_ticket = KanbanTicket(lane_id=lane.id, title="Next ticket")
        db.add_all([old_ticket, next_ticket])
        db.commit()
        board_id = int(board.id)
        old_ticket_id = int(old_ticket.id)
        next_ticket_id = int(next_ticket.id)

    chat_id = ensure_development_thread(title="Reassign me")
    rebind_development_thread(
        chat_id,
        project_id=None,
        board_provider="local",
        board_key=f"decisions:{board_id}",
        ticket_id=old_ticket_id,
    )
    moved = rebind_development_thread(
        chat_id,
        project_id=None,
        board_provider="local",
        board_key=f"decisions:{board_id}",
        ticket_id=next_ticket_id,
    )

    assert moved["ticket_id"] == next_ticket_id
    with get_session() as db:
        assert db.get(KanbanTicket, old_ticket_id).source_chat_id is None
        assert db.get(KanbanTicket, next_ticket_id).source_chat_id == chat_id
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one().local_ticket_id == next_ticket_id


def test_mutable_thread_settings_preserve_board_and_ticket_scope():
    chat_id = ensure_development_thread(
        workflow_id=44,
        title="Locked scope",
        board_provider="jira",
        board_key="engineering",
        board_ticket_key="DEV-42",
        board_ticket_title="DEV-42",
    )

    result = update_development_thread_settings(
        chat_id,
        title="Renamed thread",
        permission_profile={"mode": "trusted"},
        remote_continuation=True,
    )

    assert result["title"] == "Renamed thread"
    assert result["board_key"] == "engineering"
    assert result["board_provider"] == "jira"
    assert result["board_ticket_key"] == "DEV-42"


def test_linking_local_ticket_uses_ticket_title_and_rename_updates_both():
    with get_session() as db:
        board = KanbanBoard(name="One title board")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="In Progress", position=1)
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Canonical ticket title",
            description="One piece of work",
            position=0,
        )
        db.add(ticket)
        db.commit()
        board_id = int(board.id)
        ticket_id = int(ticket.id)

    chat_id = ensure_development_thread(workflow_id=44, title="Old conversational title")
    linked = rebind_development_thread(
        chat_id,
        project_id=None,
        board_provider="local",
        board_key=f"decisions:{board_id}",
        ticket_id=ticket_id,
        board_ticket_key=str(ticket_id),
        board_ticket_title="Stale copied title",
    )

    assert linked["title"] == "Canonical ticket title"
    with get_session() as db:
        chat = db.get(Chat, chat_id)
        ticket = db.get(KanbanTicket, ticket_id)
        assert chat.title == ticket.title == "Canonical ticket title"
        assert development_thread_metadata(chat)["board_ticket_title"] == "Canonical ticket title"

    renamed = update_development_thread_settings(chat_id, title="Renamed once")
    assert renamed["title"] == "Renamed once"
    with get_session() as db:
        chat = db.get(Chat, chat_id)
        ticket = db.get(KanbanTicket, ticket_id)
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        assert chat.title == ticket.title == item.ticket_title == "Renamed once"
        assert development_thread_metadata(chat)["board_ticket_title"] == "Renamed once"



def test_linking_a_recent_thread_creates_one_in_progress_ticket_and_moves_it_with_the_board():
    with get_session() as db:
        first_project = Project(name="Thread Board One")
        second_project = Project(name="Thread Board Two")
        db.add_all([first_project, second_project])
        db.flush()
        first_board = KanbanBoard(name="Thread Board One", default_project_id=first_project.id)
        second_board = KanbanBoard(name="Thread Board Two", default_project_id=second_project.id)
        db.add_all([first_board, second_board])
        db.flush()
        first_project.kanban_board_id = first_board.id
        second_project.kanban_board_id = second_board.id
        db.commit()
        first_board_id = first_board.id
        second_board_id = second_board.id
        second_project_id = second_project.id

    chat_id = ensure_development_thread(workflow_id=44, title="Boardless scraper work")
    linked = rebind_development_thread(
        chat_id,
        project_id=None,
        board_provider="local",
        board_key=f"decisions:{first_board_id}",
        board_ticket_title="Boardless scraper work",
    )
    moved = rebind_development_thread(
        chat_id,
        project_id=second_project_id,
        board_provider="local",
        board_key=f"decisions:{second_board_id}",
        board_ticket_title="Boardless scraper work",
    )

    assert moved["ticket_id"] == linked["ticket_id"]
    with get_session() as db:
        tickets = db.query(KanbanTicket).filter(KanbanTicket.source_chat_id == chat_id).all()
        lane = db.get(KanbanLane, tickets[0].lane_id)
        assert len(tickets) == 1
        assert lane.name == "In Progress"
        assert lane.board_id == second_board_id
        assert tickets[0].linked_project_id == second_project_id


def test_model_route_is_thread_owned_and_does_not_mutate_reusable_workflow(monkeypatch):
    chat_id = ensure_development_thread(workflow_id=44, title="Model route")
    workflow = {
        "id": 44,
        "run_settings": {"studio": {}},
        "steps": [{"id": 401, "config": {"timeout_seconds": 90}}],
    }
    workflow_updates = []
    step_updates = []
    monkeypatch.setattr("distr.core.workflow.service.get_workflow", lambda workflow_id: workflow)
    monkeypatch.setattr("distr.core.workflow.service.update_workflow", lambda workflow_id, **kwargs: workflow_updates.append((workflow_id, kwargs)) or True)
    monkeypatch.setattr("distr.core.workflow.service.update_step", lambda step_id, **kwargs: step_updates.append((step_id, kwargs)) or True)

    result = update_development_model_route(
        chat_id,
        route_mode="manual",
        provider="openai",
        model_name="gpt-5-codex",
        reasoning_effort="high",
        service_tier="priority",
    )

    assert result["model_name"] == "gpt-5-codex"
    with get_session() as db:
        chat = db.get(Chat, chat_id)
        metadata = development_thread_metadata(chat)
        assert chat.route_mode == "manual"
        assert chat.provider == "openai"
        assert metadata["model_route"]["reasoning_effort"] == "high"
        assert metadata["model_route"]["service_tier"] == "priority"
    assert workflow_updates == []
    assert step_updates == []
