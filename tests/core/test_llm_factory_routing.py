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


def test_medium_complexity_tier_stays_ahead_of_coding():
    settings = {
        "project_cli_medium_backend": "pi",
        "project_cli_medium_model_provider": "kilocode",
        "project_cli_medium_model": "kilo/auto",
        "coding_llm_provider": "OpenAI",
        "coding_llm_model": "gpt-code",
    }

    assert resolve_settings_keys(settings) == ("KiloCode", "kilo/auto")
    assert resolve_llm_candidates(settings)[:2] == [
        ("kilocode", "kilo/auto"),
        ("openai", "gpt-code"),
    ]


def test_complexity_route_mapping():
    from distr.core.llm_factory import complexity_route_to_llm, resolve_complexity_llm_candidates
    assert complexity_route_to_llm("cursor", "", "auto") is None
    assert complexity_route_to_llm("cursor", "cursor", "gpt-5") is None
    assert complexity_route_to_llm("pi", "openrouter", "minimax/minimax-m3.1-flash") == ("OpenRouter", "minimax/minimax-m3.1-flash")
    assert complexity_route_to_llm("pi", "", "deepseek-v4.1-flash:cloud") == ("Ollama", "deepseek-v4.1-flash:cloud")
    assert complexity_route_to_llm("pi", "", "deepseek/deepseek-v4.1-flash") == ("OpenRouter", "deepseek/deepseek-v4.1-flash")
    assert complexity_route_to_llm("ollama", "", "qwen3:8b") == ("Ollama", "qwen3:8b")
    settings = {
        "project_cli_low_backend": "cursor", "project_cli_low_model": "auto",
        "project_cli_low_fallback_backend": "pi", "project_cli_low_fallback_model": "deepseek-v4.1-flash:cloud",
    }
    assert resolve_complexity_llm_candidates(settings, "low") == [("Ollama", "deepseek-v4.1-flash:cloud")]
    assert resolve_complexity_llm_candidates(settings, "bogus") == []


def test_workflow_llm_keys_are_ignored():
    settings = {"workflow_llm_provider": "OpenAI", "workflow_llm_model": "gpt-5",
                "coding_llm_provider": "Ollama", "coding_llm_model": "qwen3-coder"}
    assert resolve_settings_keys(settings) == ("Ollama", "qwen3-coder")
