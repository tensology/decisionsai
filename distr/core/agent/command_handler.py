"""
Command handler - dispatches agent session commands to grouped handlers.

Extracted from session.py to reduce the ~770-line _handle_command method.
Each handler receives the AgentSession instance and the params dict.
"""

import asyncio
import logging
import os

import requests

from .constants import (
    SPEED_BOUNDS, ENGINE_TO_PROVIDER, DEFAULT_MODELS,
    VAD_CONFIDENCE_MIN, VAD_CONFIDENCE_MAX,
)

logger = logging.getLogger(__name__)


def _parse_bool(value, default: bool = True) -> bool:
    """Parse bool from value - handles bool, int, str('true'/'false'), None. Avoids bool('false')=True."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ('true', '1', 'yes', 'on')
    return default


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

def dispatch(session, command: str, params: dict) -> bool:
    """Route a command to its handler. Returns True if handled.
    
    All handlers are wrapped in try/except to prevent unhandled exceptions
    in the command worker thread from crashing the agent session.
    """
    handler = _COMMAND_MAP.get(command)
    if handler:
        try:
            handler(session, params)
        except Exception as e:
            session.logger.error(f"Error in command handler '{command}': {e}", exc_info=True)
        return True
    return False


# ---------------------------------------------------------------------------
# Lifecycle commands
# ---------------------------------------------------------------------------

def _cmd_shutdown(session, params):
    session.logger.info("Shutdown command received, stopping pipeline")
    session.running = False
    _loop = getattr(session, 'runner', None) and getattr(session.runner, '_loop', None)
    if _loop and _loop.is_running():
        async def stop_runner():
            try:
                if hasattr(session, 'runner') and session.runner and hasattr(session.runner, 'stop'):
                    await session.runner.stop()
            except (asyncio.CancelledError, RuntimeError, Exception):
                pass
        asyncio.run_coroutine_threadsafe(stop_runner(), _loop)


def _cmd_reload(session, params):
    session.logger.info("Reload command received")
    chat_id = params.get('chat_id')
    if chat_id:
        session._agent_current_chat_id_from_signal = chat_id
        session.logger.debug(f"Reload: Using chat_id={chat_id} from params for _load_config")
    session.config = session._load_config()
    session.agent_name = session._determine_agent_name()
    session.logger.debug(f"Reloaded agent name: {session.agent_name}")
    session.logger.debug("Reload: Configuration updated, agent will restart via reload_agent signal")
    session._reload_event.set()


def _cmd_file_operation_confirmation_response(session, params):
    confirmation_id = params.get('confirmation_id')
    confirmed = params.get('confirmed', False)
    session.logger.debug(f"Received file_operation_confirmation_response: confirmed={confirmed} (ID: {confirmation_id})")

    try:
        from distr.core.agent.services.safety.interceptor import (
            _ensure_response_dict_initialized, _response_dict, _response_lock,
        )
        _ensure_response_dict_initialized()
        if _response_dict is not None and _response_lock is not None:
            with _response_lock:
                _response_dict[confirmation_id] = confirmed
                current_keys = list(_response_dict.keys())
                session.logger.debug(f"Stored confirmation response in _response_dict (ID: {confirmation_id}): {confirmed}")
                if confirmation_id not in current_keys:
                    session.logger.warning(f"Confirmation ID {confirmation_id} NOT found in _response_dict after storing!")
        else:
            session.logger.warning("Shared response dict not initialized - cannot store confirmation response")
    except Exception as e:
        session.logger.error(f"Error storing confirmation response: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Model / service update commands
# ---------------------------------------------------------------------------

def _cmd_update_model(session, params):
    model_name = params.get('model_name')
    if model_name:
        session.logger.debug(f"Received update_model command: {model_name}")
        if session.chat_manager:
            session.chat_manager.update_model(model_name)
            session.logger.debug(f"Updated ChatManager model to: {model_name}")
        else:
            session.logger.warning("ChatManager not available, cannot update model")
    else:
        session.logger.warning("update_model command received without model_name")


def _load_root_chat_voice(chat_id):
    """Load voice_provider and voice_model from a chat's root ancestor.
    
    Walks up the parent chain to find the root chat (parent_id IS NULL),
    then returns (voice_provider, voice_model) from that root.
    Returns (None, None) on failure.
    """
    try:
        from distr.core.db import get_session as _gs, Chat as _C
        from sqlalchemy import text
        with _gs() as _s:
            chat = _s.get(_C, chat_id)
            if chat and chat.parent_id is not None:
                root_row = _s.execute(
                    text("""
                        WITH RECURSIVE parents(id, parent_id) AS (
                            SELECT id, parent_id FROM chats WHERE id = :start_id
                            UNION ALL
                            SELECT c.id, c.parent_id FROM chats c JOIN parents p ON c.id = p.parent_id
                        )
                        SELECT id FROM parents WHERE parent_id IS NULL
                    """),
                    {"start_id": chat_id}
                ).fetchone()
                root_id = root_row[0] if root_row else chat_id
                chat = _s.get(_C, root_id) or chat
            if chat:
                vp = (chat.voice_provider or '').strip() or None
                vm = (chat.voice_model or '').strip() or None
                return vp, vm
    except Exception:
        pass
    return None, None


def _cmd_hot_swap_llm(session, params):
    provider = params.get('provider', 'Ollama')
    model_name = params.get('model_name', '')
    chat_id = params.get('chat_id')
    voice_provider = (params.get('voice_provider') or '').strip() or None
    voice_model = (params.get('voice_model') or '').strip() or None
    session.logger.debug(
        "hot_swap_llm: chat_id=%s provider=%s model_name=%s voice=%s/%s (will swap LLM then TTS)",
        chat_id, provider, model_name, voice_provider, voice_model,
    )
    session._hot_swap_llm_service(
        provider, model_name, chat_id,
        speak=params.get('speak'),
        voice_provider=voice_provider,
        voice_model=voice_model,
    )

    # Swap TTS to match chat's voice: use params when provided (e.g. from create-chat), else load from DB
    vp, vm = voice_provider, voice_model
    if not (vp and vm) and chat_id:
        vp, vm = _load_root_chat_voice(chat_id)
        if not (vp and vm):
            session.logger.warning("hot_swap_llm: could not load chat voice for chat_id=%s", chat_id)
    if vp and vm:
        corrected_vp = _detect_correct_voice_provider(vp, vm)
        if corrected_vp != vp:
            session.logger.info("hot_swap_llm: corrected voice provider from '%s' to '%s' for voice_model='%s'", vp, corrected_vp, vm)
            vp = corrected_vp
        session.logger.debug("hot_swap_llm: swapping TTS to voice provider=%s model=%s", vp, vm)
        session._hot_swap_tts_service(vp, vm)


def _cmd_hot_swap_tts(session, params):
    session._hot_swap_tts_service(
        params.get('voice_provider', ''),
        params.get('voice_model', ''),
    )


def _cmd_update_stt_model(session, params):
    transcription_model = params.get('transcription_model')
    session.logger.debug(f"Received command: update_stt_model (Model: {transcription_model})")
    session.logger.debug(f"Current STT service type: {type(session.stt_service).__name__ if session.stt_service else 'None'}")
    session.logger.debug(f"Current config STT engine: {session.config['stt'].get('engine')}")

    if transcription_model:
        session.settings['transcription_model'] = transcription_model
        session.logger.debug(f"Updated self.settings['transcription_model'] to: {transcription_model}")

    if not transcription_model:
        session.logger.warning("Cannot hot-swap STT: transcription_model is empty")
        return

    try:
        from . import config_loader
        stt_parsed = config_loader.resolve_stt_config(transcription_model)
        if stt_parsed['engine']:
            session.config['stt']['engine'] = stt_parsed['engine']
            if 'model' in stt_parsed:
                session.config['stt']['model'] = stt_parsed['model']
            session.logger.debug(f"Updated config: STT engine={stt_parsed['engine']}, model={stt_parsed.get('model', 'N/A')}")

        old_stt_service = session.stt_service
        old_stt_type = type(old_stt_service).__name__ if old_stt_service else 'None'
        session.logger.debug(f"Replacing STT service: {old_stt_type} -> {transcription_model}")

        # Preserve pipeline direction and event loop from old service for PTT to work
        old_pipeline_direction = getattr(old_stt_service, '_pipeline_direction', None) if old_stt_service else None
        old_event_loop = getattr(old_stt_service, '_event_loop', None) if old_stt_service else None

        session._create_stt_service()

        # Copy pipeline direction and event loop to new service so PTT works immediately
        if old_pipeline_direction is not None:
            session.stt_service._pipeline_direction = old_pipeline_direction
        if old_event_loop is not None:
            session.stt_service._event_loop = old_event_loop

        new_stt_type = type(session.stt_service).__name__ if session.stt_service else 'None'
        session.logger.debug(f"New STT service created: {new_stt_type}")

        # Update pipeline to use new STT service
        from . import service_factory
        if hasattr(session, 'pipeline') and session.pipeline is not None:
            session.logger.debug("Updating pipeline with new STT service...")
            replaced = service_factory.swap_processor_in_pipeline(
                session.pipeline, old_stt_service, session.stt_service,
            )
            if not replaced:
                session.logger.warning("Could not find old STT service in pipeline to replace - pipeline may need restart")

            # Verify
            if hasattr(session.pipeline, '_processors'):
                current_processors = [type(p).__name__ for p in session.pipeline._processors]
                session.logger.debug(f"Pipeline processors after update: {current_processors}")
        else:
            session.logger.warning("Pipeline not available, cannot update STT service reference")

        # Mark the new processor as started so Pipecat doesn't reject frames
        setattr(session.stt_service, '_FrameProcessor__started', True)
        session.logger.debug("HOT-SWAP STT: marked new service as started")

        session.logger.info(f"STT MODEL UPDATED: {transcription_model} (was {old_stt_type}, now {new_stt_type})")
    except Exception as e:
        session.logger.error(f"Error hot-swapping STT model: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Audio commands
# ---------------------------------------------------------------------------

def _cmd_update_audio_devices(session, params):
    input_device = params.get('input_device')
    output_device = params.get('output_device')
    session.logger.debug(f"Received command: update_audio_devices (Input: {input_device}, Output: {output_device})")

    if input_device:
        session.config['audio']['input_device'] = input_device
        input_idx = session._get_device_index(input_device, is_input=True)
        if hasattr(session, 'transport'):
            session.logger.debug(f"Hot-swapping input device to index {input_idx} (None=Default)")
            if hasattr(session, '_main_loop') and session._main_loop.is_running():
                session._main_loop.call_soon_threadsafe(session.transport.input().set_device, input_idx)
            else:
                session.transport.input().set_device(input_idx)

    if output_device:
        session.config['audio']['output_device'] = output_device
        output_idx = session._get_device_index(output_device, is_input=False)
        if hasattr(session, 'transport'):
            session.logger.debug(f"Hot-swapping output device to index {output_idx} (None=Default)")
            if hasattr(session, '_main_loop') and session._main_loop.is_running():
                session._main_loop.call_soon_threadsafe(session.transport.output().set_device, output_idx)
            else:
                session.transport.output().set_device(output_idx)


def _cmd_set_playback_speed(session, params):
    speed = params.get('speed', 1.0)
    original_speed = speed

    if hasattr(session, 'tts_service') and session.tts_service:
        from .services import KokoroTTSService, ElevenLabsTTSService
        if isinstance(session.tts_service, KokoroTTSService):
            lo, hi = SPEED_BOUNDS['kokoro']
            speed = max(lo, min(hi, speed))
            if speed != original_speed:
                session.logger.warning(f"Playback speed clamped from {original_speed:.1f}x to {speed:.1f}x (Kokoro supports {lo}-{hi}x)")
        elif isinstance(session.tts_service, ElevenLabsTTSService):
            lo, hi = SPEED_BOUNDS['elevenlabs']
            speed = max(lo, min(hi, speed))
            if speed != original_speed:
                session.logger.warning(f"Playback speed clamped from {original_speed:.1f}x to {speed:.1f}x (ElevenLabs API supports {lo}-{hi}x)")
    else:
        session.logger.warning("TTS service not available, cannot update playback speed")
        return

    session.logger.debug(f"Playback speed changed to {speed:.1f}x")
    session.tts_service.set_playback_speed(speed)
    if hasattr(session, 'transport') and session.transport:
        session.transport.output().set_speed(speed)
    else:
        session.logger.warning("Transport not available, cannot update playback speed")


def _cmd_set_speech_volume(session, params):
    volume = params.get('volume', 100)
    volume = max(0, min(100, volume))
    session.logger.debug(f"Speech volume changed to {volume}%")
    if hasattr(session, 'transport') and session.transport:
        session.transport.output().set_volume(volume / 100.0)
    else:
        session.logger.warning("Transport not available, cannot update speech volume")


def _cmd_set_elevenlabs_voice_settings(session, params):
    from .services import ElevenLabsTTSService
    if hasattr(session, 'tts_service') and session.tts_service and isinstance(session.tts_service, ElevenLabsTTSService):
        session.tts_service.set_elevenlabs_voice_settings(
            stability=params.get('stability'),
            similarity_boost=params.get('similarity_boost'),
            style=params.get('style'),
            use_speaker_boost=params.get('use_speaker_boost'),
        )
        session.logger.debug("Updated ElevenLabs voice_settings in TTS service")
    else:
        session.logger.debug("TTS service is not ElevenLabs; ignoring set_elevenlabs_voice_settings")


def _cmd_set_vad_threshold(session, params):
    threshold = params.get('threshold', 50)
    confidence = max(VAD_CONFIDENCE_MIN, min(VAD_CONFIDENCE_MAX, threshold / 100.0))
    session.logger.debug(f"Setting VAD threshold to {threshold}% (confidence: {confidence:.2f})")

    if hasattr(session, 'vad_analyzer') and session.vad_analyzer:
        try:
            from .libs import VADParams
            if VADParams is not None:
                try:
                    vad_params = VADParams(confidence=confidence)
                    session.vad_analyzer.set_params(vad_params)
                    session.logger.debug("VAD analyzer params updated (using VADParams)")
                except Exception as e:
                    session.logger.warning(f"Failed to set VAD params with VADParams object: {e}")
            else:
                session.logger.warning("VADParams not available, skipping direct VAD update.")
        except Exception as e:
            session.logger.error(f"Failed to set VAD params: {e}")
    else:
        session.logger.warning("VAD analyzer not available")

    if hasattr(session, 'transport') and session.transport:
        try:
            session.transport.output().set_base_vad_confidence(confidence)
        except Exception as e:
            session.logger.warning(f"Failed to update transport base confidence: {e}")


# ---------------------------------------------------------------------------
# Mode commands
# ---------------------------------------------------------------------------

def _cmd_set_listening(session, params):
    enabled = params.get('enabled', True)
    old_state = session.is_listening
    session.is_listening = enabled
    session.logger.info(f"LISTENING STATE CHANGED: {old_state} -> {enabled}")
    if hasattr(session, 'llm_service') and session.llm_service:
        session.llm_service.set_listening(enabled)
    else:
        session.logger.warning("No LLM service available to update listening state")


def _cmd_set_hands_free(session, params):
    enabled = params.get('enabled', False)
    old_value = session.is_hands_free
    session.is_hands_free = enabled
    session.logger.info(f"Hands-free mode changed: {old_value} -> {enabled} (PTT mode: {not enabled})")
    if hasattr(session, 'stt_service') and session.stt_service:
        session.stt_service.set_hands_free(enabled)
    else:
        session.logger.warning("STT service not available - cannot update hands-free mode")
    if hasattr(session, 'llm_service') and session.llm_service:
        session.llm_service.set_hands_free(enabled)
    if hasattr(session, 'tts_service') and session.tts_service:
        session.tts_service.set_hands_free(enabled)
    session.logger.debug("All services updated - VAD remains enabled, interruptions filtered by services")


def _cmd_set_dictating(session, params):
    enabled = params.get('enabled', False)
    session.logger.debug(f"Dictation mode changed: {enabled}")
    if hasattr(session, 'stt_service') and session.stt_service:
        if hasattr(session.stt_service, 'set_dictating'):
            session.stt_service.set_dictating(enabled)
            session.logger.debug(f"Updated STT service dictation mode: {enabled}")
        else:
            session.logger.warning("STT service does not support set_dictating()")
    else:
        session.logger.warning("STT service not available - cannot update dictation mode")


def _cmd_set_speaker_enabled(session, params):
    """Params: speak (from web) or enabled (from Qt signal) - both mean TTS on/off."""
    val = params.get('speak', params.get('enabled'))
    enabled = _parse_bool(val, default=True)
    if hasattr(session, 'llm_service') and session.llm_service:
        session.llm_service.set_speaker_enabled(enabled)
        session.logger.debug(f"Speaker (TTS) set to: {enabled}")


def _cmd_stop_dictation(session, params):
    if hasattr(session, 'llm_service') and session.llm_service:
        if hasattr(session.llm_service, '_stop_dictation'):
            session.llm_service._stop_dictation()
            session.logger.debug("Dictation stopped via command")
        else:
            session.logger.warning("LLM service does not support dictation")


# ---------------------------------------------------------------------------
# Interaction commands
# ---------------------------------------------------------------------------

def _cmd_process_text_input(session, params):
    text = params.get('text', '')
    # Cancel welcome message task
    if session._welcome_task and not session._welcome_task.done():
        session.logger.debug("process_text_input: Cancelling welcome message task so user message is processed")
        session._welcome_task.cancel()

    is_telegram = _parse_bool(params.get('is_telegram'), default=False) if isinstance(params, dict) else False
    uploaded_image_path = params.get('uploaded_image_path', None)
    speaker_val = params.get('speak') if isinstance(params, dict) and 'speak' in params else None
    speaker_override = _parse_bool(speaker_val, default=True) if speaker_val is not None else None
    session.logger.debug(f"process_text_input: speak param={speaker_val}, parsed={speaker_override}, current _speaker_enabled={getattr(session.llm_service, '_speaker_enabled', None) if hasattr(session, 'llm_service') and session.llm_service else 'N/A'}")

    if speaker_override is not None and hasattr(session, 'llm_service') and session.llm_service:
        session.llm_service.set_speaker_enabled(speaker_override)
        session.logger.debug(f"Speaker set to {speaker_override} (from request) before processing text. After set: _speaker_enabled={session.llm_service._speaker_enabled}")

    if text and hasattr(session, 'llm_service') and session.llm_service:
        session.logger.debug(f"Processing text input: '{text[:50]}...' (is_telegram={is_telegram}, uploaded_image_path={uploaded_image_path})")
        if uploaded_image_path and os.path.exists(uploaded_image_path):
            import threading
            threading.current_thread().telegram_uploaded_image = uploaded_image_path
            session.logger.debug(f"Stored uploaded image path on agent thread: {uploaded_image_path}")

        _loop = getattr(session.runner, '_loop', None) if session.runner else None
        if _loop is None:
            _loop = getattr(session, '_main_loop', None)
        if _loop and _loop.is_running():
            asyncio.run_coroutine_threadsafe(
                session.llm_service.process_chat_input(
                    text,
                    is_telegram=is_telegram,
                    uploaded_image_path=uploaded_image_path or None,
                    speaker_enabled=speaker_override,
                ),
                _loop,
            )
        else:
            session.logger.warning(
                "Cannot process text input: event loop not available (runner=%s, _main_loop=%s)",
                getattr(session.runner, '_loop', None) if session.runner else None,
                getattr(session, '_main_loop', None),
            )
            if getattr(session, '_pending_text_inputs', None) is None:
                session._pending_text_inputs = []
            session._pending_text_inputs.append((text, is_telegram, uploaded_image_path, speaker_override))



def _cmd_push_to_talk_start(session, params):
    session.logger.debug("PTT: Push-to-talk START received")
    session.ptt_active = True

    if hasattr(session, 'tts_service') and session.tts_service and hasattr(session.tts_service, 'set_ptt_active'):
        session.tts_service.set_ptt_active(True)

    if hasattr(session, 'stt_service') and session.stt_service:
        session.stt_service.set_ptt_active(True)
    else:
        session.logger.warning("PTT: STT service not available - PTT audio may not be captured")

    # Immediate cancel so TTS/LLM stop before frame propagates
    if hasattr(session, 'llm_service') and session.llm_service and hasattr(session.llm_service, '_cancelled'):
        session.llm_service._cancelled = True
    if hasattr(session, 'tts_service') and session.tts_service:
        if hasattr(session.tts_service, '_cancelled'):
            session.tts_service._cancelled = True
        if hasattr(session.tts_service, '_text_buffer'):
            session.tts_service._text_buffer = ""

    # Cancel welcome message task if still running (PTT during welcome)
    if hasattr(session, '_welcome_task') and session._welcome_task and not session._welcome_task.done():
        session._welcome_task.cancel()
        session.logger.debug("PTT: Cancelled welcome message task")

    # Use the proper Pipecat interruption mechanism (same as barge-in and stop button).
    # push_interruption_task_frame_and_wait() broadcasts InterruptionFrame to ALL
    # processors simultaneously via PipelineTask, avoiding the sequential propagation
    # that kills the output transport's processing loop.
    if hasattr(session, 'stt_service') and session.stt_service and session.runner and session.runner._loop:
        async def send_ptt_interruption():
            try:
                await session.stt_service.push_interruption_task_frame_and_wait()
                session.logger.debug("PTT: Pipeline interruption broadcast via PipelineTask (all processors notified)")
            except Exception as e:
                session.logger.warning(f"PTT: Could not send pipeline interruption: {e}")
        asyncio.run_coroutine_threadsafe(send_ptt_interruption(), session.runner._loop)
    elif hasattr(session, 'stt_service') and session.stt_service:
        session.logger.warning("PTT: Runner/loop not available - STT will send InterruptionFrame on next frame")
    else:
        session.logger.warning("PTT: STT service not available for interruption")



def _cmd_push_to_talk_stop(session, params):
    session.logger.debug("PTT: Push-to-talk STOP received")
    session.ptt_active = False
    if hasattr(session, 'tts_service') and session.tts_service and hasattr(session.tts_service, 'set_ptt_active'):
        session.tts_service.set_ptt_active(False)
    if hasattr(session, 'stt_service') and session.stt_service:
        session.stt_service.set_ptt_active(False)
    else:
        session.logger.warning("PTT: STT service not available!")



def _cmd_interrupt_tts(session, params):
    session.logger.info("⏹ TTS interrupted")

    _loop = getattr(session.runner, '_loop', None) if session.runner else None
    if _loop is None:
        _loop = getattr(session, '_main_loop', None)

    # Set cancelled flags IMMEDIATELY (thread-safe direct attribute sets).
    if hasattr(session, 'llm_service') and session.llm_service:
        if hasattr(session.llm_service, '_cancelled'):
            session.llm_service._cancelled = True

    if hasattr(session, 'tts_service') and session.tts_service:
        if hasattr(session.tts_service, '_cancelled'):
            session.tts_service._cancelled = True
            if hasattr(session.tts_service, '_text_buffer'):
                session.tts_service._text_buffer = ""

    # Cancel welcome message task
    if session._welcome_task and not session._welcome_task.done():
        session._welcome_task.cancel()

    # IMMEDIATE: Kill audio in the OS/driver buffer by aborting the PyAudio
    # output stream directly from the command handler thread.  This doesn't
    # wait for the InterruptionFrame to propagate through the pipeline.
    transport_out = None
    if hasattr(session, 'transport') and session.transport:
        transport_out = getattr(session.transport, '_output', None)
        if transport_out is None and hasattr(session.transport, 'output'):
            try:
                transport_out = session.transport.output()
            except Exception:
                pass
    if transport_out is not None:
        # Set force_silence flag so any audio currently being written
        # by the executor thread becomes silence immediately.
        transport_out._force_silence = True
        try:
            stream = getattr(transport_out, '_out_stream', None)
            if stream and stream.is_active():
                stream.abort_stream()
                stream.start_stream()
        except Exception as e:
            logger.debug("Interrupt: could not abort output stream: %s", e)

    # Emit tts_stopped IMMEDIATELY so the GUI hides the player without
    # waiting for the async InterruptionFrame to propagate.
    if hasattr(session, 'event_queue') and session.event_queue:
        try:
            session.event_queue.put(('tts_stopped', {'duration': 0.0, 'interrupted': True}), block=False)
        except Exception:
            pass
    # Reset TTS session state so it doesn't emit a duplicate tts_stopped later
    if hasattr(session, 'tts_service') and session.tts_service:
        if hasattr(session.tts_service, '_tts_session_active'):
            session.tts_service._tts_session_active = False
        if hasattr(session.tts_service, '_tts_started_emitted'):
            session.tts_service._tts_started_emitted = False
        if hasattr(session.tts_service, '_total_audio_duration'):
            session.tts_service._total_audio_duration = 0.0

    # Broadcast InterruptionFrame to all pipeline processors via PipelineTask.
    if hasattr(session, 'stt_service') and session.stt_service:
        if _loop and _loop.is_running():
            async def send_pipeline_interruption():
                try:
                    await session.stt_service.push_interruption_task_frame_and_wait()
                except Exception as e:
                    logger.warning(f"Interrupt: pipeline interruption failed: {e}")

            asyncio.run_coroutine_threadsafe(send_pipeline_interruption(), _loop)
        else:
            logger.warning("Interrupt: event loop not available")
    else:
        logger.warning("Interrupt: STT service not available")



def _cmd_speak_text_directly(session, params):
    """Speak text directly via TTS without going through LLM."""
    from .libs import StartFrame, TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame
    text = params.get('text', '')
    if text and hasattr(session, 'llm_service') and session.llm_service:
        session.logger.debug(f"Speaking text directly via TTS: '{text[:50]}...'")
        if session.runner and session.runner._loop:
            async def speak_directly():
                pipeline_dir = session.llm_service._pipeline_direction

                is_started = getattr(session.llm_service, '_FrameProcessor__started', False)
                if not is_started:
                    session.logger.warning("LLM service not started yet - sending StartFrame first")
                    await session.llm_service.push_frame(StartFrame(), pipeline_dir)
                    await asyncio.sleep(0.01)

                await session.llm_service.push_frame(LLMFullResponseStartFrame(), pipeline_dir)
                await session.llm_service.push_frame(TextFrame(text=text), pipeline_dir)
                await session.llm_service.push_frame(LLMFullResponseEndFrame(), pipeline_dir)
                session.logger.debug("Direct TTS speech completed")

            asyncio.run_coroutine_threadsafe(speak_directly(), session.runner._loop)
        else:
            session.logger.warning("Cannot speak text directly: event loop not available")


# ---------------------------------------------------------------------------
# Chat commands
# ---------------------------------------------------------------------------

def _detect_correct_voice_provider(voice_provider: str, voice_model: str) -> str:
    """Detect the correct voice provider when voice_model doesn't match voice_provider.

    Handles cases where a chat has e.g. voice_provider='kokoro' but voice_model='some ElevenLabs voice'
    (an ElevenLabs voice). Returns the corrected provider (lowercase).
    """
    from .constants import KOKORO_VOICES, KOKORO_VOICE_BY_DISPLAY_NAME
    from .constants import normalize_voice_provider as _nvp

    vp = _nvp(voice_provider)
    vm = (voice_model or '').strip()
    if not vm:
        return vp

    # Custom voices (custom_<id>) — check DB for the actual provider
    if vm.startswith('custom_'):
        try:
            from distr.core.db import get_session, CustomVoice
            db_id = int(vm.split('_', 1)[1])
            with get_session() as sess:
                cv = sess.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                if cv and cv.provider:
                    return cv.provider.strip().lower()
        except Exception:
            pass
        return vp

    # Check if voice_model is a known Kokoro voice
    is_kokoro = vm in KOKORO_VOICES or vm in KOKORO_VOICE_BY_DISPLAY_NAME
    # Check if voice_model is a known OpenAI voice
    openai_voices = {'alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'}
    is_openai = vm.lower() in openai_voices

    # If the voice matches the claimed provider, no correction needed
    if 'kokoro' in vp and is_kokoro:
        return vp
    if 'openai' in vp and is_openai:
        return vp
    if 'elevenlabs' in vp:
        return vp  # ElevenLabs voices are dynamic (API IDs or names), trust the provider

    # Voice doesn't match claimed provider — try to detect the correct one
    if is_kokoro:
        return 'kokoro'
    if is_openai:
        return 'openai'

    # Not a known built-in voice — likely ElevenLabs (custom name or API voice ID)
    if not is_kokoro and not is_openai:
        # If the voice_model looks like a human name (not a Kokoro ID like af_heart),
        # it's probably an ElevenLabs voice
        if vp in ('kokoro', 'openai') and not any(c in vm for c in ('_', ':')):
            return 'elevenlabs'

    return vp


def _update_agent_identity_from_chat_voice(session, voice_provider, voice_model):
    """Update session.agent_name, session.role, and LLM system prompt from chat's voice.

    Ensures the agent identity (name + persona) shifts when loading a different chat
    (e.g. Heart -> Puck). Resolves ElevenLabs voice IDs to display names.
    """
    from . import service_factory
    new_name = service_factory.resolve_voice_to_display_name(
        voice_provider or '', voice_model or '', session.settings or {}
    )
    if new_name != session.agent_name:
        session.agent_name = new_name
        session.role = session._load_agent_role()
        session.logger.debug("Load chat: agent identity changed to %s", session.agent_name)
    service_factory.update_agent_name_on_llm(session.llm_service, session.agent_name, session.role)


def _cmd_transcribe_file(session, params):
    audio_file_path = params.get('audio_file_path')
    request_id = params.get('request_id')

    def _send_result(success, error, transcript):
        if session.event_queue:
            session.event_queue.put(('telegram_transcription_result', {
                'request_id': request_id,
                'success': success,
                'error': error,
                'transcript': transcript,
            }), block=False)

    if not audio_file_path:
        session.logger.error("transcribe_file command received without audio_file_path")
        _send_result(False, 'No audio_file_path provided', None)
        return

    if not hasattr(session, 'stt_service') or not session.stt_service:
        session.logger.error("transcribe_file command received but STT service not available")
        _send_result(False, 'STT service not available', None)
        return

    if not hasattr(session.stt_service, 'transcribe_file'):
        stt_name = type(session.stt_service).__name__
        session.logger.error(f"STT service {stt_name} does not support transcribe_file method")
        _send_result(False, f'STT service {stt_name} does not support file transcription', None)
        return

    session.logger.debug(f"Transcribing file {audio_file_path} using existing STT service {type(session.stt_service).__name__}")
    try:
        transcript = session.stt_service.transcribe_file(audio_file_path)
        if transcript:
            session.logger.debug(f"File transcription successful: '{transcript[:100]}...' ({len(transcript)} chars)")
            _send_result(True, None, transcript)
        else:
            session.logger.warning("File transcription returned None")
            _send_result(False, 'Transcription returned None', None)
    except Exception as e:
        session.logger.error(f"Error transcribing file: {e}", exc_info=True)
        _send_result(False, str(e), None)


def _cmd_files_dropped(session, params):
    notification_message = params.get('notification_message', '')
    session.logger.debug(f"Received files_dropped command (message length: {len(notification_message) if notification_message else 0} chars)")

    if not notification_message:
        session.logger.warning("files_dropped command received but notification_message is empty")
        return

    if not hasattr(session, 'llm_service') or not session.llm_service:
        session.logger.warning("files_dropped command received but llm_service is not available")
        return

    if hasattr(session.llm_service, '_on_files_indexed'):
        try:
            session.llm_service._on_files_indexed(notification_message)
            session.logger.debug("Successfully forwarded files_dropped notification to LLM service")
        except Exception as e:
            session.logger.error(f"Error forwarding files_dropped notification to LLM service: {e}", exc_info=True)
    else:
        session.logger.warning(f"LLM service ({type(session.llm_service).__name__}) does not have _on_files_indexed method")


def _cmd_current_chat_changed(session, params):
    """Switch the agent to a different chat.

    Single entry point for all chat switches (create-chat, load-in-agent, sidebar click).
    Does ONE database read, then orchestrates LLM/TTS swap + context load in deterministic order:

      1. Read chat from DB (single query)
      2. Resolve voice provider mismatches
      3. Swap LLM service if provider/model changed
      4. Swap TTS service if voice changed
      5. Update agent identity (name + persona)
      6. Update ChatManagerCore (bookkeeping only — listeners are NOT used for orchestration)
      7. Load chat history into LLM service
    """
    from distr.core.db import get_session, Chat, Settings as SettingsModel
    from distr.core.llm_factory import normalize_provider
    from .constants import PROVIDER_TO_ENGINE, normalize_voice_provider as _nvp

    chat_id = params.get('chat_id')
    if not chat_id:
        return

    session.logger.debug("=== LOAD CHAT %s ===", chat_id)

    # ---------------------------------------------------------------
    # 1. Single DB read: get chat + settings fallbacks
    # ---------------------------------------------------------------
    chat_provider = None
    chat_model = None
    chat_voice_provider = None
    chat_voice_model = None
    try:
        with get_session() as db:
            chat = db.get(Chat, chat_id)
            settings_row = db.query(SettingsModel).first()

            if chat:
                raw = (chat.provider or "").strip()
                chat_provider = normalize_provider(raw) if raw else None
                chat_model = (chat.model_name or "").strip() or None

                chat_voice_provider = _nvp(chat.voice_provider) if chat.voice_provider else None
                chat_voice_model = (chat.voice_model or "").strip() if chat.voice_model else None

            # Fill gaps from settings (so agent matches what the UI shows)
            if settings_row:
                if not chat_provider:
                    raw = (getattr(settings_row, 'conversational_llm_provider', None)
                           or getattr(settings_row, 'agent_provider', None) or "").strip()
                    chat_provider = normalize_provider(raw) if raw else None
                if not chat_model:
                    chat_model = (getattr(settings_row, 'conversational_llm_model', None)
                                  or getattr(settings_row, 'agent_model', None) or "").strip() or None
                if not chat_voice_provider:
                    chat_voice_provider = _nvp(getattr(settings_row, 'tts_provider', None) or "kokoro")
                if not chat_voice_model:
                    vp = chat_voice_provider or ""
                    if "kokoro" in vp:
                        chat_voice_model = (getattr(settings_row, 'kokoro_voice', None) or "").strip() or "af_heart"
                    elif "openai" in vp:
                        chat_voice_model = (getattr(settings_row, 'openai_voice', None) or "").strip() or "alloy"
                    elif "elevenlabs" in vp:
                        chat_voice_model = (getattr(settings_row, 'elevenlabs_voice', None) or "").strip() or ""
                    else:
                        chat_voice_model = "af_heart"
    except Exception as e:
        session.logger.warning("Load chat %s: DB read failed: %s", chat_id, e)

    # ---------------------------------------------------------------
    # 2. Correct voice provider mismatches (e.g. kokoro + ElevenLabs voice)
    # ---------------------------------------------------------------
    if chat_voice_provider and chat_voice_model:
        corrected = _detect_correct_voice_provider(chat_voice_provider, chat_voice_model)
        if corrected != chat_voice_provider:
            session.logger.info("Load chat %s: voice provider corrected %s -> %s (voice_model=%s)",
                                chat_id, chat_voice_provider, corrected, chat_voice_model)
            chat_voice_provider = corrected
            # Persist correction so it doesn't happen again
            try:
                with get_session() as db:
                    c = db.get(Chat, chat_id)
                    if c:
                        c.voice_provider = corrected
                        db.commit()
            except Exception:
                pass

    # ---------------------------------------------------------------
    # 3. Determine what needs swapping
    # ---------------------------------------------------------------
    provider = chat_provider or "Ollama"
    chat_engine = PROVIDER_TO_ENGINE.get(provider, "ollama")
    model_name = (chat_model or "").strip() or DEFAULT_MODELS.get(chat_engine, DEFAULT_MODELS["ollama"])

    cur_engine = (session.config or {}).get("llm", {}).get("engine", "ollama")
    cur_model = ((session.config or {}).get("llm", {}).get("model_name") or "").strip()
    need_llm_swap = (cur_engine != chat_engine) or (cur_model != model_name)

    session.logger.debug("Load chat %s: provider=%s model=%s voice=%s/%s need_llm_swap=%s",
                         chat_id, provider, model_name, chat_voice_provider, chat_voice_model, need_llm_swap)

    # ---------------------------------------------------------------
    # 4. Swap LLM if needed
    # ---------------------------------------------------------------
    if need_llm_swap:
        session._hot_swap_llm_service(provider, model_name, chat_id,
                                       voice_provider=chat_voice_provider,
                                       voice_model=chat_voice_model)

    # ---------------------------------------------------------------
    # 5. Swap TTS voice (always — even same provider may have different voice)
    # ---------------------------------------------------------------
    if chat_voice_provider and chat_voice_model:
        session._hot_swap_tts_service(chat_voice_provider, chat_voice_model)

    # ---------------------------------------------------------------
    # 6. Update agent identity (name + persona) from voice
    # ---------------------------------------------------------------
    if chat_voice_provider and chat_voice_model and session.llm_service:
        _update_agent_identity_from_chat_voice(session, chat_voice_provider, chat_voice_model)

    # ---------------------------------------------------------------
    # 7. Update ChatManagerCore (bookkeeping — no listeners trigger swaps)
    # ---------------------------------------------------------------
    if session.chat_manager:
        session.chat_manager.set_current_chat(chat_id)

    # ---------------------------------------------------------------
    # 8. Load chat history into LLM
    # ---------------------------------------------------------------
    if session.llm_service and hasattr(session.llm_service, 'on_chat_changed'):
        session.llm_service.on_chat_changed(chat_id)

    # ---------------------------------------------------------------
    # 9. Restore speaker (TTS) state from global settings
    # ---------------------------------------------------------------
    if session.llm_service:
        voice_enabled = (session.settings or {}).get(
            'voice_enabled', (session.settings or {}).get('chat_voice_enabled', True))
        session.llm_service.set_speaker_enabled(bool(voice_enabled))

    session.logger.debug("=== LOAD CHAT %s COMPLETE ===", chat_id)

    # ---------------------------------------------------------------
    # 10. Warm the Ollama model into memory (fire-and-forget)
    # ---------------------------------------------------------------
    if chat_engine == "ollama" and model_name:
        import threading

        def _warm_model():
            try:
                requests.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model_name, "prompt": "", "keep_alive": -1},
                    timeout=30,
                )
            except Exception:
                pass

        threading.Thread(target=_warm_model, daemon=True, name="ollama-warmup").start()


# ---------------------------------------------------------------------------
# Command dispatch table
# ---------------------------------------------------------------------------

_COMMAND_MAP = {
    # Lifecycle
    'shutdown': _cmd_shutdown,
    'reload': _cmd_reload,
    'file_operation_confirmation_response': _cmd_file_operation_confirmation_response,
    # Model / service updates
    'update_model': _cmd_update_model,
    'hot_swap_llm': _cmd_hot_swap_llm,
    'hot_swap_tts': _cmd_hot_swap_tts,
    'update_stt_model': _cmd_update_stt_model,
    # Audio
    'update_audio_devices': _cmd_update_audio_devices,
    'set_playback_speed': _cmd_set_playback_speed,
    'set_speech_volume': _cmd_set_speech_volume,
    'set_elevenlabs_voice_settings': _cmd_set_elevenlabs_voice_settings,
    'set_vad_threshold': _cmd_set_vad_threshold,
    # Mode
    'set_listening': _cmd_set_listening,
    'set_hands_free': _cmd_set_hands_free,
    'set_dictating': _cmd_set_dictating,
    'set_speaker_enabled': _cmd_set_speaker_enabled,
    'stop_dictation': _cmd_stop_dictation,
    # Interaction
    'process_text_input': _cmd_process_text_input,
    'push_to_talk_start': _cmd_push_to_talk_start,
    'push_to_talk_stop': _cmd_push_to_talk_stop,
    'interrupt_tts': _cmd_interrupt_tts,
    'speak_text_directly': _cmd_speak_text_directly,
    # Chat
    'transcribe_file': _cmd_transcribe_file,
    'files_dropped': _cmd_files_dropped,
    'current_chat_changed': _cmd_current_chat_changed,
}
