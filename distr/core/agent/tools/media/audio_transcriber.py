"""
Audio File Transcription Tool for LangChain.

This tool transcribes audio files (.mp3, .m4a, .wav, etc.) to text.
Supports AssemblyAI with diarization (if API key available) or falls back to Whisper.cpp.
Runs as a separate process to avoid blocking.
"""

from typing import Optional, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioTranscriberInput(BaseModel):
    """Input schema for audio_transcriber tool."""
    audio_file_path: Optional[str] = Field(default=None, description="Path to the audio file to transcribe (.mp3, .m4a, .wav, etc.). If None, will look for recently dropped audio files.")
    use_assemblyai: bool = Field(default=True, description="If True, use AssemblyAI with diarization if available, otherwise fallback to Whisper.cpp")
    transcribe_all: bool = Field(default=False, description="If True, transcribe all recently dropped audio files. If False, only transcribe the first one. Use True when user says 'transcribe the files' (plural).")


def _transcribe_with_assemblyai(audio_file_path: str, api_key: str) -> Optional[str]:
    """Transcribe audio file using AssemblyAI with diarization."""
    try:
        import assemblyai as aai
        
        # Configure API key
        aai.settings.api_key = api_key
        
        # Create transcriber
        transcriber = aai.Transcriber()
        
        # Upload audio file
        logger.info(f"AudioTranscriber: Uploading {audio_file_path} to AssemblyAI...")
        transcript = transcriber.transcribe(
            audio_file_path,
            config=aai.TranscriptionConfig(
                speaker_labels=True,  # Enable diarization
                language_detection=True
            )
        )
        
        # Wait for transcription to complete
        logger.info("AudioTranscriber: Waiting for AssemblyAI transcription to complete...")
        transcript.wait_for_completion()
        
        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AudioTranscriber: AssemblyAI transcription failed: {transcript.error}")
            return None
        
        # Format transcript with speaker labels (diarization)
        transcript_text = []
        if transcript.utterances:
            # Use utterances for speaker-separated transcript
            for utterance in transcript.utterances:
                speaker = f"Speaker {utterance.speaker}" if utterance.speaker else "Speaker"
                transcript_text.append(f"{speaker}: {utterance.text}")
            result = "\n".join(transcript_text)
        else:
            # Fallback to plain text if no utterances
            result = transcript.text
        
        logger.info(f"AudioTranscriber: AssemblyAI transcription completed ({len(result)} chars)")
        return result
        
    except ImportError:
        logger.warning("AudioTranscriber: assemblyai package not available")
        return None
    except Exception as e:
        logger.error(f"AudioTranscriber: AssemblyAI transcription error: {e}", exc_info=True)
        return None


def _transcribe_with_whispercpp(audio_file_path: str, model: str = "base.en") -> Optional[str]:
    """Transcribe audio file using Whisper.cpp."""
    try:
        from distr.core.agent.libs import pwc, WHISPER_AVAILABLE
        
        if not WHISPER_AVAILABLE:
            logger.warning("AudioTranscriber: pywhispercpp not available")
            return None
        
        logger.info(f"AudioTranscriber: Transcribing {audio_file_path} with Whisper.cpp (model: {model})...")
        
        # Load model
        whisper_model = pwc.Model(model, print_progress=False)
        
        # Transcribe
        result = whisper_model.transcribe(audio_file_path, print_progress=False)
        
        # Extract text from result
        if isinstance(result, list):
            # Result is a list of segments
            transcript_text = []
            for segment in result:
                if hasattr(segment, 'text'):
                    transcript_text.append(segment.text)
                elif isinstance(segment, dict):
                    transcript_text.append(segment.get('text', ''))
                else:
                    transcript_text.append(str(segment))
            transcript = " ".join(transcript_text)
        elif isinstance(result, dict):
            transcript = result.get('text', '')
        else:
            transcript = str(result)
        
        logger.info(f"AudioTranscriber: Whisper.cpp transcription completed ({len(transcript)} chars)")
        return transcript.strip()
        
    except ImportError:
        logger.warning("AudioTranscriber: pywhispercpp not available")
        return None
    except Exception as e:
        logger.error(f"AudioTranscriber: Whisper.cpp transcription error: {e}", exc_info=True)
        return None


