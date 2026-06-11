from __future__ import annotations


class _Manager:
    def __init__(self):
        self.sent = []

    def send_to_telegram(self, text):
        self.sent.append(text)


def test_handle_delegated_continuation_message_records_linked_event(monkeypatch):
    from distr.core.hermes_delegated.continuation import handle_delegated_continuation_message

    latest = {
        "id": 41,
        "event_type": "delegated_run_report",
        "status": "blocked",
        "project_id": 7,
        "payload": {
            "plan": {"kind": "email_document_scope"},
            "current_step": "search_email",
        },
    }
    emitted = []
    monkeypatch.setattr(
        "distr.core.hermes_delegated.continuation.find_latest_delegated_run_report",
        lambda **kwargs: latest,
    )
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kwargs: emitted.append(kwargs) or 42)
    manager = _Manager()

    assert handle_delegated_continuation_message(manager, "retry with browser fallback") is True

    assert emitted[0]["event_type"] == "delegated_continuation_requested"
    assert emitted[0]["parent_event_id"] == 41
    assert "Retry requested for delegated run 41" in manager.sent[0]
    assert "browser_automation" in manager.sent[0]


def test_handle_delegated_continuation_message_ignores_without_latest_run(monkeypatch):
    from distr.core.hermes_delegated.continuation import handle_delegated_continuation_message

    monkeypatch.setattr(
        "distr.core.hermes_delegated.continuation.find_latest_delegated_run_report",
        lambda **kwargs: None,
    )
    manager = _Manager()

    assert handle_delegated_continuation_message(manager, "continue") is False
    assert manager.sent == []


def test_handle_delegated_continuation_message_executes_retry_with_runner(monkeypatch):
    from distr.core.hermes_delegated.continuation import handle_delegated_continuation_message
    from distr.core.hermes_delegated.models import DelegatedRunReport

    latest = {
        "id": 41,
        "event_type": "delegated_run_report",
        "status": "blocked",
        "project_id": 7,
        "payload": {
            "plan": {
                "kind": "project_handoff",
                "source_surface": "telegram",
                "original_instruction": "Tell Codex to fix tests",
                "steps": [
                    {
                        "action": "dispatch_project_handoff",
                        "preferred_route": "project_cli_backend",
                        "fallback_routes": [],
                        "description": "Dispatch",
                        "params": {},
                        "verifies": [],
                    }
                ],
                "requires_approval_before": [],
                "target_backend": "codex",
                "confidence": 0.7,
            }
        },
    }
    reports = []

    class _Runner:
        def run(self, plan, context=None):
            report = DelegatedRunReport(
                status="completed",
                plan=plan,
                completed_steps=["dispatch_project_handoff"],
                evidence={"handoff": {"output": "Status: completed"}},
            )
            reports.append((report, context))
            return report

    monkeypatch.setattr(
        "distr.core.hermes_delegated.continuation.find_latest_delegated_run_report",
        lambda **kwargs: latest,
    )
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kwargs: 42)
    monkeypatch.setattr(
        "distr.core.hermes_delegated.continuation.record_delegated_run_report",
        lambda report, **kwargs: 99,
    )
    manager = _Manager()

    assert handle_delegated_continuation_message(
        manager,
        "retry with Codex",
        runner=_Runner(),
    ) is True

    assert len(reports) == 1
    assert reports[0][1]["preferred_route"] == "project_cli_backend"
    assert "Retry requested for delegated run 41" in manager.sent[0]
    assert "Delegated run 99 completed" in manager.sent[1]
    assert "Status: completed" in manager.sent[1]
