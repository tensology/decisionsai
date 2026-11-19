"""Fish Audio voice ids resolve to names even when they are not custom voices."""
from distr.core.agent.services.tts.fishaudio_descriptor import FishAudioDescriptor


def test_fish_display_name_falls_back_to_voice_list(monkeypatch):
    FishAudioDescriptor._voice_names, FishAudioDescriptor._voice_names_at = {}, 0.0
    desc = FishAudioDescriptor()
    calls = []

    def voices():
        calls.append(1)
        return [{'id': '27d552ba597a4d54b4ea5c7c3962d3dd', 'name': 'Uncle Roger'}]
    monkeypatch.setattr(desc, 'get_voices', voices)
    assert desc.resolve_display_name('27d552ba597a4d54b4ea5c7c3962d3dd', {}) == 'Uncle Roger'
    assert desc.resolve_display_name('27d552ba597a4d54b4ea5c7c3962d3dd', {}) == 'Uncle Roger'
    assert len(calls) == 1  # cached
    assert desc.resolve_display_name('unknown-id', {}) == 'unknown-id'


def test_chat_js_keeps_looking_when_display_is_the_raw_id():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / 'distr/gui/web/static/chat/js/chat.js').read_text()
    body = js[js.index('function voiceModelDisplayName'):js.index('function formatHeaderSettingsPair')]
    assert 'display !== voiceIdRaw' in body
