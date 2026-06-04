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
    assert "need your call" in triage["summary"]
    assert any(c["action_type"] == "create_ticket" for c in triage["candidates"])
    assert triage["buckets"]["make_ticket"]
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

    assert "## Quick Check-in" in markdown
    assert "## Intake Buckets" in markdown
    assert "## Needs Your Call" in markdown
    assert "Want me to move these forward" in markdown
    assert "Hermes" not in markdown


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


def test_build_daily_triage_buckets_reply_candidates():
    triage = build_daily_triage(
        work_scan={
            "proposals": [
                {
                    "action_type": "message_triage",
                    "description": "You just got a WhatsApp message from Maya.",
                    "payload": {
                        "source": "whatsapp",
                        "latest_sender": "Maya",
                        "latest_preview": "hey are you around?",
                    },
                }
            ]
        }
    )

    assert triage["candidates"][0]["action_type"] == "draft_reply"
    assert triage["buckets"]["needs_reply"][0]["source"] == "whatsapp"


def test_hermes_triage_ack_executes_whatsapp_ticket_when_linked(monkeypatch):
    from distr.core.initiative import draft_execute

    created = {}

    def fake_create(*, board_id, message_ids, candidate):
        created["board_id"] = board_id
        created["message_ids"] = message_ids
        return {"status": "created_ticket", "ticket_id": 123}

    events = []
    monkeypatch.setattr(draft_execute, "_create_whatsapp_snapshot_ticket", fake_create)
    monkeypatch.setattr("distr.core.hermes.emit_event", lambda **kw: events.append(kw))

    draft_execute.run_execute_payload({
        "kind": "hermes_triage_ack",
        "candidate": {
            "source": "whatsapp",
            "action_type": "create_ticket",
            "question": "Create ticket?",
            "payload": {
                "proposal": {
                    "payload": {
                        "linked_board_id": 7,
                        "message_ids": [42],
                    }
                }
            },
        },
    })

    assert created == {"board_id": 7, "message_ids": [42]}
    assert events[0]["payload"]["execution_result"]["status"] == "created_ticket"
