from distr.core.services.context_window import context_window_for_model


def test_gpt35_does_not_match_gpt5_window():
    assert context_window_for_model("openai", "gpt-3.5-turbo") == 16_385


def test_gpt5_uses_recommendations_before_static_fallback():
    # Cached recommendations list gpt-5 @ 128k for OpenAI tool_calling.
    assert context_window_for_model("openai", "gpt-5") == 128_000


def test_gpt5_coder_does_not_shrink_base_gpt5_lookup():
    assert context_window_for_model("openai", "gpt-5-coder") == 65_536
