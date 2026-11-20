from types import SimpleNamespace

from distr.core.initiative.service import (
    InitiativeService,
    _throwaway_action_text,
)


def test_throwaway_action_text_detects_e2e_boards():
    assert _throwaway_action_text("E2E UX test 3 (safe to delete) has 2 backlog items")
    assert _throwaway_action_text("Waiting on your approval", "throwaway local test")
    assert not _throwaway_action_text("Spoortrack has 3 backlog items that should move into Current.")


def test_log_to_chat_routes_dev_thread_nags_and_drops_throwaway_from_main(monkeypatch):
    posted = []

    class FakeManager:
        def get_current_chat(self):
            return 1381

    service = InitiativeService.__new__(InitiativeService)
    service.chat_manager = FakeManager()
    service._append_chat_notice = lambda chat_id, message, hidden=False: posted.append(
        {"chat_id": chat_id, "message": message, "hidden": hidden}
    ) or 1

    monkeypatch.setattr(
        "distr.core.initiative.service._development_thread_for_action",
        lambda action: 42 if action else None,
    )
    monkeypatch.setattr("distr.core.initiative.service._is_development_chat", lambda chat_id: False)
    monkeypatch.setattr(
        "distr.core.initiative.service._initiative_update_text",
        lambda message: message,
    )
    monkeypatch.setattr(
        "distr.core.orchestrator_user_text.humanize_orchestrator_update",
        lambda text, drop_throwaway=False, **kwargs: text,
    )

    action = SimpleNamespace(
        description="E2E UX test 3 (safe to delete) has 2 backlog items that should move into Current.",
        payload={"ticket_ids": [1], "board_id": 9},
    )
    service._log_to_chat("Waiting on your approval: " + action.description, {}, action=action)
    assert posted == [{
        "chat_id": 42,
        "message": "Waiting on your approval: " + action.description,
        "hidden": False,
    }]

    posted.clear()
    monkeypatch.setattr("distr.core.initiative.service._development_thread_for_action", lambda action: None)
    service._log_to_chat("Waiting on your approval: " + action.description, {}, action=action)
    assert posted == []

    service._log_to_chat(
        "Waiting on your approval: Spoortrack has 3 backlog items that should move into Current.",
        {},
        action=SimpleNamespace(description="Spoortrack has 3 backlog items that should move into Current.", payload={}),
    )
    assert len(posted) == 1
    assert posted[0]["chat_id"] == 1381
    assert posted[0]["hidden"] is True
