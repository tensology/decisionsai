"""
Save Audio Tool for LangChain.

This tool saves clipboard content as a WAV or MP3 file using TTS.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import time
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime
from distr.core.agent.tools.base import get_platform_modifier_key
from distr.core.agent.libs import pyautogui

logger = logging.getLogger(__name__)

EXPORT_CHUNK_MAX_ATTEMPTS = 3
EXPORT_RETRY_DELAYS_SEC = (2.0, 5.0)


def split_export_text(text: str, max_chars: int = 800) -> list[str]:
    """Split long narration into provider-safe chunks at natural boundaries."""
    value = str(text or "").strip()
    if not value:
        return []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", value)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            split_at = sentence.rfind(" ", 0, max_chars + 1)
            if split_at < max_chars // 2:
                split_at = max_chars
            piece, sentence = sentence[:split_at].strip(), sentence[split_at:].strip()
            if current:
                chunks.append(current)
                current = ""
            if piece:
                chunks.append(piece)
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def generate_export_chunk(
    text: str,
    *,
    provider: Optional[str],
    voice: Optional[str],
    max_attempts: int = EXPORT_CHUNK_MAX_ATTEMPTS,
) -> str:
    """Generate one resumable export chunk, retrying transient provider failures."""
    from distr.core.audio.tts_handler import generate_tts_audio

    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            generated_wav = generate_tts_audio(
                text,
                provider=provider,
                voice=voice,
                speed=1.0,
            )
            if generated_wav and os.path.exists(generated_wav):
                return generated_wav
            raise RuntimeError("TTS provider did not produce an audio file")
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            delay = EXPORT_RETRY_DELAYS_SEC[min(attempt - 1, len(EXPORT_RETRY_DELAYS_SEC) - 1)]
            logger.warning(
                "Save audio: Chunk attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                max_attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"TTS provider failed after {max_attempts} attempts: {last_error}"
    ) from last_error


def get_clipboard_content():
    """Get content from clipboard using platform-specific methods."""
    try:
        system = platform.system()
        
        if system == "Darwin":  # macOS
            result = subprocess.run(
                ['pbpaste'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout if result.returncode == 0 else None
        elif system == "Windows":
            result = subprocess.run(
                ['powershell', '-command', 'Get-Clipboard'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout.strip() if result.returncode == 0 else None
        else:  # Linux
            try:
                # Try xclip first
                result = subprocess.run(
                    ['xclip', '-selection', 'clipboard', '-o'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
            try:
                # Fallback to xsel
                result = subprocess.run(
                    ['xsel', '--clipboard', '--output'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                return result.stdout if result.returncode == 0 else None
            except Exception:
                pass
            return None
    except Exception as e:
        logger.error(f"Error getting clipboard content: {e}", exc_info=True)
        return None


def get_output_path(destination: str) -> str:
    """Resolve the supported user-facing output folder names."""
    folder = str(destination or "downloads").strip().lower()
    if folder in {"download", "downloads"}:
        return os.path.join(os.path.expanduser("~"), "Downloads")
    if folder == "desktop":
        return os.path.join(os.path.expanduser("~"), "Desktop")
    raise ValueError("Choose Downloads or Desktop as the audio destination.")


class SaveAudioTool(BaseTool):
    """Tool for saving clipboard content as audio file."""
    
    name: str = "save_audio"
    description: str = """EXECUTE saving clipboard content as audio file.
    
    CRITICAL: ONLY call this tool when the user EXPLICITLY says "save this as audio" or "save clipboard to audio".
    DO NOT call this tool for:
    - Conversational text or song lyrics
    - General statements or quotes
    - Questions or requests that don't explicitly mention saving audio
    - Any text that doesn't contain "save" AND "audio" together
    
    The tool automatically:
    - For "save this as audio": Copies current selection (Cmd+C), then gets clipboard content
    - For "save clipboard to audio": Uses clipboard directly (no copy needed)
    - Generates audio using TTS
    - Saves an MP3 or WAV file to Downloads or Desktop
    
    REQUIRED CALLS (EXPLICIT COMMANDS ONLY):
    - "save this as audio" -> CALL immediately
    - "save clipboard to audio" -> CALL immediately
    - "save the clipboard as audio" -> CALL immediately
    
    DO NOT CALL FOR:
    - General conversation
    - Song lyrics or quotes
    - Questions about audio
    - Any text without explicit "save" + "audio" command
    
    CALL THE TOOL - never describe it."""
    
    tts_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, tts_service=None, **kwargs):
        super().__init__(**kwargs)
        self._tts_service = tts_service

    def set_tts_service(self, tts_service) -> None:
        """Refresh the runtime TTS dependency after the tool cache is warmed."""
        self._tts_service = tts_service

    def _active_voice(self) -> tuple[Optional[str], Optional[str]]:
        """Return provider and voice metadata from any active TTS service."""
        service = self._tts_service
        if service is None:
            return None, None
        provider = getattr(service, "_provider_id", None)
        if not provider:
            module_name = type(service).__module__.rsplit(".", 1)[-1].lower()
            provider = module_name if module_name in {
                "coqui", "elevenlabs", "kokoro", "openai", "pixazo", "supertonic"
            } else None
        voice = (
            getattr(service, "voice_id", None)
            or getattr(service, "voice", None)
            or getattr(service, "voice_name", None)
        )
        return provider, voice
    
    def _run(
        self,
        text: str = "",
        audio_format: str = "mp3",
        destination: str = "downloads",
        **kwargs,
    ) -> str:
        """Execute save audio action."""
        try:
            # Validate that this is actually a save audio command
            text_lower = text.lower() if text else ""
            has_save = "save" in text_lower
            has_audio = "audio" in text_lower or "sound" in text_lower
            
            # Only proceed if both "save" and "audio" are present (explicit command)
            if text_lower and not (has_save and has_audio):
                logger.warning(f"Save audio: Invalid command - missing 'save' or 'audio' in: '{text}'")
                return "Error: This tool should only be called for explicit 'save this as audio' or 'save clipboard to audio' commands."
            
            # Check if user said "save clipboard to audio" - if so, skip copying step
            use_clipboard_directly = "clipboard" in text_lower and "save" in text_lower
            
            # Step 1: Press Cmd+C to copy (unless using clipboard directly)
            if not use_clipboard_directly:
                cmd_key = get_platform_modifier_key()
                logger.info(f"Save audio: Pressing {cmd_key}+C to copy selection")
                pyautogui.keyDown(cmd_key)
                pyautogui.press('c')
                pyautogui.keyUp(cmd_key)
                time.sleep(0.15)  # Wait for clipboard to update
            else:
                logger.info("Save audio: Using clipboard directly (no copy needed)")
            
            # Step 2: Get clipboard content
            clipboard_text = get_clipboard_content()
            if not clipboard_text or not clipboard_text.strip():
                logger.warning("Save audio: Clipboard is empty")
                return "Error: Clipboard is empty. Make sure you have text selected before using this command."
            
            logger.info(f"Save audio: Got clipboard content ({len(clipboard_text)} chars)")
            
            # Step 3: Generate audio through the shared provider registry.
            try:
                text_to_save = clipboard_text.strip()
                logger.info(f"Save audio: Generating audio for {len(text_to_save)} characters")
                from distr.core.audio.tts_handler import wav_to_mp3

                provider, voice = self._active_voice()
                chunks = split_export_text(text_to_save)
                generated_wavs = []
                for index, chunk in enumerate(chunks, start=1):
                    logger.info(
                        "Save audio: Generating chunk %d/%d (%d characters)",
                        index,
                        len(chunks),
                        len(chunk),
                    )
                    generated_wav = generate_export_chunk(
                        chunk,
                        provider=provider,
                        voice=voice,
                    )
                    generated_wavs.append(generated_wav)
                
                # Step 4: Save audio in the requested format and folder.
                output_format = str(audio_format or "mp3").strip().lower()
                if "wav" in text_lower:
                    output_format = "wav"
                if output_format not in {"mp3", "wav"}:
                    return "Error: Audio format must be MP3 or WAV."
                resolved_destination = "desktop" if "desktop" in text_lower else destination
                output_dir = get_output_path(resolved_destination)
                os.makedirs(output_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = f"clipboard_speech_{timestamp}"
                output_path = os.path.join(output_dir, f"{stem}.{output_format}")
                source_wav = generated_wavs[0]
                combined_wav = None
                if len(generated_wavs) > 1:
                    from pydub import AudioSegment

                    combined = AudioSegment.empty()
                    pause = AudioSegment.silent(duration=120)
                    for index, wav_path in enumerate(generated_wavs):
                        if index:
                            combined += pause
                        combined += AudioSegment.from_wav(wav_path)
                    combined_wav = os.path.join(output_dir, f".{stem}.wav")
                    combined.export(combined_wav, format="wav")
                    source_wav = combined_wav
                if output_format == "mp3":
                    wav_to_mp3(source_wav, output_path)
                else:
                    shutil.copyfile(source_wav, output_path)
                if combined_wav:
                    try:
                        os.remove(combined_wav)
                    except OSError:
                        logger.debug("Could not remove combined temporary WAV: %s", combined_wav)

                logger.info("Save audio: Saved audio file to %s", output_path)
                return f"Successfully saved audio to {output_path}"
                
            except Exception as e:
                logger.error(f"Error generating audio: {e}", exc_info=True)
                return f"Error generating audio: {str(e)}"
            
        except Exception as e:
            logger.error(f"Error in SaveAudioTool: {e}", exc_info=True)
            return f"Error executing save audio: {str(e)}"
    
    async def _arun(
        self,
        text: str = "",
        audio_format: str = "mp3",
        destination: str = "downloads",
        **kwargs,
    ) -> str:
        # Filter out any unexpected arguments
        return self._run(
            text=text,
            audio_format=audio_format,
            destination=destination,
        )
