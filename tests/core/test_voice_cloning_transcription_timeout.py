import sys
import threading
import types

from distr.core.audio import voice_cloning


def test_loaded_agent_stt_wait_is_short_by_default(monkeypatch, tmp_path):
    waits = []

    class _NoResultEvent:
        def wait(self, timeout=None):
            waits.append(timeout)
            return False

    class _QueueNoResult:
        def put(self, *_args, **_kwargs):
            return None

    class _App:
        agent_command_queue = _QueueNoResult()

    qt_mod = types.ModuleType("PyQt6")
    widgets_mod = types.ModuleType("PyQt6.QtWidgets")

    class _QApplication:
        @staticmethod
        def instance():
            return _App()

    widgets_mod.QApplication = _QApplication
    monkeypatch.setitem(sys.modules, "PyQt6", qt_mod)
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", widgets_mod)
    monkeypatch.setattr(threading, "Event", _NoResultEvent)

    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"not real audio")

    result = voice_cloning._transcribe_via_loaded_agent_stt(str(audio_path))

    assert result is None
    assert waits
    assert waits[-1] <= 3.0


def test_voice_cloning_openai_fallback_uses_internal_key_and_gpt_transcribe(monkeypatch, tmp_path):
    captured = {}

    class _Transcriptions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(text="voice sample transcript")

    class _OpenAI:
        def __init__(self, api_key):
            captured["api_key"] = api_key
            self.audio = types.SimpleNamespace(
                transcriptions=_Transcriptions(),
            )

    openai_module = types.ModuleType("openai")
    openai_module.OpenAI = _OpenAI
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setattr(voice_cloning, "_transcribe_via_loaded_agent_stt", lambda _path: None)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"openai_key": "internal-settings-key"},
    )

    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"audio")

    assert voice_cloning.transcribe_audio_file(str(audio_path)) == "voice sample transcript"
    assert captured["api_key"] == "internal-settings-key"
    assert captured["model"] == "gpt-transcribe"
