"""Source identities survive recreation; credentials never enter prompts or tool output."""
import json
import os
import sys

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
