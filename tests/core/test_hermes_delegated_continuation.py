from __future__ import annotations


def test_parse_delegated_continuation_intent_recognizes_retry_and_route():
    from distr.core.hermes_delegated.continuation import parse_delegated_continuation_intent

    intent = parse_delegated_continuation_intent("Retry with browser fallback and keep going")

    assert intent is not None
    assert intent.action == "retry"
    assert intent.preferred_route == "browser_automation"
    assert intent.freeform == "Retry with browser fallback and keep going"


def test_parse_delegated_continuation_intent_recognizes_cancel():
    from distr.core.hermes_delegated.continuation import parse_delegated_continuation_intent

    intent = parse_delegated_continuation_intent("Cancel the delegated run")

    assert intent is not None
    assert intent.action == "cancel"
    assert intent.preferred_route == ""


def test_record_delegated_continuation_links_latest_run_and_redacts(monkeypatch):
    from distr.core.hermes_delegated.continuation import (
        DelegatedContinuationIntent,
        record_delegated_continuation,
    )

    emitted = []
    latest = {
        "id": 41,
        "event_type": "delegated_run_report",
        "status": "blocked",
        "project_id": 7,
        "payload": {"plan": {"kind": "email_document_scope"}},
    }
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kwargs: emitted.append(kwargs) or 42)

    event_id = record_delegated_continuation(
        DelegatedContinuationIntent(
            action="retry",
            preferred_route="browser_automation",
            freeform="retry with token=abc123456789012345678901234567890",
        ),
        latest,
    )

    assert event_id == 42
    assert emitted[0]["event_type"] == "delegated_continuation_requested"
    assert emitted[0]["parent_event_id"] == 41
    assert emitted[0]["project_id"] == 7
    assert emitted[0]["payload"]["action"] == "retry"
    assert emitted[0]["payload"]["preferred_route"] == "browser_automation"
    assert "abc123456789012345678901234567890" not in str(emitted[0]["payload"])
    assert "[redacted]" in str(emitted[0]["payload"])


def test_find_latest_delegated_run_report_filters_newest_report(monkeypatch):
    from distr.core.hermes_delegated.continuation import find_latest_delegated_run_report

    events = [
        {"id": 50, "event_type": "other", "status": "observed"},
        {"id": 49, "event_type": "delegated_run_report", "status": "blocked"},
        {"id": 48, "event_type": "delegated_run_report", "status": "completed"},
    ]
    monkeypatch.setattr("distr.core.hermes.list_events", lambda **kwargs: events)

    assert find_latest_delegated_run_report(project_id=7)["id"] == 49


def test_execute_delegated_continuation_reconstructs_plan_and_records_report(monkeypatch):
    from distr.core.hermes_delegated.continuation import (
        DelegatedContinuationIntent,
        execute_delegated_continuation,
    )
    from distr.core.hermes_delegated.models import DelegatedRunReport

    recorded = []

    class _Runner:
        def run(self, plan, context=None):
            recorded.append((plan, context))
            return DelegatedRunReport(
                status="completed",
                plan=plan,
                completed_steps=["capture_source_content", "verify_result"],
                evidence={"destination_path": "/tmp/out.txt"},
            )

    monkeypatch.setattr(
        "distr.core.hermes_delegated.continuation.record_delegated_run_report",
        lambda report, **kwargs: 88,
    )
    latest = {
        "id": 41,
        "workflow_id": 1,
        "run_id": 2,
        "step_id": 3,
        "ticket_id": 4,
        "board_id": 5,
        "project_id": 6,
        "payload": {
            "plan": {
                "kind": "desktop_sequence",
                "source_surface": "telegram",
                "original_instruction": "Copy this code to Downloads called out.txt",
                "steps": [
                    {
                        "action": "capture_source_content",
                        "preferred_route": "sidecar",
                        "fallback_routes": ["desktop_accessibility"],
                        "description": "Capture content",
                        "params": {"source": "clipboard"},
                        "verifies": ["content captured"],
                    }
                ],
                "requires_approval_before": ["overwrite_existing_file"],
                "target_backend": "",
                "confidence": 0.8,
            }
        },
    }

    result = execute_delegated_continuation(
        DelegatedContinuationIntent(action="retry", preferred_route="sidecar", freeform="retry"),
        latest,
        runner=_Runner(),
    )

    assert result["executed"] is True
    assert result["run_event_id"] == 88
    assert recorded[0][0].kind == "desktop_sequence"
    assert recorded[0][0].steps[0].action == "capture_source_content"
    assert recorded[0][1]["project_id"] == 6
    assert recorded[0][1]["preferred_route"] == "sidecar"
    assert result["report"].status == "completed"
