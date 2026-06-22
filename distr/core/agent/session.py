"""
AgentSession - Pipecat-based Voice Agent Session

This module provides a Pipecat-based agent session that integrates with the
application's settings system and supports real-time voice interaction.

Key Features:
- Pipecat pipeline integration
- Signal-based communication
- Multithreaded execution
- Settings integration and reloading
- Device selection support
- Hands-free and push-to-talk modes
"""

import asyncio
import os
import sys
import logging
import signal
import time
import threading
from typing import Optional, Dict, Any
from queue import Queue, Empty

# Suppress MallocStackLogging warnings on macOS
if sys.platform == 'darwin':
    if "MallocStackLogging" in os.environ:
        del os.environ["MallocStackLogging"]
    if "MallocStackLoggingDirectory" in os.environ:
        del os.environ["MallocStackLoggingDirectory"]

# Suppress specific RuntimeWarning about unawaited coroutine
import warnings
warnings.filterwarnings(
    "ignore",
    category=RuntimeWarning,
    message="coroutine 'FrameProcessor.__process_frame_task_handler' was never awaited",
)

# Local imports
from .libs import (
    Pipeline, PipelineRunner, PipelineTask,
    LocalAudioTransportParams, SileroVADAnalyzer, VADParams,
    sd,
)
from distr.core.agent.transport import HotSwappableLocalAudioTransport
from distr.core.audio.echo_canceller import ReferenceBuffer, NLMSEchoCanceller
from .services import WhisperSTTService
try:
    from .services import VoskSTTService
    VOSK_AVAILABLE = (VoskSTTService is not None)
except ImportError:
    VOSK_AVAILABLE = False
    VoskSTTService = None
try:
    from .services import OpenAIWhisperSTTService
    OPENAI_STT_AVAILABLE = (OpenAIWhisperSTTService is not None)
except ImportError:
    OPENAI_STT_AVAILABLE = False
    OpenAIWhisperSTTService = None
try:
    from .services import AssemblyAISTTService
    ASSEMBLYAI_STT_AVAILABLE = (AssemblyAISTTService is not None)
except ImportError:
    ASSEMBLYAI_STT_AVAILABLE = False
    AssemblyAISTTService = None

from distr.core.chat_manager import ChatManagerCore
from distr.core.signals import signal_manager
from distr.core.db import get_session, Chat, Settings
from distr.core.llm_factory import normalize_provider

logger = logging.getLogger(__name__)

# Re-export for backward compatibility (importers of session.KOKORO_VOICES still work)
from distr.core.agent.constants import (
    DEFAULT_KOKORO_VOICE, DEFAULT_KOKORO_AGENT,
    DEFAULT_MODELS, PROVIDER_TO_ENGINE, API_KEY_NAMES,
    KOKORO_VOICES, KOKORO_VOICE_BY_DISPLAY_NAME,
    VAD_DEFAULT_THRESHOLD, VAD_CONFIDENCE_MIN, VAD_CONFIDENCE_MAX, VAD_START_SECS,
    DEFAULT_OPENAI_WHISPER_MODEL, DEFAULT_ASSEMBLYAI_MODEL,
    DEFAULT_VOSK_MODEL_DIR, WELCOME_DELAY_SECS, COMMAND_POLL_TIMEOUT,
)