def _transcribe_worker_thread(audio_file_path: str, output_file_path: str, use_assemblyai: bool, assemblyai_key: Optional[str], whisper_model: str, chat_manager=None, chat_id=None):
    """Worker function to run transcription in background thread and notify when done."""
    def _emit_progress(status_text: str, done: bool = False):
        if not chat_id:
            return
        try:
            from distr.core.signals import signal_manager
            signal_manager.transcription_progress.emit(int(chat_id), status_text, bool(done), False)
        except Exception:
            pass

    try:
        transcript = None
        _emit_progress(f"Transcription started for {os.path.basename(audio_file_path)}", done=False)
        
        # Try AssemblyAI first if requested and key available
        if use_assemblyai and assemblyai_key:
            logger.info("AudioTranscriber: Attempting AssemblyAI transcription...")
            _emit_progress("Uploading audio to AssemblyAI...", done=False)
            transcript = _transcribe_with_assemblyai(audio_file_path, assemblyai_key)
        
        # Fallback to Whisper.cpp if AssemblyAI failed or not available
        if not transcript:
            logger.info("AudioTranscriber: Falling back to Whisper.cpp...")
            _emit_progress("Using local Whisper transcription...", done=False)
            transcript = _transcribe_with_whispercpp(audio_file_path, whisper_model)
        
        if transcript:
            # Write transcript to file
            _emit_progress(f"Writing transcript to {os.path.basename(output_file_path)}...", done=False)
            with open(output_file_path, 'w', encoding='utf-8') as f:
                f.write(transcript)
            logger.info(f"AudioTranscriber: Transcript saved to {output_file_path}")
            _emit_progress(f"Transcription complete. Saved to {os.path.basename(output_file_path)}", done=True)
            
            # Notify user via TTS
            try:
                from distr.core.signals import speak_text_directly_event_queue
                notification = f"Transcription complete. Saved to {os.path.basename(output_file_path)}"
                speak_text_directly_event_queue(notification)
                logger.info(f"AudioTranscriber: Sent completion notification via TTS")
            except Exception as e:
                logger.warning(f"AudioTranscriber: Could not send TTS notification: {e}")
            
            # Optionally add message to chat
            if chat_manager and chat_id:
                try:
                    summary = f"✅ Transcription complete!\n\nSaved to: `{output_file_path}`\n\nTranscript preview ({len(transcript)} chars):\n{transcript[:300]}{'...' if len(transcript) > 300 else ''}"
                    chat_manager.add_assistant_message(chat_id, summary)
                    from distr.core.signals import signal_manager
                    signal_manager.chat_message_added.emit(chat_id, "assistant", summary)
                    signal_manager.chat_updated.emit(chat_id)
                    logger.info(f"AudioTranscriber: Added completion message to chat {chat_id}")
                except Exception as e:
                    logger.warning(f"AudioTranscriber: Could not add message to chat: {e}")
        else:
            error_msg = "Failed to transcribe audio file with both AssemblyAI and Whisper.cpp"
            logger.error(f"AudioTranscriber: {error_msg}")
            _emit_progress("Transcription failed with available backends.", done=True)
            
            # Notify user of error
            try:
                from distr.core.signals import speak_text_directly_event_queue
                speak_text_directly_event_queue("Transcription failed. Please check the logs.")
                logger.info(f"AudioTranscriber: Sent error notification via TTS")
            except Exception as e:
                logger.warning(f"AudioTranscriber: Could not send error notification: {e}")
            
    except Exception as e:
        logger.error(f"AudioTranscriber: Worker thread error: {e}", exc_info=True)
        _emit_progress(f"Transcription error: {str(e)[:120]}", done=True)
        
        # Notify user of error
        try:
            from distr.core.signals import speak_text_directly_event_queue
            speak_text_directly_event_queue(f"Transcription error: {str(e)[:100]}")
        except Exception:
            pass


