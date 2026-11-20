"""Shared terminal setup for the macOS, Linux, and Windows launchers."""

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import getpass
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event
import warnings
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from distr.core.install_profile import InstallProfile
from distr.core.install_requirements import selected_requirements


MARKER = ROOT / "installer" / ".requirements_installed_profile"
KEY_PAGES = {
    "openai": "https://platform.openai.com/api-keys",
    "openrouter": "https://openrouter.ai/keys",
    "assemblyai": "https://www.assemblyai.com/app",
    "elevenlabs": "https://elevenlabs.io/app/settings/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "groq": "https://console.groq.com/keys",
}
CHAT_PROVIDERS = {
    "openai": "OpenAI (ChatGPT API)",
    "openrouter": "OpenRouter",
    "ollama": "Ollama (local)",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "": "Set up chat later",
}
STT_PROVIDERS = {
    "assemblyai": "AssemblyAI (cloud)",
    "openai": "OpenAI transcription (cloud)",
    "whisper": "Whisper.cpp base.en (local, 148 MB model)",
    "vosk": "Vosk small English (local, 41 MB model)",
    "": "Set up speech later",
}
TTS_PROVIDERS = {
    "openai": "OpenAI speech (cloud)",
    "elevenlabs": "ElevenLabs (cloud)",
    "kokoro": "Kokoro (local, 327 MB voice pack)",
}


@contextmanager
def input_terminal(enabled):
    if not enabled:
        yield None
        return
    if sys.stdin.isatty():
        yield sys.stdin
        return
    try:
        terminal = open("CONIN$" if os.name == "nt" else "/dev/tty", encoding="utf-8")
    except OSError:
        yield None
        return
    with terminal:
        yield terminal


def read_answer(terminal, prompt):
    print(prompt, end="", flush=True)
    value = terminal.readline()
    if not value:
        raise EOFError
    return value.strip()


def pick(terminal, title, choices, default):
    items = list(choices)
    print("\n" + title)
    for position, name in enumerate(items, 1):
        print(f"  {position}. {choices[name]}")
    while True:
        value = read_answer(terminal, f"Choose [{items.index(default) + 1}]: ")
        if not value:
            return default
        if value.isascii() and value.isdecimal() and 1 <= int(value) <= len(items):
            return items[int(value) - 1]
        print(f"Enter a number from 1 to {len(items)}.")


def connect(terminal, vendor, keys):
    if vendor not in KEY_PAGES or vendor in keys:
        return True
    while True:
        action = pick(terminal, f"Connect {vendor}", {
            "paste": "Paste an API key",
            "open": "Open the provider’s API key page",
            "back": "Choose another provider",
        }, "paste")
        if action == "back":
            return False
        if action == "open":
            print(KEY_PAGES[vendor])
            webbrowser.open(KEY_PAGES[vendor])
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                value = getpass.getpass(f"{vendor} API key (hidden): ").strip()
            except getpass.GetPassWarning:
                print("Hidden input is unavailable. Reopen setup in a terminal to enter a key.")
                return False
        if value:
            keys[vendor] = value
            return True


def provider(terminal, title, choices, default, keys):
    while True:
        selected = pick(terminal, title, choices, default)
        if connect(terminal, selected, keys):
            return selected


def model(terminal, title, default):
    while True:
        answer = read_answer(terminal, f"{title}{f' [{default}]' if default else ''}: ") or default
        if answer and not answer.startswith("-"):
            return answer
        print("Enter a model ID from your provider.")


