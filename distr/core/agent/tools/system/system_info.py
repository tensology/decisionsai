"""
System Information Tool

A tool that provides internal system information including:
- Application settings (LLM providers, models, TTS, STT)
- Operating system information
- Installed models
- Configuration details

Useful for queries like "what are your models", "what is your setup", "show me your configuration"
"""

import logging
import platform
import sys
import os
import json
from typing import Optional, Any
from langchain.tools import BaseTool
from pydantic import Field

logger = logging.getLogger(__name__)

_REFERENCE_MARKER = "\n\nREFERENCE:\n"


def _blank(val: Any) -> bool:
    s = ("" if val is None else str(val)).strip()
    return not s or s.lower() == "not set"


def _model_spoken(model: Any) -> str:
    s = ("" if model is None else str(model)).strip().replace("_", " ").replace(":", " ")
    return s or "not set"


def _provider_spoken(provider_id: Any) -> str:
    p = ("" if provider_id is None else str(provider_id)).strip().lower()
    return {
        "ollama": "Ollama on your computer",
        "openai": "OpenAI in the cloud",
        "anthropic": "Anthropic in the cloud",
        "groq": "Groq in the cloud",
        "openrouter": "OpenRouter in the cloud",
        "kilocode": "KiloCode in the cloud",
        "gemini": "Google Gemini in the cloud",
    }.get(p, p.replace("_", " ") if p else "your saved provider")


def _tts_stack_spoken(settings: dict, voice_display: str) -> str:
    prov = (settings.get("voice_provider") or "kokoro").strip().lower()
    label = {
        "kokoro": "Kokoro",
        "elevenlabs": "ElevenLabs",
        "openai": "OpenAI TTS",
        "coqui": "Coqui",
        "vibevoice": "VibeVoice",
    }.get(prov, prov or "your TTS provider")
    vn = voice_display if not _blank(voice_display) else "the default voice"
    return f"{label} with the {vn} voice"


def _build_voice_friendly_config_summary(info: dict, voice_display: str) -> str:
    """Plain sentences for voice; no lists (system prompt / TTS)."""
    settings = info.get("settings") or {}
    if isinstance(settings, dict) and settings.get("error"):
        return (
            "I tried to read your saved preferences but hit a snag, so I can't spell out your exact "
            "models from here. Open Settings from the app and check LLMs and Audio if you need the numbers."
        )

    llm = settings.get("llm") or {}
    conv_p = _provider_spoken(llm.get("conversational_provider"))
    conv_m = _model_spoken(llm.get("conversational_model"))
    cod_p = _provider_spoken(llm.get("coding_provider"))
    cod_m = _model_spoken(llm.get("coding_model"))
    vis_p = _provider_spoken(llm.get("vision_provider"))
    vis_m = _model_spoken(llm.get("vision_model"))
    img_p = _provider_spoken(llm.get("image_provider"))
    img_m = _model_spoken(llm.get("image_model"))

    parts: list[str] = []
    parts.append(
        f"For this chat I'm on {conv_p}, using the model {conv_m}. "
        "That's the brain answering you right now."
    )

    same_coding = (cod_p == conv_p and cod_m == conv_m) or (
        _blank(llm.get("coding_model")) and _blank(llm.get("coding_provider"))
    )
    if not same_coding and not (_blank(llm.get("coding_model")) and _blank(llm.get("coding_provider"))):
        parts.append(f"When you ask for code help I switch to {cod_p} with {cod_m}.")

    if not (_blank(llm.get("vision_model")) and _blank(llm.get("vision_provider"))):
        if (vis_p, vis_m) != (conv_p, conv_m):
            parts.append(f"For screenshots and vision I have {vis_p} with {vis_m}.")

    if not (_blank(llm.get("image_model")) and _blank(llm.get("image_provider"))):
        if (img_p, img_m) != (conv_p, conv_m):
            parts.append(f"For image generation it's {img_p} with {img_m}.")

    tts_line = _tts_stack_spoken(settings if isinstance(settings, dict) else {}, voice_display)
    parts.append(f"When I speak back to you, audio runs through {tts_line}.")

    stt = settings.get("stt") or {}
    tm = (stt.get("model") or "").strip()
    if tm:
        parts.append(f"Your speech-to-text choice in Settings is {tm}.")

    mode = settings.get("mode") or {}
    hf = mode.get("hands_free", True)
    if isinstance(hf, str):
        hf = hf.lower() in ("1", "true", "yes", "on")
    parts.append(
        "You're in hands-free listening so I keep an ear open between turns."
        if hf
        else "You're in push-to-talk, so I listen when you hold the key."
    )

    osys = (info.get("operating_system") or {}).get("system") or ""
    if osys and osys.lower() != "unknown":
        parts.append(f"This machine reports {osys}.")

    return " ".join(parts)