class AudioTranscriberTool(BaseTool):
    """Tool for transcribing audio files to text."""
    
    name: str = "audio_transcriber"
    description: str = (
        "Transcribe audio files (.mp3, .m4a, .wav, .flac, .ogg, etc.) to text. "
        "Uses AssemblyAI with speaker diarization if API key is available, otherwise falls back to Whisper.cpp. "
        "Outputs a .txt file with the same filename in the same location as the audio file. "
        "Use this when the user says 'transcribe this', 'transcribe the file', 'transcribe the files', or 'transcribe the file/s I just gave you' after dropping audio file(s). "
        "If the user says 'transcribe the files' (plural), transcribe ALL recently dropped audio files. "
        "If the user says 'transcribe the file' (singular), transcribe only the first/most recent file. "
        "The transcription runs in a background thread and does not block. Returns immediately with a status message."
    )
    args_schema: type[BaseModel] = AudioTranscriberInput
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self.chat_manager = chat_manager
    
    def _find_recent_audio_files(self, multiple: bool = False) -> list[str]:
        """Find recently dropped audio file(s).
        
        Args:
            multiple: If True, return all audio files. If False, return only the first one.
        
        Returns:
            List of audio file paths (empty list if none found)
        """
        try:
            import json
            storage_file = os.path.join(os.path.expanduser("~"), ".decisions", "dropped_files", "current_files.json")
            
            if not os.path.exists(storage_file):
                return []
            
            with open(storage_file, 'r') as f:
                data = json.load(f)
            
            audio_files = data.get("audio_files", [])
            if not audio_files:
                return []
            
            # Filter to only existing files
            existing_files = [f for f in audio_files if os.path.exists(f)]
            if not existing_files:
                return []

            # Prefer files associated with the current chat when possible.
            current_chat_id = None
            if self.chat_manager:
                try:
                    current_chat_id = self.chat_manager.get_current_chat()
                except Exception:
                    current_chat_id = None

            chat_files_index = data.get("chat_files_index", {})
            file_chat_mapping = data.get("file_chat_mapping", {})
            if current_chat_id:
                chat_bucket = chat_files_index.get(str(current_chat_id), {})
                if isinstance(chat_bucket, dict):
                    bucket_audio = chat_bucket.get("audio_files", [])
                    bucket_existing = [p for p in bucket_audio if os.path.exists(p)]
                    if bucket_existing:
                        existing_files = bucket_existing

                chat_files = []
                if not (isinstance(chat_bucket, dict) and chat_bucket.get("audio_files")):
                    for path in existing_files:
                        chat_ids = file_chat_mapping.get(path, [])
                        if not isinstance(chat_ids, list):
                            chat_ids = [chat_ids] if chat_ids else []
                        if current_chat_id in chat_ids:
                            chat_files.append(path)
                    if chat_files:
                        existing_files = chat_files

            # Sort by drop timestamp, most recent first.
            file_timestamps = data.get("file_timestamps", {})
            existing_files.sort(key=lambda p: file_timestamps.get(p, 0), reverse=True)
            
            if multiple:
                return existing_files
            else:
                # Return most recent file as list for consistency
                return [existing_files[0]] if existing_files else []
            
        except Exception as e:
            logger.warning(f"AudioTranscriber: Error finding recent audio file(s): {e}")
            return []
    
    def _run(self, audio_file_path: Optional[str] = None, use_assemblyai: bool = True, transcribe_all: bool = False, last_user_message: str = None, **kwargs) -> str:
        """Transcribe audio file(s) to text.
        
        Args:
            audio_file_path: Optional path to specific audio file. If None, will use recently dropped files.
            use_assemblyai: Whether to use AssemblyAI (if available).
            transcribe_all: If True, transcribe all recently dropped audio files. If False, only the first one.
        """
        try:
            audio_files_to_transcribe = []
            
            # If specific path provided, use it
            if audio_file_path:
                if not os.path.exists(audio_file_path):
                    return f"Error: Audio file not found: {audio_file_path}"
                
                # Check if it's an audio file
                audio_extensions = {'.mp3', '.m4a', '.wav', '.flac', '.ogg', '.opus', '.aac', '.m4b', '.wma'}
                file_ext = os.path.splitext(audio_file_path)[1].lower()
                if file_ext not in audio_extensions:
                    return f"Error: File is not a supported audio format. Supported: {', '.join(audio_extensions)}"
                
                audio_files_to_transcribe = [audio_file_path]
                logger.info(f"AudioTranscriber: Using provided audio file: {audio_file_path}")
            else:
                # Find recently dropped audio files
                logger.info(f"AudioTranscriber: No audio file path provided, looking for recently dropped audio files (transcribe_all={transcribe_all})...")
                audio_files_to_transcribe = self._find_recent_audio_files(multiple=transcribe_all)
                
                if not audio_files_to_transcribe:
                    return "Error: No audio file specified and no recently dropped audio files found. Please provide an audio file path or drop an audio file first."
                
                logger.info(f"AudioTranscriber: Found {len(audio_files_to_transcribe)} audio file(s) to transcribe")
            
            # Get settings for API keys
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            assemblyai_key = settings.get('assemblyai_key', '') if settings.get('assemblyai_enabled', False) else None
            whisper_model = "base.en"  # Default Whisper model
            
            # Get current chat ID for notifications
            chat_id = None
            if self.chat_manager:
                try:
                    chat_id = self.chat_manager.get_current_chat()
                except Exception as e:
                    logger.warning(f"AudioTranscriber: Could not get current chat ID: {e}")
            
            # Start transcription for each file in background threads
            started_count = 0
            for audio_file_path in audio_files_to_transcribe:
                # Generate output file path (same location, same name, .txt extension)
                audio_path = Path(audio_file_path)
                output_file_path = audio_path.with_suffix('.txt')
                
                logger.info(f"AudioTranscriber: Starting background transcription of {audio_file_path} -> {output_file_path}")
                
                # Start transcription in background thread (non-blocking)
                thread = threading.Thread(
                    target=_transcribe_worker_thread,
                    args=(audio_file_path, str(output_file_path), use_assemblyai, assemblyai_key, whisper_model),
                    kwargs={"chat_manager": self.chat_manager, "chat_id": chat_id},
                    daemon=True  # Daemon thread so it doesn't prevent app shutdown
                )
                thread.start()
                started_count += 1
            
            # Return immediately with status message
            if len(audio_files_to_transcribe) == 1:
                audio_path = Path(audio_files_to_transcribe[0])
                output_file_path = audio_path.with_suffix('.txt')
                return f"Busy transcribing {os.path.basename(audio_files_to_transcribe[0])}. Will get back to you when it's done. The transcript will be saved to {os.path.basename(output_file_path)} in the same folder."
            else:
                file_names = [os.path.basename(f) for f in audio_files_to_transcribe]
                return f"Busy transcribing {len(audio_files_to_transcribe)} audio file(s): {', '.join(file_names)}. Will get back to you when they're done. Each transcript will be saved as a .txt file in the same folder as the audio file."
                
        except Exception as e:
            logger.error(f"AudioTranscriber: Error in _run: {e}", exc_info=True)
            return f"Error transcribing audio file(s): {str(e)}"
    
    async def _arun(self, audio_file_path: Optional[str] = None, use_assemblyai: bool = True, transcribe_all: bool = False, last_user_message: str = None, **kwargs) -> str:
        """Async version of _run."""
        return self._run(audio_file_path, use_assemblyai, transcribe_all, last_user_message, **kwargs)

