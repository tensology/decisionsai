from distr.core.services.context_window import context_window_for_model


def test_gpt35_does_not_match_gpt5_window():
    assert context_window_for_model("openai", "gpt-3.5-turbo") == 16_385


def test_gpt5_uses_recommendations_before_static_fallback():
    # Cached recommendations list gpt-5 @ 128k for OpenAI tool_calling.
    assert context_window_for_model("openai", "gpt-5") == 128_000


def test_gpt5_coder_does_not_shrink_base_gpt5_lookup():
    assert context_window_for_model("openai", "gpt-5-coder") == 65_536


def test_implausibly_small_recommendation_does_not_override_known_static(monkeypatch):
    monkeypatch.setattr(
        "distr.core.services.context_window.load_recommendations",
        lambda: {
            "providers": {
                "anthropic": {
                    "categories": {
                        "tool_calling": {
                            "paid": {
                                "model_id": "claude-3-sonnet-4-7",
                                "context_window": 2_048,
                            }
                        }
                    }
                }
            }
        },
    )

    assert context_window_for_model("anthropic", "claude-3-sonnet-4-7") == 200_000


def test_openrouter_vendor_prefix_resolves_underlying_model_window(monkeypatch):
    monkeypatch.setattr(
        "distr.core.services.context_window.load_recommendations",
        lambda: {"providers": {}},
    )

    assert context_window_for_model("openrouter", "anthropic/claude-3.5-sonnet") == 200_000


def test_openrouter_parser_preserves_provider_context_length():
    from distr.gui.utils.get_openrouter_models import _parse_openrouter_model

    parsed = _parse_openrouter_model(
        {
            "id": "anthropic/claude-3.5-sonnet",
            "name": "Claude 3.5 Sonnet",
            "context_length": 200_000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "supported_parameters": ["tools"],
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        }
    )

    assert parsed["context_window"] == 200_000
