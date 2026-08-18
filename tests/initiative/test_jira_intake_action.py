from distr.core.initiative.action_handlers import run_jira_intake_from_initiative


def test_initiative_jira_intake_stages_batch(monkeypatch):
    called = {}

    monkeypatch.setattr(
        "distr.core.kanban.jira_intake.run_jira_morning_intake",
        lambda **kwargs: called.update(kwargs) or {
            "reason": "ok",
            "created": [{"id": 11, "external_id": "ACME-1", "key": "ACME-1"}],
        },
    )

    class _Board:
        id = 7

    class _Q:
        def filter(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def first(self):
            return _Board()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def query(self, *a, **k):
            return _Q()

        def commit(self):
            return None

    monkeypatch.setattr("distr.core.db.get_session", lambda: _Session())
    monkeypatch.setattr(
        "distr.core.kanban.ticket_audit.append_ticket_audit_entry",
        lambda *a, **k: None,
    )

    result = run_jira_intake_from_initiative({"issue_keys": ["ACME-1", "ACME-2"]})
    assert result["success"] is True
    assert called["keys"] == ["ACME-1", "ACME-2"]
    assert called["board_id"] == 7
    assert "Staged 1 Jira" in result["message"]
