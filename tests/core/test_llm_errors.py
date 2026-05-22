from distr.core.llm_errors import (
    LLMModelError,
    extract_provider_error_message,
    format_model_error,
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

