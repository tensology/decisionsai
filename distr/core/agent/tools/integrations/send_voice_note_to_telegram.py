from typing import Type, Optional, Any
from pydantic import BaseModel, Field
import os
import tempfile
from openai import OpenAI
from langchain.tools import BaseTool

class SendVoiceNoteToTelegramInput(BaseModel):
    message: str = Field(description="ONLY the exact words to be spoken in the voice note. Do NOT include your own commentary, confirmation text, or meta-text like 'Sure, sending...' — only the content the user wants spoken.")

class SendVoiceNoteToTelegramTool(BaseTool):
    name: str = "send_voice_note_to_telegram"
    description: str = """Send a voice note to Telegram.
    Use this tool when the user asks to "send a voice note" or "tell him/her [message]" via Telegram.
    This tool converts the text message into an audio voice note using AI TTS and sends it directly.
    The agent should separately confirm to the user "Sure, sending..." using its normal voice, 
    but ONLY the content in 'message' will be sent to Telegram as audio.
    """
    args_schema: Type[BaseModel] = SendVoiceNoteToTelegramInput
    
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self.event_queue = event_queue

    def _run(self, message: str, **kwargs) -> str:
        try:
            # Load settings to get TTS provider and voice
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()

            # Resolve voice from current chat first, then fall back to global settings
            chat_voice_provider = ""
            chat_voice_model = ""
            try:
                from distr.core.db import get_session, Chat
                chat_id = settings.get('agent_current_chat_id')
                if chat_id:
                    with get_session() as session:
                        chat = session.query(Chat).filter(Chat.id == int(chat_id)).first()
                        if chat:
                            root = chat
                            while root.parent_id:
                                parent = session.query(Chat).filter(Chat.id == root.parent_id).first()
                                if not parent:
                                    break
                                root = parent
                            chat_voice_provider = (root.voice_provider or "").strip()
                            chat_voice_model = (root.voice_model or "").strip()
            except Exception:
                pass

            tts_provider = chat_voice_provider or settings.get('tts_provider', 'Kokoro (Offline)')
            tts_lower = tts_provider.lower()

            # Resolve voice model from chat → provider-specific global setting
            if chat_voice_model:
                resolved_voice = chat_voice_model
            elif 'kokoro' in tts_lower:
                resolved_voice = settings.get('kokoro_voice', 'af_heart')
            elif 'openai' in tts_lower:
                resolved_voice = settings.get('openai_voice', 'alloy')
            elif 'elevenlabs' in tts_lower:
                resolved_voice = settings.get('elevenlabs_voice', '')
            elif 'voxcpm' in tts_lower:
                resolved_voice = settings.get('voxcpm_voice', 'default')
            else:
                resolved_voice = ''
            
            temp_file_path = None
            
            if 'kokoro' in tts_lower:
                # Use Kokoro TTS with the resolved voice
                kokoro_voice = resolved_voice or 'af_heart'
                
                try:
                    from distr.core.agent.libs import Kokoro, KOKORO_AVAILABLE
                    if not KOKORO_AVAILABLE:
                        return f"Error sending voice note: Kokoro TTS is not available"
                    
                    # Get Kokoro model and voices paths - use same paths as session.py
                    # First try settings, then try the default app model paths
                    kokoro_model_path = settings.get('kokoro_model_path', '')
                    kokoro_voices_path = settings.get('kokoro_voices_path', '')
                    
                    if not kokoro_model_path or not kokoro_voices_path or not os.path.exists(kokoro_model_path):
                        # Use the same paths as session.py uses for the main TTS service
                        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
                        models_dir = os.path.join(base_dir, "agent", "models")
                        app_model = os.path.join(models_dir, "kokoro-v1.0.onnx")
                        app_voices = os.path.join(models_dir, "voices-v1.0.bin")
                        
                        if os.path.exists(app_model) and os.path.exists(app_voices):
                            kokoro_model_path = app_model
                            kokoro_voices_path = app_voices
                        else:
                            # Try legacy paths as last resort
                            legacy_model = os.path.expanduser("~/.local/share/kokoro/models/kokoro-v2_1.onnx")
                            legacy_voices = os.path.expanduser("~/.local/share/kokoro/voices")
                            if os.path.exists(legacy_model) and os.path.exists(legacy_voices):
                                kokoro_model_path = legacy_model
                                kokoro_voices_path = legacy_voices
                            else:
                                return f"Error sending voice note: Kokoro model not found at {app_model} or {legacy_model}"
                    
                    # Handle custom voice — resolve base voice + reference for Kanade conversion
                    reference_path = None
                    actual_voice = kokoro_voice
                    if kokoro_voice.startswith("custom_"):
                        try:
                            from distr.core.db import get_session, CustomVoice
                            db_id = int(kokoro_voice.split("_", 1)[1])
                            session = get_session()
                            try:
                                cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                                if cv and cv.audio_dir and os.path.isdir(cv.audio_dir):
                                    for fname in os.listdir(cv.audio_dir):
                                        if fname.lower().endswith(('.wav', '.flac', '.ogg', '.mp3', '.m4a', '.webm')):
                                            reference_path = os.path.join(cv.audio_dir, fname)
                                            break
                                gender = getattr(cv, 'gender', 'female') if cv else 'female'
                                actual_voice = "am_puck" if gender == "male" else "af_heart"
                            finally:
                                session.close()
                        except Exception as e:
                            logger.warning(f"Failed to resolve custom voice {kokoro_voice}: {e}")
                            actual_voice = "af_heart"

                    # Initialize Kokoro and generate audio
                    kokoro = Kokoro(kokoro_model_path, kokoro_voices_path)
                    # Normalize smart quotes for correct pronunciation
                    from distr.core.agent.services.tts.kokoro import _normalize_text_for_tts
                    message = _normalize_text_for_tts(message)
                    audio_data, sample_rate = kokoro.create(message, voice=actual_voice, speed=1.0)

                    # Apply Kanade voice conversion for custom voices
                    if reference_path and audio_data is not None and len(audio_data) > 0:
                        try:
                            from distr.core.audio.voice_cloner import convert_voice, get_output_sample_rate
                            audio_data = convert_voice(audio_data, sample_rate, reference_path)
                            sample_rate = get_output_sample_rate()
                        except Exception as vc_err:
                            logger.error(f"Voice cloning failed, using base voice: {vc_err}")
                    
                    # Save to temporary file (WAV format for Kokoro)
                    import wave
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                        temp_file_path = temp_file.name
                        with wave.open(temp_file_path, 'wb') as wav_file:
                            wav_file.setnchannels(1)  # Mono
                            wav_file.setsampwidth(2)  # 16-bit
                            wav_file.setframerate(sample_rate)
                            # Convert float32 to int16
                            import numpy as np
                            audio_int16 = (audio_data * 32767).astype(np.int16)
                            wav_file.writeframes(audio_int16.tobytes())
                    
                except Exception as e:
                    return f"Error generating voice note with Kokoro: {str(e)}"
                    
            elif 'openai' in tts_lower:
                # Use OpenAI TTS with the resolved voice
                openai_key = settings.get('openai_key', '')
                if not openai_key:
                    return f"Error sending voice note: OpenAI API key not configured in settings"
                
                openai_voice = resolved_voice or 'alloy'
                
                client = OpenAI(api_key=openai_key)
                
                # Generate speech
                response = client.audio.speech.create(
                    model="tts-1",
                    voice=openai_voice,
                    input=message
                )
                
                # Save to temporary file
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                    temp_file_path = temp_file.name
                    response.stream_to_file(temp_file_path)
                    
            elif 'elevenlabs' in tts_lower:
                # Use ElevenLabs TTS with the resolved voice
                elevenlabs_key = settings.get('elevenlabs_key', '')
                elevenlabs_voice = resolved_voice or settings.get('elevenlabs_voice', '')
                
                if not elevenlabs_key or not elevenlabs_voice:
                    return f"Error sending voice note: ElevenLabs API key or voice not configured in settings"
                
                try:
                    from elevenlabs import ElevenLabs
                    client = ElevenLabs(api_key=elevenlabs_key)
                    
                    # Generate audio
                    audio_stream = client.text_to_speech.convert(
                        text=message,
                        voice_id=elevenlabs_voice,
                        model_id="eleven_multilingual_v2",
                        output_format="mp3_44100_128",
                        voice_settings={
                            "stability": 0.50,
                            "similarity_boost": 0.60,
                            "style": 0.25,
                            "use_speaker_boost": True,
                            "speed": 0.90
                        }
                    )
                    
                    # Collect audio bytes
                    audio_bytes = b""
                    for chunk in audio_stream:
                        audio_bytes += chunk
                    
                    # Save to temporary file
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                        temp_file_path = temp_file.name
                        temp_file.write(audio_bytes)
                        
                except Exception as e:
                    return f"Error generating voice note with ElevenLabs: {str(e)}"

            elif 'voxcpm' in tts_lower:
                # Use VoxCPM TTS with the resolved voice
                try:
                    from distr.core.audio.tts_handler import _generate_voxcpm
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                        temp_file_path = temp_file.name
                    _generate_voxcpm(message, resolved_voice or 'default', 1.0, temp_file_path)
                except Exception as e:
                    return f"Error generating voice note with VoxCPM: {str(e)}"

            else:
                return f"Error sending voice note: Unknown TTS provider: {tts_provider}"
            
            if not temp_file_path or not os.path.exists(temp_file_path):
                return f"Error sending voice note: Failed to generate audio file"
            
            # Send event to main process to handle Telegram upload
            # For voice notes: no text/caption, no screenshot, just the audio
            self.event_queue.put(('send_to_telegram', {
                'text': '',  # No text/caption for voice notes - just send the audio
                'audio_file_path': temp_file_path,
                'is_done': False,  # Don't trigger screenshot for voice notes
                'provider': 'tool',
                'is_voice_note': True  # Flag to indicate this is a voice note
            }), block=False)
            
            return f"Voice note sent: '{message}'"
            
        except Exception as e:
            return f"Error sending voice note: {str(e)}"
    
    async def _arun(self, message: str, **kwargs) -> str:
        """Async run method"""
        # Ignore extra kwargs like 'last_user_message' that may be passed by LLM service
        return self._run(message=message)
