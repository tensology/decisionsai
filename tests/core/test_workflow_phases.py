"""Phase composition: weight classes set budget and gates, not step count."""

from __future__ import annotations

from distr.core.workflow.phases import (
    PHASES,
    WORKFLOW_CLASSES,
    build_workflow_definition,
    class_policy,
    classify,
    compose_instruction,
    phases_for_class,
)


def test_plain_question_is_light():
    assert classify("What is the status of the Merrypak ticket?", has_repo_target=False) == "light"


def test_repo_change_defaults_to_standard():
    assert classify("Add a retry to the WhatsApp relay") == "standard"


def test_risk_keyword_forces_critical():
    assert classify("Add a migration for the invoices table") == "critical"
    assert classify("Rotate the production API token") == "critical"


def test_risk_can_only_escalate_never_downgrade():
    # An explicit "light" override cannot make a migration cheap.
    assert classify("schema migration", requested="light") == "heavy"
    assert classify("deploy to production", requested="light") == "heavy"
    assert classify("drop table users", requested="standard") == "critical"


def test_explicit_override_respected_when_not_risky():
    assert classify("tidy the readme", requested="standard") == "standard"
    assert classify("tidy the readme", requested="heavy") == "heavy"


def test_unknown_requested_class_falls_back_to_signals():
    assert classify("tidy the readme", requested="nonsense") == "standard"


def test_every_class_phase_exists():
    for key, policy in WORKFLOW_CLASSES.items():
        for phase in policy["phases"]:
            assert phase in PHASES, f"{key} references unknown phase {phase}"


def test_phases_returned_in_canonical_order():
    phases = phases_for_class("standard")
    assert phases.index("understand") < phases.index("implement") < phases.index("test")
    assert phases.index("test") < phases.index("report")


def test_light_class_never_deploys_or_reviews():
    policy = class_policy("light")
    assert policy["may_deploy"] is False
    assert policy["independent_review"] is False


def test_critical_class_may_deploy_and_requires_gate():
    policy = class_policy("critical")
    assert policy["may_deploy"] is True
    assert policy["human_gate"] is True


def test_compose_instruction_keeps_one_session_and_orders_phases():
    text = compose_instruction(["understand", "implement", "test"], goal="Fix the 500")

    assert "GOAL" in text
    assert "Fix the 500" in text
    assert "one continuous session" in text.lower()
    assert text.index("UNDERSTAND") < text.index("IMPLEMENT") < text.index("TEST")
    assert "COMPLETION CONTRACT" in text


def test_compose_instruction_forbids_deploy_by_default():
    text = compose_instruction(["implement", "test"])
    assert "Do not deploy" in text


def test_compose_instruction_allows_deploy_only_when_asked():
    text = compose_instruction(["implement", "deploy"], may_deploy=True)
    assert "Do not deploy" not in text
    assert "Deploy only the exact commit" in text


def test_compose_instruction_stops_before_irreversible_when_gated():
    text = compose_instruction(["implement"], needs_human_gate=True)
    assert "needs_input before anything irreversible" in text


def test_standard_workflow_is_a_single_working_step():
    definition = build_workflow_definition("Fix it", "standard", goal="Fix the 500")
    assert len(definition["steps"]) == 1
    assert definition["steps"][0]["action_type"] == "send_to_project_cli"


def test_heavy_workflow_adds_a_separate_review_step():
    definition = build_workflow_definition("Big job", "heavy", goal="Add reporting")
    names = [step["name"] for step in definition["steps"]]
    assert names == ["Heavy session", "Independent review"]
    assert definition["steps"][1]["config"]["read_only_expected"] is True


def test_light_workflow_has_no_repo_phases():
    phases = phases_for_class("light")
    assert "implement" not in phases
    assert "test" not in phases


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def test_workflow_weight_class_reads_managed_marker():
    from distr.core.workflow.phases import workflow_weight_class

    settings = '{"managed_by": "phase_engine", "weight_class": "heavy"}'
    assert workflow_weight_class(settings) == "heavy"


def test_workflow_weight_class_ignores_unmanaged_workflows():
    from distr.core.workflow.phases import workflow_weight_class

    assert workflow_weight_class('{"weight_class": "heavy"}') is None
    assert workflow_weight_class('{"managed_by": "someone_else", "weight_class": "heavy"}') is None
    assert workflow_weight_class(None) is None
    assert workflow_weight_class('{"managed_by": "phase_engine", "weight_class": "nonsense"}') is None


def test_class_workflow_names_are_readable():
    from distr.core.workflow.phases import class_workflow_name

    assert class_workflow_name("standard") == "Ticket: standard (one session)"
    assert class_workflow_name("critical") == "Ticket: critical (one session)"


def test_route_ticket_explains_a_risk_escalation(monkeypatch):
    from distr.core.workflow import phases

    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: 99)
    plan = phases.route_ticket("Add a migration for invoices", ticket_id=42)

    assert plan["class"] == "critical"
    assert plan["workflow_id"] == 99
    assert "migration" in plan["reason"]
    assert "Ticket #42" in plan["announcement"]
    assert plan["overridable"] is True


def test_route_ticket_explains_a_question(monkeypatch):
    from distr.core.workflow import phases

    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: 11)
    plan = phases.route_ticket("Where is my order?", has_repo_target=False)

    assert plan["class"] == "light"
    assert "question" in plan["reason"]


def test_route_ticket_reports_an_explicit_override(monkeypatch):
    from distr.core.workflow import phases

    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: 12)
    plan = phases.route_ticket("tidy the readme", requested="heavy")

    assert plan["class"] == "heavy"
    assert plan["reason"] == "you asked for this one"


def test_composed_instruction_marks_the_reply_phase_for_run_time_enrichment():
    from distr.core.workflow.phases import REPLY_PHASE_MARKER, compose_instruction

    text = compose_instruction(["understand", "report", "reply"])
    assert REPLY_PHASE_MARKER in text


def test_composed_instruction_without_reply_has_no_marker():
    from distr.core.workflow.phases import REPLY_PHASE_MARKER, compose_instruction

    text = compose_instruction(["understand", "implement", "test", "report"])
    assert REPLY_PHASE_MARKER not in text


def test_route_ticket_workflow_id_returns_the_managed_workflow(monkeypatch):
    from distr.core.workflow import phases

    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: 55)
    assert phases.route_ticket_workflow_id("add a button") == 55


def test_route_ticket_workflow_id_creates_then_falls_back_to_standard(monkeypatch):
    from distr.core.workflow import phases

    calls = {"ensured": 0}

    def _ensure():
        calls["ensured"] += 1
        return {"standard": 77}

    monkeypatch.setattr(phases, "ensure_class_workflows", _ensure)
    monkeypatch.setattr(
        phases,
        "find_class_workflow_id",
        lambda key: 77 if key == "standard" else None,
    )

    assert phases.route_ticket_workflow_id("schema migration") == 77
    assert calls["ensured"] == 1


def test_route_ticket_survives_missing_managed_workflow(monkeypatch):
    from distr.core.workflow import phases

    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: None)
    plan = phases.route_ticket("add a button")

    assert plan["class"] == "standard"
    assert plan["workflow_id"] is None
