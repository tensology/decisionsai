from __future__ import annotations

from distr.core.hermes_daily_triage import (
    build_daily_triage,
    enqueue_triage_candidates,
    format_triage_markdown,
)
from distr.core.initiative.draft_queue import DraftQueue


def test_build_daily_triage_turns_messages_into_decision_candidates():
    scan = {
        "connected_sources": [
            {"provider": "telegram", "label": "Telegram", "connected": True},
            {"provider": "whatsapp", "label": "WhatsApp", "connected": True},
            {"provider": "clickup", "label": "ClickUp", "connected": False},
        ],
        "messages": {
            "whatsapp": [
                {
                    "id": 10,
                    "sender": "Client",
                    "text_preview": "Can you book talent for the event next week?",
                }
            ]
        },
        "proposals": [
            {
                "action_type": "message_triage",
                "description": "1 WhatsApp message looks like a booking request.",
                "payload": {"source": "whatsapp", "confidence": 0.8},
            }
        ],
    }

    triage = build_daily_triage(work_scan=scan)

    assert triage["mode"] == "daily_standup_triage"
    assert "decision candidate" in triage["summary"]
    assert any(c["action_type"] == "create_ticket" for c in triage["candidates"])
    assert any(s["provider"] == "clickup" and not s["connected"] for s in triage["source_health"])


def test_format_triage_markdown_asks_for_decisions():
    triage = build_daily_triage(
        work_scan={
            "connected_sources": [{"provider": "telegram", "label": "Telegram", "connected": True}],
            "proposals": [
                {
                    "action_type": "ticket_lane_move",
                    "description": "Board has 2 backlog items ready.",
                    "payload": {"source": "initiative_work_scan", "ticket_ids": [1, 2]},
                }
            ],
        }
    )

    markdown = format_triage_markdown(triage)

    assert "## Standup Triage" in markdown
    assert "## Decisions I Need From You" in markdown
    assert "Should I promote" in markdown


def test_enqueue_triage_candidates_dedupes(tmp_path):
    triage = build_daily_triage(
        work_scan={
            "proposals": [
                {
                    "action_type": "message_triage",
                    "description": "Telegram message may need a ticket.",
                    "payload": {"source": "telegram"},
                }
            ]
        }
    )
    queue = DraftQueue(path=str(tmp_path / "drafts.json"))

    assert enqueue_triage_candidates(queue, triage["candidates"], limit=3) == 1
    assert enqueue_triage_candidates(queue, triage["candidates"], limit=3) == 0

    entries = queue.get_all()
    assert len(entries) == 1
    assert entries[0].action_type == "hermes_triage_candidate"
    assert entries[0].execute_payload["kind"] == "hermes_triage_ack"
