from pathlib import Path
from contextlib import nullcontext
from io import StringIO
import runpy
import subprocess
import sys

import pytest

from distr.core.install_requirements import selected_requirements


REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"
WIZARD = REQUIREMENTS.parent / "bin" / "first_run.py"


@pytest.mark.parametrize(
    ("stt", "tts", "expected"),
    [
        ("openai", "openai", ()),
        ("assemblyai", "elevenlabs", ("assemblyai", "elevenlabs")),
        ("whisper", "kokoro", ("pywhispercpp", "kokoro-onnx")),
        ("vosk", "supertonic", ("vosk", "supertonic")),
    ],
)
def test_first_run_installs_only_selected_provider_extensions(stt, tts, expected):
    requirements = selected_requirements(REQUIREMENTS, stt=stt, tts=tts)
    assert "PyQt6" in requirements
    assert any(line.startswith("pipecat-ai[silero]") for line in requirements)
    for name in ("pywhispercpp", "vosk", "assemblyai", "elevenlabs", "kokoro-onnx", "supertonic"):
        assert any(line.startswith(name) for line in requirements) == (name in expected)
    for prefix in ("torch", "kanade-tokenizer", "sentence-transformers", "coqui-tts",
                   "pytest", "pyqt6-webengine", "playwright"):
        assert not any(line.lower().startswith(prefix) for line in requirements)


@pytest.fixture
def wizard():
    return runpy.run_path(str(WIZARD))["run"].__globals__


def test_quick_setup_reuses_one_hidden_key_and_defaults_to_system_audio(wizard, monkeypatch, capsys):
    prompts = []
    monkeypatch.setattr(wizard["getpass"], "getpass", lambda prompt: prompts.append(prompt) or "fixture-secret")
    profile, keys = wizard["choose_profile"](StringIO("\n\n\n"))
    settings = profile.settings(keys)
    assert len(prompts) == 1
    assert settings["openai_key"] == "fixture-secret"
    assert settings["openai_enabled"] is True
    assert settings["llm_provider"] == settings["conversational_llm_provider"] == "openai"
    assert settings["transcription_model"].startswith("OpenAI (")
    assert settings["voice_provider"] == "openai"
    assert settings["tts_voice"] == "alloy"
    assert settings["input_device"] == settings["output_device"] == "System Default"
    assert "fixture-secret" not in capsys.readouterr().out


def test_manual_setup_connects_selected_vendors_and_uses_requested_models(wizard, monkeypatch):
    opened = []
    monkeypatch.setattr(wizard["webbrowser"], "open", opened.append)
    monkeypatch.setattr(wizard["getpass"], "getpass", lambda prompt: prompt.split()[0] + "-fixture")
    terminal = StringIO("\n".join((
        "2", "2", "2", "openai/gpt-4o", "1", "1", "2", "2", "1",
        "", "custom-voice-id", "USB Microphone", "", "", "",
    )))
    profile, keys = wizard["choose_profile"](terminal)
    settings = profile.settings(keys)
    assert opened == ["https://openrouter.ai/keys"]
    assert set(keys) == {"openrouter", "assemblyai", "elevenlabs"}
    assert settings["agent_model"] == "openai/gpt-4o"
    assert settings["transcription_model"] == "AssemblyAI (universal-2)"
    assert settings["elevenlabs_voice"] == "custom-voice-id"
    assert settings["input_device"] == "USB Microphone"


def test_cloud_setup_persists_settings_and_reruns_without_downloads(wizard, monkeypatch, tmp_path):
    from distr.core import utils

    marker = tmp_path / "profile.json"
    packages = []
    saved = []
    wizard.update(MARKER=marker, ROOT=REQUIREMENTS.parent, install_packages=packages.append,
                  requirements_missing=lambda requirements: False)
    monkeypatch.setattr(utils, "save_settings_to_db", saved.append)
    monkeypatch.setattr(wizard["getpass"], "getpass", lambda prompt: "fixture-secret")
    wizard["input_terminal"] = lambda enabled: nullcontext(StringIO("\n\n\n"))
    wizard["run"]()
    wizard["input_terminal"] = lambda enabled: pytest.fail("Already configured")
    wizard["run"]()
    assert len(saved) == len(packages) == 1
    assert saved[0]["openai_key"] == "fixture-secret"
    assert all(not item.startswith("pywhispercpp") for item in packages[0])
    assert "fixture-secret" not in marker.read_text()


