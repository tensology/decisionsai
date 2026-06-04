from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from distr.core.initiative.draft_queue import DraftEntry, DraftQueue
from distr.core.initiative.proposed_action import ProposedAction
from distr.core.initiative.service import InitiativeService
from distr.core.initiative.tiers import PermissionTier
from distr.core.integrations.telegram.sender import _audit_outbound_telegram_text


def _service(tmp_path):
    svc = InitiativeService.__new__(InitiativeService)
    svc.telegram_manager = MagicMock()
    svc.telegram_manager.telegram_user_id = 123
    svc.chat_manager = None
    svc._draft_queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    return svc


def test_initiative_approval_telegram_is_conversational_and_has_no_payload(tmp_path):
    svc = _service(tmp_path)
    action = ProposedAction(
        action_type="ticket_lane_move",
        description="My Board has 2 backlog item(s) that could be promoted into Current.",
        payload={
            "board_id": 1,
            "ticket_ids": [7, 12],
            "target_lane": "Current",
            "source": "initiative_work_scan",
            "confidence": 0.72,
            "risk_level": "medium",
        },
        draft='My Board has 2 backlog item(s).\n\nPayload: {"board_id": 1}',
    )

    svc._draft_and_ask(
        action,
        {"initiative_allow_telegram": True},
        tier=PermissionTier.APPROVE,
    )

    sent = svc.telegram_manager.send_to_telegram.call_args.kwargs["text"]
    assert "I spotted a board change" in sent
    assert "Payload:" not in sent
    assert "Draft:" not in sent
    assert "[Initiative]" not in sent
    assert "[APPROVE]" not in sent


def test_duplicate_pending_initiative_draft_does_not_notify_again(tmp_path):
    svc = _service(tmp_path)
    action = ProposedAction(
        action_type="ticket_lane_move",
        description="Promote two backlog items.",
        payload={"board_id": 1, "ticket_ids": [7, 12], "target_lane": "Current"},
    )

    settings = {"initiative_allow_telegram": True}
    svc._draft_and_ask(action, settings, tier=PermissionTier.APPROVE)
    svc._draft_and_ask(action, settings, tier=PermissionTier.APPROVE)

    assert len(svc._draft_queue.get_all()) == 1
    assert svc.telegram_manager.send_to_telegram.call_count == 1


def test_telegram_sender_audit_removes_raw_initiative_blocks():
    text = (
        "[Initiative][APPROVE] I'd like to: Move tickets.\n\n"
        "Draft:\nMove tickets.\n\n"
        'Payload: {"board_id": 1, "ticket_ids": [7, 12]}\n'
        "Approve or reject this in the app."
    )

    clean = _audit_outbound_telegram_text(text)

    assert "Move tickets" in clean
    assert "Payload:" not in clean
    assert "Draft:" not in clean
    assert "[Initiative]" not in clean


def test_old_style_readout_is_compact(monkeypatch, tmp_path):
    from distr.core.integrations.telegram.messages import TelegramMessagesMixin

    now = datetime.now(tz=timezone.utc)
    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    queue.add(DraftEntry(
        id="abcdef123456",
        action_type="ticket_lane_move",
        description="Move two backlog tickets into Current.",
        draft='Move two tickets.\n\nPayload: {"board_id": 1}',
        reason="approval",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    ))
    monkeypatch.setattr(
        "distr.core.initiative.draft_queue.DraftQueue",
        lambda: queue,
        raising=False,
    )

    handler = TelegramMessagesMixin()
    handler.send_to_telegram = MagicMock()

    handled = handler._handle_initiative_draft_command("what needs approval")

    assert handled is True
    sent = handler.send_to_telegram.call_args.args[0]
    assert "Pending approval:" in sent
    assert "Payload:" not in sent
    assert "Draft:" not in sent


def test_hermes_triage_reply_approves_first_candidate(monkeypatch, tmp_path):
    from distr.core.integrations.telegram.messages import TelegramMessagesMixin

    now = datetime.now(tz=timezone.utc)
    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    queue.add(DraftEntry(
        id="hermes-one",
        action_type="hermes_triage_candidate",
        description="Roland sent a WhatsApp that looks like a booking. Should I create a ticket?",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        execute_payload={"kind": "hermes_triage_ack", "candidate": {"id": "one"}},
    ))
    queue.add(DraftEntry(
        id="hermes-two",
        action_type="hermes_triage_candidate",
        description="Promote Player1Sport backlog items?",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        execute_payload={"kind": "hermes_triage_ack", "candidate": {"id": "two"}},
    ))
    monkeypatch.setattr(
        "distr.core.initiative.draft_queue.DraftQueue",
        lambda: queue,
        raising=False,
    )
    monkeypatch.setattr(
        "distr.core.initiative.draft_execute.approve_draft_in_queue",
        lambda q, draft_id: q.remove(draft_id),
        raising=False,
    )

    handler = TelegramMessagesMixin()
    handler.send_to_telegram = MagicMock()

    handled = handler._handle_initiative_draft_command("approve")

    assert handled is True
    assert queue.get_by_id("hermes-one") is None
    assert queue.get_by_id("hermes-two") is not None
    sent = handler.send_to_telegram.call_args.args[0]
    assert "Approved:" in sent
    assert "Roland" in sent


def test_hermes_triage_reply_can_show_numbered_decisions(monkeypatch, tmp_path):
    from distr.core.integrations.telegram.messages import TelegramMessagesMixin

    now = datetime.now(tz=timezone.utc)
    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    queue.add(DraftEntry(
        id="hermes-one",
        action_type="hermes_triage_candidate",
        description="Create a ticket from Telegram?",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    ))
    monkeypatch.setattr(
        "distr.core.initiative.draft_queue.DraftQueue",
        lambda: queue,
        raising=False,
    )

    handler = TelegramMessagesMixin()
    handler.send_to_telegram = MagicMock()

    handled = handler._handle_initiative_draft_command("show hermes decisions")

    assert handled is True
    sent = handler.send_to_telegram.call_args.args[0]
    assert "Pending items:" in sent
    assert "Hermes" not in sent
    assert "1. Create a ticket from Telegram?" in sent