def choose_profile(terminal):
    if terminal is None:
        print("Installing core only. Run setup again with --setup-providers to connect providers.")
        return InstallProfile("", ""), {}

    from distr.core.agent.constants import DEFAULT_MODELS, DEFAULT_OPENAI_WHISPER_MODEL
    from distr.core.agent.services.tts.openai_tts_config import DEFAULT_OPENAI_TTS_MODEL

    print("\nDecisionsAI first-run setup")
    keys = {}
    mode = pick(terminal, "How would you like to set up DecisionsAI?", {
        "quick": "Quick Setup: one OpenAI API key for chat, transcription, and voice",
        "manual": "Manual Setup: choose each provider and model",
        "later": "Install core and connect providers later",
    }, "quick")
    if mode == "quick" and connect(terminal, "openai", keys):
        profile = InstallProfile(
            "openai", DEFAULT_MODELS["openai"], "openai", DEFAULT_OPENAI_WHISPER_MODEL,
            "openai", DEFAULT_OPENAI_TTS_MODEL, "alloy",
        )
    elif mode == "later":
        profile = InstallProfile("", "")
    else:
        profile = choose_manual_profile(terminal, keys)
    print(f"\nChat: {CHAT_PROVIDERS[profile.chat_provider]}{f' / {profile.chat_model}' if profile.chat_model else ''}")
    print("Transcription: " + " / ".join(filter(None, (STT_PROVIDERS[profile.stt_provider], profile.stt_model))))
    print("Speech: " + " / ".join(filter(None, (
        TTS_PROVIDERS.get(profile.tts_provider, "Set up later"), profile.tts_model, profile.voice,
    ))))
    print(f"Audio: {profile.input_device} / {profile.output_device}")
    if profile.download_ollama:
        print(f"Ollama: download {profile.chat_model} (can be several GB)")
    if pick(terminal, "Apply these settings and install the selected dependencies?",
            {"install": "Install and save settings", "cancel": "Cancel setup"}, "install") == "cancel":
        raise InterruptedError
    return profile, keys


def choose_manual_profile(terminal, keys):
    from distr.core.agent.constants import (
        DEFAULT_ASSEMBLYAI_MODEL, DEFAULT_MODELS, DEFAULT_OPENAI_WHISPER_MODEL,
        KOKORO_VOICES, VALID_ASSEMBLYAI_MODELS, VALID_OPENAI_TRANSCRIPTION_MODELS,
    )
    from distr.core.agent.services.tts.openai_tts_config import (
        DEFAULT_OPENAI_TTS_MODEL, VALID_OPENAI_TTS_MODELS, voices_for_openai_tts_model,
    )

    chat = provider(terminal, "1. Chat provider", CHAT_PROVIDERS, "", keys)
    chat_model = model(terminal, "Chat model ID", DEFAULT_MODELS.get(chat, "")) if chat else ""
    download_ollama = False
    if chat == "ollama" and shutil.which("ollama"):
        download_ollama = pick(terminal, "Local Ollama model", {
            "later": "Use an installed model or download it later",
            "download": f"Download {chat_model} now (can be several GB)",
        }, "later") == "download"
    stt = provider(terminal, "2. Speech to text", STT_PROVIDERS, "", keys)
    stt_model = ""
    if stt == "assemblyai":
        stt_model = pick(terminal, "Transcription model",
                         {item: item for item in VALID_ASSEMBLYAI_MODELS},
                         DEFAULT_ASSEMBLYAI_MODEL)
    if stt == "openai":
        stt_model = pick(terminal, "Transcription model",
                         {item: item for item in VALID_OPENAI_TRANSCRIPTION_MODELS},
                         DEFAULT_OPENAI_WHISPER_MODEL)

    tts = tts_model = voice = ""
    if stt:
        tts = provider(terminal, "3. Text to speech", TTS_PROVIDERS, "openai", keys)
        if tts == "openai":
            tts_model = pick(terminal, "Voice model", {item: item for item in VALID_OPENAI_TTS_MODELS},
                             DEFAULT_OPENAI_TTS_MODEL)
            voice = pick(terminal, "Voice", {item: item for item in sorted(voices_for_openai_tts_model(tts_model))},
                         "alloy")
        elif tts == "elevenlabs":
            tts_model = model(terminal, "Voice model ID", "eleven_flash_v2_5")
            voice = model(terminal, "Voice ID from your ElevenLabs account", "")
        else:
            voice = pick(terminal, "Kokoro voice", KOKORO_VOICES, "af_heart")

    print("\n4. Audio devices")
    print("System Default follows your operating system when headphones or microphones change.")
    input_device = read_answer(terminal, "Microphone name [System Default]: ") or "System Default"
    output_device = read_answer(terminal, "Speaker name [System Default]: ") or "System Default"
    return InstallProfile(chat, chat_model, stt, stt_model, tts, tts_model, voice, input_device,
                          output_device, download_ollama)


def install_packages(requirements):
    with tempfile.TemporaryDirectory(prefix="decisions-install-") as directory:
        requirements_file = Path(directory) / "requirements.txt"
        requirements_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements_file)],
                       check=True, env={**os.environ, "DEBUG": "0"})


