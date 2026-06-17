from distr.core.llm_errors import (
    LLMModelError,
    extract_provider_error_message,
    format_model_error,
    format_model_error_for_tts,
    is_formatted_model_error_message,
)


def test_extracts_openai_compatible_error_message():
    err = (
        "Error code: 404 - {'error': {'message': 'Ling-2.6-1T is no longer "
        "available as a free model.', 'code': 404}}"
    )

    assert extract_provider_error_message(err) == "Ling-2.6-1T is no longer available as a free model."


def test_formats_unavailable_model_error_with_provider_and_fix():
    msg = format_model_error(
        "Error code: 404 - {'error': {'message': 'model is no longer available'}}",
        provider="OpenRouter",
        model="Ling-2.6-1T",
        operation="compose a ticket",
    )

    assert "Model is unavailable or not supported" in msg
    assert "OpenRouter" in msg
    assert "Ling-2.6-1T" in msg
    assert "Settings -> LLMs" in msg


def test_llm_model_error_wraps_original_exception_message():
    wrapped = LLMModelError(
        RuntimeError("insufficient_quota"),
        provider="OpenAI",
        model="gpt-test",
    )

    assert "quota or billing" in str(wrapped)
    assert wrapped.provider == "OpenAI"
    assert wrapped.model == "gpt-test"


def test_rate_limit_tts_message_is_human_not_technical():
    spoken = format_model_error_for_tts(
        "Error code: 429 - {'status': 429, 'title': 'Too Many Requests'}",
        provider="NVIDIA",
        model="deepseek-ai/deepseek-v4-flash",
    )
    assert "429" not in spoken
    assert "Provider:" not in spoken
    assert "rate limit" in spoken.lower()


def test_formatted_model_error_detector():
    chat = format_model_error(
        "Error code: 429 - {'status': 429}",
        provider="NVIDIA",
        model="deepseek-ai/deepseek-v4-flash",
    )
    assert is_formatted_model_error_message(chat)
    assert not is_formatted_model_error_message("Try again in a minute.")