class AgentSession:
    """
    Pipecat-based agent session that manages voice interaction.
    
    This class creates and manages a Pipecat pipeline with STT, LLM, and TTS services.
    It supports settings-based configuration, device selection, and signal-based
    communication with the main application.
    """
    
    # Default configurations (used when settings are not available)
    DEFAULT_CONFIG = {
        'stt': {
            'engine': 'whisper',
            'model_path': 'base.en'
        },
        'llm': {
            'engine': 'ollama',
            'model_name': DEFAULT_MODELS['ollama'],
            'system_prompt': None
        },
        'tts': {
            'engine': 'kokoro',
            'voice_name': DEFAULT_KOKORO_VOICE
        },
        'audio': {
            'input_sample_rate': 16000,
            'output_sample_rate': 44100,  # Stable playback rate; transport resamples TTS to this
            'input_device': None,  # None = system default
            'output_device': None  # None = system default
        },
        'vad': {
            'enabled': True
        }
    }
    
    def __init__(self, 
                 settings: Optional[Dict[str, Any]] = None,
                 input_device: Optional[str] = None,
                 output_device: Optional[str] = None,
                 command_queue: Optional[Queue] = None,
                 event_queue: Optional[Queue] = None,
                 confirmation_results_dict=None,
                 skip_welcome: bool = False,
                 agent_current_chat_id: Optional[int] = None,
                 **kwargs):
        """
        Initialize the agent session.
        
        Args:
            settings: Settings dictionary from database
            input_device: Input device name (overrides settings)
            output_device: Output device name (overrides settings)
            command_queue: Queue for receiving commands from main process
            event_queue: Queue for sending events to main process
            skip_welcome: If True, skip the welcome message on startup (used for reloads)
            agent_current_chat_id: Optional chat ID passed from model_hot_reload signal (for hot reloads)
        """
        self.logger = logging.getLogger(__name__)
        self.settings = settings or {}
        self._agent_current_chat_id_from_signal = agent_current_chat_id  # Store for _load_config
        self.command_queue = command_queue
        self.event_queue = event_queue
        self.confirmation_results_dict = confirmation_results_dict
        self.skip_welcome = skip_welcome

        # Set module-level event queue so speak_text_directly_event_queue()
        # can route TTS text through the event_queue from anywhere in the
        # agent subprocess (tools, LLM service, workflows, etc.).
        from distr.core.signals import set_agent_event_queue
        set_agent_event_queue(event_queue)
        
        self.running = False
        self._stop_event = threading.Event()
        self._reload_event = asyncio.Event() # Changed from threading.Event() as per instruction
        self._restart_requested = False # Added as per instruction
        
        # Pipeline components
        self.pipeline = None
        self.runner = None
        self.task = None
        self.transport = None
        self.stt_service = None
        self.llm_service = None
        self.tts_service = None
        self.vad_analyzer = None
        self.chat_manager = None  # Will be instantiated in _create_services
        
        # Thread management
        self._command_thread = None
        
        # Welcome message task (for cancellation)
        self._welcome_task = None
        
        # Device configuration
        self.input_device = input_device
        self.output_device = output_device
        
        # State management
        self.is_listening = True  # Default to listening enabled
        # Default to False (push-to-talk mode) if not set
        # Explicitly check for False to avoid any truthy value issues
        hands_free_setting = settings.get('hands_free_mode', False)
        self.is_hands_free = bool(hands_free_setting) if hands_free_setting is not None else False
        self.ptt_active = False  # Push-to-talk state
        self.is_dictating = False
        
        # Log initial state for debugging
        self.logger.debug(f"AgentSession initialized: hands_free_mode={self.is_hands_free} (PTT mode: {not self.is_hands_free}, default: push-to-talk)")
        self.logger.debug(f"  - Raw setting value: {settings.get('hands_free_mode', 'NOT SET')}")
        self.logger.debug(f"  - Settings keys: {list(settings.keys()) if settings else 'NO SETTINGS'}")
        self.logger.debug(f"  - Agent Model in Settings: {settings.get('agent_model', 'NOT SET')}")
        
        # Agent name default; will be set from config (chat's voice) after _load_config so role matches voice
        self.agent_name = DEFAULT_KOKORO_AGENT
        self._custom_voice_personality = ''  # Personality from custom voice DB, appended to role
        
        # Load configuration from settings/DB (includes current chat's voice from agent_current_chat_id)
        self.config = self._load_config()
        
        # Set agent name and role from TTS config (chat's voice) so LLM gets correct persona.
        try:
            from . import service_factory
            tts_cfg = self.config.get('tts') or {}
            engine = (tts_cfg.get('engine') or '').strip().lower()
            # Resolve voice_model for custom voice personality loading
            vm = tts_cfg.get('voice_name') or tts_cfg.get('voice_id') or ''
            if vm:
                self._load_custom_voice_personality(engine or 'kokoro', vm)
            self.agent_name = service_factory.resolve_agent_name_from_tts_config(tts_cfg, self.settings)
            self.role = self._load_agent_role()
            self.logger.debug("Agent name from config: '%s' (%s)", self.agent_name, engine or 'default')
        except Exception as e:
            self.logger.warning("Could not set agent/role from config (using fallback): %s", e, exc_info=True)
            self.agent_name = DEFAULT_KOKORO_AGENT
            self.role = self._load_agent_role()
        
        # Log final STT configuration for debugging
        self.logger.debug(f"🔍 FINAL STT CONFIG: engine={self.config['stt'].get('engine')}, model={self.config['stt'].get('model', 'N/A')}")
        self.logger.debug(f"AgentSession initialized with agent: {self.agent_name}")
    
    def _determine_agent_name(self) -> str:
        """Determine agent name from current TTS config or settings.
        
        Delegates to service_factory.resolve_agent_name_from_tts_config which is
        the single source of truth for voice → display name resolution.
        """
        from . import service_factory
        tts_cfg = (self.config or {}).get('tts') or {}
        return service_factory.resolve_agent_name_from_tts_config(tts_cfg, self.settings)
    
    def _load_custom_voice_personality(self, provider: str, voice_id: str):
        """Look up personality from CustomVoice DB for the given provider/voice_id.
        Sets self._custom_voice_personality. Call before _load_agent_role()."""
        self._custom_voice_personality = ''
        if not voice_id:
            return
        try:
            from distr.core.db import get_session, CustomVoice
            session = get_session()
            try:
                if voice_id.startswith('custom_'):
                    db_id = int(voice_id.split('_', 1)[1])
                    cv = session.query(CustomVoice).filter(CustomVoice.id == db_id, CustomVoice.status == 'ready').first()
                else:
                    cv = session.query(CustomVoice).filter(
                        CustomVoice.provider == provider,
                        CustomVoice.provider_voice_id == voice_id,
                        CustomVoice.status == 'ready',
                    ).first()
                if cv and cv.personality:
                    self._custom_voice_personality = cv.personality.strip()
            finally:
                session.close()
        except Exception as e:
            self.logger.debug("Could not load custom voice personality: %s", e)

    def _load_agent_role(self) -> str:
        """Return the agent persona, appending any custom voice personality."""
        from .constants import DEFAULT_PERSONA
        role = DEFAULT_PERSONA
        if getattr(self, '_custom_voice_personality', ''):
            role += f"\n\n{self._custom_voice_personality}"
        return role
    
    def _load_config(self, agent_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load configuration from settings or use defaults"""
        agent_config = agent_config or {}
        config = self.DEFAULT_CONFIG.copy()
        
        # Log all settings for debugging
        self.logger.debug(f"🔧 _load_config: settings keys = {list(self.settings.keys())}")
        self.logger.debug(f"🔧 _load_config: agent_config = {agent_config}")
        
        # STT configuration
        # PRIORITY: User settings (transcription_model) FIRST, then agent config as fallback
        transcription_model = self.settings.get('transcription_model', '')
        # Also check old input_speech setting for backwards compatibility
        if not transcription_model:
            transcription_model = self.settings.get('input_speech', '')
            if transcription_model:
                self.logger.debug(f"📝 STT Configuration: Using legacy input_speech='{transcription_model}' (transcription_model not set)")
        
        # If still no transcription model, fall back to agent config
        if not transcription_model:
            stt_engine = agent_config.get('sst') or agent_config.get('stt')
            if stt_engine:
                transcription_model = stt_engine
                self.logger.debug(f"📝 STT Configuration: Using agent config fallback: '{transcription_model}'")
        
        self.logger.debug(f"📝 STT Configuration: transcription_model='{transcription_model}'")
        self.logger.debug(f"📝 STT Configuration: All settings keys: {list(self.settings.keys())}")
        self.logger.debug(f"📝 STT Configuration: input_speech='{self.settings.get('input_speech', 'NOT SET')}'")
        
        from . import config_loader
        stt_parsed = config_loader.resolve_stt_config(transcription_model)
        if stt_parsed['engine']:
            config['stt']['engine'] = stt_parsed['engine']
            if 'model' in stt_parsed:
                config['stt']['model'] = stt_parsed['model']
            self.logger.debug(f"✅ Selected STT engine: {stt_parsed['engine']} (model: {stt_parsed.get('model', 'N/A')})")
        # Fallback to old input_speech setting
        elif self.settings.get('input_speech') == 'Whisper':
            config['stt']['engine'] = 'whisper'
            self.logger.debug(f"✅ Selected STT engine: whisper (from input_speech fallback)")
        elif self.settings.get('input_speech') == 'Vosk':
            config['stt']['engine'] = 'vosk'
            self.logger.debug(f"✅ Selected STT engine: vosk (from input_speech fallback)")
        else:
            self.logger.warning(f"⚠️  No STT engine matched transcription_model='{transcription_model}', using default: whisper")
            config['stt']['engine'] = 'whisper'
        
        # LLM and TTS configuration: check current chat first (provider, model, voice), then fall back to settings
        # Query database directly since ChatManager might not exist yet during initialization
        chat_provider = None
        chat_model = None
        chat_voice_provider = None
        chat_voice_model = None
        try:
            session = get_session()
            settings_row = session.query(Settings).first()
            chat = None
            
            # Use agent_current_chat_id from signal first (for hot reloads), then from settings
            effective_chat_id = self._agent_current_chat_id_from_signal
            if not effective_chat_id and settings_row:
                effective_chat_id = getattr(settings_row, 'agent_current_chat_id', None) if settings_row else None
            
            last_cid = getattr(settings_row, 'last_chat_id', None) if settings_row else None
            self.logger.debug(f"🔧 _load_config: effective_chat_id={effective_chat_id} (from_signal={self._agent_current_chat_id_from_signal is not None}), last_chat_id={last_cid}")
            # Store for _create_services so chat_manager gets initial current chat (PTT and first input use same chat as web).
            self._initial_chat_id = effective_chat_id or last_cid

            # Prefer agent_current_chat_id (chat "in agent") so TTS/voice from loaded chat is used after create/load-in-agent
            if effective_chat_id:
                chat = session.get(Chat, effective_chat_id)
                if chat:
                    self.logger.debug(f"🔧 _load_config: Loaded chat by effective_chat_id={effective_chat_id}: provider={getattr(chat, 'provider', None)}, model_name={getattr(chat, 'model_name', None)}")
                else:
                    self.logger.warning(f"🔧 _load_config: No chat found for effective_chat_id={effective_chat_id}, falling back to last_chat_id")
            if not chat and last_cid:
                chat = session.get(Chat, last_cid)
                if chat:
                    self.logger.debug(f"🔧 _load_config: Loaded chat by last_chat_id={last_cid}: provider={getattr(chat, 'provider', None)}, model_name={getattr(chat, 'model_name', None)}")
            if chat:
                if chat.provider:
                    raw = (chat.provider or "").strip()
                    chat_provider = normalize_provider(raw) if raw else None
                    chat_model = chat.model_name
                    self.logger.debug(f"Using chat provider from database: {chat_provider}, model: {chat_model}")
                if getattr(chat, 'voice_provider', None) or getattr(chat, 'voice_model', None):
                    from .constants import normalize_voice_provider as _nvp
                    chat_voice_provider = _nvp(chat.voice_provider) if chat.voice_provider else None
                    chat_voice_model = (chat.voice_model or "").strip() if chat.voice_model else None
                    if chat_voice_provider or chat_voice_model:
                        self.logger.debug(f"Using chat voice from database: provider={chat_voice_provider}, model={chat_voice_model} (TTS will use this voice)")
            session.close()
        except Exception as e:
            self.logger.warning(f"Could not get chat provider from database: {e}")
        
        # Also try ChatManager if it exists (for hot-reload scenarios)
        if not chat_provider and self.chat_manager:
            current_chat_id = self.chat_manager.get_current_chat()
            if current_chat_id:
                try:
                    session = get_session()
                    chat = session.get(Chat, current_chat_id)
                    if chat:
                        if chat.provider:
                            raw = (chat.provider or "").strip()
                            chat_provider = normalize_provider(raw) if raw else None
                            chat_model = chat.model_name
                            self.logger.debug(f"Using chat provider from ChatManager: {chat_provider}, model: {chat_model}")
                        if not chat_voice_provider and (getattr(chat, 'voice_provider', None) or getattr(chat, 'voice_model', None)):
                            chat_voice_provider = _nvp(chat.voice_provider) if chat.voice_provider else None
                            chat_voice_model = (chat.voice_model or "").strip() if chat.voice_model else None
                            if chat_voice_provider or chat_voice_model:
                                self.logger.debug(f"Using chat voice from ChatManager: provider={chat_voice_provider}, model={chat_voice_model}")
                    session.close()
                except Exception as e:
                    self.logger.warning(f"Could not get chat provider from ChatManager: {e}")
        
        # Use chat provider/model first; then conversational_llm_* (what web/settings UI shows); then legacy agent_*
        raw_provider = chat_provider or self.settings.get('conversational_llm_provider') or self.settings.get('agent_provider', 'Ollama')
        provider = normalize_provider(raw_provider)
        model_name = (chat_model or self.settings.get('conversational_llm_model') or self.settings.get('agent_model', '') or '').strip()

        # Infer provider from model when model clearly indicates a different provider
        from distr.core.llm_factory import infer_provider_from_model
        provider = infer_provider_from_model(provider, model_name)
        self.logger.debug("_load_config: LLM provider=%s model=%s", provider, model_name)

        # Map provider to engine and resolve config
        from .constants import PROVIDER_TO_ENGINE
        engine = PROVIDER_TO_ENGINE.get(provider, 'ollama')
        config['llm']['engine'] = engine
        config['llm']['model_name'] = model_name or agent_config.get('llm') or DEFAULT_MODELS.get(engine, DEFAULT_MODELS['ollama'])
        if engine in API_KEY_NAMES:
            config['llm']['api_key'] = self.settings.get(API_KEY_NAMES[engine], '')
        
        # TTS configuration
        # Prefer current chat's voice, then voice_provider setting, then tts_provider setting.
        from .constants import normalize_voice_provider
        tts_engine_override = agent_config.get('tts')
        if tts_engine_override:
            config['tts']['engine'] = normalize_voice_provider(tts_engine_override)

        # Resolve voice provider: chat voice > voice_provider setting > tts_provider setting
        from distr.core.agent.services.tts.registry import tts_registry
        voice_provider_raw = chat_voice_provider or self.settings.get('voice_provider', '') or self.settings.get('tts_provider', '')
        voice_provider = normalize_voice_provider(voice_provider_raw)
        enabled_voice_providers = set(tts_registry.provider_ids())
        if voice_provider not in enabled_voice_providers:
            self.logger.warning(
                "Voice provider %s is retired or unavailable; using Kokoro",
                voice_provider,
            )
            voice_provider = 'kokoro'
        voice_model_from_chat = chat_voice_model

        # Voice settings lookup table: provider -> (engine, voice_key, default, extra_keys)
        # Built dynamically from the TTS provider registry.
        _VOICE_SETTINGS = {d.id: d.get_voice_settings_entry() for d in tts_registry.all_providers()}
        vp_entry = _VOICE_SETTINGS.get(voice_provider, _VOICE_SETTINGS['kokoro'])
        tts_engine, voice_settings_key, voice_default, extra_keys = vp_entry
        config['tts']['engine'] = tts_engine
        resolved_voice = voice_model_from_chat or self.settings.get(voice_settings_key, voice_default)
        config['tts']['voice_id'] = resolved_voice
        config['tts']['voice_name'] = resolved_voice if tts_engine != 'elevenlabs' else None
        for cfg_key, settings_key in extra_keys.items():
            config['tts'][cfg_key] = self.settings.get(settings_key) or ''
        self.logger.debug("TTS: engine=%s voice=%s (from %s)", tts_engine, resolved_voice, "chat" if chat_voice_provider else "settings")
        # Add more TTS engines as needed
        
        # Audio device configuration
        if self.input_device:
            config['audio']['input_device'] = self.input_device
        elif self.settings.get('input_device') and self.settings.get('input_device') != 'System Default':
            config['audio']['input_device'] = self.settings.get('input_device')
        
        if self.output_device:
            config['audio']['output_device'] = self.output_device
        elif self.settings.get('output_device') and self.settings.get('output_device') != 'System Default':
            config['audio']['output_device'] = self.settings.get('output_device')
        
        # Keep hardware output at 44.1 kHz (Bluetooth/macOS native). TTS engines
        # emit at their own rates; transport resamples continuously to this rate.
        from .constants import SAMPLE_RATE_PLAYBACK
        config['audio']['output_sample_rate'] = SAMPLE_RATE_PLAYBACK
        
        return config
    
    def _get_device_index(self, device_name: Optional[str], is_input: bool) -> Optional[int]:
        """Get device index from device name."""
        from . import config_loader
        return config_loader.resolve_device_index(device_name, is_input, sd_module=sd)
    
    def _create_stt_service(self):
        """Create STT service based on configuration"""
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
        models_dir = os.path.join(base_dir, "distr", "core", "agent", "models")

        # Clean up old STT service before creating new one
        # CRITICAL: Preserve hands-free and PTT state before cleanup
        old_hands_free = None
        old_ptt_active = None
        if hasattr(self, 'stt_service') and self.stt_service is not None:
            old_service_type = type(self.stt_service).__name__
            self.logger.debug(f"Cleaning up old STT service: {old_service_type}")
            
            # Preserve state from old service
            if hasattr(self.stt_service, '_is_hands_free'):
                old_hands_free = self.stt_service._is_hands_free
            elif hasattr(self.stt_service, 'is_hands_free'):
                old_hands_free = self.stt_service.is_hands_free
            else:
                # Fall back to session state
                old_hands_free = self.is_hands_free
            
            if hasattr(self.stt_service, '_ptt_active'):
                old_ptt_active = self.stt_service._ptt_active
            elif hasattr(self.stt_service, 'ptt_active'):
                old_ptt_active = self.stt_service.ptt_active
            else:
                # Fall back to session state
                old_ptt_active = getattr(self, 'ptt_active', False)
            
            self.logger.debug(f"  Preserved state: hands_free={old_hands_free}, ptt_active={old_ptt_active}")
            
            try:
                # Try to clean up any resources
                # Use explicit cleanup method if available (e.g., WhisperSTTService)
                if hasattr(self.stt_service, 'cleanup'):
                    try:
                        self.logger.debug("Calling STT service cleanup method")
                        self.stt_service.cleanup()
                    except Exception as cleanup_error:
                        # Log but don't crash on cleanup errors (e.g., Metal backend issues)
                        self.logger.warning(f"Error during STT service cleanup: {cleanup_error}")

                # Fallback: manual cleanup for services without cleanup method
                if hasattr(self.stt_service, 'model'):
                    # Vosk/Whisper models might need cleanup
                    self.stt_service.model = None
                if hasattr(self.stt_service, 'recognizer'):
                    # Vosk recognizer cleanup
                    self.stt_service.recognizer = None
                # Clear audio buffers
                if hasattr(self.stt_service, '_audio_buffer'):
                    self.stt_service._audio_buffer = []
                if hasattr(self.stt_service, '_ptt_buffer_accumulator'):
                    self.stt_service._ptt_buffer_accumulator = []
            except Exception as e:
                self.logger.debug(f"Error cleaning up old STT service: {e}")
            self.stt_service = None
        
        # Create STT service
        stt_config = self.config['stt']

        if stt_config['engine'] == 'whisper':
            # Check if pywhispercpp is available (may fail to compile on Windows)
            from distr.core.agent.libs import WHISPER_AVAILABLE
            if not WHISPER_AVAILABLE:
                self.logger.warning("⚠️  Whisper.cpp (pywhispercpp) not available — falling back to Vosk STT")
                stt_config = dict(stt_config)
                stt_config['engine'] = 'vosk'
            else:
                whisper_model = stt_config['model_path']
                self.logger.info(f"🔧 Creating Whisper.cpp STT service with model: {whisper_model} (managed by pywhispercpp)")
                self.stt_service = WhisperSTTService(
                    model_path=whisper_model,
                    event_queue=self.event_queue,
                    is_hands_free=self.is_hands_free
                )
                self.logger.info(f"✅ Whisper.cpp STT service created successfully (type: {type(self.stt_service).__name__})")
        if stt_config['engine'] == 'vosk':
            if not VOSK_AVAILABLE or VoskSTTService is None:
                raise ImportError("VoskSTTService is not available. Please ensure vosk is installed and model is downloaded.")
            vosk_model_path = os.path.join(models_dir, DEFAULT_VOSK_MODEL_DIR)
            if not os.path.exists(vosk_model_path):
                raise FileNotFoundError(f"Vosk model not found at {vosk_model_path}. Please download it using Settings > AI > Transcription Model.")
            self.stt_service = VoskSTTService(
                model_path=vosk_model_path,
                event_queue=self.event_queue,
                is_hands_free=self.is_hands_free
            )
            self.logger.debug(f"✅ Vosk STT service created successfully")
        if stt_config['engine'] == 'openai_whisper':
            if not OPENAI_STT_AVAILABLE or OpenAIWhisperSTTService is None:
                raise ImportError("OpenAIWhisperSTTService is not available. Please ensure openai library is installed.")
            api_key = self.settings.get('openai_key', '')
            if not api_key:
                raise ValueError("OpenAI API key is required but not found in settings for STT")
            model = stt_config.get('model', DEFAULT_OPENAI_WHISPER_MODEL)
            self.logger.debug(f"Creating OpenAI Whisper STT service with model: {model}")
            self.stt_service = OpenAIWhisperSTTService(
                api_key=api_key,
                model=model,
                event_queue=self.event_queue,
                is_hands_free=self.is_hands_free
            )
            self.logger.debug(f"✅ OpenAI Whisper STT service created successfully")
        if stt_config['engine'] == 'assemblyai':
            if not ASSEMBLYAI_STT_AVAILABLE or AssemblyAISTTService is None:
                raise ImportError("AssemblyAISTTService is not available. Please ensure assemblyai library is installed. Install with: pip install assemblyai")
            api_key = self.settings.get('assemblyai_key', '')
            if not api_key:
                raise ValueError("AssemblyAI API key is required but not found in settings for STT")
            model_name = stt_config.get('model', DEFAULT_ASSEMBLYAI_MODEL)
            self.logger.info(f"🔧 Creating AssemblyAI STT service with model: {model_name}")
            self.stt_service = AssemblyAISTTService(
                api_key=api_key,
                model=model_name,
                event_queue=self.event_queue,
                is_hands_free=self.is_hands_free
            )
            self.logger.info(f"✅ AssemblyAI STT service created successfully (type: {type(self.stt_service).__name__})")
        if stt_config['engine'] not in (
            'whisper',
            'vosk',
            'openai_whisper',
            'assemblyai',
        ):
            raise ValueError(f"Unsupported STT engine: {stt_config['engine']}")
        
        # CRITICAL: Restore hands-free and PTT state after creating new STT service
        # This ensures all three interaction modes work correctly after swapping:
        #
        # 1. VAD (Voice Activity Detection):
        #    - Always enabled in the transport
        #    - Sends UserStartedSpeakingFrame/UserStoppedSpeakingFrame to STT
        #    - STT filters these based on hands_free mode:
        #      * Hands-free: VAD frames processed → can interrupt TTS/LLM
        #      * PTT: VAD frames silently ignored → no interruptions
        #
        # 2. Push-to-Talk (PTT) Mode:
        #    - set_ptt_active(True) when button pressed
        #    - STT cancels ongoing transcription, sends InterruptionFrame
        #    - Audio is KILLED (not ducked) via InterruptionFrame
        #    - VAD frames are ignored while PTT is active
        #
        # 3. Continuous Talk (Hands-free) Mode:
        #    - set_hands_free(True)
        #    - VAD frames are processed and can interrupt
        #    - Audio is ducked (volume reduced) when user speaks
        #    - Interruptions happen automatically via VAD
        if old_hands_free is not None or old_ptt_active is not None:
            # Use preserved state if available, otherwise use session state
            hands_free_state = old_hands_free if old_hands_free is not None else self.is_hands_free
            ptt_state = old_ptt_active if old_ptt_active is not None else getattr(self, 'ptt_active', False)
            
            self.logger.debug(f"🔄 Restoring STT state after swap: hands_free={hands_free_state}, ptt_active={ptt_state}")
            
            # Restore hands-free state (affects VAD interruption filtering)
            if hasattr(self.stt_service, 'set_hands_free'):
                self.stt_service.set_hands_free(hands_free_state)
                self.logger.debug(f"  ✅ Hands-free state restored: {hands_free_state} (VAD interruptions: {'enabled' if hands_free_state else 'filtered'})")
            else:
                self.logger.warning("  ⚠️  New STT service doesn't support set_hands_free() - VAD behavior may be incorrect")
            
            # Restore PTT state if it was active (affects interruption handling)
            if ptt_state:
                if hasattr(self.stt_service, 'set_ptt_active'):
                    self.stt_service.set_ptt_active(ptt_state, queue_interruption=True)
                    self.logger.debug(f"  ✅ PTT state restored: {ptt_state} (interruptions via InterruptionFrame)")
                else:
                    self.logger.warning("  ⚠️  New STT service doesn't support set_ptt_active() - PTT may not work correctly")
        else:
            # No old service existed, just ensure current session state is set
            if hasattr(self.stt_service, 'set_hands_free'):
                self.stt_service.set_hands_free(self.is_hands_free)
            if getattr(self, 'ptt_active', False):
                if hasattr(self.stt_service, 'set_ptt_active'):
                    self.stt_service.set_ptt_active(self.ptt_active)
        if getattr(self, 'is_dictating', False):
            if hasattr(self.stt_service, 'set_dictating'):
                self.stt_service.set_dictating(self.is_dictating)
        if hasattr(self.stt_service, 'set_vad_threshold'):
            try:
                self.stt_service.set_vad_threshold(
                    self.settings.get('vad_threshold', VAD_DEFAULT_THRESHOLD)
                )
            except Exception as e:
                self.logger.warning("Failed to initialize STT VAD threshold mapping: %s", e)

        self.logger.info(
            "✅ STT service swap complete - VAD enabled, hands_free=%s, ptt_active=%s, dictating=%s",
            self.is_hands_free,
            getattr(self, 'ptt_active', False),
            getattr(self, 'is_dictating', False),
        )
        
        self._emit_stt_ready("stt_service_created")

    def _emit_stt_ready(self, reason: str):
        """Notify the GUI that STT/PTT capture is safe to use."""
        if not self.event_queue:
            return
        try:
            self.event_queue.put(('stt_ready', {'reason': reason}), block=False)
            self.logger.info("Emitted stt_ready (%s)", reason)
        except Exception as e:
            self.logger.warning("Failed to emit stt_ready (%s): %s", reason, e)

    def _create_services(self):
        """Create STT, LLM, and TTS services based on configuration"""
        # Log system resources once at startup
        try:
            from distr.core.system_resources import log_system_resources
            log_system_resources()
        except Exception:
            pass

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
        models_dir = os.path.join(base_dir, "distr", "core", "agent", "models")

        # Create ChatManager for agent process
        # This ensures we have a database connection and can manage chats
        if not self.chat_manager:
            self.chat_manager = ChatManagerCore()
            self.logger.debug("ChatManagerCore instantiated in AgentSession (no Qt needed)")
            # Set initial current chat from settings so PTT and first input use same chat as web (congruent agent).
            if getattr(self, '_initial_chat_id', None):
                self.chat_manager.set_current_chat(self._initial_chat_id)
                self.logger.debug("ChatManager: set initial current chat to %s (from agent_current_chat_id/last_chat_id)", self._initial_chat_id)
            self._setup_signal_bridging()

        # Create STT service
        self._create_stt_service()

        # Determine agent name from TTS config BEFORE creating LLM service
        from . import service_factory
        tts_config = self.config['tts']
        self.agent_name = service_factory.resolve_agent_name_from_tts_config(tts_config, self.settings)
        self.logger.debug("Agent name set to '%s' (engine=%s)", self.agent_name, tts_config.get('engine'))
        
        # Warm the tool cache and start background embedding index build.
        # This populates _tool_cache so retrieval-based loading works, and
        # kicks off build_index_async so the embedding index is ready by the
        # time the first user message arrives.
        try:
            from distr.core.agent.tools.loader import warm_tool_cache
            warm_tool_cache(
                chat_manager=self.chat_manager,
                llm_service=None,  # LLM service not created yet
                tts_service=getattr(self, 'tts_service', None),
                llm_model=self.config['llm'].get('model_name'),
                event_queue=self.event_queue,
                command_queue=self.command_queue,
                confirmation_results_dict=self.confirmation_results_dict,
            )
        except Exception as e:
            self.logger.warning("warm_tool_cache failed (tools will load on demand): %s", e)

        try:
            from distr.core.mcp.runtime import init_mcp_stack

            init_mcp_stack()
        except Exception as e:
            self.logger.warning("init_mcp_stack failed (MCP tools unavailable): %s", e)

        # Create LLM service (delegates to service_factory via _create_llm_service_only)
        self._create_llm_service_only()
        self.logger.debug(f"LLM service created: {self.config['llm']['engine']} / {self.config['llm'].get('model_name')}")
        
        # Create TTS service via the registry-based service factory
        self._create_tts_service_only()

        # For ElevenLabs, update agent name with the resolved voice name
        tts_config = self.config['tts']
        if tts_config['engine'] == 'elevenlabs' and hasattr(self.tts_service, '_resolved_voice_name'):
            self._apply_agent_name(self.tts_service._resolved_voice_name)

        # For custom voices, ensure personality is in agent role before LLM/TTS wiring.
        _voice_key = tts_config.get('voice_name') or tts_config.get('voice_id') or ''
        if _voice_key.startswith('custom_'):
            from . import service_factory
            self._load_custom_voice_personality(tts_config.get('engine') or 'kokoro', _voice_key)
            self.role = self._load_agent_role()
            service_factory.update_agent_name_on_llm(self.llm_service, self.agent_name, self.role)

        # Pass TTS service to LLM service so tools can use it
        if hasattr(self, 'llm_service') and self.llm_service:
            self.llm_service.set_tts_service(self.tts_service)
            
    def _setup_signal_bridging(self):
        """Bridge signals from ChatManager and SignalManager to event_queue"""
        if not self.event_queue:
            return
            
        self.logger.debug("Setting up signal bridging to event_queue")
        
        # Bridge ChatManagerCore events (pure Python callbacks) to event_queue
        if self.chat_manager:
            self.chat_manager.on('chat_created',
                lambda chat_id: self.event_queue.put(('chat_created', {'chat_id': chat_id}))
            )
            self.chat_manager.on('chat_updated',
                lambda chat_id: self.event_queue.put(('chat_updated', {'chat_id': chat_id}))
            )
            self.chat_manager.on('chat_deleted',
                lambda chat_id: self.event_queue.put(('chat_deleted', {'chat_id': chat_id}))
            )
            self.chat_manager.on('current_chat_changed',
                lambda chat_id: self.event_queue.put(('current_chat_changed', {'chat_id': chat_id}))
            )
            
        # Bridge SignalManager signals (emitted by OllamaLLMService)
        # Note: signals must be connected to a slot, lambda works fine
        # Guard against the signal_manager being GC'd in spawned child processes
        # (no QApplication exists there, so QObject instances get deleted immediately)
        try:
            signal_manager.chat_stream_started.connect(
                lambda chat_id: self.event_queue.put(('chat_stream_started', {'chat_id': chat_id}))
            )
            signal_manager.chat_stream_token.connect(
                lambda token: self.event_queue.put(('chat_stream_token', {'token': token}))
            )
            signal_manager.chat_stream_finished.connect(
                lambda chat_id: self.event_queue.put(('chat_stream_finished', {'chat_id': chat_id}))
            )
            signal_manager.chat_stream_error.connect(
                lambda error: self.event_queue.put((
                    'chat_stream_error',
                    {
                        'error': error,
                        'chat_id': (self.chat_manager.get_current_chat() if self.chat_manager else None),
                    }
                ))
            )
            signal_manager.typing_indicator_changed.connect(
                lambda show: self.event_queue.put(('typing_indicator_changed', {'show': show}))
            )
            signal_manager.chat_message_added.connect(
                lambda chat_id, role, content: self.event_queue.put(('chat_message_added', {'chat_id': chat_id, 'role': role, 'content': content}))
            )
            signal_manager.transcription_progress.connect(
                lambda chat_id, status_text, done, clear_live_preview=False, discard_live_preview=False: self.event_queue.put(
                    ('transcription_progress', {
                        'chat_id': int(chat_id),
                        'status_text': status_text or '',
                        'done': bool(done),
                        'clear_live_preview': bool(clear_live_preview),
                        'discard_live_preview': bool(discard_live_preview),
                    }), block=False
                )
            )
            # Bridge speak_text_directly so TTS works from agent subprocess.
            # Qt signals don't cross process boundaries, so we route via event_queue
            # to the main process which re-emits on its signal_manager (which has slots connected).
            signal_manager.speak_text_directly.connect(
                lambda text: self.event_queue.put(('speak_text_directly', {'text': text}))
            )
            # Model hot-reload signal - update ChatManager model immediately
            signal_manager.model_hot_reload.connect(self._on_model_hot_reload)
            # Dictation hotkey signals — hold-to-dictate keyboard shortcut
            signal_manager.dictation_hotkey_pressed.connect(self._on_dictation_hotkey_pressed)
            signal_manager.dictation_hotkey_released.connect(self._on_dictation_hotkey_released)
            signal_manager.ticket_dictation_hotkey_pressed.connect(self._on_ticket_dictation_hotkey_pressed)
            signal_manager.ticket_dictation_hotkey_released.connect(self._on_dictation_hotkey_released)
            self.logger.debug("Signal bridging setup complete")
        except RuntimeError as e:
            # signal_manager QObject has been deleted (happens in spawned child processes
            # where no QApplication exists). Agent uses event_queue for all comms anyway.
            self.logger.debug("Signal bridging skipped (no QApplication in this process): %s", e)
    
    def _on_model_hot_reload(self, provider: str, model_name: str, chat_id: Optional[int] = None):
        """Handle model hot-reload signal — hot-swap LLM and TTS to match chat.
        
        NOTE: In the agent subprocess this is connected to signal_manager.model_hot_reload
        (a Qt signal), but since there's no Qt event loop in the agent process, this handler
        is effectively unreachable there. The agent uses event_queue instead, which routes
        through check_agent_events -> _cmd_hot_swap_llm in command_handler.py.
        
        Kept for the main-process path (settings UI model change) where Qt signals work.
        Delegates to command_handler._cmd_hot_swap_llm logic to avoid duplication.
        """
        from distr.core.chat import valid_llm_providers

        if model_name in valid_llm_providers():
            self.logger.warning("AgentSession: Rejecting provider name as model: %s", model_name)
            return

        self.logger.debug("Model hot-reload (signal): %s / %s (chat_id=%s)", provider, model_name, chat_id)

        if chat_id:
            self._agent_current_chat_id_from_signal = chat_id
        if self.chat_manager:
            self.chat_manager.update_provider(provider)
            self.chat_manager.update_model(model_name)

        # Delegate to the same path as the command queue handler
        from .command_handler import _cmd_hot_swap_llm
        _cmd_hot_swap_llm(self, {
            'provider': provider,
            'model_name': model_name,
            'chat_id': chat_id,
        })

    def _on_dictation_hotkey_pressed(self):
        try:
            if self.llm_service and hasattr(self.llm_service, '_start_dictation'):
                self.llm_service._start_dictation(one_shot=True)
        except Exception as e:
            self.logger.debug("Dictation hotkey press failed: %s", e)

    def _on_ticket_dictation_hotkey_pressed(self):
        try:
            if self.llm_service and hasattr(self.llm_service, '_start_dictation'):
                self.llm_service._start_dictation(one_shot=True, output_mode="ticket")
        except Exception as e:
            self.logger.debug("Ticket dictation hotkey press failed: %s", e)

    def _on_dictation_hotkey_released(self):
        try:
            if self.llm_service and hasattr(self.llm_service, '_stop_dictation'):
                if getattr(self.llm_service, '_dictation_one_shot', False):
                    self.logger.debug("Dictation hotkey release: waiting for one-shot transcript before stopping")
                    return
                self.llm_service._stop_dictation()
        except Exception as e:
            self.logger.debug("Dictation hotkey release failed: %s", e)

    def _create_llm_service_only(self):
        """Create a new LLM service from current self.config['llm'].

        Returns the newly created service (also sets self.llm_service).
        """
        from . import service_factory
        self.llm_service = service_factory.create_llm_service(
            self.config['llm'],
            role=self.role,
            agent_name=self.agent_name,
            event_queue=self.event_queue,
            is_listening=self.is_listening,
            chat_manager=self.chat_manager,
            command_queue=self.command_queue,
            confirmation_results_dict=self.confirmation_results_dict,
            is_hands_free=self.is_hands_free,
            voice_enabled=self.settings.get('voice_enabled', self.settings.get('chat_voice_enabled', True)),
            tts_service=getattr(self, 'tts_service', None),
        )
        return self.llm_service

    def _hot_swap_llm_service(
        self,
        provider: str,
        model_name: str,
        chat_id: int = None,
        speak: bool = None,
        voice_provider: str = None,
        voice_model: str = None,
    ):
        """Swap the LLM service in the running pipeline without restarting.

        Follows the same pattern as the proven STT hot-swap (preserve pipeline
        direction + event loop, replace processor in _processors list).
        speak: When provided (from web request), set speaker on new LLM immediately so TTS responds.
        voice_provider/voice_model: When provided (e.g. from create-chat), set agent identity from
        these so persona matches the new chat immediately.
        """
        self.logger.debug(
            "HOT-SWAP LLM: provider=%s, model=%s, chat_id=%s, speak=%s, voice=%s/%s",
            provider, model_name, chat_id, speak, voice_provider, voice_model,
        )

        old_service = self.llm_service
        old_pipeline_direction = getattr(old_service, '_pipeline_direction', None) if old_service else None
        old_event_loop = getattr(old_service, '_event_loop', None) if old_service else None

        norm_provider = normalize_provider(provider)
        engine = PROVIDER_TO_ENGINE.get(norm_provider, 'ollama')

        # Reload config for new provider/model
        self.config['llm']['engine'] = engine
        self.config['llm']['model_name'] = model_name

        # Resolve API key from settings
        from distr.core.settings import load_settings_from_db
        fresh_settings = load_settings_from_db()
        self.settings = fresh_settings
        if engine in API_KEY_NAMES:
            self.config['llm']['api_key'] = (fresh_settings.get(API_KEY_NAMES[engine]) or '').strip()

        # Reload agent role so persona matches the chat's voice (use params when provided, else DB)
        vp = (voice_provider or '').strip() or None
        vm = (voice_model or '').strip() or None
        if not (vp and vm) and chat_id:
            try:
                from distr.core.db import get_session, Chat
                with get_session() as db_sess:
                    chat = db_sess.get(Chat, chat_id)
                    if chat and getattr(chat, 'voice_model', None):
                        vp = (chat.voice_provider or '').strip() or None
                        vm = (chat.voice_model or '').strip() or None
            except Exception as e:
                self.logger.warning("HOT-SWAP LLM: could not load chat voice info: %s", e)
        if vp and vm:
            from . import service_factory
            new_name = service_factory.resolve_voice_to_display_name(vp, vm, self.settings or {})
            if new_name != self.agent_name:
                self.agent_name = new_name
                self.role = self._load_agent_role()
                self.logger.info("HOT-SWAP LLM: agent name changed to %s", self.agent_name)

        # Disconnect old LLM service's ChatManagerCore event listeners before replacing.
        if old_service and self.chat_manager:
            for event_name, method_name in [('chat_deleted', 'on_chat_deleted'),
                                             ('chat_cleared', 'on_chat_cleared')]:
                handler = getattr(old_service, method_name, None)
                if handler:
                    self.chat_manager.off(event_name, handler)

        # Create new service
        try:
            self._create_llm_service_only()
        except Exception as e:
            self.logger.error("HOT-SWAP LLM: failed to create new service: %s", e, exc_info=True)
            return

        # Copy preserved pipeline state
        if old_pipeline_direction is not None:
            self.llm_service._pipeline_direction = old_pipeline_direction
        if old_event_loop is not None:
            self.llm_service._event_loop = old_event_loop

        # Apply web speak override immediately (new LLM created from settings; web request takes precedence)
        if speak is not None:
            self.llm_service.set_speaker_enabled(bool(speak))
            self.logger.debug("HOT-SWAP LLM: speaker set to %s (from web request)", speak)

        # Replace in pipeline
        from . import service_factory
        if hasattr(self, 'pipeline') and self.pipeline is not None and old_service is not None:
            if not service_factory.swap_processor_in_pipeline(self.pipeline, old_service, self.llm_service):
                self.logger.warning("HOT-SWAP LLM: could not find old service in pipeline processors")

        # Mark the new processor as started so Pipecat doesn't reject frames.
        # We can't send StartFrame via process_frame because the TaskManager
        # isn't initialized yet (it's created BY StartFrame processing).
        setattr(self.llm_service, '_FrameProcessor__started', True)
        self.logger.debug("HOT-SWAP LLM: marked new service as started")

        # Update chat_manager to reflect the provider/model we actually use.
        # NOTE: Do NOT call set_current_chat here — the caller (_cmd_current_chat_changed)
        # handles that after the swap to avoid triggering on_chat_changed on the old service.
        if chat_id and self.chat_manager:
            if provider and model_name:
                self.chat_manager.current_provider = norm_provider
                self.chat_manager.current_model = model_name

        self.logger.debug("HOT-SWAP LLM: complete (engine=%s, model=%s)", engine, model_name)

    def _do_elevenlabs_quota_fallback(self):
        """Fallback to Kokoro when ElevenLabs quota exceeded. Returns new TTS service for retry."""
        kokoro_voice = (self.settings or {}).get('kokoro_voice', '') or DEFAULT_KOKORO_VOICE
        self._hot_swap_tts_service('kokoro', kokoro_voice)
        return self.tts_service

    def _create_tts_service_only(self):
        """Create a TTS service from current self.config['tts'].

        Returns the newly created service (also sets self.tts_service).
        """
        from . import service_factory
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
        models_dir = os.path.join(base_dir, "distr", "core", "agent", "models")
        # Build a settings dict with _event_queue so the factory can pass it through
        factory_settings = dict(self.settings)
        factory_settings['_event_queue'] = self.event_queue
        factory_settings['_on_quota_exceeded'] = self._do_elevenlabs_quota_fallback
        self.tts_service = service_factory.create_tts_service(
            self.config['tts'],
            settings=factory_settings,
            stt_service=self.stt_service,
            is_hands_free=self.is_hands_free,
            models_dir=models_dir,
        )
        return self.tts_service

    def _hot_swap_tts_service(self, voice_provider: str, voice_model: str):
        """Swap TTS service in the running pipeline without restarting."""
        from .constants import normalize_voice_provider
        from . import service_factory
        from distr.core.agent.services.tts.registry import tts_registry
        from distr.core.settings import load_settings_from_db

        self.settings = load_settings_from_db()

        vp = normalize_voice_provider(voice_provider)
        self.logger.debug("HOT-SWAP TTS: provider=%s model=%s", vp, voice_model)

        old_service = self.tts_service
        old_pipeline_direction = getattr(old_service, '_pipeline_direction', None) if old_service else None
        old_event_loop = getattr(old_service, '_event_loop', None) if old_service else None

        # Resolve agent name from the new voice
        new_agent_name = service_factory.resolve_voice_to_display_name(vp, voice_model or '', self.settings or {})
        self._load_custom_voice_personality(vp, voice_model or '')

        # Look up the provider descriptor from the registry
        try:
            descriptor = tts_registry.get(vp)
        except KeyError:
            self.logger.warning("HOT-SWAP TTS: unknown provider %s, skipping", voice_provider)
            return

        # Get hot-swap config from the descriptor
        hot_swap_cfg = descriptor.get_hot_swap_config(voice_model, self.settings or {})

        # No-op guard: if target TTS config already matches current runtime config,
        # do not recreate/swap the service.
        current_engine = self.config['tts'].get('engine')
        current_voice_name = self.config['tts'].get('voice_name')
        current_voice_id = self.config['tts'].get('voice_id')
        target_engine = hot_swap_cfg.get('engine')
        target_voice_name = hot_swap_cfg.get('voice_name')
        target_voice_id = hot_swap_cfg.get('voice_id')
        if (
            old_service is not None
            and current_engine == target_engine
            and current_voice_name == target_voice_name
            and current_voice_id == target_voice_id
        ):
            self.logger.debug(
                "HOT-SWAP TTS: no-op (already at target config: engine=%s voice_name=%s voice_id=%s)",
                target_engine, target_voice_name, target_voice_id,
            )
            return

        # Update self.config['tts'] with the hot-swap config
        self.config['tts']['engine'] = hot_swap_cfg['engine']
        if 'voice_name' in hot_swap_cfg:
            self.config['tts']['voice_name'] = hot_swap_cfg['voice_name']
        if 'voice_id' in hot_swap_cfg:
            self.config['tts']['voice_id'] = hot_swap_cfg['voice_id']
        if 'api_key' in hot_swap_cfg:
            self.config['tts']['api_key'] = hot_swap_cfg['api_key']
        if 'device' in hot_swap_cfg:
            self.config['tts']['device'] = hot_swap_cfg['device']

        # Unload Kanade voice cloner if switching away from a custom Kokoro voice
        if hot_swap_cfg.get('unload_kanade'):
            try:
                from distr.core.audio.voice_cloner import unload_model
                unload_model()
            except Exception:
                pass

        # Clear custom voice personality only when leaving a custom_* voice.
        if not (voice_model or '').startswith('custom_'):
            self._custom_voice_personality = ''

        # --- In-place swap path ---
        if hot_swap_cfg.get('in_place') and old_service is not None:
            if vp == 'kokoro':
                from .services import KokoroTTSService
                resolved = hot_swap_cfg['voice_name']
                self._load_custom_voice_personality('kokoro', resolved)

                if resolved.startswith('custom_'):
                    _ref_path, _base_voice = self._resolve_kokoro_custom_voice(resolved)
                    new_agent_name = service_factory.resolve_voice_to_display_name('kokoro', resolved, self.settings or {})
                    if isinstance(old_service, KokoroTTSService):
                        old_service.set_voice(_base_voice)
                        old_service.set_reference_voice(_ref_path)
                        self._apply_agent_name(new_agent_name)
                        self.logger.debug("HOT-SWAP TTS: complete (voice cloning, ref=%s)", _ref_path)
                        if self.chat_manager:
                            self.chat_manager.current_voice_provider = vp
                            self.chat_manager.current_voice_model = voice_model or ''
                        return
                else:
                    from .constants import KOKORO_VOICES
                    new_agent_name = KOKORO_VOICES.get(resolved, DEFAULT_KOKORO_AGENT)
                    if isinstance(old_service, KokoroTTSService):
                        old_service.set_voice(resolved)
                        old_service.set_reference_voice(None)
                        self._apply_agent_name(new_agent_name)
                        self.logger.debug("HOT-SWAP TTS: complete (in-place voice=%s)", resolved)
                        if self.chat_manager:
                            self.chat_manager.current_voice_provider = vp
                            self.chat_manager.current_voice_model = voice_model or ''
                        return

        # --- Full service replacement (non-in-place path) ---
        self._apply_agent_name(new_agent_name)

        if old_service is not None and hasattr(old_service, "abort_pending_synthesis"):
            try:
                old_service.abort_pending_synthesis()
            except Exception:
                self.logger.debug("HOT-SWAP TTS: abort_pending_synthesis failed", exc_info=True)

        try:
            self._create_tts_service_only()
        except Exception as e:
            self.logger.error("HOT-SWAP TTS: failed: %s", e, exc_info=True)
            return

        if old_pipeline_direction is not None:
            self.tts_service._pipeline_direction = old_pipeline_direction
        if old_event_loop is not None:
            self.tts_service._event_loop = old_event_loop

        if hasattr(self, 'pipeline') and self.pipeline is not None and old_service is not None:
            if not service_factory.swap_processor_in_pipeline(self.pipeline, old_service, self.tts_service):
                self.logger.warning("HOT-SWAP TTS: could not find old service in pipeline")

        setattr(self.tts_service, '_FrameProcessor__started', True)

        if hasattr(self.tts_service, 'set_hands_free'):
            self.tts_service.set_hands_free(self.is_hands_free)
        if hasattr(self.tts_service, 'set_ptt_active'):
            self.tts_service.set_ptt_active(bool(getattr(self, 'ptt_active', False)))

        if hasattr(self, 'llm_service') and self.llm_service:
            self.llm_service.set_tts_service(self.tts_service)
            if hasattr(self.llm_service, 'set_agent_name'):
                self.llm_service.set_agent_name(self.agent_name)

        if self.chat_manager:
            self.chat_manager.current_voice_provider = vp
            self.chat_manager.current_voice_model = voice_model or ''

        if hasattr(self.stt_service, 'set_dictating') and getattr(self, 'is_dictating', False):
            self.stt_service.set_dictating(True)

        try:
            from .command_handler import _sync_audio_input_power
            _sync_audio_input_power(self, "hot_swap_tts")
        except Exception as exc:
            self.logger.debug("HOT-SWAP TTS: audio input power sync failed: %s", exc)

        self.logger.debug("HOT-SWAP TTS: complete (engine=%s, voice=%s)", self.config['tts']['engine'], voice_model)
        self.role = self._load_agent_role()
        service_factory.update_agent_name_on_llm(self.llm_service, self.agent_name, self.role)

    def _apply_agent_name(self, new_name: str):
        """Update agent name, role (incl. custom voice personality), and LLM system prompt."""
        from . import service_factory
        if new_name and new_name != self.agent_name:
            self.logger.info("Agent name changed to %s", new_name)
            self.agent_name = new_name
        elif new_name:
            self.agent_name = new_name
        self.role = self._load_agent_role()
        service_factory.update_agent_name_on_llm(self.llm_service, self.agent_name, self.role)

    def _resolve_kokoro_custom_voice(self, voice_id: str):
        """Resolve a Kokoro custom voice (custom_N) to (ref_audio_path, base_voice).
        
        Returns (None, 'af_heart') on failure.
        """
        ref_path = None
        base_voice = 'af_heart'
        try:
            from distr.core.db import get_session as _gs, CustomVoice as _CV
            db_id = int(voice_id.split('_', 1)[1])
            with _gs() as sess:
                cv = sess.query(_CV).filter(
                    _CV.id == db_id, _CV.provider == 'kokoro', _CV.status == 'ready'
                ).first()
                if cv and cv.audio_dir:
                    for fn in os.listdir(cv.audio_dir):
                        if fn.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm')):
                            ref_path = os.path.join(cv.audio_dir, fn)
                            break
                gender = getattr(cv, 'gender', 'female') if cv else 'female'
                base_voice = 'am_puck' if gender == 'male' else 'af_heart'
        except Exception as e:
            self.logger.warning("Could not resolve Kokoro custom voice: %s", e)
        return ref_path, base_voice

    def _create_pipeline(self):
        """Create the Pipecat pipeline"""
        # Clean up old pipeline if it exists
        if hasattr(self, 'pipeline') and self.pipeline is not None:
            self.logger.info("Cleaning up old pipeline before creating new one")
            try:
                # Stop the old pipeline if it's running
                if hasattr(self.pipeline, 'cancel'):
                    self.pipeline.cancel()
                if hasattr(self, 'pipeline_task') and self.pipeline_task:
                    if hasattr(self.pipeline_task, 'cancel'):
                        self.pipeline_task.cancel()
            except Exception as e:
                self.logger.debug(f"Error cleaning up old pipeline: {e}")
            self.pipeline = None
            self.pipeline_task = None
        
        # Create services (this will clean up old STT service)
        self._create_services()
        
        # Always create VAD if enabled in config
        # VAD is always enabled - InterruptionFrames from VAD are filtered in services based on hands-free mode
        # This allows VAD to still detect speech for STT even in PTT mode, but interruptions only apply in hands-free mode
        self.vad_analyzer = None
        if self.config['vad']['enabled']:
            self.vad_analyzer = SileroVADAnalyzer()
            
            # DEBUG: Inspect VAD params to fix "unexpected keyword argument" error
            try:
                import inspect
                sig = inspect.signature(self.vad_analyzer.set_params)
                self.logger.debug(f"DEBUG: SileroVADAnalyzer.set_params signature: {sig}")
                self.logger.debug(f"DEBUG: SileroVADAnalyzer dir: {dir(self.vad_analyzer)}")
            except Exception as e:
                self.logger.error(f"DEBUG: Failed to inspect VAD: {e}")

            # Adjust VAD to trigger quickly for first word detection
            try:
                # Load threshold from settings (default 50 -> 0.5 confidence)
                vad_threshold = self.settings.get('vad_threshold', VAD_DEFAULT_THRESHOLD)
                confidence = max(VAD_CONFIDENCE_MIN, min(VAD_CONFIDENCE_MAX, vad_threshold / 100.0))
                if VADParams:
                    params = VADParams(start_secs=VAD_START_SECS, confidence=confidence)
                    self.vad_analyzer.set_params(params)
                else:
                    self.vad_analyzer.set_params(start_secs=VAD_START_SECS, confidence=confidence)
                self.logger.debug(f"VAD analyzer initialized with confidence {confidence:.2f}")
                
                # Initialize transport's base confidence (will be done after transport creation, but we can save it here if needed)
                # Actually, transport isn't created yet. We'll set it after transport creation.
            except Exception as e:
                self.logger.debug(f"VAD set_params failed: {e}")
            self.logger.info(f"VAD analyzer created (always enabled - interruptions filtered by hands_free={self.is_hands_free})")
        else:
            self.logger.info(f"VAD disabled in config")
            confidence = 0.5 # Default for later use if needed
        
        # Get device indices
        input_device_idx = self._get_device_index(
            self.config['audio']['input_device'],
            is_input=True
        )
        output_device_idx = self._get_device_index(
            self.config['audio']['output_device'],
            is_input=False
        )
        
        # Verify audio devices are accessible and log them clearly
        try:
            if sd:
                devices = sd.query_devices()
                
                # Log INPUT device
                if input_device_idx is not None:
                    if input_device_idx < len(devices):
                        input_device = devices[input_device_idx]
                        self.logger.debug(f"🎤 INPUT device: {input_device['name']}, max_input_channels={input_device.get('max_input_channels', 0)}")
                        self.logger.info(f"AUDIO INPUT: {input_device['name']}")
                        if input_device.get('max_input_channels', 0) == 0:
                            self.logger.warning("Input device has no input channels!")
                            self.logger.warning("Input device has no input channels!")
                    else:
                        self.logger.warning(f"Input device index {input_device_idx} is out of range!")
                else:
                    default_input = sd.query_devices(kind='input')
                    self.logger.debug(f"🎤 Using default INPUT device: {default_input['name'] if default_input else 'None'}")
                    self.logger.info(f"AUDIO INPUT (default): {default_input['name'] if default_input else 'None'}")
                
                # Log OUTPUT device
                if output_device_idx is not None:
                    if output_device_idx < len(devices):
                        output_device = devices[output_device_idx]
                        self.logger.debug(f"🔊 OUTPUT device: {output_device['name']}, max_output_channels={output_device.get('max_output_channels', 0)}")
                        self.logger.info(f"AUDIO OUTPUT: {output_device['name']}")
                        if output_device.get('max_output_channels', 0) == 0:
                            self.logger.warning("Output device has no output channels!")
                    else:
                        self.logger.warning(f"Output device index {output_device_idx} is out of range!")
                else:
                    default_output = sd.query_devices(kind='output')
                    self.logger.debug(f"🔊 Using default OUTPUT device: {default_output['name'] if default_output else 'None'}")
                    self.logger.info(f"AUDIO OUTPUT (default): {default_output['name'] if default_output else 'None'}")
            else:
                self.logger.warning("sounddevice not available, cannot verify audio devices")
        except Exception as e:
            self.logger.warning(f"Could not verify audio devices: {e}")
        
        # Create transport
        audio_config = self.config['audio']
        
        # Log device configuration for debugging
        self.logger.info(f"Audio config: input_sample_rate={audio_config['input_sample_rate']}, output_sample_rate={audio_config['output_sample_rate']}")
        self.logger.debug(f"Device indices: input={input_device_idx}, output={output_device_idx}")
        
        # Set pipecat logging to WARNING to reduce console noise
        pipecat_logger = logging.getLogger("pipecat")
        pipecat_logger.setLevel(logging.WARNING)
        self.logger.debug("Set pipecat logging to WARNING level")
        
        # Create AEC (Acoustic Echo Cancellation) filter and shared reference buffer.
        # The reference buffer carries the speaker output signal; the NLMS filter
        # subtracts it from the mic input before the VAD ever sees it.
        aec_ref_buf = ReferenceBuffer(
            max_duration_secs=2.0,
            sample_rate=audio_config['output_sample_rate'],
        )
        aec_filter = NLMSEchoCanceller(
            reference_buffer=aec_ref_buf,
            filter_length=800,    # 50ms impulse response @ 16kHz
            mu=0.5,
            output_sample_rate=audio_config['output_sample_rate'],
        )
        
        self.transport = HotSwappableLocalAudioTransport(
            LocalAudioTransportParams(
                sample_rate=audio_config['input_sample_rate'],
                audio_out_sample_rate=audio_config['output_sample_rate'],  # Use audio_out_sample_rate (correct attribute name)
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=self.vad_analyzer,
                input_device_index=input_device_idx,
                output_device_index=output_device_idx,
                audio_in_filter=aec_filter,
            ),
            event_queue=self.event_queue,
            aec_reference_buffer=aec_ref_buf,
            output_device_name=audio_config.get('output_device') or 'System Default',
        )
        
        # Give the STT service access to the AEC reference buffer so it can
        # gate VAD interruptions during TTS playback (echo suppression).
        if self.stt_service is not None:
            self.stt_service._aec_ref_buf = aec_ref_buf
            # Provide a callback so the echo gate can cancel the welcome task
            # on barge-in without needing a direct session reference.
            def _cancel_welcome():
                if self._welcome_task and not self._welcome_task.done():
                    self._welcome_task.cancel()
            self.stt_service._cancel_welcome_callback = _cancel_welcome
        
        # Initialize base VAD confidence in transport for ducking logic
        if self.config['vad']['enabled']:
            try:
                vad_threshold = self.settings.get('vad_threshold', VAD_DEFAULT_THRESHOLD)
                confidence = max(VAD_CONFIDENCE_MIN, min(VAD_CONFIDENCE_MAX, vad_threshold / 100.0))
                self.transport.output().set_base_vad_confidence(confidence)
                self.logger.debug(f"Initialized transport base VAD confidence to {confidence:.2f}")
            except Exception as e:
                self.logger.warning(f"Failed to set initial transport base VAD confidence: {e}")
        
        # Set initial volume and speed from settings
        initial_volume = self.settings.get('speech_volume', 100) / 100.0
        initial_speed = self.settings.get('playback_speed', 1.0)
        self.transport.output().set_volume(initial_volume)
        self.transport.output().set_speed(initial_speed)
        
        self.logger.info(f"Transport created: audio_in={True}, audio_out={True}, volume={initial_volume:.2f}, speed={initial_speed:.2f}x")
        
        # Create pipeline
        self.pipeline = Pipeline(
            [
                self.transport.input(),
                self.stt_service,
                self.llm_service,
                self.tts_service,
                self.transport.output()
            ]
        )
        self.llm_service._audio_transport_output = self.transport.output()
        
        # Create task — disable idle timeout since this is a persistent desktop assistant,
        # not a temporary call session. Pipecat's default (300s) kills the pipeline.
        self.task = PipelineTask(
            self.pipeline,
            idle_timeout_secs=None,
        )
        
        # Create runner (will use current running event loop)
        self.runner = PipelineRunner()
        
        self.logger.info("Pipeline created successfully")
    
    async def _run_pipeline(self):
        """Run the pipeline in async context"""
        try:
            # Set event loop immediately so interrupt_tts / send_text_input can schedule work
            # even during _create_pipeline() (e.g. while whisper is loading).
            self._main_loop = asyncio.get_running_loop()
            self.running = True

            # Create pipeline (no loop needed - will use current running loop)
            self._create_pipeline()
            try:
                from .command_handler import _sync_audio_input_power
                _sync_audio_input_power(self, "pipeline_start")
            except Exception as e:
                self.logger.debug("Initial audio input power sync failed: %s", e)

            self._emit_stt_ready("pipeline_start")

            # Flush any process_text_input that arrived before the loop was ready (e.g. from web send-to-agent)
            pending = getattr(self, '_pending_text_inputs', None)
            if pending:
                self._pending_text_inputs = []
                for item in pending:
                    if len(item) == 5:
                        text, is_telegram, uploaded_image_path, speaker_override, telegram_input_type = item
                    else:
                        text, is_telegram, uploaded_image_path, speaker_override = item
                        telegram_input_type = None
                    if text and hasattr(self, 'llm_service') and self.llm_service:
                        self.logger.debug("Processing pending text input: '%s...'", (text or "")[:50])
                        asyncio.create_task(self.llm_service.process_chat_input(text, is_telegram=is_telegram, uploaded_image_path=uploaded_image_path or None, speaker_enabled=speaker_override, telegram_input_type=telegram_input_type))
            
            # Handle SIGTERM for clean exit
            try:
                def handle_sigterm(*args):
                    raise KeyboardInterrupt()
                signal.signal(signal.SIGTERM, handle_sigterm)
            except (ValueError, RuntimeError):
                # Signal handlers may not work in all contexts
                pass
            
            # Run pipeline
            self.logger.info("Starting pipeline...")
            
            # Create a task to send welcome message after pipeline starts (only if enabled)
            async def send_welcome_after_start():
                # Check if welcome should be skipped (e.g., during reload)
                if self.skip_welcome:
                    self.logger.debug("Skipping welcome message (reload detected)")
                    return
                
                # Check if welcome/greet is enabled in settings (defaults to True)
                welcome_enabled = self.settings.get('welcome_greet_me', True)
                if not welcome_enabled:
                    self.logger.info("Welcome/Greet Me is disabled in settings - skipping welcome message")
                    return
                
                # Wait for pipeline to initialize (give it time to process StartFrame).
                # Short delay (1.5s) so welcome plays soon on fresh load or after load-in-agent reload.
                try:
                    await asyncio.sleep(WELCOME_DELAY_SECS)
                except asyncio.CancelledError:
                    self.logger.debug("Welcome message task cancelled during initialization wait")
                    raise  # Re-raise to properly handle cancellation
                
                # Send welcome message directly to TTS, bypassing the pipeline frame queue.
                # Pushing frames from the LLM via push_frame() from a concurrent asyncio task
                # doesn't reliably flow through Pipecat's internal processor queues.
                # Instead, we call the LLM's send_welcome_message but redirect frame
                # delivery to the TTS's process_frame directly.
                try:
                    self.logger.info("Sending welcome message (pipeline ready)")
                    
                    if not self.llm_service or not self.tts_service:
                        self.logger.warning("Welcome message skipped - LLM or TTS service not available")
                        return
                    
                    from pipecat.frames.frames import TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame
                    from pipecat.processors.frame_processor import FrameDirection
                    
                    tts = self.tts_service
                    direction = FrameDirection.DOWNSTREAM
                    
                    # Use the LLM's _build_welcome_sentences which loads chat history
                    # and generates a conversation summary via the provider's API.
                    # We route frames directly to TTS (pipeline frame queue is unreliable
                    # for concurrent tasks).
                    welcome_sentences = await self.llm_service._build_welcome_sentences(
                        self.agent_name or "Heart"
                    )
                    
                    full_message = " ".join(welcome_sentences)
                    self.logger.info("AGENT_WELCOME_TTS_TEXT: %s", full_message)

                    # Same as _cmd_speak_text_directly: ensure Kokoro pushes audio to desktop even if
                    # another thread left telegram_request=True (welcome runs concurrently with pipeline).
                    cur = threading.current_thread()
                    prev_force_desktop = bool(getattr(cur, 'force_desktop_tts', False))
                    prev_tts_force = bool(getattr(tts, '_force_desktop_tts', False))
                    cur.force_desktop_tts = True
                    tts._force_desktop_tts = True
                    try:
                        await tts.process_frame(LLMFullResponseStartFrame(), direction)
                        if not (
                            getattr(self, '_welcome_task', None) and self._welcome_task.cancelled()
                        ) and not (hasattr(tts, '_cancelled') and tts._cancelled):
                            self.logger.info("AGENT_WELCOME_TTS_OUT: %s", full_message)
                            await tts.process_frame(TextFrame(text=full_message), direction)

                        await tts.process_frame(LLMFullResponseEndFrame(), direction)
                        if hasattr(tts, "_drain_speak_queue"):
                            await tts._drain_speak_queue()
                    finally:
                        cur.force_desktop_tts = prev_force_desktop
                        tts._force_desktop_tts = prev_tts_force
                    
                    # Persist to LLM message history so context is maintained
                    if hasattr(self.llm_service, '_messages'):
                        self.llm_service._messages.append({"role": "assistant", "content": full_message})
                    
                    self.logger.info("Welcome message sent via direct TTS routing")
                    
                except asyncio.CancelledError:
                    # Task was cancelled (e.g., by process_text_input) — clean up TTS state
                    self.logger.info("Welcome message cancelled — sending LLMFullResponseEndFrame and tts_stopped")
                    try:
                        await tts.process_frame(LLMFullResponseEndFrame(), direction)
                    except Exception:
                        pass
                    # Ensure tts_stopped fires so the player window closes
                    if hasattr(self, 'event_queue') and self.event_queue:
                        try:
                            self.event_queue.put(('tts_stopped', {'duration': 0.0}), block=False)
                        except Exception:
                            pass
                    # Reset TTS cancelled flag so the next generation works
                    if hasattr(tts, "abort_pending_synthesis"):
                        tts.abort_pending_synthesis()
                    elif hasattr(tts, '_cancelled'):
                        tts._cancelled = False
                    if hasattr(tts, '_tts_session_active'):
                        tts._tts_session_active = False
                    if hasattr(tts, '_current_telegram_request'):
                        tts._current_telegram_request = False
                    raise  # Re-raise to properly handle cancellation
                except Exception as e:
                    self.logger.error(f"Error sending welcome message: {e}", exc_info=True)
            
            # Start welcome message task (runs concurrently with pipeline)
            self._welcome_task = asyncio.create_task(send_welcome_after_start())
            
            try:
                # Run pipeline (this blocks until pipeline stops)
                await self.runner.run(self.task)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self.logger.info("Pipeline interrupted")
            except Exception as e:
                # Suppress RuntimeError about closed event loop during shutdown
                if "Event loop is closed" not in str(e) and "coroutine" not in str(e).lower():
                    self.logger.error(f"Pipeline error: {e}", exc_info=True)
            finally:
                self.running = False
                # Clean shutdown - stop transport first, then runner
                try:
                    if hasattr(self, 'transport') and self.transport:
                        # Stop the transport to close audio streams
                        if hasattr(self.transport, 'cleanup'):
                            try:
                                await self.transport.cleanup()
                            except (RuntimeError, asyncio.CancelledError):
                                # Suppress errors during shutdown
                                pass
                except (RuntimeError, asyncio.CancelledError, Exception) as e:
                    # Suppress errors during shutdown
                    if "Event loop is closed" not in str(e):
                        self.logger.debug(f"Error cleaning up transport: {e}")
                
                try:
                    if hasattr(self, 'runner') and self.runner:
                        try:
                            await self.runner.stop()
                        except (RuntimeError, asyncio.CancelledError):
                            # Suppress errors during shutdown
                            pass
                except (RuntimeError, asyncio.CancelledError, Exception) as e:
                    # Suppress errors during shutdown
                    if "Event loop is closed" not in str(e):
                        self.logger.debug(f"Error stopping runner: {e}")

                # Clean up STT service to prevent Metal crashes on exit
                try:
                    if hasattr(self, 'stt_service') and self.stt_service:
                        self.logger.debug("Cleaning up STT service on shutdown")
                        if hasattr(self.stt_service, 'cleanup'):
                            try:
                                self.stt_service.cleanup()
                            except Exception as cleanup_error:
                                # Log but don't crash - Metal cleanup may fail
                                self.logger.warning(f"STT cleanup error on shutdown (non-fatal): {cleanup_error}")
                        self.stt_service = None
                except Exception as e:
                    # Suppress all STT cleanup errors during shutdown
                    self.logger.debug(f"Error during STT cleanup on shutdown: {e}")

                # Cancel welcome task if still running
                try:
                    if self._welcome_task and not self._welcome_task.done():
                        self._welcome_task.cancel()
                        try:
                            await self._welcome_task
                        except (asyncio.CancelledError, RuntimeError):
                            pass
                except (RuntimeError, Exception):
                    pass
        except Exception as e:
            self.logger.error(f"Error in pipeline: {e}", exc_info=True)
            self.running = False
    
    def _command_worker(self):
        """Worker thread that processes commands from the main process"""
        while not self._stop_event.is_set():
            try:
                if self.command_queue:
                    try:
                        command, params = self.command_queue.get(timeout=COMMAND_POLL_TIMEOUT)
                        
                        # CRITICAL: Clear thread-local flags to prevent state leakage from previous commands
                        # This thread (Thread-1) is reused, so flags set in one command persist to the next if not cleared
                        try:
                            if hasattr(threading.current_thread(), 'telegram_request'):
                                del threading.current_thread().telegram_request
                            if hasattr(threading.current_thread(), 'telegram_file_sent'):
                                del threading.current_thread().telegram_file_sent
                        except Exception as e:
                            self.logger.error(f"Error clearing thread flags: {e}")
                            
                        self.logger.debug(f"[COMMAND WORKER] Received command: {command} with params: {params}")
                        if command == 'update_stt_model':
                            self.logger.debug(f"🎤 [COMMAND WORKER] Processing update_stt_model command: {params.get('transcription_model')}")
                        self._handle_command(command, params)
                    except Empty:
                        continue
                else:
                    time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Error processing command: {e}", exc_info=True)
    
    def _handle_command(self, command: str, params: Dict[str, Any]):
        """Handle commands from the main process."""
        from . import command_handler
        if not command_handler.dispatch(self, command, params):
            self.logger.warning(f"Unknown command: {command}")
    
    def start(self):
        """Start the agent session"""
        if self.running:
            self.logger.warning("Session already running")
            return

        self.logger.debug("Starting agent session...")

        # Start command processing thread
        if self.command_queue:
            self._command_thread = threading.Thread(target=self._command_worker, daemon=True)
            self._command_thread.start()

        # Run pipeline in async context (like test_pipecat.py)
        try:
            asyncio.run(self._run_pipeline())
        except KeyboardInterrupt:
            self.logger.debug("Agent session interrupted")
        except Exception as e:
            self.logger.error(f"Error running agent session: {e}", exc_info=True)
        finally:
            self.running = False
            self.stop()

        self.logger.debug("Agent session started")
    
    def stop(self):
        """Stop the agent session and clean up resources"""
        # Prevent infinite recursion
        if hasattr(self, '_stopping') and self._stopping:
            return
        self._stopping = True
        
        self.logger.debug("Stopping agent session...")
        self.running = False
        
        # Signal stop event
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        
        # Stop transport to prevent audio callbacks from using closed event loop
        try:
            if hasattr(self, 'transport') and self.transport:
                # Transport cleanup will be handled in _run_pipeline finally block
                pass
        except Exception as e:
            self.logger.debug(f"Error stopping transport: {e}")
        
        # Join command thread
        if hasattr(self, '_command_thread') and self._command_thread and self._command_thread.is_alive():
            try:
                self._command_thread.join(timeout=1.0)
            except Exception as e:
                self.logger.debug(f"Error joining command thread: {e}")
        
        self.logger.debug("Agent session stopped")
    
    def reload(self):
        """Reload configuration from settings"""
        if self.command_queue:
            self.command_queue.put(('reload', {}))
        else:
            self.config = self._load_config()
            self._reload_event.set()
