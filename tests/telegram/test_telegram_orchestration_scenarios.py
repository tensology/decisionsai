"""Paul's Telegram instructions, end to end through the deterministic routing layers.

Paul, 26 Nov 2026: "I will be talking to Telegram a lot about the tickets and what
needs to be done ... I don't know if my instructions are going to go to the right
project. I don't know if it's going to be able to read WhatsApp and respond."

Each scenario is a realistic phrasing. Nothing here reaches Telegram, WhatsApp, or a
model: sends are mocked and the database is an in-memory copy of the real project and
board layout (names only).
"""

from __future__ import annotations

import contextlib
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.initiative import outcome_flow
from distr.core.work_intake import WorkIntake, WorkIntakeAction
from distr.core.work_intake.service import OrchestratorIntakeService
from distr.core.workflow import interactions

# The live layout on 26 Nov 2026: (project, in_use, boards). Plan My Charter is the
# only project marked in use, which is what used to swallow unnamed Telegram work.
LAYOUT = [
    ("AuctionNow", False, ["AuctionNow"]),
    ("Merrypak", False, ["Merrypak"]),
    ("Tensology", False, ["Tensology"]),
    ("Player1Sport", False, ["Player1Sport", "Player1Sport"]),
    ("Spoortrack (Old)", False, ["Spoortrack (Old)"]),
    ("Snuza", False, ["Snuza"]),
    ("DecisionsAI", False, ["DecisionsAI"]),
    ("Spoortrack", False, ["Spoortrack", "STK board"]),
    ("Plan My Charter", True, ["Plan My Charter"]),
    ("Trading", False, ["Trading"]),
    ("MailShot", False, ["MailShot"]),
]


def _factory():
    from distr.core.db import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def world(monkeypatch):
    from distr.core.db.kanban import KanbanBoard, KanbanLane
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow

    factory = _factory()
    monkeypatch.setattr("distr.core.work_intake.service.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator.emit_channel_intake_event", lambda **k: None)
    started = []
    monkeypatch.setattr(OrchestratorIntakeService, "_start_workflow",
                        lambda self, intake, decision: started.append((decision.ticket_id, decision.project_id)))
    monkeypatch.setattr(OrchestratorIntakeService, "_start_development_thread",
                        lambda self, intake, decision: started.append((decision.ticket_id, decision.project_id)), raising=False)
    monkeypatch.setattr(OrchestratorIntakeService, "_start_lightweight_execution",
                        lambda self, intake, decision: started.append((decision.ticket_id, decision.project_id)))
    monkeypatch.setattr("distr.core.automation_orchestrator.dispatch_matching_channel_automations",
                        lambda **k: [], raising=False)
    ids: dict[str, int] = {}
    with _session_ctx(factory) as db:
        workflow = AutoWorkflow(name="Development", status="active")
        db.add(workflow)
        db.flush()
        position = 0
        for name, in_use, boards in LAYOUT:
            project = Project(name=name, folder_location=f"/tmp/{name.lower()}", in_use=in_use)
            db.add(project)
            db.flush()
            ids[name] = project.id
            for board_name in boards:
                # Like the live boards: no default workflow (the managed class workflows are retired).
                board = KanbanBoard(name=board_name, default_project_id=project.id, default_workflow_id=None,
                                    in_use=in_use, archived=False, position=position)
                position += 1
                db.add(board)
                db.flush()
                db.add(KanbanLane(board_id=board.id, name="Backlog", position=0))
                project.kanban_board_id = board.id
    return {"factory": factory, "ids": ids, "started": started}


def _telegram(text: str, *, voice: bool = False) -> WorkIntake:
    return WorkIntake(source="telegram", user_text="" if voice else text, transcript=text if voice else "",
                      source_user_id="paul", source_thread_id="paul")


def _tickets(world):
    from distr.core.db.kanban import KanbanTicket

    with _session_ctx(world["factory"]) as db:
        return [(row.id, row.linked_project_id, row.title) for row in db.query(KanbanTicket).all()]


# ---------------------------------------------------------------------------
# 1. Shortcuts: nothing may answer for the model unless it is really the shortcut's job
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "tell the Merrypak guys we're on it",
    "what's happening with Player One",
    "what did Greg say",
    "fix the CAPTCHA on Merrypak registration",
])
def test_ordinary_instructions_are_not_swallowed_by_a_shortcut(text, monkeypatch):
    monkeypatch.setattr(outcome_flow, "latest_open_flow", lambda: None)
    assert outcome_flow.handle_telegram_text(text) is None


