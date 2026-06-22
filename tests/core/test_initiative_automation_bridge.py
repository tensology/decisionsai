"""Initiative-to-Automations recommendation bridge tests."""

from __future__ import annotations

import pytest


def test_recommends_whatsapp_ticket_automation_for_linked_whatsapp_work(monkeypatch):
    from distr.core.initiative.automation_recommendations import recommend_automation_from_work_scan

    monkeypatch.setattr(
        "distr.core.initiative.automation_recommendations.find_automations_by_preset",
        lambda preset_id, active_only=True: [],
    )

    action = recommend_automation_from_work_scan(
        {
            "messages": {"whatsapp": [{"id": 1, "text_preview": "Please quote this", "linked_board_id": 12}]},
            "proposals": [
                {
                    "action_type": "message_triage",
                    "payload": {"source": "whatsapp", "linked_board_id": 12},
                }
            ],
        }
    )

    assert action is not None
    assert action.action_type == "automation_recommendation"
    assert action.payload["preset_id"] == "whatsapp_to_tickets"
    assert "WhatsApp" in action.description


def test_recommendation_is_suppressed_when_active_preset_exists(monkeypatch):
    from distr.core.initiative.automation_recommendations import recommend_automation_from_work_scan

    monkeypatch.setattr(
        "distr.core.initiative.automation_recommendations.find_automations_by_preset",
        lambda preset_id, active_only=True: [{"id": "auto_1", "preset_id": preset_id, "status": "active"}],
    )

    action = recommend_automation_from_work_scan(
        {"messages": {"email": [{"id": "m1", "subject": "Need a decision"}]}, "proposals": []}
    )

    assert action is None


def test_automation_preset_install_approval_creates_automation(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'initiative_bridge.sqlite3'}")

    from distr.core.db import init_db

    init_db()

    from distr.core.automation_resolver import find_automations_by_preset
    from distr.core.initiative.draft_execute import run_execute_payload

    run_execute_payload(
        {
            "kind": "automation_preset_install",
            "preset_id": "email_action_items",
            "source": "initiative",
        }
    )

    rows = find_automations_by_preset("email_action_items")
    assert len(rows) == 1
    assert rows[0]["name"] == "Email action items"
    assert rows[0]["status"] == "active"
    assert rows[0]["schedule"]["kind"] == "daily"


def test_invalid_automation_preset_install_raises_without_creating(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'initiative_bridge_invalid.sqlite3'}")

    from distr.core.db import init_db

    init_db()

    from distr.core.automation.store import list_automations
    from distr.core.initiative.draft_execute import run_execute_payload

    before_count = len(list_automations())
    with pytest.raises(ValueError, match="unknown automation preset"):
        run_execute_payload({"kind": "automation_preset_install", "preset_id": "missing"})

    assert len(list_automations()) == before_count


def test_daily_plan_prompt_queues_executable_automation_install(monkeypatch):
    from distr.core.initiative.daily_plan_prompt import maybe_suggest_daily_plan_automation

    queued = []

    class Service:
        def _get_level(self, settings):
            return "assist"

        def _log_to_chat(self, message):
            pass

        def _draft_and_ask(self, action, settings, tier=None):
            queued.append(action)

    monkeypatch.setattr("distr.core.automation_resolver.has_active_daily_plan_automation", lambda: False)
    monkeypatch.setattr("distr.core.engagement_gates.daily_plan_prompt_due", lambda: True)
    monkeypatch.setattr("distr.core.engagement_gates.mark_daily_plan_prompt_sent", lambda: None)

    maybe_suggest_daily_plan_automation(Service(), {"initiative_allow_telegram": False})

    assert len(queued) == 1
    assert queued[0].action_type == "automation_recommendation"
    assert queued[0].payload["preset_id"] == "daily_plan"
