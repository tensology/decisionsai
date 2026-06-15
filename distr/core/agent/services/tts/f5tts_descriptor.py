"""
F5-TTS — retired offline provider.

Evaluated and removed: model load and synthesis were too slow for live voice agent use.
Descriptor kept so legacy settings/chat values still normalize to ``f5tts``.
"""

from distr.core.agent.services.tts.retired_tts_descriptor import RetiredTTSProviderDescriptor


class F5TTSDescriptor(RetiredTTSProviderDescriptor):
    provider_id = "f5tts"
    display_name = "F5-TTS (Offline)"
    settings_key_name = "f5tts_voice"
    default_voice_id = "default"
    output_sample_rate = 24000
    agent_display_name = "F5-TTS"
    name_match_tokens = ("f5-tts", "f5 tts", "f5tts")


DESCRIPTOR = F5TTSDescriptor()
