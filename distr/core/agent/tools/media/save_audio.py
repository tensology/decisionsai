"""
Save Audio Tool for LangChain.

This tool saves clipboard content as an audio file using TTS.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import time
import os
import platform
import subprocess
from datetime import datetime
import numpy as np
from distr.core.agent.tools.base import get_platform_modifier_key
from distr.core.agent.libs import (
    pyautogui, PYAUTOGUI_AVAILABLE,
    sf, SOUNDFILE_AVAILABLE,
    wavfile, SCIPY_AVAILABLE
)

logger = logging.getLogger(__name__)


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


def get_desktop_path():
    """Get the desktop path for the current user."""
    home = os.path.expanduser("~")
    return os.path.join(home, "Desktop")


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
    - Saves audio file to Desktop
    
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
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute save audio action."""
        try:
            # Validate that this is actually a save audio command
            text_lower = text.lower() if text else ""
            has_save = "save" in text_lower
            has_audio = "audio" in text_lower or "sound" in text_lower
            
            # Only proceed if both "save" and "audio" are present (explicit command)
            if not (has_save and has_audio):
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
            
            # Step 3: Generate audio using TTS
            if not self._tts_service:
                return "Error: TTS service not available"
            
            try:
                # Use Kokoro TTS to generate audio
                # The TTS service has a kokoro instance that can create audio
                text_to_save = clipboard_text.strip()
                
                # Generate audio synchronously
                logger.info(f"Save audio: Generating audio for {len(text_to_save)} characters")
                # Normalize smart quotes for correct pronunciation
                from distr.core.agent.services.tts.kokoro import _normalize_text_for_tts
                text_to_save = _normalize_text_for_tts(text_to_save)
                audio, sample_rate = self._tts_service.kokoro.create(
                    text_to_save, 
                    voice=self._tts_service.voice, 
                    speed=1.0
                )
                
                if audio is None or len(audio) == 0:
                    return "Error: Failed to generate audio"
                
                # Apply Kanade voice conversion for custom voices
                if getattr(self._tts_service, '_voice_cloning_enabled', False):
                    ref_path = getattr(self._tts_service, '_reference_voice_path', None)
                    if ref_path:
                        try:
                            from distr.core.audio.voice_cloner import convert_voice, get_output_sample_rate
                            audio = convert_voice(audio, sample_rate, ref_path)
                            sample_rate = get_output_sample_rate()
                        except Exception as vc_err:
                            logger.error(f"Voice cloning failed in save_audio, using base voice: {vc_err}")
                
                # Step 4: Save audio to desktop
                desktop_path = get_desktop_path()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"audio_{timestamp}.wav"
                filepath = os.path.join(desktop_path, filename)
                
                # Convert audio to int16 and save as WAV
                audio_int16 = (audio * 32767).astype(np.int16)
                
                # Use scipy.io.wavfile to save WAV file
                if SCIPY_AVAILABLE:
                    wavfile.write(filepath, sample_rate, audio_int16)
                    logger.info(f"Save audio: Saved audio file to {filepath}")
                    return f"Successfully saved audio to {filename} on Desktop"
                elif SOUNDFILE_AVAILABLE:
                    # Fallback: use soundfile if scipy not available
                    sf.write(filepath, audio_int16, sample_rate)
                    logger.info(f"Save audio: Saved audio file to {filepath}")
                    return f"Successfully saved audio to {filename} on Desktop"
                else:
                    return "Error: Neither scipy nor soundfile is available. Please install one: pip install scipy or pip install soundfile"
                
            except Exception as e:
                logger.error(f"Error generating audio: {e}", exc_info=True)
                return f"Error generating audio: {str(e)}"
            
        except Exception as e:
            logger.error(f"Error in SaveAudioTool: {e}", exc_info=True)
            return f"Error executing save audio: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments
        return self._run(text=text)

