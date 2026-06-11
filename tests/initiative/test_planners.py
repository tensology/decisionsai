"""Tests for R4 planner helpers (prompt selection, date_info, TTS excerpt)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from distr.core.initiative.context import ContextBundle
from distr.core.initiative import planners


def test_planner_scope_for_task_name():
    assert planners.planner_scope_for_task_name("Morning Brief") == "morning"
    assert planners.planner_scope_for_task_name("Day Planner") == "day"
    assert planners.planner_scope_for_task_name("week planner") == "week"
    assert planners.planner_scope_for_task_name("Month Planner") == "month"


def test_build_date_info_week():
    tz = timezone.utc
    # Wednesday 2026-05-06
    local = datetime(2026, 5, 6, 12, 0, tzinfo=tz)
    info = planners.build_date_info("week", local_now=local)
    assert info["period"] == "week"
    assert info["week_start"] == "2026-05-04"
    assert info["week_end"] == "2026-05-10"


def test_build_date_info_morning():
    tz = timezone.utc
    local = datetime(2026, 5, 31, 7, 0, tzinfo=tz)
    info = planners.build_date_info("morning", local_now=local)
    assert info["period"] == "morning"
    assert info["local_iso_date"] == "2026-05-31"


def test_tts_excerpt_from_markdown_strips_headers():
    md = "## Focus\n\nDo the **thing** now.\n\nMore text."
    out = planners.tts_excerpt_from_markdown(md, max_len=200)
    assert "Focus" in out
    assert "thing" in out
    assert "#" not in out
    assert "**" not in out


def test_generate_planner_markdown_uses_scope_specific_system_prompt():
    """Avoid requiring ``litellm`` installed at import time (stub module in sys.modules)."""
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="## Focus\n\nTest plan."))]
        )
    )
    bundle = ContextBundle(current_datetime="2026-05-01T08:00:00Z")
    settings = {
        "conversational_llm_provider": "ollama",
        "conversational_llm_model": "llama3.2",
        "ollama_url": "http://localhost:11434",
    }
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        md, date_info = planners.generate_planner_markdown(
            "day", settings, bundle, "Custom instruction from task row."
        )
    assert "Focus" in md
    assert date_info.get("period") == "day"
    fake_litellm.completion.assert_called_once()
    call_kw = fake_litellm.completion.call_args.kwargs
    messages = call_kw["messages"]
    assert messages[0]["role"] == "system"
    assert "day-planning assistant" in messages[0]["content"].lower()
    assert "Current first" in messages[0]["content"]
    assert "Telegram voice note" in messages[0]["content"]
    user = messages[1]["content"]
    assert "Custom instruction from task row." in user


def test_generate_planner_markdown_omits_custom_temperature_for_o_series():
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="## Standup Triage\n\nUse the live model."))]
        )
    )
    bundle = ContextBundle(current_datetime="2026-05-31T07:00:00Z")
    settings = {
        "conversational_llm_provider": "openai",
        "conversational_llm_model": "o4-mini",
    }
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        planners.generate_planner_markdown(
            "morning", settings, bundle, "Produce a concise morning brief."
        )
    call_kw = fake_litellm.completion.call_args.kwargs
    assert call_kw["model"] == "o4-mini"
    assert "temperature" not in call_kw
    assert call_kw["max_tokens"] == 3072


def test_morning_brief_prompt_demands_specific_next_action():
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="## Standup Triage\n\nFix the stale brief."))]
        )
    )
    bundle = ContextBundle(
        current_datetime="2026-05-31T07:00:00Z",
        work_scan={"proposals": [{"description": "Ticket ready to execute"}]},
    )
    settings = {
        "conversational_llm_provider": "ollama",
        "conversational_llm_model": "llama3.2",
        "ollama_url": "http://localhost:11434",
    }
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        md, date_info = planners.generate_planner_markdown(
            "morning", settings, bundle, "Produce a concise morning brief."
        )
    assert "Standup Triage" in md
    assert date_info.get("period") == "morning"
    messages = fake_litellm.completion.call_args.kwargs["messages"]
    assert "daily check-in orchestrator" in messages[0]["content"].lower()
    assert "Needs Your Call" in messages[0]["content"]
    assert "Today's Outcomes" in messages[0]["content"]
    assert "Telegram" in messages[0]["content"]
    assert "Hermes" not in messages[0]["content"]
    assert "work_scan" in messages[1]["content"]


def test_generate_planner_markdown_falls_back_when_llms_fail():
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(side_effect=RuntimeError("provider unavailable"))
    bundle = ContextBundle(
        current_datetime="2026-05-31T07:00:00Z",
        work_scan={
            "connected_sources": [
                {"label": "Telegram", "connected": True},
                {"label": "ClickUp", "connected": False},
            ],
            "boards": [
                {
                    "name": "Product",
                    "lanes": [
                        {"ticket_count": 2},
                        {"ticket_count": 1},
                    ],
                }
            ],
            "proposals": [{"description": "Promote the ready ticket into Current."}],
        },
    )
    settings = {
        "conversational_llm_provider": "openai",
        "conversational_llm_model": "o4-mini",
    }
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        md, date_info = planners.generate_planner_markdown(
            "morning", settings, bundle, "Produce a concise morning brief."
        )
    assert date_info.get("period") == "morning"
    assert "## Morning Check-in" in md
    assert "Telegram" in md
    assert "ClickUp" in md
    assert "Promote the ready ticket" in md
    assert "Planner LLM fallback was used" in md


def test_fallback_day_plan_is_outcome_driven_and_tts_clean():
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(side_effect=RuntimeError("provider unavailable"))
    bundle = ContextBundle(
        current_datetime="2026-06-11T07:00:00Z",
        work_scan={
            "connected_sources": [
                {"label": "WhatsApp", "connected": True},
                {"label": "Jira", "connected": True},
            ],
            "boards": [
                {
                    "name": "Player1Sport",
                    "lanes": [
                        {"name": "Backlog", "ticket_count": 5},
                        {"name": "Current", "ticket_count": 1},
                    ],
                }
            ],
            "proposals": [
                {
                    "description": "Player1Sport has 5 backlog items that should be promoted into Current."
                }
            ],
        },
        stuck_tasks=[{"title": "RelightSA quote is waiting for a decision."}],
        unfinished_workflows=[{"name": "Merrypak WhatsApp intake"}],
    )
    settings = {
        "conversational_llm_provider": "openai",
        "conversational_llm_model": "o4-mini",
    }

    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        md, date_info = planners.generate_planner_markdown(
            "day", settings, bundle, "Build an outcome-driven daily plan."
        )

    spoken = planners.tts_excerpt_from_markdown(md)
    assert date_info.get("period") == "day"
    assert "## Outcome for Today" in md
    assert "Player1Sport" in md
    assert "Choose the Player1Sport outcome" in md
    assert "item(s)" not in md
    assert "lane(s)" not in md
    assert "ticket(s)" not in md
    assert "(s)" not in spoken


def test_fallback_day_plan_prioritizes_current_before_backlog_for_telegram():
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(side_effect=RuntimeError("provider unavailable"))
    bundle = ContextBundle(
        current_datetime="2026-06-11T07:00:00Z",
        work_scan={
            "connected_sources": [
                {"label": "Telegram", "connected": True},
                {"label": "Gmail", "connected": True},
                {"label": "Slack", "connected": True},
            ],
            "boards": [
                {
                    "name": "Player1Sport",
                    "lanes": [
                        {
                            "name": "Current",
                            "ticket_count": 1,
                            "tickets": [
                                {
                                    "title": "Publish match-day booking flow",
                                    "description_preview": "Finish the booking flow so clubs can reserve player slots today.",
                                    "priority": "high",
                                }
                            ],
                        },
                        {
                            "name": "Backlog",
                            "ticket_count": 2,
                            "tickets": [
                                {"title": "Rewrite old team copy", "priority": "low"},
                                {"title": "Tidy admin filters", "priority": "medium"},
                            ],
                        },
                    ],
                }
            ],
            "proposals": [
                {
                    "description": "Gmail: coach asked whether the booking flow will be ready today.",
                    "payload": {"source": "gmail", "confidence": 0.8},
                },
                {
                    "description": "Slack: player onboarding is blocked until booking is stable.",
                    "payload": {"source": "slack", "confidence": 0.8},
                },
            ],
        },
    )

    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        md, _ = planners.generate_planner_markdown("day", {}, bundle, "Plan my Telegram morning brief.")

    spoken = planners.tts_excerpt_from_markdown(md, max_len=650)
    assert "Publish match-day booking flow" in md
    assert "Rewrite old team copy" not in md.split("## Outcome for Today", 1)[1].split("##", 1)[0]
    assert "Gmail" in md
    assert "Slack" in md
    assert "Telegram" in md
    assert "move " not in spoken.lower()
    assert len(spoken) <= 650


def test_fallback_day_plan_pulls_backlog_outcomes_when_current_is_empty():
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(side_effect=RuntimeError("provider unavailable"))
    bundle = ContextBundle(
        current_datetime="2026-06-11T07:00:00Z",
        work_scan={
            "connected_sources": [{"label": "WhatsApp", "connected": True}],
            "boards": [
                {
                    "name": "RelightSA",
                    "lanes": [
                        {"name": "Current", "ticket_count": 0, "tickets": []},
                        {
                            "name": "Backlog",
                            "ticket_count": 2,
                            "tickets": [
                                {
                                    "title": "Send client quote decision",
                                    "description_preview": "Quote approval is blocking the installation schedule.",
                                    "priority": "critical",
                                },
                                {
                                    "title": "Clean up project notes",
                                    "description_preview": "Low urgency admin cleanup.",
                                    "priority": "low",
                                },
                            ],
                        },
                    ],
                }
            ],
            "proposals": [
                {
                    "description": "WhatsApp: client is waiting for the quote decision.",
                    "payload": {"source": "whatsapp", "confidence": 0.9},
                }
            ],
        },
    )

    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        md, _ = planners.generate_planner_markdown("day", {}, bundle, "Plan my Telegram morning brief.")

    assert "Send client quote decision" in md
    assert "Clean up project notes" not in md.split("## Outcome for Today", 1)[1].split("##", 1)[0]
    assert "Current is empty" in md
    assert "WhatsApp" in md


def test_day_plan_builds_orchestration_actions_for_implied_lane_moves():
    bundle = ContextBundle(
        current_datetime="2026-06-11T07:00:00Z",
        work_scan={
            "boards": [
                {
                    "id": 22,
                    "name": "RelightSA",
                    "lanes": [
                        {"name": "Current", "ticket_count": 0, "tickets": []},
                        {
                            "name": "Backlog",
                            "ticket_count": 2,
                            "tickets": [
                                {
                                    "id": 501,
                                    "title": "Send client quote decision",
                                    "description_preview": "Quote approval is blocking installation.",
                                    "priority": "critical",
                                },
                                {
                                    "id": 502,
                                    "title": "Clean up notes",
                                    "priority": "low",
                                },
                            ],
                        },
                    ],
                }
            ],
        },
    )

    actions = planners.build_planner_orchestration_actions(bundle, scope="day")

    assert actions == [
        {
            "action_type": "ticket_lane_move",
            "description": "Make 'Send client quote decision' current for today's RelightSA outcome.",
            "payload": {
                "board_id": 22,
                "ticket_ids": [501],
                "target_lane": "Current",
                "source": "planner_orchestration",
                "confidence": 0.74,
                "risk_level": "low",
            },
        }
    ]


def test_fallback_week_plan_builds_weekly_arc_not_ticket_dump():
    fake_litellm = MagicMock()
    fake_litellm.AuthenticationError = type("AuthenticationError", (Exception,), {})
    fake_litellm.completion = MagicMock(side_effect=RuntimeError("provider unavailable"))
    bundle = ContextBundle(
        current_datetime="2026-06-11T07:00:00Z",
        work_scan={
            "connected_sources": [{"label": "Gmail", "connected": True}],
            "boards": [
                {
                    "name": "DecisionsAI",
                    "lanes": [
                        {
                            "name": "Current",
                            "ticket_count": 1,
                            "tickets": [
                                {
                                    "title": "Make Hermes weekly planning useful in Telegram",
                                    "description_preview": "The assistant should plan the week from boards and connected messages.",
                                    "priority": "high",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        unfinished_workflows=[{"name": "Daily Plan automation"}],
    )

    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        md, date_info = planners.generate_planner_markdown("week", {}, bundle, "Plan the week.")

    assert date_info.get("period") == "week"
    assert "## Week Outcome" in md
    assert "Make Hermes weekly planning useful in Telegram" in md
    assert "## This Week" in md
    assert "ticket dump" not in md.lower()
