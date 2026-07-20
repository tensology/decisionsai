from distr.core.agent.services.tts.kokoro_descriptor import (
    _phonemizer_safe_text,
    _split_text_for_kokoro,
)


def test_phonemizer_safe_text_collapses_multiline_and_control_separators():
    text = "Step started.\n\nUse model\u2028Ornith.\x00 Continue."

    cleaned = _phonemizer_safe_text(text)

    assert cleaned == "Step started. Use model Ornith. Continue."
    assert "\n" not in cleaned
    assert "\u2028" not in cleaned
    assert "\x00" not in cleaned


def test_kokoro_retry_sized_chunks_remain_single_line():
    text = _phonemizer_safe_text(("A useful workflow update, " * 30) + "done.")

    chunks = _split_text_for_kokoro(text, max_chars=140)

    assert len(chunks) > 1
    assert all(len(chunk) <= 140 and "\n" not in chunk for chunk in chunks)