@pytest.mark.parametrize("text", [
    "check WhatsApp",
    "can you check whatsapp for me",
    "um yeah so just have a look at whatsapp and see what came in",
])
def test_check_whatsapp_acknowledges_first_then_reads_real_data(text, monkeypatch):
    monkeypatch.setattr(outcome_flow, "whatsapp_activity_text", lambda: "WhatsApp, last 24 hours:\n- Merrypak group (Merrypak): 2 messages")
    monkeypatch.setattr(outcome_flow, "_pending_proposals", lambda limit=10: [])
    out = outcome_flow.handle_telegram_text(text)
    assert out["ack"] == "Okay, checking WhatsApp now."
    assert "Merrypak" in out["text"]
    assert "All clear" not in out["text"]


@pytest.mark.parametrize("text", [
    "check WhatsApp and tell the Merrypak guys we're on it",
    "look at whatsapp and let Greg know it's sorted",
    "check whatsapp and message Merrypak",
])
def test_whatsapp_instruction_to_reply_is_not_reduced_to_a_read_out(text):
    assert outcome_flow.is_whatsapp_check_request(text) is False


@pytest.mark.parametrize("text", ["any new proposals", "any new proposals?", "/proposals", "show me the proposals"])
def test_any_new_proposals_lists_incoming_with_evidence(text, monkeypatch):
    monkeypatch.setattr(outcome_flow, "_pending_proposals", lambda limit=10: [])
    monkeypatch.setattr(outcome_flow, "whatsapp_activity_text", lambda: "No messages in the last 24 hours from WhatsApp chats linked to a project.")
    out = outcome_flow.handle_telegram_text(text)
    assert out is not None
    assert "No proposals are waiting" in out["text"]
    assert "WhatsApp" in out["text"]  # evidence, not a bare "all clear"


# ---------------------------------------------------------------------------
# 2. Work goes to the right project, or Paul is asked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "project", "voice"), [
    ("fix the CAPTCHA on Merrypak registration", "Merrypak", False),
    ("um so yeah can you uh fix the captcha thing on uh Merrypak registration", "Merrypak", True),
    ("fix the login on the Player One app", "Player1Sport", False),
    ("fix the map on old spoortrack", "Spoortrack (Old)", False),
    ("fix the map on spoortrack", "Spoortrack", False),
    ("Snuza: fix the sleep chart labels", "Snuza", False),
])
def test_named_work_lands_on_that_project_and_starts(world, text, project, voice):
    decision = OrchestratorIntakeService().ingest(_telegram(text, voice=voice))
    assert decision.action in {WorkIntakeAction.RUN_WORKFLOW, WorkIntakeAction.CREATE_TICKET}, decision.response_text
    assert decision.status == "ticket_created", decision.response_text
    assert decision.project_id == world["ids"][project]
    assert world["started"] == [(decision.ticket_id, world["ids"][project])]  # work actually kicked off
    assert f"Ticket #{decision.ticket_id}" in decision.response_text  # honest receipt


@pytest.mark.parametrize("text", [
    "fix the captcha on the registration page",          # no project at all
    "fix the login on the Acme Widgets site",            # a project that doesn't exist
])
def test_unnamed_or_unknown_project_asks_instead_of_guessing(world, text):
    decision = OrchestratorIntakeService().ingest(_telegram(text))
    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO, decision.project_id
    assert decision.ticket_id is None
    assert "?" in decision.response_text
    assert _tickets(world) == []
    assert world["started"] == []


def test_two_projects_named_asks_with_the_real_options(world):
    decision = OrchestratorIntakeService().ingest(_telegram("fix the trading hours banner on Merrypak"))
    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO
    assert "Merrypak" in decision.response_text and "Trading" in decision.response_text
    assert _tickets(world) == []


def test_web_keeps_the_active_project_fallback(world):
    # The desktop app is looking at a project; Telegram is not.
    _board, _lane, project, _workflow = OrchestratorIntakeService()._resolve_scope(
        WorkIntake(source="web", user_text="fix the captcha on the registration page"))
    assert project.id == world["ids"]["Plan My Charter"]


@pytest.mark.parametrize("text", [
    "tell the Merrypak guys we're on it",
    "what's happening with Player One",
    "what did Greg say",
    "check WhatsApp",
    "any new proposals",
    "hmm",
])
def test_questions_and_messages_to_clients_never_create_work(world, text):
    decision = OrchestratorIntakeService().ingest(_telegram(text))
    assert decision.action in {WorkIntakeAction.ANSWER_DIRECTLY, WorkIntakeAction.ASK_MISSING_INFO}
    assert _tickets(world) == []


def test_follow_up_on_an_existing_ticket_updates_that_ticket():
    decision = OrchestratorIntakeService().classify(_telegram("update ticket 330 to also check the mobile layout"))
    assert decision.action == WorkIntakeAction.UPDATE_TICKET
    assert decision.diagnostics["ticket_id"] == 330


# ---------------------------------------------------------------------------
# 3. A message about one project never steers another project's work
# ---------------------------------------------------------------------------

