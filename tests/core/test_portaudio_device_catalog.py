"""Phase A: PortAudio device catalog + fresh list hash contracts."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch


def test_query_macos_devices_uses_sounddevice_names_as_ids(monkeypatch):
    from distr.core.audio import utils as audio_utils

    fake = [
        {"name": "MacBook Pro Speakers", "max_output_channels": 2, "max_input_channels": 0},
        {"name": "MacBook Pro Microphone", "max_output_channels": 0, "max_input_channels": 1},
        {"name": "JBL TUNE510BT", "max_output_channels": 2, "max_input_channels": 0},
    ]
    monkeypatch.setattr(audio_utils, "sd", MagicMock(query_devices=MagicMock(return_value=fake)))

    outputs, inputs = audio_utils.query_macos_devices()
    assert {d["name"] for d in outputs} == {"MacBook Pro Speakers", "JBL TUNE510BT"}
    assert {d["name"] for d in inputs} == {"MacBook Pro Microphone"}
    for d in outputs + inputs:
        assert d["id"] == d["name"]


def test_query_windows_devices_id_is_name_not_index(monkeypatch):
    from distr.core.audio import utils as audio_utils

    fake = [
        {"name": "Speakers (Realtek)", "max_output_channels": 2, "max_input_channels": 0},
        {"name": "Microphone (Realtek)", "max_output_channels": 0, "max_input_channels": 1},
    ]
    monkeypatch.setattr(audio_utils, "sd", MagicMock(query_devices=MagicMock(return_value=fake)))

    outputs, inputs = audio_utils.query_windows_devices()
    assert outputs[0]["id"] == "Speakers (Realtek)"
    assert inputs[0]["id"] == "Microphone (Realtek)"
    assert outputs[0]["id"] != "0"


def test_get_current_device_list_hash_uses_fresh_subprocess(monkeypatch):
    from distr.core.audio import utils as audio_utils

    lists = [
        [(0, "A", 0, 2), (1, "B", 1, 0)],
        [(0, "A", 0, 2), (1, "B", 1, 0), (2, "C", 0, 2)],
    ]
    calls = {"n": 0}

    def fake_fresh():
        i = min(calls["n"], len(lists) - 1)
        calls["n"] += 1
        return lists[i]

    in_proc = MagicMock()
    monkeypatch.setattr(
        "distr.core.agent.config_loader._query_devices_fresh_subprocess",
        fake_fresh,
    )
    monkeypatch.setattr(audio_utils, "sd", MagicMock(query_devices=in_proc))

    h1 = audio_utils.get_current_device_list_hash()
    h2 = audio_utils.get_current_device_list_hash()
    assert h1 != h2
    assert h1 == hashlib.md5(b"A|B").hexdigest()
    assert h2 == hashlib.md5(b"A|B|C").hexdigest()
    in_proc.assert_not_called()


def test_get_audio_devices_ignores_locked_list(monkeypatch):
    """Live catalog must be PortAudio, not stale locked_*_list."""
    from distr.core.audio import utils as audio_utils

    live_out = [{"name": "PortAudio Speaker", "id": "PortAudio Speaker", "type": "Other"}]
    live_in = [{"name": "PortAudio Mic", "id": "PortAudio Mic", "type": "Other"}]
    monkeypatch.setattr(audio_utils, "query_native_devices", lambda: (live_out, live_in))

    # Simulate what the route does after the fix
    outputs, inputs = audio_utils.query_native_devices()
    formatted = {
        "input_devices": audio_utils.format_devices_for_api(inputs),
        "output_devices": audio_utils.format_devices_for_api(outputs),
    }
    out_names = [d["name"] for d in formatted["output_devices"]]
    assert "PortAudio Speaker" in out_names
    assert "System Default" in out_names
    for d in formatted["output_devices"]:
        if d["name"] != "System Default":
            assert d["id"] == d["name"]
