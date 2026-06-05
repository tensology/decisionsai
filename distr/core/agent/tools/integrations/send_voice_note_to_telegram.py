from typing import Type, Optional, Any
from pydantic import BaseModel, Field
import os
import tempfile
import logging
from langchain.tools import BaseTool

logger = logging.getLogger(__name__)

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
            event_queue = self.event_queue
            if event_queue is None:
                try:
                    from distr.core.signals import get_agent_event_queue

                    event_queue = get_agent_event_queue()
                    self.event_queue = event_queue
                except Exception:
                    event_queue = None
            if event_queue is None:
                return "Error sending voice note: Telegram delivery bridge is unavailable."

            from distr.core.agent.constants import normalize_voice_provider
            from distr.core.agent.services.tts.registry import tts_registry
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
            provider_id = normalize_voice_provider(tts_provider)

            # Resolve voice model from chat → provider-specific global setting
            if chat_voice_model:
                resolved_voice = chat_voice_model
            else:
                try:
                    descriptor = tts_registry.get(provider_id)
                    resolved_voice = descriptor.get_telegram_voice_id(settings)
                except KeyError:
                    resolved_voice = ''

            # Generate audio via the registry descriptor
            try:
                descriptor = tts_registry.get(provider_id)
            except KeyError:
                return f"Error sending voice note: Unknown TTS provider: {tts_provider}"

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file_path = temp_file.name

            try:
                descriptor.generate_audio(
                    message,
                    resolved_voice or descriptor.default_voice,
                    1.0,
                    temp_file_path,
                )
            except Exception as e:
                # Clean up temp file on failure
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                return f"Error generating voice note with {descriptor.name}: {str(e)}"

            if not temp_file_path or not os.path.exists(temp_file_path):
                return f"Error sending voice note: Failed to generate audio file"
            
            # Send event to main process to handle Telegram upload
            # For voice notes: no text/caption, no screenshot, just the audio
            event_queue.put(('send_to_telegram', {
                'text': '',  # No text/caption for voice notes - just send the audio
                'audio_file_path': temp_file_path,
                'is_done': False,  # Don't trigger screenshot for voice notes
                'provider': 'tool',
                'is_voice_note': True,  # Flag to indicate this is a voice note
                'voice_note_message': message,
                'explicit_notification_intent': True,
                'engagement_kind': 'voice_note',
                'engagement_subject_type': 'tool',
                'engagement_subject_id': 'send_voice_note_to_telegram',
            }), block=False)
            
            return f"Voice note sent: '{message}'"
            
        except Exception as e:
            return f"Error sending voice note: {str(e)}"
    
    async def _arun(self, message: str, **kwargs) -> str:
        """Async run method"""
        # Ignore extra kwargs like 'last_user_message' that may be passed by LLM service
        return self._run(message=message)