def test_new_instruction_is_not_fed_to_another_projects_checkpoint(world, monkeypatch):
    pending = [{"token": "t1", "run_id": 77, "allowed_actions": '["continue", "stop", "feedback"]', "kind": "step_review"}]
    monkeypatch.setattr(interactions, "pending_interactions", lambda **k: pending)
    resolve = Mock(return_value={"action": "feedback"})
    monkeypatch.setattr(interactions, "resolve_interaction", resolve)
    monkeypatch.setattr(interactions, "_run_project_id", lambda run_id: world["ids"]["Snuza"], raising=False)
    assert interactions.handle_telegram_workflow_reply(
        "fix the CAPTCHA on Merrypak registration", chat_id="paul") is None
    resolve.assert_not_called()
    # Guidance that is about the paused run itself still reaches it.
    interactions.handle_telegram_workflow_reply("use the Snuza staging API instead", chat_id="paul")
    resolve.assert_called_once()


def test_not_done_feedback_only_steers_the_flow_it_is_about(world, monkeypatch):
    flow = {"chat_id": 501, "ticket_id": 12, "status": "awaiting_decision", "updated": 1}
    monkeypatch.setattr(outcome_flow, "latest_open_flow", lambda: ("k", flow))
    monkeypatch.setattr(outcome_flow, "_chat_project_id", lambda chat_id: world["ids"]["Snuza"], raising=False)
    steer = Mock()
    monkeypatch.setattr(outcome_flow, "_thread_steer", steer)
    monkeypatch.setattr(outcome_flow, "save_flow", lambda *a, **k: None)
    monkeypatch.setattr(outcome_flow, "audit", lambda *a, **k: None)
    assert outcome_flow.handle_telegram_text("the Merrypak registration captcha is still broken") is None
    assert outcome_flow.handle_telegram_text("ticket 330 still isn't fixed") is None
    steer.assert_not_called()
    assert outcome_flow.handle_telegram_text("it's still broken")["text"].startswith("Thanks for flagging")
    steer.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Nothing goes to a client without Paul's approval
# ---------------------------------------------------------------------------

def test_agent_whatsapp_send_leaves_a_draft_instead_of_sending(monkeypatch):
    from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool

    tool = KanbanTicketTool.__new__(KanbanTicketTool)
    manager = Mock()
    relay = Mock(return_value={"success": True})
    monkeypatch.setattr("distr.core.integrations.whatsapp.relay_client.send_message_via_relay", relay)
    import sys
    import types

    menu = types.ModuleType("distr.gui.oracle.menu")
    menu.is_whatsapp_enabled_in_settings = lambda: True
    monkeypatch.setitem(sys.modules, "distr.gui.oracle", types.ModuleType("distr.gui.oracle"))
    monkeypatch.setitem(sys.modules, "distr.gui.oracle.menu", menu)
    object.__setattr__(tool, "_get_whatsapp_manager", lambda: manager)
    drafted = Mock(return_value="Draft saved for Merrypak group. Review it in WhatsApp before sending.")
    object.__setattr__(tool, "_action_whatsapp_set_draft", drafted)
    posted = Mock()
    monkeypatch.setattr("requests.post", posted)
    out = tool._action_whatsapp_send_message(jid="123@g.us", jid_phone="", text="Hi all, we're on it.")
    posted.assert_not_called()
    relay.assert_not_called()
    drafted.assert_called_once()
    assert "not sent" in out.lower()


def test_work_without_a_workflow_starts_in_the_tickets_development_thread(world, monkeypatch):
    """Live boards have no workflow, which used to answer "Merrypak has no workflow linked"."""
    monkeypatch.undo()
    monkeypatch.setattr("distr.core.work_intake.service.get_session", lambda: _session_ctx(world["factory"]))
    monkeypatch.setattr("distr.core.orchestrator.emit_channel_intake_event", lambda **k: None)
    monkeypatch.setattr("distr.core.automation_orchestrator.dispatch_matching_channel_automations",
                        lambda **k: [], raising=False)
    ensure = Mock(return_value=4321)
    dispatch = Mock(return_value={"started": True, "status": "initializing"})
    monkeypatch.setattr("distr.core.workflow.development_threads.ensure_development_thread", ensure)
    monkeypatch.setattr("distr.core.workflow.development_harness.dispatch_development_prompt", dispatch)
    decision = OrchestratorIntakeService().ingest(_telegram("fix the CAPTCHA on Merrypak registration"))
    assert decision.status == "ticket_created", decision.response_text
    kwargs = ensure.call_args.kwargs
    assert kwargs["project_id"] == world["ids"]["Merrypak"]
    assert kwargs["ticket_id"] == decision.ticket_id
    assert kwargs["starting_question"] == "fix the CAPTCHA on Merrypak registration"
    assert dispatch.call_args.args == (4321, "fix the CAPTCHA on Merrypak registration")
    assert "Development thread #4321" in decision.response_text
