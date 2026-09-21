"""Library TTS setup binds existing API voices and never clones."""

import importlib.util
from pathlib import Path


def _mod():
    path = Path(__file__).resolve().parents[2] / "scripts" / "setup_tts_voices.py"
    spec = importlib.util.spec_from_file_location("setup_tts_voices", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pick_keeps_english_current_and_replaces_japanese():
    setup = _mod()
    voices = [
        {"id": "ja-1", "name": "しおり (ナレーション) (ja)"},
        {"id": "en-1", "name": "Sarah"},
        {"id": "en-2", "name": "Energetic Male"},
    ]
    kept = setup.pick_library_voice(
        voices, preferred=("Sarah",), current_id="en-2", replace_eastern=True
    )
    assert kept["id"] == "en-2"
    swapped = setup.pick_library_voice(
        voices, preferred=("Sarah",), current_id="ja-1", replace_eastern=True
    )
    assert swapped["id"] == "en-1"


def test_plan_binds_library_ids_without_clone(monkeypatch):
    setup = _mod()
    settings = {
        "fishaudio_key": "sk-fish",
        "elevenlabs_key": "sk-el",
        "pixazo_key": "sk-px",
        "fishaudio_voice": "ja-1",
        "elevenlabs_voice": "CwhRBWXzGAHq8TQ4Fs17",
        "pixazo_voice": "voxcpm",
    }
    libraries = {
        "fishaudio": [
            {"id": "ja-1", "name": "しおり (ja)"},
            {"id": "sarah-f", "name": "Sarah"},
        ],
        "elevenlabs": [
            {"id": "CwhRBWXzGAHq8TQ4Fs17", "name": "Roger - Laid-Back"},
            {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah - Mature"},
        ],
        "pixazo": [{"id": "voxcpm", "name": "VoxCPM 2 (default, free)"}],
    }

    class _Desc:
        def __init__(self, pid, key):
            self.id = pid
            self.name = pid
            self.settings_key = key

    monkeypatch.setattr(
        "distr.core.agent.services.tts.registry.tts_registry.get",
        lambda pid: _Desc(pid, f"{pid}_voice"),
    )
    plan = setup.plan_library_setup(
        settings,
        fetch_voices=lambda pid: libraries[pid],
        activate="fishaudio",
    )
    assert plan["bindings"]["fishaudio"]["id"] == "sarah-f"
    assert plan["bindings"]["elevenlabs"]["id"] == "CwhRBWXzGAHq8TQ4Fs17"
    assert plan["bindings"]["pixazo"]["id"] == "voxcpm"
    assert "clone" not in str(plan).lower()
    out = setup.apply_library_setup(dict(settings), plan)
    assert out["fishaudio_enabled"] is True
    assert out["fishaudio_voice"] == "sarah-f"
    assert out["elevenlabs_voice"] == "CwhRBWXzGAHq8TQ4Fs17"
    assert out["voice_provider"] == "fishaudio"
    assert out["tts_voice"] == "sarah-f"
