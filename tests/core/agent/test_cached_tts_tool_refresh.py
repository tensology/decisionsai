from distr.core.agent.services.llm.core_mixin import LLMSharedMixin
from distr.core.agent.tools.media.save_audio import (
    SaveAudioTool,
    generate_export_chunk,
    split_export_text,
)


class _DummyLlm(LLMSharedMixin):
    def __init__(self) -> None:
        self._model_name = "test-model"
        self.chat_manager = None
        self.event_queue = None
        self.command_queue = None
        self.confirmation_results_dict = None
        self._tools = []
        self._tools_dict = {}

    def _wire_request_tool_callback(self) -> None:
        pass


def test_long_export_text_is_split_at_natural_boundaries() -> None:
    text = "First sentence. " + ("long words " * 120) + "Final sentence."
    chunks = split_export_text(text, max_chars=160)

    assert len(chunks) > 2
    assert all(0 < len(chunk) <= 160 for chunk in chunks)
    assert " ".join(chunks).replace("  ", " ") == text.strip().replace("  ", " ")


def test_export_chunk_retries_a_transient_provider_failure(monkeypatch, tmp_path) -> None:
    generated_wav = tmp_path / "generated.wav"
    generated_wav.write_bytes(b"wav")
    attempts = []

    def _generate(text, provider, voice, speed):
        attempts.append((text, provider, voice, speed))
        if len(attempts) == 1:
            raise RuntimeError("temporary provider error")
        return str(generated_wav)

    monkeypatch.setattr("distr.core.audio.tts_handler.generate_tts_audio", _generate)
    monkeypatch.setattr(
        "distr.core.agent.tools.media.save_audio.time.sleep",
        lambda _: None,
    )

    result = generate_export_chunk(
        "Narration chunk",
        provider="pixazo",
        voice="custom_14",
    )

    assert result == str(generated_wav)
    assert len(attempts) == 2


def test_set_tts_service_refreshes_warmed_save_audio_tool(monkeypatch) -> None:
    from distr.core.agent.tools import loader

    save_audio = SaveAudioTool(tts_service=None)
    speech_service = object()
    monkeypatch.setattr(loader, "_tool_cache", {"save_audio": save_audio})
    monkeypatch.setattr(loader, "get_warmed_tools_list", lambda: [save_audio])

    llm = _DummyLlm()
    llm.set_tts_service(speech_service)

    assert save_audio._tts_service is speech_service
    assert llm._tools_dict["save_audio"] is save_audio


def test_save_audio_honours_mp3_and_downloads(monkeypatch, tmp_path) -> None:
    from distr.core.agent.tools.media import save_audio as save_audio_module

    class _FakeSpeechService:
        _provider_id = "pixazo"
        voice_id = "custom_7"

    generated = []
    converted = []
    source_wav = tmp_path / "generated.wav"
    source_wav.write_bytes(b"wav")
    monkeypatch.setattr(save_audio_module, "get_clipboard_content", lambda: "Clipboard narration")
    monkeypatch.setattr(save_audio_module, "get_output_path", lambda destination: str(tmp_path))

    def _generate(text, provider, voice, speed):
        generated.append((text, provider, voice, speed))
        return str(source_wav)

    def _convert(wav_path, mp3_path):
        converted.append((wav_path, mp3_path))
        from pathlib import Path

        Path(mp3_path).write_bytes(b"mp3")
        return mp3_path

    monkeypatch.setattr("distr.core.audio.tts_handler.generate_tts_audio", _generate)
    monkeypatch.setattr("distr.core.audio.tts_handler.wav_to_mp3", _convert)

    tool = SaveAudioTool(tts_service=_FakeSpeechService())
    result = tool._run(
        text="Save the clipboard as audio",
        audio_format="mp3",
        destination="downloads",
    )

    assert "Successfully saved audio" in result
    assert result.endswith(".mp3")
    assert generated == [("Clipboard narration", "pixazo", "custom_7", 1.0)]
    assert len(converted) == 1
