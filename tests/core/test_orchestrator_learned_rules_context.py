"""Unit tests for board learned rules context helpers."""

from __future__ import annotations

import contextlib
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base


def _factory(tmp_path):
    import distr.core.db.orchestrator  # noqa: F401

    db_path = tmp_path / "learned_rules.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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


def test_build_learned_rules_context_includes_enabled_board_rules(tmp_path):
    from distr.core.orchestrator import build_learned_rules_context, record_learning_signal

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    from unittest.mock import patch

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_learning_signal(
            scope="board",
            scope_id=7,
            rule_type="validation_failure",
            summary="Always run npm test before marking UI tickets complete.",
            payload={"verdict": "fail"},
        )
        record_learning_signal(
            scope="board",
            scope_id=7,
            rule_type="ide_iteration",
            summary="Report files changed and tests run when returning from IDE.",
            payload={"run_id": 1},
        )
        context = build_learned_rules_context(7)

    assert "[BOARD LEARNED RULES]" in context
    assert "npm test" in context
    assert "files changed" in context


def test_record_routing_override_emits_event_and_learning_signal(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorLearnedRule
    from distr.core.orchestrator import build_learned_rules_context, record_routing_override

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        event_id = record_routing_override(
            override="promote_to_codex",
            requested_backend="codex",
            original_backend="cursor",
            final_backend="codex",
            board_id=7,
            project_id=3,
            ticket_id=12,
            reasons=["Detected override phrase: promote to codex."],
        )

        with get_session() as session:
            event = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == event_id).one()
            rule = session.query(OrchestratorLearnedRule).filter(OrchestratorLearnedRule.scope_id == 7).one()
        context = build_learned_rules_context(7)

    assert event.event_type == "routing_override_applied"
    assert event.status == "applied"
    assert json.loads(event.payload)["override"] == "promote_to_codex"
    assert rule.rule_type == "routing_override"
    assert "codex" in rule.summary.lower()
    assert "promote to codex" in context.lower()


def test_backend_handoff_redacts_secrets_and_records_memory(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorLearnedRule
    from distr.core.orchestrator import (
        build_backend_handoff_packet,
        record_backend_handoff,
        record_human_intervention_memory,
    )

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        packet = build_backend_handoff_packet(
            backend_id="codex",
            model="gpt-test",
            instruction="Use Authorization: Bearer sk-testsecret1234567890 and fix the UI.",
            project_id=3,
            workflow_id=4,
            run_id=5,
            step_id=6,
            ticket_id=7,
            board_id=8,
            execution_session_id=9,
            callback={
                "api_key": "super-secret",
                "bridge_url": "http://127.0.0.1/codex-events?internal_token=short-proof-token",
            },
        )
        handoff_id = record_backend_handoff(packet=packet, status="dispatched")
        intervention_id = record_human_intervention_memory(
            label="missed requirement",
            message="The worker skipped the required visual check.",
            workflow_id=4,
            run_id=5,
            step_id=6,
            ticket_id=7,
            board_id=8,
            project_id=3,
            execution_session_id=9,
            handoff_event_id=handoff_id,
        )

        with get_session() as session:
            handoff = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == handoff_id).one()
            intervention = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == intervention_id).one()
            learned = session.query(OrchestratorLearnedRule).filter(OrchestratorLearnedRule.rule_type == "human_intervention").one()

    payload = json.loads(handoff.payload)
    assert handoff.event_type == "backend_handoff_created"
    assert payload["payload_hash"]
    assert "sk-testsecret" not in json.dumps(payload)
    assert "short-proof-token" not in json.dumps(payload)
    assert payload["callback"]["api_key"] == "[redacted]"
    assert payload["callback"]["bridge_url"].endswith("internal_token=[redacted]")
    assert intervention.event_type == "human_intervention_recorded"
    assert json.loads(intervention.payload)["label"] == "missed_requirement"
    assert learned.scope == "board"
    assert "visual check" in learned.summary


def test_handoff_text_redacts_named_secret_values_quoted_by_workers():
    from distr.core.orchestrator import redact_handoff_payload

    text = (
        "The source `SECRET_KEY` was a hard-coded real value "
        "(`'django-insecure-do-not-persist-this-value'); replace it with an env var."
    )

    redacted = redact_handoff_payload(text)

    assert "do-not-persist" not in redacted
    assert "SECRET_KEY" in redacted
    assert "[redacted]" in redacted


def test_handoff_redaction_preserves_token_usage_metrics():
    from distr.core.orchestrator import redact_handoff_payload

    redacted = redact_handoff_payload({
        "totalTokens": 4200,
        "input_tokens": 4000,
        "output_token_count": 200,
        "access_token": "must-hide",
    })

    assert redacted["totalTokens"] == 4200
    assert redacted["input_tokens"] == 4000
    assert redacted["output_token_count"] == 200
    assert redacted["access_token"] == "[redacted]"


def test_handoff_redaction_preserves_contract_labels_and_runtime_ids():
    from distr.core.orchestrator import redact_handoff_payload

    redacted = redact_handoff_payload({
        "contract": "ui_acceptance_contract_if_applicable: N/A",
        "request_uid": "f66c49834e3b4d8b826a89a6904f18dd",
    })

    assert redacted["contract"] == "ui_acceptance_contract_if_applicable: N/A"
    assert redacted["request_uid"] == "f66c49834e3b4d8b826a89a6904f18dd"


def test_build_standards_context_appends_board_rules(tmp_path):
    from distr.core.orchestrator import record_learning_signal
    from distr.core.workflow.standards_memory import build_standards_context

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    from unittest.mock import patch

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_learning_signal(
            scope="board",
            scope_id=3,
            rule_type="validation_failure",
            summary="Do not skip browser validation for frontend tickets.",
        )
        context = build_standards_context("Follow ticket instructions carefully.", board_id=3)

    assert "Follow ticket instructions carefully." in context
    assert "[UNIVERSAL WORKFLOW QUALITY STANDARDS]" in context
    assert "browser validation" in context


def test_build_standards_context_appends_visual_taste_memory(tmp_path):
    from distr.core.orchestrator import record_ui_feedback_label
    from distr.core.workflow.standards_memory import build_standards_context

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    from unittest.mock import patch

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_ui_feedback_label(
            label="approved",
            reason="Dense operational layouts with clear hierarchy.",
            board_id=3,
        )
        context = build_standards_context("Follow ticket instructions carefully.", board_id=3)

    assert "[VISUAL TASTE MEMORY]" in context
    assert "Dense operational layouts" in context
