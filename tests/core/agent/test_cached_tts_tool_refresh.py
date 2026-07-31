from distr.core.agent.services.llm.core_mixin import LLMSharedMixin
from distr.core.agent.tools.media.save_audio import SaveAudioTool


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
    import numpy as np
    from distr.core.agent.tools.media import save_audio as save_audio_module

    class _FakeKokoro:
        @staticmethod
        def create(text, voice, speed):
            assert text == "Clipboard narration"
            return np.array([0.0, 0.25, -0.25], dtype=np.float32), 24000

    class _FakeSpeechService:
        kokoro = _FakeKokoro()
        voice = "test-voice"
        _voice_cloning_enabled = False

    written = []
    converted = []
    monkeypatch.setattr(save_audio_module, "get_clipboard_content", lambda: "Clipboard narration")
    monkeypatch.setattr(save_audio_module, "get_output_path", lambda destination: str(tmp_path))
    monkeypatch.setattr(save_audio_module, "SCIPY_AVAILABLE", True)

    def _write_wav(path, sample_rate, audio):
        written.append((path, sample_rate, audio))
        with open(path, "wb") as handle:
            handle.write(b"wav")

    def _convert(wav_path, mp3_path):
        converted.append((wav_path, mp3_path))
        with open(mp3_path, "wb") as handle:
            handle.write(b"mp3")
        return mp3_path

    monkeypatch.setattr(save_audio_module.wavfile, "write", _write_wav)
    monkeypatch.setattr("distr.core.audio.tts_handler.wav_to_mp3", _convert)

    tool = SaveAudioTool(tts_service=_FakeSpeechService())
    result = tool._run(
        text="Save the clipboard as audio",
        audio_format="mp3",
        destination="downloads",
    )

    assert "Successfully saved audio" in result
    assert result.endswith(".mp3")
    assert len(written) == 1
    assert len(converted) == 1
    assert not (tmp_path / converted[0][0]).exists()
