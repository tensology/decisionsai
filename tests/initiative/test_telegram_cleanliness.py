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


def test_surface_draft_queue_does_not_pollute_unrelated_chat(tmp_path):
    svc = _service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    svc._draft_queue.add(DraftEntry(
        id="hermes-7",
        action_type="orchestrator_triage_candidate",
        description="Player1Sport (jira) has 53 fetched item(s) available for review.",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    ))
    chat_manager = MagicMock()
    chat_manager.get_current_chat.return_value = 42
    chat_manager.get_chat_title.return_value = "Board header and list view buttons"
    chat_manager.get_chat_history.return_value = [
        {"role": "user", "content": "change board buttons/list view"},
    ]
    svc.chat_manager = chat_manager

    svc._surface_draft_queue(42)

    chat_manager.add_assistant_message.assert_not_called()


def test_surface_draft_queue_allows_explicit_initiative_chat(tmp_path):
    svc = _service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    svc._draft_queue.add(DraftEntry(
        id="hermes-7",
        action_type="orchestrator_triage_candidate",
        description="Player1Sport (jira) has 53 fetched item(s) available for review.",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    ))
    chat_manager = MagicMock()
    chat_manager.get_current_chat.return_value = 42
    chat_manager.get_chat_title.return_value = "Initiative approvals"
    chat_manager.get_chat_history.return_value = [
        {"role": "user", "content": "what needs approval"},
    ]
    svc.chat_manager = chat_manager

    svc._surface_draft_queue(42)

    sent = chat_manager.add_assistant_message.call_args.args[1]
    assert "Pending approval:" in sent
    assert "hermes-7" in sent


def test_surface_draft_queue_expires_stale_approvals_before_chat_surface(tmp_path):
    svc = _service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    svc._draft_queue.add(DraftEntry(
        id="hermes-old",
        action_type="orchestrator_triage_candidate",
        description="Old Jira review should not resurface.",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=(now - timedelta(days=3)).isoformat(),
        expires_at=(now - timedelta(minutes=1)).isoformat(),
    ))
    chat_manager = MagicMock()
    chat_manager.get_current_chat.return_value = 42
    chat_manager.get_chat_title.return_value = "Initiative approvals"
    chat_manager.get_chat_history.return_value = [
        {"role": "user", "content": "what needs approval"},
    ]
    svc.chat_manager = chat_manager

    svc._surface_draft_queue(42)

    chat_manager.add_assistant_message.assert_not_called()
    assert svc._draft_queue.get_by_id("hermes-old") is None


def test_orchestrator_triage_reply_approves_first_candidate(monkeypatch, tmp_path):
    from distr.core.integrations.telegram.messages import TelegramMessagesMixin

    now = datetime.now(tz=timezone.utc)
    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    queue.add(DraftEntry(
        id="hermes-one",
        action_type="orchestrator_triage_candidate",
        description="Roland sent a WhatsApp that looks like a booking. Should I create a ticket?",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        execute_payload={"kind": "orchestrator_triage_ack", "candidate": {"id": "one"}},
    ))
    queue.add(DraftEntry(
        id="hermes-two",
        action_type="orchestrator_triage_candidate",
        description="Promote Player1Sport backlog items?",
        draft="Decision draft",
        reason="Hermes standup",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        execute_payload={"kind": "orchestrator_triage_ack", "candidate": {"id": "two"}},
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


def test_orchestrator_triage_reply_approves_all_with_clean_plural_wording(monkeypatch, tmp_path):
    from distr.core.integrations.telegram.messages import TelegramMessagesMixin

    now = datetime.now(tz=timezone.utc)
    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    for draft_id in ("hermes-one", "hermes-two"):
        queue.add(DraftEntry(
            id=draft_id,
            action_type="orchestrator_triage_candidate",
            description="Create a ticket?",
            draft="Decision draft",
            reason="Hermes standup",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
            execute_payload={"kind": "orchestrator_triage_ack", "candidate": {"id": draft_id}},
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

    handled = handler._handle_initiative_draft_command("approve all")

    assert handled is True
    sent = handler.send_to_telegram.call_args.args[0]
    assert sent == "Approved 2 pending items."
    assert "item(s)" not in sent


def test_orchestrator_triage_reply_can_show_numbered_decisions(monkeypatch, tmp_path):
    from distr.core.integrations.telegram.messages import TelegramMessagesMixin

    now = datetime.now(tz=timezone.utc)
    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    queue.add(DraftEntry(
        id="hermes-one",
        action_type="orchestrator_triage_candidate",
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

    handled = handler._handle_initiative_draft_command("show orchestrator decisions")

    assert handled is True
    sent = handler.send_to_telegram.call_args.args[0]
    assert "Pending items:" in sent
    assert "Hermes" not in sent
    assert "1. Create a ticket from Telegram?" in sent
