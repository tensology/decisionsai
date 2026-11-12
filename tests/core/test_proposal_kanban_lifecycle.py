"""Proposal accept binds a Backlog card; Complete stays human-only; lane history is recorded."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.db import Chat, get_session
from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketLaneHistory
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem
from distr.core.incoming import proposals as proposals_mod
from distr.core.kanban.lane_history import HUMAN_ACTOR_HEADER, HUMAN_ACTOR_VALUE, current_ticket_lane, list_lane_history
from distr.core.kanban.lifecycle import move_ticket_to_delivery_lane


def _patch_handoff(monkeypatch):
    monkeypatch.setattr("distr.core.incoming.media.ensure_proposal_attachments", lambda *a, **k: {"attachments": [], "failures": []})
    monkeypatch.setattr("distr.core.incoming.media.proposal_attachments", lambda *a, **k: [])
    monkeypatch.setattr("distr.core.incoming.media.register_thread_attachments", lambda *a, **k: {})
    monkeypatch.setattr("distr.core.incoming.transcription.prepare_thread_transcripts", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.incoming.events.publish_incoming_change", Mock())
    monkeypatch.setattr("distr.core.incoming.source_context.register_source_context", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.incoming.project_links.proposal_in_scope", lambda *a, **k: True)
    monkeypatch.setattr("distr.core.workflow.development_harness.development_execution_active", lambda cid: False)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "automation_cli_backend": "pi",
            "automation_cli_model_provider": "ollama",
            "automation_cli_model": "deepseek-v4.1-flash:cloud",
        },
    )


def _make_project(tmp_path, name="Proposal kanban fixture"):
    with get_session() as db:
        project = Project(name=name, folder_location=str(tmp_path))
        db.add(project)
        db.commit()
        return int(project.id)


def _proposal(project_id, *, suffix="mail-1", message_ids=None, **extra):
    body = {
        "id": f"local:gmail:{suffix}",
        "origin": "local",
        "source": "gmail",
        "dedupe_key": f"gmail:{suffix}",
        "project_id": project_id,
        "title": f"Review {suffix}",
        "description": "Normalize the delivery labels.",
        "message_ids": message_ids or [f"msg-{suffix}"],
    }
    body.update(extra)
    return body


def _ticket_state(chat_id):
    with get_session() as db:
        chat = db.get(Chat, int(chat_id))
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).one()
        ticket = db.get(KanbanTicket, int(item.local_ticket_id)) if item.local_ticket_id else None
        lane = db.get(KanbanLane, ticket.lane_id) if ticket and ticket.lane_id else None
        params = json.loads(chat.params or "{}") if chat else {}
        return {
            "chat": chat,
            "item": item,
            "ticket": ticket,
            "lane": lane.name if lane else None,
            "board_key": item.board_key,
            "params": params,
        }


def _lanes(ticket_id):
    with get_session() as db:
        ticket = db.get(KanbanTicket, int(ticket_id))
        lane = db.get(KanbanLane, ticket.lane_id)
        rows = db.query(KanbanLane).filter(KanbanLane.board_id == lane.board_id).all()
        return {row.name: int(row.id) for row in rows}, int(ticket.id), int(lane.board_id)


def _history(ticket_id):
    with get_session() as db:
        return list_lane_history(db, int(ticket_id), limit=50)


def _kanban_client():
    from distr.gui.web.routes.kanban import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    return TestClient(app)


def test_accept_creates_backlog_card_and_binds_before_dispatch(monkeypatch, tmp_path):
    _patch_handoff(monkeypatch)
    project_id = _make_project(tmp_path)
    proposal = _proposal(project_id)
    seen = []

    def dispatch(cid, prompt, **kwargs):
        state = _ticket_state(cid)
        seen.append(
            {
                "chat_id": cid,
                "ticket_id": int(state["ticket"].id),
                "lane": state["lane"],
                "board_key": state["board_key"],
            }
        )
        return {"status": "running", "started": True}

    monkeypatch.setattr("distr.core.workflow.development_harness.dispatch_development_prompt", dispatch)
    chat_id = proposals_mod.create_proposal_thread(proposal, start_work=True)
    assert seen and seen[0]["chat_id"] == chat_id
    assert seen[0]["lane"] == "Backlog"
    assert seen[0]["board_key"].startswith("decisions:")
    state = _ticket_state(chat_id)
    assert state["lane"] == "Backlog"
    assert int(state["item"].local_ticket_id) == seen[0]["ticket_id"]
    assert int(state["ticket"].source_chat_id) == chat_id
    assert int(state["ticket"].linked_project_id) == project_id
    history = _history(state["ticket"].id)
    assert any(row["reason"] == "created" and row["to_lane"] == "Backlog" for row in history)


def test_reaccept_and_existing_gmail_ticket_are_idempotent(monkeypatch, tmp_path):
    _patch_handoff(monkeypatch)
    project_id = _make_project(tmp_path)
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.dispatch_development_prompt",
        lambda *a, **k: {"status": "running", "started": True},
    )
    first = proposals_mod.create_proposal_thread(_proposal(project_id, suffix="idem"), start_work=True)
    first_ticket = int(_ticket_state(first)["ticket"].id)
    again = proposals_mod.create_proposal_thread(_proposal(project_id, suffix="idem"), start_work=True)
    assert again == first
    assert int(_ticket_state(again)["ticket"].id) == first_ticket

    # Pre-existing Gmail intake card (the 295 pattern) is reused and not moved.
    message_id = "gmail-message-295"
    with get_session() as db:
        from distr.core.kanban.lifecycle import DELIVERY_SOURCE_LANE, ensure_delivery_lanes
        from distr.core.kanban.proposal_tickets import ensure_linked_project_board

        board = ensure_linked_project_board(db, project_id)
        lanes = ensure_delivery_lanes(db, int(board.id))
        progress = lanes["In Progress"]
        existing = KanbanTicket(
            lane_id=int(progress.id),
            title="Existing Gmail intake",
            description="Created from mail intake.",
            linked_project_id=project_id,
            source_external_id=message_id,
            source_provider="gmail",
            source_label="gmail",
        )
        db.add(existing)
        db.commit()
        existing_id = int(existing.id)

    reused_chat = proposals_mod.create_proposal_thread(
        _proposal(project_id, suffix="gmail-295", message_ids=[message_id]),
        start_work=True,
    )
    reused = _ticket_state(reused_chat)
    assert int(reused["ticket"].id) == existing_id
    assert reused["lane"] == "In Progress"


def test_tensology_reuses_thread_and_card_both_directions(monkeypatch, tmp_path):
    _patch_handoff(monkeypatch)
    from distr.core.tensology_client import _ensure_tensology_kanban_ticket, _existing_tensology_thread
    from distr.core.workflow.development_threads import ensure_development_thread, rebind_development_thread

    project_id = _make_project(tmp_path, name="Jackcat fixture")
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.dispatch_development_prompt",
        lambda *a, **k: {"status": "running", "started": True},
    )

    incoming = {
        "id": "tensology:69",
        "origin": "tensology",
        "source": "whatsapp",
        "title": "Jackcat enquiry",
        "description": "Quote the job.",
        "project_id": project_id,
        "message_ids": ["wa-69"],
    }
    # Incoming first: Tensology mirror must reuse the same thread and card.
    def _authoritative(*a, **k):
        raise AssertionError("local tensology-shaped id should not refetch in this branch")

    # create_proposal_thread treats tensology: ids as upstream; stub the client.
    client = Mock()
    client.get.return_value = {
        **incoming,
        "id": 69,
        "status": "pending",
        "attachments": [],
        "project": {"id": 10069, "name": "Jackcat fixture", "decisions_project_id": project_id},
    }
    monkeypatch.setattr("distr.core.tensology_client.configured_tensology_client", lambda **k: client)
    monkeypatch.setattr("distr.core.tensology_client._resolve_tensology_project", lambda payload: {"id": int(payload.get("decisions_project_id") or payload.get("id") or project_id)})
    monkeypatch.setattr("distr.core.tensology_client.decide_work_request", Mock())
    monkeypatch.setattr("distr.core.incoming.proposals._from_tensology", lambda row, *a, **k: {**incoming, "id": "tensology:69", "project_id": project_id, "attachments": []})

    chat_id = proposals_mod.create_proposal_thread(incoming, start_work=True)
    ticket_id = int(_ticket_state(chat_id)["ticket"].id)

    tensology_ticket = {
        "reference": "PROJ-178-003",
        "title": "Jackcat enquiry",
        "description": "Quote the job.",
        "decisions_project_id": project_id,
        "project_id": 10069,
        "source_metadata": {"incoming_proposal_id": 69},
        "incoming_proposal": {"id": 69, "title": "Jackcat enquiry"},
    }
    assert _existing_tensology_thread(tensology_ticket) == chat_id
    mirrored = _ensure_tensology_kanban_ticket(tensology_ticket, {"id": project_id, "name": "Jackcat fixture"}, None)
    assert mirrored == ticket_id

    # Tensology first: Incoming accept reuses the earlier thread and card.
    project_id_2 = _make_project(tmp_path / "second", name="Second fixture")
    first_mirror = _ensure_tensology_kanban_ticket(
        {
            "reference": "PROJ-200-001",
            "title": "Earlier Tensology card",
            "decisions_project_id": project_id_2,
            "project_id": 10200,
            "source_metadata": {"incoming_proposal_id": 200},
            "incoming_proposal": {"id": 200, "title": "Earlier Tensology card"},
        },
        {"id": project_id_2, "name": "Second fixture"},
        None,
    )
    with get_session() as db:
        ticket = db.get(KanbanTicket, first_mirror)
        lane = db.get(KanbanLane, ticket.lane_id)
        board_key = f"decisions:{lane.board_id}"
    prior_chat = ensure_development_thread(
        title="Earlier Tensology card",
        project_id=project_id_2,
        ticket_id=first_mirror,
        source_type="incoming_proposal",
        source_ref="tensology:200",
        board_key=board_key,
        board_provider="local",
        board_ticket_key="PROJ-200-001",
        board_ticket_title="Earlier Tensology card",
    )
    rebind_development_thread(
        prior_chat,
        project_id=project_id_2,
        ticket_id=first_mirror,
        board_key=board_key,
        board_provider="local",
    )

    later = {
        "id": "tensology:200",
        "origin": "tensology",
        "source": "whatsapp",
        "title": "Earlier Tensology card",
        "project_id": project_id_2,
        "message_ids": ["wa-200"],
        "attachments": [],
    }
    client.get.return_value = {
        **later,
        "id": 200,
        "status": "pending",
        "attachments": [],
        "project": {"id": 10200, "name": "Second fixture", "decisions_project_id": project_id_2},
    }
    monkeypatch.setattr(
        "distr.core.incoming.proposals._from_tensology",
        lambda row, *a, **k: {**later, "id": "tensology:200", "project_id": project_id_2, "attachments": []},
    )
    accepted = proposals_mod.create_proposal_thread(later, start_work=True)
    assert accepted == prior_chat
    assert int(_ticket_state(accepted)["ticket"].id) == first_mirror


def test_workflow_progresses_proposal_card_backlog_to_qa(monkeypatch, tmp_path):
    _patch_handoff(monkeypatch)
    project_id = _make_project(tmp_path)
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.dispatch_development_prompt",
        lambda *a, **k: {"status": "running", "started": True},
    )
    chat_id = proposals_mod.create_proposal_thread(_proposal(project_id, suffix="flow"), start_work=True)
    ticket_id = int(_ticket_state(chat_id)["ticket"].id)
    assert _ticket_state(chat_id)["lane"] == "Backlog"
    with get_session() as db:
        assert move_ticket_to_delivery_lane(db, ticket_id, "In Progress") is True
        db.commit()
    assert _ticket_state(chat_id)["lane"] == "In Progress"
    with get_session() as db:
        assert move_ticket_to_delivery_lane(db, ticket_id, "QA") is True
        db.commit()
    assert _ticket_state(chat_id)["lane"] == "QA"
    history = _history(ticket_id)
    assert any(row["from_lane"] == "Backlog" and row["to_lane"] == "In Progress" and row["source"] == "lifecycle" for row in history)
    assert any(row["from_lane"] == "In Progress" and row["to_lane"] == "QA" and row["actor"] == "automation" for row in history)


def test_ensure_local_ticket_uses_in_progress_only_when_thread_is_running(monkeypatch, tmp_path):
    from distr.core.workflow.development_threads import ensure_development_thread, rebind_development_thread
    from distr.core.kanban.proposal_tickets import ensure_linked_project_board

    project_id = _make_project(tmp_path, name="Running thread fixture")
    with get_session() as db:
        board = ensure_linked_project_board(db, project_id)
        board_key = f"decisions:{int(board.id)}"
        db.commit()
    from distr.core.workflow.development_threads import _ensure_local_ticket_for_thread

    idle = ensure_development_thread(title="Idle thread", project_id=project_id, source_type="prompt", source_ref="idle-1")
    rebind_development_thread(idle, project_id=project_id, board_key=board_key, board_provider="local")
    assert _ticket_state(idle)["lane"] == "Backlog"

    running = ensure_development_thread(title="Running thread", project_id=project_id, source_type="prompt", source_ref="run-1")
    monkeypatch.setattr("distr.core.workflow.development_threads._scope_execution_active", lambda *a, **k: True)
    with get_session() as db:
        root = db.get(Chat, running)
        _id, _key, lane = _ensure_local_ticket_for_thread(
            db, root, board_key=board_key, ticket_id=None, title="Running thread",
        )
        assert not db.query(Chat).filter_by(parent_id=running, response="Ticket created in In Progress.").count()
        db.commit()
    assert lane == "In Progress"
    with get_session() as db:
        created = db.query(KanbanTicket).filter(KanbanTicket.source_chat_id == int(running)).one()
        created_lane = db.get(KanbanLane, created.lane_id)
        assert created_lane.name == "In Progress"
        assert db.query(Chat).filter_by(parent_id=running, response="Ticket created in In Progress.").count() == 1


def test_lane_update_posts_one_notice_to_the_proposal_thread(monkeypatch, tmp_path):
    _patch_handoff(monkeypatch)
    project_id = _make_project(tmp_path)
    chat_id = int(proposals_mod.create_proposal_thread(_proposal(project_id, suffix="lane-update"), start_work=False))
    ticket_id = int(_ticket_state(chat_id)["ticket"].id)
    lanes, _, _ = _lanes(ticket_id)
    client = _kanban_client()
    endpoint = f"/api/tickets/tickets/{ticket_id}"

    for _ in range(2):
        assert client.put(endpoint, json={"lane_id": lanes["In Progress"]}).status_code == 200
    assert current_ticket_lane(ticket_id) == "In Progress"
    with get_session() as db:
        assert db.query(Chat).filter_by(parent_id=chat_id, response="Ticket moved: Backlog to In Progress.").count() == 1


def test_complete_is_human_board_only(monkeypatch, tmp_path):
    from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
    from distr.core.initiative.action_handlers import move_tickets
    from distr.core.kanban.lifecycle import require_automation_lane

    _patch_handoff(monkeypatch)
    project_id = _make_project(tmp_path)
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.dispatch_development_prompt",
        lambda *a, **k: {"status": "running", "started": True},
    )
    chat_id = proposals_mod.create_proposal_thread(_proposal(project_id, suffix="complete"), start_work=True)
    ticket_id = int(_ticket_state(chat_id)["ticket"].id)
    with get_session() as db:
        move_ticket_to_delivery_lane(db, ticket_id, "In Progress")
        move_ticket_to_delivery_lane(db, ticket_id, "QA")
        db.commit()
    lanes, _, board_id = _lanes(ticket_id)
    complete_id = lanes["Complete"]
    client = _kanban_client()

    # Update endpoint may never set Complete.
    update = client.put(f"/api/tickets/tickets/{ticket_id}", json={"lane_id": complete_id})
    assert update.status_code == 403
    assert "Complete" in update.text
    assert current_ticket_lane(ticket_id) == "QA"

    # Agent/internal HTTP move without the human marker is refused.
    agent_move = client.put(f"/api/tickets/tickets/{ticket_id}/move", json={"lane_id": complete_id, "position": 0})
    assert agent_move.status_code == 403
    assert "Only you can move a ticket to Complete" in agent_move.text
    assert current_ticket_lane(ticket_id) == "QA"

    # Header alone, or a mismatched Origin, is not enough.
    header_only = client.put(
        f"/api/tickets/tickets/{ticket_id}/move",
        json={"lane_id": complete_id, "position": 0},
        headers={HUMAN_ACTOR_HEADER: HUMAN_ACTOR_VALUE},
    )
    assert header_only.status_code == 403
    spoofed = client.put(
        f"/api/tickets/tickets/{ticket_id}/move",
        json={"lane_id": complete_id, "position": 0},
        headers={HUMAN_ACTOR_HEADER: HUMAN_ACTOR_VALUE, "Origin": "http://evil.example"},
    )
    assert spoofed.status_code == 403

    # Chat tool and Initiative cannot complete or skip lanes.
    tool_result = KanbanTicketTool()._action_move_ticket(ticket_id, "Complete")
    assert "Only you can move a QA ticket to Complete" in tool_result
    assert current_ticket_lane(ticket_id) == "QA"
    with pytest.raises(ValueError, match="Only a human may move"):
        require_automation_lane("Complete")
    with pytest.raises(ValueError, match="Only a human may move"):
        move_tickets({"board_id": board_id, "target_lane": "Complete", "ticket_ids": [ticket_id]})

    # Fresh Backlog ticket cannot skip to QA via Initiative.
    other = proposals_mod.create_proposal_thread(_proposal(project_id, suffix="skip"), start_work=False)
    other_id = int(_ticket_state(other)["ticket"].id)
    with pytest.raises(ValueError, match="may not move"):
        move_tickets({"board_id": board_id, "target_lane": "QA", "ticket_ids": [other_id]})
    assert current_ticket_lane(other_id) == "Backlog"

    # Genuine board UI drag (header + same-origin) still works.
    human = client.put(
        f"/api/tickets/tickets/{ticket_id}/move",
        json={"lane_id": complete_id, "position": 0},
        headers={HUMAN_ACTOR_HEADER: HUMAN_ACTOR_VALUE, "Origin": "http://testserver"},
    )
    assert human.status_code == 200, human.text
    assert current_ticket_lane(ticket_id) == "Complete"
    history = _history(ticket_id)
    assert any(row["to_lane"] == "Complete" and row["actor"] == "user" and row["reason"] == "board_move" for row in history)

    listed = client.get(f"/api/tickets/tickets/{ticket_id}/lane-history")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["success"] is True
    assert body["ticket_id"] == ticket_id
    assert any(row["to_lane"] == "Complete" for row in body["entries"])
    assert all(row.get("created_at_zone") == "UTC" for row in body["entries"])


def test_lane_history_and_thread_context_include_current_lane(monkeypatch, tmp_path):
    from distr.core.developer_context import DeveloperContextAssembler, format_developer_context_dict_for_prompt
    from distr.core.workflow.development_harness import _ticket_lane_scope_line

    _patch_handoff(monkeypatch)
    project_id = _make_project(tmp_path)
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.dispatch_development_prompt",
        lambda *a, **k: {"status": "running", "started": True},
    )
    chat_id = proposals_mod.create_proposal_thread(_proposal(project_id, suffix="context"), start_work=True)
    ticket_id = int(_ticket_state(chat_id)["ticket"].id)
    with get_session() as db:
        move_ticket_to_delivery_lane(db, ticket_id, "In Progress")
        db.commit()

    scope = DeveloperContextAssembler()._fetch_active_thread_scope(chat_id)
    assert scope.get("ticket_id") == ticket_id or int(scope.get("ticket_id") or 0) == ticket_id
    assert scope.get("ticket_lane") == "In Progress"
    assert scope.get("ticket_lane_history")
    rendered = format_developer_context_dict_for_prompt({"active_thread": scope})
    assert "lane=In Progress" in rendered
    assert "ticket_lane_history" in rendered
    line = _ticket_lane_scope_line({"ticket_id": ticket_id})
    assert "Ticket lane: In Progress" in line
    assert "Backlog→In Progress" in line or "created→Backlog" in line
