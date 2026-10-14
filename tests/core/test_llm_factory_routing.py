from distr.core.llm_factory import resolve_llm_candidates, resolve_settings_keys


def test_workflow_defaults_to_coding_before_conversational():
    settings = {
        "coding_llm_provider": "OpenAI",
        "coding_llm_model": "gpt-code",
        "conversational_llm_provider": "Anthropic",
        "conversational_llm_model": "claude-chat",
    }

    assert resolve_settings_keys(settings) == ("OpenAI", "gpt-code")
    assert resolve_llm_candidates(settings)[:2] == [
        ("openai", "gpt-code"),
        ("anthropic", "claude-chat"),
    ]


def test_explicit_workflow_route_stays_ahead_of_coding():
    settings = {
        "workflow_llm_provider": "KiloCode",
        "workflow_llm_model": "kilo/auto",
        "coding_llm_provider": "OpenAI",
        "coding_llm_model": "gpt-code",
    }

    assert resolve_settings_keys(settings) == ("KiloCode", "kilo/auto")
    assert resolve_llm_candidates(settings)[:2] == [
        ("kilocode", "kilo/auto"),
        ("openai", "gpt-code"),
    ]
