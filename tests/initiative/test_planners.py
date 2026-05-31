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
    assert "daily standup triage orchestrator" in messages[0]["content"].lower()
    assert "Decisions I Need From You" in messages[0]["content"]
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
    assert "## Standup Triage" in md
    assert "Telegram" in md
    assert "ClickUp" in md
    assert "Promote the ready ticket" in md
    assert "Planner LLM fallback was used" in md
