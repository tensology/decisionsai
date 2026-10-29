from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from distr.core.services import settings_service


@pytest.mark.parametrize("remember", [True, False])
def test_reconnect_targets_follow_saved_selections_and_clear_when_disabled(monkeypatch, remember):
    stored = {"locked_input": "Old Mic", "locked_output": "Old Speakers", "unrelated": "keep"}
    monkeypatch.setattr(settings_service, "load_settings_from_db", lambda: stored.copy())
    saved = MagicMock()
    emit = MagicMock()
    monkeypatch.setattr(settings_service, "save_settings_to_db", saved)
    monkeypatch.setattr(settings_service, "_safe_emit", emit)
    monkeypatch.setattr(settings_service, "_signal", lambda _: object())
    settings_service.save_audio_settings(SimpleNamespace(
        input_device="New Mic", output_device="Headset", remember_audio_settings=remember,
        locked_input="Old Mic", locked_output="Old Speakers",
    ))
    value = saved.call_args.args[0]
    assert value["input_device"] == "New Mic"
    assert value["output_device"] == "Headset"
    assert value["locked_input"] == ("New Mic" if remember else None)
    assert value["locked_output"] == ("Headset" if remember else None)
    assert value["unrelated"] == "keep"
    assert emit.call_count == 1
