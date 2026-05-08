"""Tests for Pi / CLI completion notes on Kanban tickets."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.kanban.ticket_writeback import apply_pi_cli_note_to_ticket, append_pi_cli_summary_to_ticket
from distr.core.pi_rpc import PiMessage, PiRpcSession


def test_apply_pi_cli_note_to_empty_description():
    ticket = SimpleNamespace(description=None, workflow_status=None)
    apply_pi_cli_note_to_ticket(ticket, "Done.", outcome_status="completed")

    assert "[Pi CLI] Status: completed" in (ticket.description or "")
    assert "Done." in ticket.description
    assert ticket.workflow_status == "completed"


def test_apply_pi_cli_note_caps_summary_and_description():
    ticket = SimpleNamespace(description="Prior", workflow_status=None)
    long_body = "z" * 5000
    apply_pi_cli_note_to_ticket(ticket, long_body, outcome_status="failed", max_summary_chars=100, max_desc_len=500)

    assert ticket.workflow_status == "failed"
    assert len(ticket.description) <= 500
    assert "..." in ticket.description


def test_infer_pi_turn_outcome_failed_when_recent_tool_error():
    rpc = PiRpcSession(1, "/tmp")
    rpc.messages = [
        PiMessage(role="user", content="run"),
        PiMessage(role="assistant", content="trying"),
        PiMessage(role="tool_result", tool_result="oops", is_error=True),
    ]
    assert rpc._infer_pi_turn_outcome() == "failed"


def test_agent_end_ticket_writeback_calls_append(monkeypatch):
    recorded = []

    def fake_append(ticket_id, summary, outcome_status="completed"):
        recorded.append((ticket_id, summary, outcome_status))

    monkeypatch.setattr(
        "distr.core.kanban.ticket_writeback.append_pi_cli_summary_to_ticket",
        fake_append,
    )

    rpc = PiRpcSession(1, "/tmp")
    rpc.messages.append(PiMessage(role="user", content="fix bug"))
    rpc.messages.append(PiMessage(role="assistant", content="patched"))
    rpc._ticket_writeback_queue.append(7)
    rpc._process_event({"type": "agent_end", "messages": []})

    assert len(recorded) == 1
    assert recorded[0][0] == 7
    assert recorded[0][2] == "completed"
    assert "Result:" in recorded[0][1]


def test_append_pi_cli_summary_writes_ticket_audit_entry(monkeypatch):
    fake_ticket = SimpleNamespace(id=123, description="", workflow_status=None, board_id=None)
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = fake_ticket

    class _Ctx:
        def __enter__(self):
            return fake_db

        def __exit__(self, exc_type, exc, tb):
            return False

    captured = {}

    def fake_append_ticket_audit_entry(db, **kwargs):
        captured["db"] = db
        captured["kwargs"] = kwargs

    monkeypatch.setattr("distr.core.db.get_session", lambda: _Ctx())
    monkeypatch.setattr(
        "distr.core.kanban.ticket_writeback.append_ticket_audit_entry",
        fake_append_ticket_audit_entry,
    )

    append_pi_cli_summary_to_ticket(ticket_id=123, summary="CLI summary", outcome_status="failed")

    assert captured["db"] is fake_db
    assert captured["kwargs"]["ticket_id"] == 123
    assert captured["kwargs"]["execution_lane"] == "cli"
    assert captured["kwargs"]["status"] == "failed"
    assert captured["kwargs"]["final_verdict"] == "needs_changes"
