"""
Chatterbox — retired offline provider.

Evaluated and removed: synthesis quality/speed did not meet live agent requirements.
Descriptor kept so legacy settings/chat values still normalize to ``chatterbox``.
"""

from distr.core.agent.services.tts.retired_tts_descriptor import RetiredTTSProviderDescriptor


class ChatterboxDescriptor(RetiredTTSProviderDescriptor):
    provider_id = "chatterbox"
    display_name = "Chatterbox (Offline)"
    settings_key_name = "chatterbox_voice"
    default_voice_id = "default"
    output_sample_rate = 24000
    agent_display_name = "Chatterbox"
    name_match_tokens = ("chatterbox", "chatter box")


DESCRIPTOR = ChatterboxDescriptor()