def test_local_selection_downloads_only_one_stt_and_one_voice_pack(wizard, monkeypatch, tmp_path):
    from distr.core import packaged_stt, paths

    downloads = []
    monkeypatch.setattr(packaged_stt, "cached_model_path", lambda engine: None)
    monkeypatch.setattr(packaged_stt, "download_model", lambda engine, *_: downloads.append(engine))
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path)
    wizard["install_models"](wizard["InstallProfile"]("ollama", "local", "vosk", "", "kokoro", "", "af_heart"))
    assert downloads == ["vosk", "kokoro"]


def test_local_setup_reuses_cached_model_for_each_engine(wizard, monkeypatch, tmp_path):
    from distr.core import packaged_stt

    whisper = tmp_path / packaged_stt.WHISPER_FILE
    vosk = tmp_path / packaged_stt.VOSK_DIR / "am" / "final.mdl"
    vosk.parent.mkdir(parents=True)
    vosk.touch()
    whisper.touch()
    monkeypatch.setattr(packaged_stt, "_root", lambda: tmp_path)
    monkeypatch.setattr(packaged_stt, "download_model", lambda *_: pytest.fail("Model already cached"))
    profile = wizard["InstallProfile"]
    wizard["install_models"](profile("", "", "vosk"))
    wizard["install_models"](profile("", "", "whisper"))
    assert packaged_stt.cached_model_path("vosk") == str(vosk.parent.parent)
    assert packaged_stt.cached_model_path("whisper") == str(whisper)


def test_ollama_model_pull_requires_explicit_choice(wizard, monkeypatch):
    commands = []
    monkeypatch.setattr(wizard["subprocess"], "run", lambda command, **kwargs: commands.append(command))
    wizard["install_models"](wizard["InstallProfile"]("ollama", "local-model"))
    assert commands == []
    wizard["install_models"](wizard["InstallProfile"]("ollama", "local-model", download_ollama=True))
    assert commands == [["ollama", "pull", "local-model"]]


def test_non_interactive_reconfiguration_preserves_existing_preferences(wizard, tmp_path):
    wizard.update(MARKER=tmp_path / "profile.json",
                  install_packages=lambda _: pytest.fail("Must not install packages"))
    with pytest.raises(InterruptedError, match="interactive terminal"):
        wizard["run"](configure=True, interactive=False)
    assert not wizard["MARKER"].exists()


def test_retry_keeps_selected_profile_and_key(wizard, monkeypatch, tmp_path):
    from distr.core import utils

    packages = []
    attempts = []
    saved = []
    profile = wizard["InstallProfile"]("openrouter", "openai/gpt-4o", "vosk")

    def prepare_models(selected):
        attempts.append(selected)
        if len(attempts) == 1:
            raise OSError("Connection interrupted")

    wizard.update(MARKER=tmp_path / "profile.json", install_packages=packages.append,
                  install_models=prepare_models,
                  input_terminal=lambda _: nullcontext(StringIO("1\n")),
                  choose_profile=lambda _: (profile, {"openrouter": "fixture-secret"}))
    monkeypatch.setattr(utils, "save_settings_to_db", saved.append)
    wizard["run"]()
    assert attempts == [profile, profile]
    assert len(packages) == len(saved) == 1
    assert saved[0]["openrouter_key"] == "fixture-secret"
    assert "fixture-secret" not in wizard["MARKER"].read_text()


def test_cancelled_reconfiguration_leaves_the_existing_profile_intact(wizard, monkeypatch, tmp_path):
    from distr.core import utils

    marker = tmp_path / "profile.json"
    previous = '{"choices": {}, "requirements": "previous-fingerprint"}'
    marker.write_text(previous)

    def prepare_models(_):
        raise OSError("Connection interrupted")

    wizard.update(MARKER=marker, install_packages=lambda _: None, install_models=prepare_models,
                  input_terminal=lambda _: nullcontext(StringIO("\n")),
                  choose_profile=lambda _: (wizard["InstallProfile"]("openai", "gpt-4o"), {}))
    monkeypatch.setattr(utils, "save_settings_to_db", lambda _: pytest.fail("Must not change active settings"))
    with pytest.raises(InterruptedError, match="Setup cancelled"):
        wizard["run"](configure=True)
    assert marker.read_text() == previous


def test_provider_menu_runs_before_third_party_dependencies_are_installed():
    code = """
from io import StringIO
from runpy import run_path
setup = run_path("bin/first_run.py")
profile, keys = setup["choose_profile"](StringIO("3\\n\\n"))
assert not keys
assert profile.settings(keys)["transcription_model"] == "Disabled"
"""
    subprocess.run([sys.executable, "-S", "-c", code], cwd=REQUIREMENTS.parent, check=True)
