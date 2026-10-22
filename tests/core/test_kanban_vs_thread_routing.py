"""kanbanroute 2026-10-22: ticket/board language -> Kanban (create_ticket), not Development threads."""
import json

import pytest

from distr.core.workflow.development_threads import kanban_redirect, orchestrator_surface
from distr.core.workflow.oracle_routing_rule import classify_instruction

PROJECTS = [(7, "Spoortrack (Old)"), (9, "DecisionsAI")]
KCASES = [
("please create tickets for this","kanban"),
("add a ticket for the login bug","kanban"),
("move that to done","kanban"),
("what's on the board?","kanban"),
("create a ticket in DecisionsAI for the refusal bug","kanban"),
("make three tickets for the onboarding work","kanban"),
("list the tickets on the DecisionsAI board","kanban"),
("show me the kanban board","kanban"),
("move ticket 42 to in progress","kanban"),
("close the ticket about the installer","kanban"),
("update the ticket with the new deadline","kanban"),
("what tickets are in the backlog?","kanban"),
("open the DecisionsAI board","kanban"),
("track this","unspecified"),
("fix the bug described in ticket 42","development"),
("create a development thread for ticket 42","development"),
("add a thread to the DecisionsAI project and ask what it is for","development"),
("implement the login page in DecisionsAI","development"),
("what is the point of this project?","chat"),
("then run the tests and tell me what failed","development"),
]


@pytest.mark.parametrize("text,expected", KCASES)
def test_surface_table(text, expected):
    assert orchestrator_surface(text) == expected


@pytest.mark.parametrize("text,expected", [c for c in KCASES if c[1] == "kanban"])
def test_rule_routes_kanban(text, expected):
    assert classify_instruction(text, projects=PROJECTS)["route"] == "kanban"


def test_kanban_keeps_project_context():
    r = classify_instruction("create a ticket in DecisionsAI for the refusal bug", projects=PROJECTS)
    assert r == {"route": "kanban", "project_id": 9}
    assert classify_instruction("create tickets for this", in_dev_thread=True, projects=PROJECTS)["route"] == "kanban"


@pytest.mark.parametrize("text,action", [
    ("please create tickets for this", "create_ticket_batch"),
    ("add a ticket for the login bug", "create_ticket"),
    ("move that to done", "move_ticket"),
    ("what's on the board?", "list_tickets"),
    ("close the ticket about the installer", "update_ticket"),
    ("open the DecisionsAI board", "open_board"),
])
def test_redirect_action(text, action):
    assert kanban_redirect(text)["action"] == action
    assert kanban_redirect(text)["tool"] == "create_ticket"


def test_development_control_redirects_ticket_requests(monkeypatch):
    import distr.core.workflow.oracle_routing_rule as rr
    from distr.core.agent.tools.integrations.development_control import DevelopmentControlTool

    monkeypatch.setattr(rr, "_project_names", lambda: PROJECTS)
    for action in ("create_thread", "send_instruction"):
        out = json.loads(DevelopmentControlTool()._run(
            action=action, thread_id=1, instruction="make tickets",
            last_user_message="please create tickets for this in DecisionsAI"))
        assert out["surface"] == "kanban" and out["created"] is False
        assert out["redirect"] == {"tool": "create_ticket", "action": "create_ticket_batch", "project_name": "DecisionsAI"}