def requirements_missing(requirements):
    for requirement in requirements:
        if requirement.startswith("--"):
            continue
        name = requirement.partition("[")[0]
        for separator in (" ", ">", "<", "=", "!", "~", ";"):
            name = name.partition(separator)[0]
        try:
            version(name)
        except PackageNotFoundError:
            return True
    return False


def install_speech_models(profile):
    local_stt = profile.stt_provider in ("whisper", "vosk")
    local_tts = profile.tts_provider == "kokoro"
    if not (local_stt or local_tts):
        return

    from distr.core.packaged_stt import cached_model_path, download_model
    from distr.core.paths import user_data_root

    def fetch(engine):
        previous = -1
        show_bar = sys.stdout.isatty()

        def progress(received, total):
            nonlocal previous
            percent = received * 100 // total
            if show_bar and percent != previous:
                width = 24
                filled = percent * width // 100
                bar = "#" * filled + "-" * (width - filled)
                print(f"\r[{bar}] {percent:3d}%  {received / 1e6:6.1f} / {total / 1e6:.1f} MB",
                      end="", flush=True)
                previous = percent

        try:
            download_model(engine, progress, Event())
        finally:
            if previous >= 0:
                print()
        print("Model verified and installed.", flush=True)

    if local_stt and not cached_model_path(profile.stt_provider):
        print(f"Downloading {STT_PROVIDERS[profile.stt_provider]}...", flush=True)
        fetch(profile.stt_provider)
    if local_tts and not all(
        (user_data_root() / "models" / filename).is_file()
        for filename in ("kokoro-v1.0.onnx", "voices-v1.0.bin")
    ):
        print("Downloading the Kokoro voice pack...", flush=True)
        fetch("kokoro")


def install_models(profile):
    install_speech_models(profile)
    if profile.download_ollama:
        subprocess.run(["ollama", "pull", profile.chat_model], check=True)


def install_step(operation, argument, terminal, label):
    while True:
        try:
            operation(argument)
            return
        except InterruptedError:
            raise
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            if terminal is None:
                raise
            print(f"\n{label} failed. {exc}", file=sys.stderr)
        if pick(terminal, label, {"retry": "Retry", "cancel": "Cancel setup"}, "cancel") == "cancel":
            raise InterruptedError("Setup cancelled.")


def apply_profile(profile, keys, existing, configure, terminal):
    requirements = selected_requirements(ROOT / "requirements.txt", stt=profile.stt_provider, tts=profile.tts_provider)
    if sys.platform == "darwin":
        requirements.append("pyobjc-framework-Cocoa")
    fingerprint = hashlib.sha256("\n".join(requirements).encode()).hexdigest()
    if not existing or fingerprint != existing["requirements"] or requirements_missing(requirements):
        print("\nInstalling DecisionsAI and selected provider libraries...", flush=True)
        install_step(install_packages, requirements, terminal, "Installing provider libraries")
    if configure or existing is None:
        install_step(install_models, profile, terminal, "Preparing local models")
        from distr.core.utils import save_settings_to_db
        save_settings_to_db(profile.settings(keys))
        print("\nSetup complete. You can update your choices in Settings.")
        if profile.chat_provider == "ollama" and not profile.download_ollama:
            if not shutil.which("ollama"):
                print("Install Ollama from https://ollama.com/download.")
            print(f"Local chat uses your Ollama server. If needed, run: ollama pull {profile.chat_model}")
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    pending = MARKER.with_suffix(".pending")
    pending.write_text(json.dumps({"choices": asdict(profile), "requirements": fingerprint}), encoding="utf-8")
    pending.replace(MARKER)


def run(configure=False, interactive=True):
    existing = json.loads(MARKER.read_text(encoding="utf-8")) if MARKER.is_file() else None
    if existing is not None and not configure:
        apply_profile(InstallProfile(**existing["choices"]), {}, existing, False, None)
        return
    with input_terminal(interactive) as terminal:
        if configure and terminal is None:
            raise InterruptedError("Provider setup requires an interactive terminal.")
        profile, keys = choose_profile(terminal)
        apply_profile(profile, keys, existing, configure, terminal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()
    try:
        run(args.configure, not args.non_interactive)
    except (KeyboardInterrupt, EOFError, InterruptedError) as exc:
        print(f"\n{exc or 'Setup cancelled.'} Run the installer again to continue.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print("\nA dependency or model could not be installed. Review the error above and rerun setup.",
              file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"\nSetup could not finish. {exc}. Run the installer again to retry.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
