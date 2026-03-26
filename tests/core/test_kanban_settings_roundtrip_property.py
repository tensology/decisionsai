# Feature: kanban-cli-settings-restructure, Property 3: Global kanban settings round-trip persistence
# Validates: Requirements 1.4, 2.4, 4.5, 5.2, 6.2, 8.8
"""Property-based test: for any valid kanban settings configuration
(agent_enabled, frequency, time, hours, days, monthly_day, source_lane,
done_lane, LLM provider/model for all three roles, CLI tool, CLI auth),
saving the configuration via save_settings_to_db() and then loading it
via load_settings_from_db() should produce values equivalent to the
original configuration for all kanban-prefixed keys."""

import json
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

# Import the module under test eagerly so patching works
import distr.core.settings as settings_mod


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_safe_text = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_"),
    min_size=0,
    max_size=30,
)

_provider_strategy = st.sampled_from(["", "Ollama", "OpenAI", "Anthropic", "Groq", "OpenRouter"])

_model_strategy = st.sampled_from(["", "qwen3:8b", "gpt-4o", "claude-3-sonnet", "llama3:8b"])

_frequency_strategy = st.sampled_from(["hourly", "daily", "weekly", "fortnightly", "monthly"])

_time_strategy = st.from_regex(r"[0-2][0-9]:[0-5][0-9]", fullmatch=True).filter(
    lambda t: int(t.split(":")[0]) <= 23
)

_hours_strategy = st.lists(
    st.integers(min_value=0, max_value=23), min_size=0, max_size=10, unique=True
).map(sorted)

_days_strategy = st.lists(
    st.integers(min_value=0, max_value=6), min_size=0, max_size=7, unique=True
).map(sorted)

_monthly_day_strategy = st.integers(min_value=1, max_value=28)

_cli_tool_strategy = st.sampled_from(["", "cursor", "gemini", "claude", "kiro"])


@st.composite
def kanban_settings_strategy(draw):
    """Generate a complete valid kanban settings configuration."""
    return {
        "kanban_agent_enabled": draw(st.booleans()),
        "kanban_agent_frequency": draw(_frequency_strategy),
        "kanban_agent_time": draw(_time_strategy),
        "kanban_agent_hours": json.dumps(draw(_hours_strategy)),
        "kanban_agent_days": json.dumps(draw(_days_strategy)),
        "kanban_agent_monthly_day": draw(_monthly_day_strategy),
        "kanban_agent_source_lane": draw(_safe_text),
        "kanban_agent_done_lane": draw(_safe_text),
        "kanban_agent_orchestrator_provider": draw(_provider_strategy),
        "kanban_agent_orchestrator_model": draw(_model_strategy),
        "kanban_agent_coder_provider": draw(_provider_strategy),
        "kanban_agent_coder_model": draw(_model_strategy),
        "kanban_agent_sub_provider": draw(_provider_strategy),
        "kanban_agent_sub_model": draw(_model_strategy),
        "kanban_cli_tool": draw(_cli_tool_strategy),
        "kanban_cli_auth": draw(_safe_text),
    }


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

KANBAN_KEYS = [
    "kanban_agent_enabled",
    "kanban_agent_frequency",
    "kanban_agent_time",
    "kanban_agent_hours",
    "kanban_agent_days",
    "kanban_agent_monthly_day",
    "kanban_agent_source_lane",
    "kanban_agent_done_lane",
    "kanban_agent_orchestrator_provider",
    "kanban_agent_orchestrator_model",
    "kanban_agent_coder_provider",
    "kanban_agent_coder_model",
    "kanban_agent_sub_provider",
    "kanban_agent_sub_model",
    "kanban_cli_tool",
    "kanban_cli_auth",
]


@settings(max_examples=100)
@given(kanban_cfg=kanban_settings_strategy())
def test_kanban_settings_round_trip(kanban_cfg: dict) -> None:
    """**Validates: Requirements 1.4, 2.4, 4.5, 5.2, 6.2, 8.8**

    For any valid kanban settings configuration, saving via
    save_settings_to_db() and loading via load_settings_from_db()
    should produce equivalent values for all kanban-prefixed keys."""

    # In-memory store simulating the database
    store: dict = {}

    def fake_core_save(settings_dict):
        store.update(settings_dict)

    def fake_core_load():
        return dict(store)

    with patch.object(settings_mod, "core_save_settings", side_effect=fake_core_save), \
         patch.object(settings_mod, "core_load_settings", side_effect=fake_core_load):

        # Save the generated kanban settings
        settings_mod.save_settings_to_db(kanban_cfg)

        # Load them back
        loaded = settings_mod.load_settings_from_db()

        # Verify all kanban-prefixed keys round-trip correctly
        for key in KANBAN_KEYS:
            expected = kanban_cfg[key]
            actual = loaded.get(key)
            assert actual == expected, (
                f"Round-trip mismatch for {key!r}: "
                f"saved {expected!r}, loaded {actual!r}"
            )