class SystemInfoTool(BaseTool):
    """
    Tool to retrieve system information including settings, models, and OS details.

    When called, this tool:
    1. Fetches settings from the database
    2. Gets OS and system information
    3. Retrieves installed/cached model information
    4. Returns formatted system information

    The conversational model reported matches the **current chat's** provider/model
    (from chat_manager) when available, so "what model are you using" matches the UI.
    """

    chat_manager: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, chat_manager=None, **kwargs):
        base_kwargs = {k: v for k, v in kwargs.items() if k != "chat_manager"}
        super().__init__(**base_kwargs)
        self.chat_manager = chat_manager

    name: str = "system_info"
    description: str = (
        "USE THIS TOOL when the user asks about system configuration, models, setup, or internal information. "
        "CRITICAL: Use this tool for ANY question about configured models, LLM settings, or system configuration. "
        "Examples: 'what are your models', 'what model are you using', 'what is your setup', 'show me your configuration', "
        "'what LLM are you using', 'what models do you have', 'what's your system info', "
        "'what are your conversational LLM', 'what is your coding model', 'what vision model are you using', "
        "'what image model do you have', 'tell me about your models', 'what models are configured', "
        "'show me your models', 'what are your conversational coding vision or image models'. "
        "Returns a short conversational paragraph FIRST, then a line REFERENCE: and structured details below. "
        "Speak only the paragraph to the user over voice; never read the REFERENCE block aloud."
    )
    
    def get_triggers(self) -> list:
        """Return trigger phrases that should match this tool."""
        return [
            "what are your models",
            "what model are you using",
            "what is your setup",
            "show me your configuration",
            "what llm are you using",
            "what models do you have",
            "what's your system info",
            "what are your conversational",
            "what is your coding",
            "what vision model",
            "what image model",
            "tell me about your models",
            "what models are configured",
            "show me your models",
            "what are your conversational llm",
            "what are your coding",
            "what are your vision",
            "what are your image",
            "what conversational model",
            "what coding model",
            "what vision model",
            "what image model",
            "your models",
            "your setup",
            "your configuration",
            "system info",
            "system information",
            # Not fast-matched: "what can you do", capabilities, tutorial — those belong
            # in normal LLM turns so answers are conversational (fast matcher would say "Done").
        ]
    
    def _run(self, query: str = "", **kwargs) -> str:
        """Execute system info retrieval."""
        try:
            return self._get_system_info()
        except Exception as e:
            logger.error(f"Error getting system info: {e}", exc_info=True)
            return f"Error retrieving system information: {str(e)}"
    
    async def _arun(self, query: str = "", **kwargs) -> str:
        """Async execution."""
        return self._run(query=query, **kwargs)
    
    def _get_system_info(self) -> str:
        """Gather and format system information."""
        info = {}
        
        # 1. Operating System Information
        info['operating_system'] = {
            'system': platform.system(),
            'release': platform.release(),
            'version': platform.version(),
            'machine': platform.machine(),
            'processor': platform.processor(),
            'python_version': sys.version.split()[0],
            'python_implementation': platform.python_implementation(),
        }
        
        # 2. Application Settings
        settings = {}
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            
            # Use current chat's provider/model when available (matches chat header and actual LLM)
            conv_provider = settings.get('conversational_llm_provider') or settings.get('agent_provider', 'Not set')
            conv_model = settings.get('conversational_llm_model') or settings.get('agent_model', 'Not set')
            if self.chat_manager and self.chat_manager.get_current_chat():
                conv_provider = getattr(self.chat_manager, 'current_provider', conv_provider) or conv_provider
                conv_model = getattr(self.chat_manager, 'current_model', conv_model) or conv_model

            info['settings'] = {
                'llm': {
                    'conversational_provider': conv_provider,
                    'conversational_model': conv_model,
                    'coding_provider': settings.get('coding_llm_provider', 'Not set'),
                    'coding_model': settings.get('coding_llm_model', 'Not set'),
                    'vision_provider': settings.get('vision_llm_provider', 'Not set'),
                    'vision_model': settings.get('vision_llm_model', 'Not set'),
                    'image_provider': settings.get('image_llm_provider', 'Not set'),
                    'image_model': settings.get('image_llm_model', 'Not set'),
                },
                'tts': {
                    'provider': settings.get('voice_provider', 'Not set'),
                    'voice': self._get_voice_display_name(settings),
                },
                'stt': {
                    'model': settings.get('transcription_model', 'Not set'),
                },
                'audio': {
                    'input_device': settings.get('input_device', 'Not set'),
                    'output_device': settings.get('output_device', 'Not set'),
                },
                'mode': {
                    'hands_free': settings.get('hands_free_mode', True),
                    'listening': settings.get('last_listening_state', True),
                }
            }
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
            info['settings'] = {'error': f'Could not load settings: {str(e)}'}
        
        # 3. Configured Models (what's actually set in settings)
        llm_settings = info.get('settings', {}).get('llm', {})
        info['configured_models'] = {
            'conversational': {
                'provider': llm_settings.get('conversational_provider', 'Not set'),
                'model': llm_settings.get('conversational_model', 'Not set'),
            },
            'coding': {
                'provider': llm_settings.get('coding_provider', 'Not set'),
                'model': llm_settings.get('coding_model', 'Not set'),
            },
            'vision': {
                'provider': llm_settings.get('vision_provider', 'Not set'),
                'model': llm_settings.get('vision_model', 'Not set'),
            },
            'image': {
                'provider': llm_settings.get('image_provider', 'Not set'),
                'model': llm_settings.get('image_model', 'Not set'),
            },
        }
        
        # 4. API Provider Status
        try:
            info['api_providers'] = {
                'openai': {
                    'enabled': settings.get('openai_enabled', False),
                    'validated': settings.get('openai_key', '').strip() != '',
                },
                'anthropic': {
                    'enabled': settings.get('anthropic_enabled', False),
                    'validated': settings.get('anthropic_key', '').strip() != '',
                },
                'openrouter': {
                    'enabled': settings.get('openrouter_enabled', False),
                    'validated': settings.get('openrouter_key', '').strip() != '',
                },
                'elevenlabs': {
                    'enabled': settings.get('elevenlabs_enabled', False),
                    'validated': settings.get('elevenlabs_key', '').strip() != '',
                },
                'assemblyai': {
                    'enabled': settings.get('assemblyai_enabled', False),
                    'validated': settings.get('assemblyai_key', '').strip() != '',
                },
            }
        except Exception:
            info['api_providers'] = {}

        voice_display = self._get_voice_display_name(settings) if settings else ""
        spoken = _build_voice_friendly_config_summary(info, voice_display)
        technical = self._format_system_info_reference_section(info)
        return spoken + _REFERENCE_MARKER + technical
    
    def _get_voice_display_name(self, settings: dict) -> str:
        """Get the display name for the configured voice."""
        try:
            voice_provider = settings.get('voice_provider', 'kokoro')
            
            if voice_provider == 'kokoro':
                voice_id = settings.get('kokoro_voice', 'af_heart')
                # Map voice ID to display name
                try:
                    from distr.core.agent.session import KOKORO_VOICES
                    return KOKORO_VOICES.get(voice_id, voice_id.replace('af_', '').replace('am_', '').title())
                except ImportError:
                    # Fallback: extract name from ID
                    return voice_id.replace('af_', '').replace('am_', '').title()
            elif voice_provider == 'elevenlabs':
                voice_id = settings.get('elevenlabs_voice', '')
                return voice_id if voice_id else 'Not set'
            elif voice_provider == 'openai':
                voice_id = settings.get('openai_voice', '')
                return voice_id if voice_id else 'Not set'
            else:
                return 'Not set'
        except Exception as e:
            logger.error(f"Error getting voice display name: {e}")
            return 'Not set'
    
    def _format_system_info_reference_section(self, info: dict) -> str:
        """Structured details for screen or follow-up; not meant to be read aloud (after REFERENCE:)."""
        lines = []
        lines.append("=== SYSTEM INFORMATION ===\n")
        
        # Operating System
        lines.append("OPERATING SYSTEM:")
        os_info = info.get('operating_system', {})
        lines.append(f"  System: {os_info.get('system', 'Unknown')} {os_info.get('release', '')}")
        lines.append(f"  Version: {os_info.get('version', 'Unknown')}")
        lines.append(f"  Machine: {os_info.get('machine', 'Unknown')}")
        lines.append(f"  Python: {os_info.get('python_version', 'Unknown')} ({os_info.get('python_implementation', 'Unknown')})")
        lines.append("")
        
        # Configured Models (what's actually set)
        configured = info.get('configured_models', {})
        lines.append("CONFIGURED MODELS:")
        conv = configured.get('conversational', {})
        lines.append(f"  Conversational LLM: {conv.get('provider', 'Not set')} - {conv.get('model', 'Not set')}")
        coding = configured.get('coding', {})
        lines.append(f"  Coding LLM: {coding.get('provider', 'Not set')} - {coding.get('model', 'Not set')}")
        vision = configured.get('vision', {})
        lines.append(f"  Vision LLM: {vision.get('provider', 'Not set')} - {vision.get('model', 'Not set')}")
        image = configured.get('image', {})
        lines.append(f"  Image LLM: {image.get('provider', 'Not set')} - {image.get('model', 'Not set')}")
        lines.append("")
        
        # LLM Settings (for reference)
        settings = info.get('settings', {})
        llm = settings.get('llm', {})
        lines.append("LLM SETTINGS (Detailed):")
        lines.append(f"  Conversational: {llm.get('conversational_provider', 'Not set')} - {llm.get('conversational_model', 'Not set')}")
        lines.append(f"  Coding: {llm.get('coding_provider', 'Not set')} - {llm.get('coding_model', 'Not set')}")
        lines.append(f"  Vision: {llm.get('vision_provider', 'Not set')} - {llm.get('vision_model', 'Not set')}")
        lines.append(f"  Image: {llm.get('image_provider', 'Not set')} - {llm.get('image_model', 'Not set')}")
        lines.append("")
        
        # TTS Settings
        tts = settings.get('tts', {})
        lines.append("TEXT-TO-SPEECH (TTS):")
        lines.append(f"  Provider: {tts.get('provider', 'Not set')}")
        lines.append(f"  Voice: {tts.get('voice', 'Not set')}")
        lines.append("")
        
        # STT Settings
        stt = settings.get('stt', {})
        lines.append("SPEECH-TO-TEXT (STT):")
        lines.append(f"  Model: {stt.get('model', 'Not set')}")
        lines.append("")
        
        # Audio Devices
        audio = settings.get('audio', {})
        lines.append("AUDIO DEVICES:")
        lines.append(f"  Input: {audio.get('input_device', 'Not set')}")
        lines.append(f"  Output: {audio.get('output_device', 'Not set')}")
        lines.append("")
        
        # Mode
        mode = settings.get('mode', {})
        lines.append("MODE:")
        lines.append(f"  Hands-Free: {'Enabled' if mode.get('hands_free', True) else 'Disabled (PTT mode)'}")
        lines.append(f"  Listening: {'Enabled' if mode.get('listening', True) else 'Disabled'}")
        lines.append("")
        
        
        # API Providers
        api_providers = info.get('api_providers', {})
        lines.append("API PROVIDERS:")
        for provider, status in api_providers.items():
            enabled = "✓" if status.get('enabled', False) else "✗"
            validated = "✓" if status.get('validated', False) else "✗"
            lines.append(f"  {provider.capitalize()}: Enabled={enabled}, Key={validated}")
        lines.append("")
        lines.append(
            "For spoken tips and everything you can ask, say what can you do or open Help in the app."
        )
        lines.append("")

        return "\n".join(lines)

