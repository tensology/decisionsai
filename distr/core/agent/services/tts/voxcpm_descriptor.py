"""
VoxCPM — retired offline provider.

Evaluated and removed: requires CUDA for practical speed; MPS crashes and CPU is too slow.
Descriptor kept so legacy settings/chat values still normalize to ``voxcpm``.
"""

from distr.core.agent.services.tts.retired_tts_descriptor import RetiredTTSProviderDescriptor


class VoxCPMDescriptor(RetiredTTSProviderDescriptor):
    provider_id = "voxcpm"
    display_name = "VoxCPM (Offline)"
    settings_key_name = "voxcpm_voice"
    default_voice_id = "default"
    output_sample_rate = 48000
    agent_display_name = "VoxCPM"
    name_match_tokens = ("voxcpm", "vox cpm")


DESCRIPTOR = VoxCPMDescriptor()
