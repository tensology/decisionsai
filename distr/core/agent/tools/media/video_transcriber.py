"""
Video to Text Transcription Tool

This tool extracts audio from video files and transcribes them to text with:
- Preflight checks for available backends
- Intermediate artifact preservation (WAV files)
- Deterministic output paths
- Fallback chain of transcription backends
- Multiple output formats (txt, md, srt, vtt)
- Better error messages
"""

import logging
import os
import json
import subprocess
import platform
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ==================== Backend Interface ====================

class TranscriptionBackend:
    """Base interface for transcription backends"""
    
    def is_available(self) -> Tuple[bool, str]:
        """Check if backend is available. Returns (available, reason)"""
        raise NotImplementedError
    
    def transcribe(self, audio_path: str, output_path: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Transcribe audio file. Returns (transcript_text, metadata_dict)"""
        raise NotImplementedError
    
    def get_name(self) -> str:
        """Get backend name"""
        raise NotImplementedError


class AssemblyAIBackend(TranscriptionBackend):
    """AssemblyAI transcription backend"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
    
    def get_name(self) -> str:
        return "AssemblyAI"
    
    def is_available(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "API key not configured"
        
        try:
            import assemblyai as aai
            # Test API key by making a simple request
            aai.settings.api_key = self.api_key
            # Just check if we can create a transcriber (doesn't make API call)
            transcriber = aai.Transcriber()
            return True, "API key configured and service available"
        except ImportError:
            return False, "assemblyai package not installed (pip install assemblyai)"
        except Exception as e:
            return False, f"Service check failed: {str(e)}"
    
    def transcribe(self, audio_path: str, output_path: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        try:
            import assemblyai as aai
            
            aai.settings.api_key = self.api_key
            transcriber = aai.Transcriber()
            
            config = aai.TranscriptionConfig(
                speaker_labels=options.get('speaker_labels', True) if options else True,
                language_detection=options.get('language_detection', True) if options else True
            )
            
            logger.info(f"AssemblyAI: Uploading {audio_path}...")
            transcript = transcriber.transcribe(audio_path, config=config)
            
            logger.info("AssemblyAI: Waiting for transcription to complete...")
            transcript.wait_for_completion()
            
            if transcript.status == aai.TranscriptStatus.error:
                error_msg = f"AssemblyAI transcription failed: {transcript.error}"
                logger.error(error_msg)
                return None, {'error': error_msg}
            
            # Format with speaker labels if available
            transcript_text = []
            metadata = {
                'backend': 'AssemblyAI',
                'status': 'success',
                'has_speaker_labels': bool(transcript.utterances),
                'language': getattr(transcript, 'language_code', None)
            }
            
            if transcript.utterances:
                for utterance in transcript.utterances:
                    speaker = f"Speaker {utterance.speaker}" if utterance.speaker else "Speaker"
                    transcript_text.append(f"{speaker}: {utterance.text}")
                result = "\n".join(transcript_text)
            else:
                result = transcript.text
            
            logger.info(f"AssemblyAI: Transcription completed ({len(result)} chars)")
            return result, metadata
            
        except Exception as e:
            error_msg = f"AssemblyAI transcription error: {e}"
            logger.error(error_msg, exc_info=True)
            return None, {'error': error_msg}


class WhisperCppBackend(TranscriptionBackend):
    """Whisper.cpp local backend"""
    
    def __init__(self, model: str = "base.en"):
        self.model = model
    
    def get_name(self) -> str:
        return f"Whisper.cpp ({self.model})"
    
    def is_available(self) -> Tuple[bool, str]:
        try:
            from distr.core.agent.libs import pwc, WHISPER_AVAILABLE
            if not WHISPER_AVAILABLE:
                return False, "pywhispercpp not available (install whisper.cpp and pywhispercpp)"
            
            # Try to load the model to verify it exists
            try:
                whisper_model = pwc.Model(self.model, print_progress=False)
                return True, f"Model '{self.model}' available"
            except Exception as e:
                return False, f"Model '{self.model}' not found or cannot be loaded: {str(e)}"
        except ImportError:
            return False, "pywhispercpp not installed (pip install pywhispercpp)"
        except Exception as e:
            return False, f"Whisper.cpp check failed: {str(e)}"
    
    def transcribe(self, audio_path: str, output_path: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        try:
            from distr.core.agent.libs import pwc, WHISPER_AVAILABLE
            
            if not WHISPER_AVAILABLE:
                return None, {'error': 'pywhispercpp not available'}
            
            logger.info(f"Whisper.cpp: Transcribing {audio_path} with model {self.model}...")
            whisper_model = pwc.Model(self.model, print_progress=False)
            result = whisper_model.transcribe(audio_path, print_progress=False)
            
            # Extract text from result
            if isinstance(result, list):
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
            
            metadata = {
                'backend': 'Whisper.cpp',
                'model': self.model,
                'status': 'success'
            }
            
            logger.info(f"Whisper.cpp: Transcription completed ({len(transcript)} chars)")
            return transcript.strip(), metadata
            
        except Exception as e:
            error_msg = f"Whisper.cpp transcription error: {e}"
            logger.error(error_msg, exc_info=True)
            return None, {'error': error_msg}


class OpenAIWhisperBackend(TranscriptionBackend):
    """OpenAI Whisper API backend"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
    
    def get_name(self) -> str:
        return "OpenAI Whisper API"
    
    def is_available(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "API key not configured"
        
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            # Just verify we can create a client (doesn't make API call)
            return True, "API key configured and client available"
        except ImportError:
            return False, "openai package not installed (pip install openai)"
        except Exception as e:
            return False, f"OpenAI client check failed: {str(e)}"
    
    def transcribe(self, audio_path: str, output_path: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        try:
            from openai import OpenAI
            import wave
            import io
            import numpy as np
            
            client = OpenAI(api_key=self.api_key)
            
            # Read audio file and convert to format OpenAI expects
            # OpenAI accepts: mp3, mp4, mpeg, mpga, m4a, wav, webm
            logger.info(f"OpenAI Whisper: Transcribing {audio_path}...")
            
            with open(audio_path, 'rb') as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=options.get('language', 'en') if options else 'en'
                )
            
            metadata = {
                'backend': 'OpenAI Whisper API',
                'model': 'whisper-1',
                'status': 'success'
            }
            
            logger.info(f"OpenAI Whisper: Transcription completed ({len(transcript.text)} chars)")
            return transcript.text, metadata
            
        except Exception as e:
            error_msg = f"OpenAI Whisper API error: {e}"
            logger.error(error_msg, exc_info=True)
            return None, {'error': error_msg}


# ==================== Audio Extraction ====================

def extract_audio_from_video(video_path: str, output_audio_path: Optional[str] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Extract audio from video file using ffmpeg.
    
    Returns:
        (audio_path, metadata_dict) or (None, error_dict)
    """
    if output_audio_path is None:
        video_path_obj = Path(video_path)
        output_audio_path = str(video_path_obj.with_suffix('.audio.wav'))
    
    try:
        # Check if ffmpeg is available
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            timeout=5
        )
        if result.returncode != 0:
            return None, {'error': 'ffmpeg not found or not working', 'command': 'ffmpeg -version'}
        
        # Extract audio: mono, 16kHz, 16-bit PCM WAV
        cmd = [
            'ffmpeg', '-y',  # Overwrite output
            '-i', video_path,
            '-ac', '1',  # Mono
            '-ar', '16000',  # 16kHz sample rate
            '-acodec', 'pcm_s16le',  # 16-bit PCM
            output_audio_path
        ]
        
        logger.info(f"Extracting audio: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            error_msg = f"ffmpeg extraction failed: {result.stderr[-500:] if result.stderr else 'Unknown error'}"
            logger.error(error_msg)
            return None, {
                'error': error_msg,
                'command': ' '.join(cmd),
                'stderr': result.stderr[-500:] if result.stderr else None
            }
        
        if not os.path.exists(output_audio_path):
            return None, {'error': 'Audio extraction completed but output file not found'}
        
        # Get audio metadata
        metadata = {
            'input_video': video_path,
            'output_audio': output_audio_path,
            'extraction_success': True
        }
        
        # Try to get duration and sample rate
        try:
            probe_cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration:stream=sample_rate',
                '-of', 'json',
                output_audio_path
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            if probe_result.returncode == 0:
                probe_data = json.loads(probe_result.stdout)
                if 'format' in probe_data:
                    metadata['duration'] = float(probe_data['format'].get('duration', 0))
                if 'streams' in probe_data and probe_data['streams']:
                    metadata['sample_rate'] = int(probe_data['streams'][0].get('sample_rate', 16000))
        except Exception as e:
            logger.warning(f"Could not get audio metadata: {e}")
        
        logger.info(f"Audio extracted successfully: {output_audio_path}")
        return output_audio_path, metadata
        
    except FileNotFoundError:
        return None, {
            'error': 'ffmpeg not found. Install ffmpeg: brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)',
            'hint': 'Install ffmpeg and ensure it is on PATH'
        }
    except subprocess.TimeoutExpired:
        return None, {'error': 'Audio extraction timed out after 5 minutes'}
    except Exception as e:
        error_msg = f"Audio extraction error: {e}"
        logger.error(error_msg, exc_info=True)
        return None, {'error': error_msg}


# ==================== Output Formatting ====================

def format_transcript(transcript: str, format_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """Format transcript in different output formats."""
    if format_type == 'txt':
        return transcript
    elif format_type == 'md':
        # Markdown format with optional metadata header
        md = ""
        if metadata:
            md += "---\n"
            md += f"Transcription Metadata\n"
            md += f"Backend: {metadata.get('backend', 'Unknown')}\n"
            if 'model' in metadata:
                md += f"Model: {metadata.get('model')}\n"
            if 'duration' in metadata:
                md += f"Duration: {metadata.get('duration', 0):.2f}s\n"
            md += "---\n\n"
        md += transcript
        return md
    elif format_type == 'srt':
        # Simple SRT format (basic, no timestamps from backend)
        lines = transcript.split('\n')
        srt = ""
        for i, line in enumerate(lines, 1):
            if line.strip():
                srt += f"{i}\n"
                srt += f"00:00:00,000 --> 00:00:01,000\n"  # Placeholder timestamps
                srt += f"{line.strip()}\n\n"
        return srt
    elif format_type == 'vtt':
        # WebVTT format
        vtt = "WEBVTT\n\n"
        lines = transcript.split('\n')
        for i, line in enumerate(lines):
            if line.strip():
                vtt += f"00:00:00.000 --> 00:00:01.000\n"  # Placeholder timestamps
                vtt += f"{line.strip()}\n\n"
        return vtt
    else:
        return transcript


# ==================== Preflight Checks ====================

def _agent_models_dir() -> str:
    """Same models directory as AgentSession (vosk / whisper weights)."""
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
    return os.path.join(_root, 'distr', 'core', 'agent', 'models')


def _vosk_transcription_preflight() -> Tuple[bool, str]:
    try:
        from distr.core.agent.constants import DEFAULT_VOSK_MODEL_DIR
        import vosk  # noqa: F401
    except ImportError:
        return False, 'vosk package not installed'
    path = os.path.join(_agent_models_dir(), DEFAULT_VOSK_MODEL_DIR)
    if not os.path.isdir(path):
        return False, f'model not found at {path} (run python bin/setup_vosk.py)'
    return True, f"model '{DEFAULT_VOSK_MODEL_DIR}' present"


def _vibevoice_asr_preflight() -> Tuple[bool, str]:
    try:
        from distr.core.agent.services.tts.vibevoice_runtime import vibevoice_asr_runtime_ready
    except Exception as e:
        return False, f'runtime check import error: {e}'
    if vibevoice_asr_runtime_ready():
        return True, 'vibevoice package importable'
    return (
        False,
        'vibevoice not installed or not importable — run ./scripts/install_vibevoice.sh in your venv',
    )


def check_transcription_backends(assemblyai_key: Optional[str] = None, openai_key: Optional[str] = None) -> Dict[str, Any]:
    """Check which transcription backends are available.
    
    Returns:
        Dict with backend availability and status
    """
    backends = []
    
    # Check AssemblyAI
    assemblyai_backend = AssemblyAIBackend(api_key=assemblyai_key)
    available, reason = assemblyai_backend.is_available()
    backends.append({
        'name': 'AssemblyAI',
        'available': available,
        'reason': reason
    })
    
    # Check Whisper.cpp
    whisper_backend = WhisperCppBackend()
    available, reason = whisper_backend.is_available()
    backends.append({
        'name': 'Whisper.cpp',
        'available': available,
        'reason': reason
    })
    
    # Check OpenAI Whisper
    openai_backend = OpenAIWhisperBackend(api_key=openai_key)
    available, reason = openai_backend.is_available()
    backends.append({
        'name': 'OpenAI Whisper API',
        'available': available,
        'reason': reason
    })

    # Vosk (local STT — same tree as live agent fallback)
    vosk_available, vosk_reason = _vosk_transcription_preflight()
    backends.append({
        'name': 'Vosk (local)',
        'available': vosk_available,
        'reason': vosk_reason,
    })

    # VibeVoice ASR (local — Settings → LLMs; requires separate install)
    vv_available, vv_reason = _vibevoice_asr_preflight()
    backends.append({
        'name': 'VibeVoice ASR (local)',
        'available': vv_available,
        'reason': vv_reason,
    })
    
    # Check ffmpeg
    ffmpeg_available = False
    ffmpeg_reason = "Not checked"
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        ffmpeg_available = result.returncode == 0
        ffmpeg_reason = "Available" if ffmpeg_available else "Not found or not working"
    except Exception:
        ffmpeg_reason = "Not found. Install: brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)"
    
    available_backends = [b for b in backends if b['available']]
    
    return {
        'backends': backends,
        'available_backends': available_backends,
        'ffmpeg_available': ffmpeg_available,
        'ffmpeg_reason': ffmpeg_reason,
        'has_any_backend': len(available_backends) > 0
    }


# ==================== Main Tool ====================

class VideoTranscriberInput(BaseModel):
    """Input schema for video transcriber tool."""
    video_file_path: str = Field(description="Path to video file (.mp4, .mov, .avi, etc.)")
    output_format: str = Field(default="txt", description="Output format: txt, md, srt, or vtt")
    keep_audio: bool = Field(default=True, description="Keep extracted audio file even if transcription succeeds")
    backend_priority: Optional[str] = Field(default=None, description="Preferred backend: 'assemblyai', 'whispercpp', or 'openai'. If None, uses automatic fallback chain.")


class VideoTranscriberTool(BaseTool):
    """Tool for transcribing video files to text."""
    
    name: str = "video_transcriber"
    description: str = (
        "Transcribe video files (.mp4, .mov, .avi, etc.) to text by extracting audio and transcribing it.\n"
        "Features:\n"
        "- Extracts audio from video using ffmpeg\n"
        "- Supports multiple transcription backends (AssemblyAI, Whisper.cpp, OpenAI Whisper)\n"
        "- Automatic fallback chain if preferred backend fails\n"
        "- Preserves intermediate audio files for debugging\n"
        "- Supports multiple output formats (txt, md, srt, vtt)\n"
        "- Deterministic output paths (video.mp4 -> video.transcript.txt)\n"
        "\n"
        "Use this when user says:\n"
        "- 'Transcribe this video'\n"
        "- 'Extract text from video'\n"
        "- 'Convert video to text'\n"
    )
    args_schema: type[BaseModel] = VideoTranscriberInput
    
    def _run(self, video_file_path: str, output_format: str = "txt", keep_audio: bool = True, backend_priority: Optional[str] = None, **kwargs) -> str:
        """Transcribe video file to text."""
        try:
            # Validate input file
            if not os.path.exists(video_file_path):
                return f"Error: Video file not found: {video_file_path}"
            
            if not os.path.isfile(video_file_path):
                return f"Error: Path is not a file: {video_file_path}"
            
            video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
            file_ext = Path(video_file_path).suffix.lower()
            if file_ext not in video_extensions:
                return f"Error: File is not a supported video format. Supported: {', '.join(video_extensions)}"
            
            # Load settings for API keys
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            assemblyai_key = settings.get('assemblyai_key', '') if settings.get('assemblyai_enabled', False) else None
            openai_key = None
            if settings and settings.get('openai_enabled'):
                k = (settings.get('openai_key') or '').strip()
                openai_key = k or None
            
            # Preflight check: verify at least one backend is available
            logger.info("Running preflight checks...")
            preflight = check_transcription_backends(assemblyai_key=assemblyai_key, openai_key=openai_key)
            
            if not preflight['ffmpeg_available']:
                return f"Error: ffmpeg not available. {preflight['ffmpeg_reason']}"
            
            if not preflight['has_any_backend']:
                backends_status = "\n".join([f"- {b['name']}: {b['reason']}" for b in preflight['backends']])
                return f"Error: No transcription backend available.\n\nBackend status:\n{backends_status}\n\nPlease install at least one backend:\n- AssemblyAI: pip install assemblyai and configure API key\n- Whisper.cpp: Install whisper.cpp and pywhispercpp\n- OpenAI Whisper: pip install openai and configure API key"
            
            logger.info(f"Preflight check passed. Available backends: {[b['name'] for b in preflight['available_backends']]}")
            
            # Step 1: Extract audio
            logger.info(f"Extracting audio from {video_file_path}...")
            audio_path, audio_metadata = extract_audio_from_video(video_file_path)
            
            if not audio_path:
                error_info = audio_metadata.get('error', 'Unknown error')
                command = audio_metadata.get('command', 'N/A')
                stderr = audio_metadata.get('stderr', '')
                return f"Error: Failed to extract audio from video.\n\nError: {error_info}\nCommand: {command}\n\nHint: Ensure ffmpeg is installed and the video file is not corrupted."
            
            logger.info(f"Audio extracted: {audio_path}")
            
            # Step 2: Build backend chain based on priority
            backends: List[TranscriptionBackend] = []
            
            if backend_priority == 'assemblyai':
                backends.append(AssemblyAIBackend(api_key=assemblyai_key))
                backends.append(WhisperCppBackend())
                backends.append(OpenAIWhisperBackend(api_key=openai_key))
            elif backend_priority == 'whispercpp':
                backends.append(WhisperCppBackend())
                backends.append(AssemblyAIBackend(api_key=assemblyai_key))
                backends.append(OpenAIWhisperBackend(api_key=openai_key))
            elif backend_priority == 'openai':
                backends.append(OpenAIWhisperBackend(api_key=openai_key))
                backends.append(AssemblyAIBackend(api_key=assemblyai_key))
                backends.append(WhisperCppBackend())
            else:
                # Default priority: AssemblyAI > Whisper.cpp > OpenAI
                backends.append(AssemblyAIBackend(api_key=assemblyai_key))
                backends.append(WhisperCppBackend())
                backends.append(OpenAIWhisperBackend(api_key=openai_key))
            
            # Step 3: Try transcription with fallback chain
            transcript = None
            transcript_metadata = None
            used_backend = None
            
            for backend in backends:
                available, reason = backend.is_available()
                if not available:
                    logger.info(f"Skipping {backend.get_name()}: {reason}")
                    continue
                
                logger.info(f"Attempting transcription with {backend.get_name()}...")
                transcript, transcript_metadata = backend.transcribe(audio_path)
                
                if transcript:
                    used_backend = backend.get_name()
                    logger.info(f"Successfully transcribed with {used_backend}")
                    break
                else:
                    error = transcript_metadata.get('error', 'Unknown error') if transcript_metadata else 'Unknown error'
                    logger.warning(f"{backend.get_name()} failed: {error}")
            
            if not transcript:
                # Transcription failed - keep audio file
                return f"Error: Transcription failed with all available backends.\n\nExtracted audio saved to: {audio_path}\n\nYou can:\n- Retry transcription later\n- Use the audio file with another tool"
            
            # Step 4: Format and save transcript
            video_path_obj = Path(video_file_path)
            output_file = video_path_obj.with_suffix(f'.transcript.{output_format}')
            
            formatted_transcript = format_transcript(transcript, output_format, transcript_metadata)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(formatted_transcript)
            
            logger.info(f"Transcript saved to: {output_file}")
            
            # Step 5: Clean up audio file if requested
            if not keep_audio and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                    logger.info(f"Removed intermediate audio file: {audio_path}")
                except Exception as e:
                    logger.warning(f"Could not remove audio file: {e}")
            
            return f"✅ Transcription complete!\n\nVideo: {os.path.basename(video_file_path)}\nBackend: {used_backend}\nTranscript: {os.path.basename(output_file)}\n\nTranscript preview ({len(transcript)} chars):\n{transcript[:200]}{'...' if len(transcript) > 200 else ''}"
            
        except Exception as e:
            logger.error(f"Video transcription error: {e}", exc_info=True)
            return f"Error: {str(e)}"
    
    async def _arun(self, video_file_path: str, output_format: str = "txt", keep_audio: bool = True, backend_priority: Optional[str] = None, **kwargs) -> str:
        """Async version of _run."""
        # Filter out any unexpected arguments (like 'last_user_message' from LLM service)
        return self._run(video_file_path, output_format, keep_audio, backend_priority)

