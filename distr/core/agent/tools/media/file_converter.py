"""
File Converter Tool for LangChain.

This tool converts files between different formats:
- Audio files: flac, mp3, wav, m4a, ogg, etc.
- Video files: extract audio or convert to audio formats
- Audio/Video to text: transcribe to text file (without JSON)
- Image files: convert between image formats (webp, jpg, png, gif, bmp, tiff, etc.)
"""

from typing import Optional, Any, Tuple
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
import os
import subprocess
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt6.QtWidgets import QProgressDialog, QApplication
from PyQt6.QtCore import QThread, pyqtSignal, Qt

logger = logging.getLogger(__name__)


class FileConverterInput(BaseModel):
    """Input schema for file_converter tool."""
    file_path: Optional[str] = Field(default=None, description="Path to the file to convert. If None, will look for recently dropped files.")
    target_format: str = Field(description="Target format: 'mp3', 'wav', 'flac', 'm4a', 'ogg', 'text', 'webp', 'jpg', 'png', etc. For video files, use audio formats to extract audio. For images, use image formats like 'webp', 'jpg', 'png', 'gif', 'bmp', 'tiff'.")
    convert_all: bool = Field(default=False, description="If True, convert all recently dropped files. If False, only convert the first one.")


def _check_ffmpeg() -> bool:
    """Check if ffmpeg is available."""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        return False


def _convert_audio_file(input_path: str, output_path: str, target_format: str) -> Tuple[bool, str]:
    """Convert audio file to target format using ffmpeg.
    
    Returns:
        (success, error_message)
    """
    try:
        # Map format to ffmpeg codec
        format_codecs = {
            'mp3': 'libmp3lame',
            'wav': 'pcm_s16le',
            'flac': 'flac',
            'm4a': 'aac',
            'aac': 'aac',
            'ogg': 'libvorbis',
            'opus': 'libopus',
            'wma': 'wmav2'
        }
        
        codec = format_codecs.get(target_format.lower())
        if not codec:
            return False, f"Unsupported audio format: {target_format}"
        
        # Build ffmpeg command
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-codec:a', codec,
            '-y',  # Overwrite output file
            output_path
        ]
        
        # Add quality settings for lossy formats
        if target_format.lower() in ['mp3', 'm4a', 'aac', 'ogg', 'opus']:
            if target_format.lower() == 'mp3':
                cmd.insert(-1, '-q:a')
                cmd.insert(-1, '2')  # High quality
            elif target_format.lower() in ['m4a', 'aac']:
                cmd.insert(-1, '-b:a')
                cmd.insert(-1, '192k')
            elif target_format.lower() in ['ogg', 'opus']:
                cmd.insert(-1, '-q:a')
                cmd.insert(-1, '5')  # High quality
        
        logger.info(f"FileConverter: Converting {input_path} to {output_path} using ffmpeg")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown ffmpeg error"
            logger.error(f"FileConverter: ffmpeg conversion failed: {error_msg}")
            return False, f"Conversion failed: {error_msg[:200]}"
        
        if not os.path.exists(output_path):
            return False, "Conversion completed but output file not found"
        
        logger.info(f"FileConverter: Successfully converted {input_path} to {output_path}")
        return True, ""
        
    except subprocess.TimeoutExpired:
        return False, "Conversion timed out after 5 minutes"
    except Exception as e:
        logger.error(f"FileConverter: Error converting audio file: {e}", exc_info=True)
        return False, f"Conversion error: {str(e)}"


def _extract_audio_from_video(video_path: str, output_path: str, target_format: str) -> Tuple[bool, str]:
    """Extract audio from video file and convert to target format.
    
    Returns:
        (success, error_message)
    """
    try:
        # Map format to ffmpeg codec
        format_codecs = {
            'mp3': 'libmp3lame',
            'wav': 'pcm_s16le',
            'flac': 'flac',
            'm4a': 'aac',
            'aac': 'aac',
            'ogg': 'libvorbis',
            'opus': 'libopus'
        }
        
        codec = format_codecs.get(target_format.lower())
        if not codec:
            return False, f"Unsupported audio format: {target_format}"
        
        # Build ffmpeg command to extract audio
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vn',  # No video
            '-codec:a', codec,
            '-y',  # Overwrite output file
            output_path
        ]
        
        # Add quality settings for lossy formats
        if target_format.lower() in ['mp3', 'm4a', 'aac', 'ogg', 'opus']:
            if target_format.lower() == 'mp3':
                cmd.insert(-1, '-q:a')
                cmd.insert(-1, '2')  # High quality
            elif target_format.lower() in ['m4a', 'aac']:
                cmd.insert(-1, '-b:a')
                cmd.insert(-1, '192k')
            elif target_format.lower() in ['ogg', 'opus']:
                cmd.insert(-1, '-q:a')
                cmd.insert(-1, '5')  # High quality
        
        logger.info(f"FileConverter: Extracting audio from {video_path} to {output_path}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown ffmpeg error"
            logger.error(f"FileConverter: ffmpeg extraction failed: {error_msg}")
            return False, f"Audio extraction failed: {error_msg[:200]}"
        
        if not os.path.exists(output_path):
            return False, "Extraction completed but output file not found"
        
        logger.info(f"FileConverter: Successfully extracted audio from {video_path} to {output_path}")
        return True, ""
        
    except subprocess.TimeoutExpired:
        return False, "Extraction timed out after 5 minutes"
    except Exception as e:
        logger.error(f"FileConverter: Error extracting audio from video: {e}", exc_info=True)
        return False, f"Extraction error: {str(e)}"


def _convert_image_file(input_path: str, output_path: str, target_format: str) -> Tuple[bool, str]:
    """Convert image file to target format using PIL/Pillow.
    
    PIL only supports raster formats. SVG and other vector formats are skipped.
    
    Returns:
        (success, error_message)
    """
    try:
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError:
            return False, "PIL/Pillow is not installed. Please install it with: pip install pillow"
        ext = os.path.splitext(input_path)[1].lower()
        if ext == '.svg':
            return False, "SVG is not supported by PIL (raster-only). Use PNG/JPEG or convert SVG externally."
        # Map format to PIL format name
        format_map = {
            'webp': 'WEBP',
            'jpg': 'JPEG',
            'jpeg': 'JPEG',
            'png': 'PNG',
            'gif': 'GIF',
            'bmp': 'BMP',
            'tiff': 'TIFF',
            'tif': 'TIFF'
        }
        pil_format = format_map.get(target_format.lower())
        if not pil_format:
            return False, f"Unsupported image format: {target_format}"
        try:
            img = Image.open(input_path)
        except UnidentifiedImageError:
            logger.debug("FileConverter: Unsupported or invalid image (e.g. SVG or wrong extension): %s", input_path)
            return False, "Unsupported or invalid image format (PIL could not identify the file)."
        
        # Convert RGBA to RGB for formats that don't support transparency (JPEG, BMP)
        if pil_format in ('JPEG', 'BMP') and img.mode in ('RGBA', 'LA', 'P'):
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode == 'RGBA':
                rgb_img.paste(img, mask=img.split()[-1])
            else:
                rgb_img.paste(img)
            img = rgb_img
        elif pil_format == 'PNG' and img.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P'):
            # Ensure PNG-compatible mode
            if img.mode == 'P':
                img = img.convert('RGBA')
            else:
                img = img.convert('RGB')
        elif pil_format == 'WEBP':
            # WebP supports transparency, but convert P mode to RGBA
            if img.mode == 'P':
                img = img.convert('RGBA')
            elif img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')
        
        # Save with appropriate quality settings
        save_kwargs = {}
        if pil_format == 'WEBP':
            save_kwargs = {'quality': 80, 'method': 6}  # Good compression
        elif pil_format == 'JPEG':
            save_kwargs = {'quality': 85, 'optimize': True}
        elif pil_format == 'PNG':
            save_kwargs = {'optimize': True}
        
        logger.info(f"FileConverter: Converting image {input_path} to {output_path} (format: {pil_format})")
        img.save(output_path, pil_format, **save_kwargs)
        
        if not os.path.exists(output_path):
            return False, "Conversion completed but output file not found"
        
        # Log file size comparison
        try:
            original_size = os.path.getsize(input_path)
            new_size = os.path.getsize(output_path)
            compression_ratio = (1 - (new_size / original_size)) * 100 if original_size > 0 else 0
            
            if original_size < 1024:
                orig_str = f"{original_size} B"
            elif original_size < 1024 * 1024:
                orig_str = f"{original_size / 1024:.1f} KB"
            else:
                orig_str = f"{original_size / (1024 * 1024):.1f} MB"
            
            if new_size < 1024:
                new_str = f"{new_size} B"
            elif new_size < 1024 * 1024:
                new_str = f"{new_size / 1024:.1f} KB"
            else:
                new_str = f"{new_size / (1024 * 1024):.1f} MB"
            
            logger.info(f"FileConverter: Image conversion complete: {orig_str} → {new_str} ({compression_ratio:.1f}% {'smaller' if compression_ratio > 0 else 'larger'})")
        except Exception as e:
            logger.warning(f"FileConverter: Could not calculate file size comparison: {e}")
        
        logger.info(f"FileConverter: Successfully converted image {input_path} to {output_path}")
        return True, ""
        
    except Exception as e:
        logger.error(f"FileConverter: Error converting image file: {e}", exc_info=True)
        return False, f"Image conversion error: {str(e)}"


def _transcribe_to_text(input_path: str, output_path: str, chat_manager=None, chat_id=None) -> Tuple[bool, str]:
    """Transcribe audio/video file to text (without JSON).
    
    Returns:
        (success, error_message)
    """
    try:
        from distr.core.agent.tools.media.audio_transcriber import (
            _transcribe_with_assemblyai,
            _transcribe_with_whispercpp
        )
        
        # Get settings for API keys
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        assemblyai_key = settings.get('assemblyai_key', '') if settings.get('assemblyai_enabled', False) else None
        whisper_model = "base.en"  # Default Whisper model
        
        transcript = None
        
        # Try AssemblyAI first if available
        if assemblyai_key:
            logger.info("FileConverter: Attempting AssemblyAI transcription...")
            transcript = _transcribe_with_assemblyai(input_path, assemblyai_key)
        
        # Fallback to Whisper.cpp if AssemblyAI failed or not available
        if not transcript:
            logger.info("FileConverter: Falling back to Whisper.cpp...")
            transcript = _transcribe_with_whispercpp(input_path, whisper_model)
        
        if transcript:
            # Write transcript to file (text only, no JSON)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(transcript)
            logger.info(f"FileConverter: Transcript saved to {output_path}")
            
            # Notify user via TTS
            try:
                from distr.core.signals import speak_text_directly_event_queue
                notification = f"Transcription complete. Saved to {os.path.basename(output_path)}"
                speak_text_directly_event_queue(notification)
            except Exception as e:
                logger.warning(f"FileConverter: Could not send TTS notification: {e}")
            
            # Optionally add message to chat
            if chat_manager and chat_id:
                try:
                    summary = f"✅ Transcription complete!\n\nSaved to: `{output_path}`\n\nTranscript preview ({len(transcript)} chars):\n{transcript[:300]}{'...' if len(transcript) > 300 else ''}"
                    chat_manager.add_assistant_message(chat_id, summary)
                    from distr.core.signals import signal_manager
                    signal_manager.chat_message_added.emit(chat_id, "assistant", summary)
                    signal_manager.chat_updated.emit(chat_id)
                except Exception as e:
                    logger.warning(f"FileConverter: Could not add message to chat: {e}")
            
            return True, ""
        else:
            error_msg = "Failed to transcribe file with both AssemblyAI and Whisper.cpp"
            logger.error(f"FileConverter: {error_msg}")
            return False, error_msg
            
    except Exception as e:
        logger.error(f"FileConverter: Error transcribing file: {e}", exc_info=True)
        return False, f"Transcription error: {str(e)}"


def _convert_worker_thread(file_path: str, output_path: str, target_format: str, is_video: bool, is_image: bool = False, progress_callback=None, chat_manager=None, chat_id=None):
    """Worker function to run conversion in background thread and notify when done.
    
    Args:
        progress_callback: Optional callback function(file_path, status) to update progress
        is_image: Whether the file is an image file
    """
    try:
        success = False
        error_msg = ""
        
        if progress_callback:
            progress_callback(file_path, "Converting...")
        
        if target_format.lower() == 'text':
            # Transcribe to text
            success, error_msg = _transcribe_to_text(file_path, output_path, chat_manager, chat_id)
        elif is_image:
            # Convert image file
            success, error_msg = _convert_image_file(file_path, output_path, target_format)
        elif is_video:
            # Extract audio from video
            success, error_msg = _extract_audio_from_video(file_path, output_path, target_format)
        else:
            # Convert audio file
            success, error_msg = _convert_audio_file(file_path, output_path, target_format)
        
        if success:
            if progress_callback:
                progress_callback(file_path, "Complete")
            
            # Notify user via TTS (only for single file conversions to avoid spam)
            if not progress_callback:
                try:
                    from distr.core.signals import speak_text_directly_event_queue
                    notification = f"File conversion complete. Saved to {os.path.basename(output_path)}"
                    speak_text_directly_event_queue(notification)
                    logger.info(f"FileConverter: Sent completion notification via TTS")
                except Exception as e:
                    logger.warning(f"FileConverter: Could not send TTS notification: {e}")
            
            # Optionally add message to chat (only for single file to avoid spam)
            if not progress_callback and chat_manager and chat_id:
                try:
                    summary = f"✅ File conversion complete!\n\nSaved to: `{output_path}`"
                    chat_manager.add_assistant_message(chat_id, summary)
                    from distr.core.signals import signal_manager
                    signal_manager.chat_message_added.emit(chat_id, "assistant", summary)
                    signal_manager.chat_updated.emit(chat_id)
                except Exception as e:
                    logger.warning(f"FileConverter: Could not add message to chat: {e}")
        else:
            if progress_callback:
                progress_callback(file_path, f"Failed: {error_msg[:50]}")
            
            # Notify user of error (only for single file)
            if not progress_callback:
                try:
                    from distr.core.signals import speak_text_directly_event_queue
                    speak_text_directly_event_queue(f"File conversion failed: {error_msg[:100]}")
                except Exception:
                    pass
        
        return success, error_msg
            
    except Exception as e:
        logger.error(f"FileConverter: Worker thread error: {e}", exc_info=True)
        
        if progress_callback:
            progress_callback(file_path, f"Error: {str(e)[:50]}")
        
        # Notify user of error (only for single file)
        if not progress_callback:
            try:
                from distr.core.signals import speak_text_directly_event_queue
                speak_text_directly_event_queue(f"Conversion error: {str(e)[:100]}")
            except Exception:
                pass
        
        return False, str(e)


class FileConverterTool(BaseTool):
    """Tool for converting files between different formats."""
    
    name: str = "file_converter"
    description: str = (
        "Convert files between different formats. "
        "Supports:\n"
        "- Audio format conversion: flac, mp3, wav, m4a, ogg, opus, aac, wma\n"
        "- Video to audio: extract audio from video files (.mp4, .mov, .avi, etc.) to audio formats\n"
        "- Audio/Video to text: transcribe audio or video files to text (outputs .txt file, no JSON)\n"
        "- Image format conversion: webp, jpg, jpeg, png, gif, bmp, tiff (requires PIL/Pillow)\n"
        "\n"
        "Use this when the user says:\n"
        "- 'convert this file to mp3/wav/flac/etc'\n"
        "- 'convert this audio file to [format]'\n"
        "- 'convert this video to audio'\n"
        "- 'convert this to text' (transcribes without JSON)\n"
        "- 'convert this image to webp/jpg/png/etc'\n"
        "- 'convert the file I just gave you to [format]'\n"
        "\n"
        "The conversion runs in a background thread and does not block. Returns immediately with a status message."
    )
    args_schema: type[BaseModel] = FileConverterInput
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self.chat_manager = chat_manager
    
    def _find_recent_files(self, multiple: bool = False) -> list[str]:
        """Find recently dropped file(s).
        
        Args:
            multiple: If True, return all files. If False, return only the first one.
        
        Returns:
            List of file paths (empty list if none found)
        """
        try:
            import json
            storage_file = os.path.join(os.path.expanduser("~"), ".decisions", "dropped_files", "current_files.json")
            
            if not os.path.exists(storage_file):
                return []
            
            with open(storage_file, 'r') as f:
                data = json.load(f)
            
            # Get all file types
            all_files = []
            all_files.extend(data.get("audio_files", []))
            all_files.extend(data.get("video_files", []))
            all_files.extend(data.get("image_files", []))
            all_files.extend(data.get("document_files", []))
            all_files.extend(data.get("other_files", []))
            
            if not all_files:
                return []
            
            # Filter to only existing files
            existing_files = [f for f in all_files if os.path.exists(f)]
            
            if multiple:
                return existing_files
            else:
                # Return first file as list for consistency
                return [existing_files[0]] if existing_files else []
            
        except Exception as e:
            logger.warning(f"FileConverter: Error finding recent file(s): {e}")
            return []
    
    def _is_video_file(self, file_path: str) -> bool:
        """Check if file is a video file."""
        video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.3gp', '.ogv'}
        file_ext = os.path.splitext(file_path)[1].lower()
        return file_ext in video_extensions
    
    def _is_audio_file(self, file_path: str) -> bool:
        """Check if file is an audio file."""
        audio_extensions = {'.mp3', '.m4a', '.wav', '.flac', '.ogg', '.opus', '.aac', '.m4b', '.wma', '.mka'}
        file_ext = os.path.splitext(file_path)[1].lower()
        return file_ext in audio_extensions
    
    def _is_image_file(self, file_path: str) -> bool:
        """Check if file is an image file."""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.svg', '.ico', '.heic', '.heif'}
        file_ext = os.path.splitext(file_path)[1].lower()
        return file_ext in image_extensions
    
    def _run(self, file_path: Optional[str] = None, target_format: str = "", convert_all: bool = False, **kwargs) -> str:
        """Convert file(s) to target format.
        
        Args:
            file_path: Optional path to specific file. If None, will use recently dropped files.
            target_format: Target format (mp3, wav, flac, text, etc.)
            convert_all: If True, convert all recently dropped files. If False, only the first one.
        """
        try:
            # Validate target_format is provided
            if not target_format:
                return "Error: target_format is required. Please specify the target format (e.g., 'mp3', 'wav', 'flac', 'text', 'webp', 'jpg', 'png')."
            
            # Check if ffmpeg is available (needed for audio/video conversion, not for images)
            valid_image_formats = {'webp', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'tif'}
            if target_format.lower() not in valid_image_formats and target_format.lower() != 'text' and not _check_ffmpeg():
                return "Error: ffmpeg is not installed or not available. Please install ffmpeg to convert audio/video files."
            
            files_to_convert = []
            
            # If specific path provided, use it
            if file_path:
                if not os.path.exists(file_path):
                    return f"Error: File not found: {file_path}"
                
                files_to_convert = [file_path]
                logger.info(f"FileConverter: Using provided file: {file_path}")
            else:
                # Find recently dropped files
                logger.info(f"FileConverter: No file path provided, looking for recently dropped files (convert_all={convert_all})...")
                files_to_convert = self._find_recent_files(multiple=convert_all)
                
                if not files_to_convert:
                    return "Error: No file specified and no recently dropped files found. Please provide a file path or drop a file first."
                
                logger.info(f"FileConverter: Found {len(files_to_convert)} file(s) to convert")
            
            # Validate target format
            valid_audio_formats = {'mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'opus', 'wma'}
            valid_image_formats = {'webp', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'tif'}
            if (target_format.lower() not in valid_audio_formats and 
                target_format.lower() not in valid_image_formats and 
                target_format.lower() != 'text'):
                return f"Error: Unsupported target format: {target_format}. Supported formats: audio ({', '.join(sorted(valid_audio_formats))}), images ({', '.join(sorted(valid_image_formats))}), text"
            
            # Get current chat ID for notifications
            chat_id = None
            if self.chat_manager:
                try:
                    chat_id = self.chat_manager.get_current_chat()
                except Exception as e:
                    logger.warning(f"FileConverter: Could not get current chat ID: {e}")
            
            # Filter files to only valid ones
            valid_files = []
            for file_path in files_to_convert:
                is_video = self._is_video_file(file_path)
                is_audio = self._is_audio_file(file_path)
                is_image = self._is_image_file(file_path)
                
                # Check if target format matches file type
                if target_format.lower() in valid_image_formats:
                    if not is_image:
                        logger.warning(f"FileConverter: File {file_path} is not an image file, skipping")
                        continue
                elif target_format.lower() == 'text':
                    # Text transcription works for audio/video
                    if not is_video and not is_audio:
                        logger.warning(f"FileConverter: File {file_path} is not an audio or video file for transcription, skipping")
                        continue
                elif target_format.lower() in valid_audio_formats:
                    # Audio format conversion works for audio/video
                    if not is_video and not is_audio:
                        logger.warning(f"FileConverter: File {file_path} is not an audio or video file, skipping")
                        continue
                
                valid_files.append(file_path)
            
            if not valid_files:
                return "Error: No valid files to convert. Please provide audio, video, or image files matching the target format."
            
            # Use background threading without progress dialog (to avoid Qt errors in agent process)
            return self._convert_multiple_files_threading(valid_files, target_format, chat_id)
                
        except Exception as e:
            logger.error(f"FileConverter: Error in _run: {e}", exc_info=True)
            return f"Error converting file(s): {str(e)}"
    
    def _convert_multiple_files_with_progress(self, files_to_convert: list[str], target_format: str, chat_id: Optional[int]) -> str:
        """Convert multiple files with progress dialog showing file-by-file progress.
        
        Uses ThreadPoolExecutor with max_workers=3 to limit concurrent conversions.
        """
        try:
            # Try to get QApplication instance (may not be available in agent process)
            app = None
            can_show_dialog = False
            try:
                app = QApplication.instance()
                # IMPORTANT: Check if we're on the main thread
                # Qt widgets can only be created on the main thread
                # This prevents Qt errors when tool is called from agent process
                import threading
                is_main_thread = threading.current_thread() is threading.main_thread()

                if app is not None and is_main_thread:
                    can_show_dialog = True
                elif app is not None and not is_main_thread:
                    logger.info("FileConverter: QApplication exists but not on main thread, using background threading")
            except Exception:
                pass

            # In agent process, QApplication might not be available - use fallback
            if not can_show_dialog:
                logger.info("FileConverter: QApplication not available or not on main thread, using background threading without progress dialog")
                return self._convert_multiple_files_threading(files_to_convert, target_format, chat_id)

            # Try to create progress dialog - if it fails, fall back to threading
            # IMPORTANT: Wrap in try-except to catch Qt errors
            try:
                # Test if we can create widgets (this will fail if QApplication is not properly initialized)
                # This happens when tool is called from agent process instead of main GUI process
                test_dialog = QProgressDialog("test", "Cancel", 0, 1)
                test_dialog.close()
                test_dialog.deleteLater()  # Use deleteLater() instead of del for Qt objects
            except Exception as e:
                logger.warning(f"FileConverter: Cannot create QProgressDialog in this process: {e}, using background threading")
                return self._convert_multiple_files_threading(files_to_convert, target_format, chat_id)
            
            # Prepare file conversion tasks
            conversion_tasks = []
            for file_path in files_to_convert:
                is_video = self._is_video_file(file_path)
                is_image = self._is_image_file(file_path)
                file_path_obj = Path(file_path)
                if target_format.lower() == 'text':
                    output_file_path = file_path_obj.with_suffix('.txt')
                elif target_format.lower() in {'jpg', 'jpeg'}:
                    # Handle jpg/jpeg extension properly
                    output_file_path = file_path_obj.with_suffix('.jpg')
                else:
                    output_file_path = file_path_obj.with_suffix(f'.{target_format.lower()}')
                
                conversion_tasks.append({
                    'input': file_path,
                    'output': str(output_file_path),
                    'is_video': is_video,
                    'is_image': is_image
                })
            
            # Create progress dialog (with error handling)
            try:
                dialog_title = "File Conversion Progress" if len(conversion_tasks) > 1 else "Converting File"
                initial_file = os.path.basename(conversion_tasks[0]['input']) if conversion_tasks else "file"
                initial_label = f"Converting: {initial_file}\nPreparing..." if len(conversion_tasks) == 1 else f"Preparing to convert {len(conversion_tasks)} files..."
                progress_dialog = QProgressDialog(initial_label, "Cancel", 0, len(conversion_tasks))
                progress_dialog.setWindowTitle(dialog_title)
                progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
                progress_dialog.setMinimumDuration(0)
                progress_dialog.setValue(0)
            except Exception as e:
                logger.warning(f"FileConverter: Failed to create progress dialog: {e}, using background threading")
                return self._convert_multiple_files_threading(files_to_convert, target_format, chat_id)
            
            # Style the progress dialog with black background and white text
            try:
                progress_dialog.setStyleSheet("""
                QProgressDialog {
                    background-color: #000000;
                    color: #ffffff;
                }
                QProgressDialog QLabel {
                    color: #ffffff;
                    font-size: 14px;
                }
                QProgressDialog QProgressBar {
                    border: 1px solid #565869;
                    border-radius: 4px;
                    background-color: #202123;
                    text-align: center;
                }
                QProgressDialog QProgressBar::chunk {
                    background-color: #007bff;
                    border-radius: 3px;
                }
                QProgressDialog QPushButton {
                    background-color: #000000;
                    color: #ffffff;
                    border: 1px solid #565869;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 14px;
                    min-width: 100px;
                }
                QProgressDialog QPushButton:hover {
                    background-color: #1a1a1a;
                    border-color: #7a7c8c;
                }
                QProgressDialog QPushButton:pressed {
                    background-color: #2a2a2a;
                }
                """)
            except Exception as e:
                logger.warning(f"FileConverter: Failed to style progress dialog: {e}")
            
            try:
                progress_dialog.show()
                app.processEvents()
            except Exception as e:
                logger.warning(f"FileConverter: Failed to show progress dialog: {e}, using background threading")
                return self._convert_multiple_files_threading(files_to_convert, target_format, chat_id)
            
            # Track progress
            completed_count = 0
            file_status = {}  # file_path -> status message
            
            def update_progress(file_path: str, status: str):
                """Update progress dialog with file status (called from worker thread - thread-safe via QMetaObject)."""
                file_status[file_path] = status
                # Note: We'll update the dialog from main thread in as_completed loop
            
            # Use ThreadPoolExecutor with max 3 workers
            with ThreadPoolExecutor(max_workers=3) as executor:
                # Submit all tasks
                future_to_task = {}
                for task in conversion_tasks:
                    future = executor.submit(
                        _convert_worker_thread,
                        task['input'],
                        task['output'],
                        target_format,
                        task['is_video'],
                        task.get('is_image', False),
                        update_progress,
                        self.chat_manager,
                        chat_id
                    )
                    future_to_task[future] = task
                
                # Process completed tasks (runs in main thread)
                for future in as_completed(future_to_task):
                    if progress_dialog.wasCanceled():
                        # Cancel remaining tasks
                        for f in future_to_task:
                            if not f.done():
                                f.cancel()
                        progress_dialog.close()
                        return "File conversion cancelled by user."
                    
                    task = future_to_task[future]
                    try:
                        success, error_msg = future.result()
                        completed_count += 1
                        progress_dialog.setValue(completed_count)
                        
                        # Update label with current status (main thread safe)
                        current_file = os.path.basename(task['input'])
                        status_text = "✓ Complete" if success else f"✗ Failed: {error_msg[:30]}"
                        if len(conversion_tasks) == 1:
                            progress_dialog.setLabelText(f"Converting: {current_file}\n{status_text}")
                        else:
                            progress_dialog.setLabelText(f"Converting: {current_file}\n{status_text}\n\nCompleted: {completed_count}/{len(conversion_tasks)}")
                        app.processEvents()
                    except Exception as e:
                        completed_count += 1
                        progress_dialog.setValue(completed_count)
                        current_file = os.path.basename(task['input'])
                        if len(conversion_tasks) == 1:
                            progress_dialog.setLabelText(f"Converting: {current_file}\n✗ Error: {str(e)[:30]}")
                        else:
                            progress_dialog.setLabelText(f"Converting: {current_file}\n✗ Error: {str(e)[:30]}\n\nCompleted: {completed_count}/{len(conversion_tasks)}")
                        app.processEvents()
                        logger.error(f"FileConverter: Error converting {task['input']}: {e}")
            
            progress_dialog.close()
            
            # Send final notification
            try:
                from distr.core.signals import speak_text_directly_event_queue
                speak_text_directly_event_queue(f"Converted {completed_count} of {len(conversion_tasks)} files")
            except Exception:
                pass
            
            # Add summary to chat
            if self.chat_manager and chat_id:
                try:
                    summary = f"✅ Converted {completed_count} of {len(conversion_tasks)} file(s) to {target_format}"
                    self.chat_manager.add_assistant_message(chat_id, summary)
                    from distr.core.signals import signal_manager
                    signal_manager.chat_message_added.emit(chat_id, "assistant", summary)
                    signal_manager.chat_updated.emit(chat_id)
                except Exception as e:
                    logger.warning(f"FileConverter: Could not add message to chat: {e}")
            
            return f"Converted {completed_count} of {len(conversion_tasks)} file(s) to {target_format}. Output files saved in the same folders as source files."
            
        except Exception as e:
            logger.error(f"FileConverter: Error in multi-file conversion: {e}", exc_info=True)
            return f"Error during file conversion: {str(e)}"
    
    def _convert_multiple_files_threading(self, files_to_convert: list[str], target_format: str, chat_id: Optional[int]) -> str:
        """Fallback method using simple threading (when QApplication not available)."""
        # Use ThreadPoolExecutor with max 3 workers
        conversion_tasks = []
        for file_path in files_to_convert:
            is_video = self._is_video_file(file_path)
            is_image = self._is_image_file(file_path)
            file_path_obj = Path(file_path)
            if target_format.lower() == 'text':
                output_file_path = file_path_obj.with_suffix('.txt')
            elif target_format.lower() in {'jpg', 'jpeg'}:
                output_file_path = file_path_obj.with_suffix('.jpg')
            else:
                output_file_path = file_path_obj.with_suffix(f'.{target_format.lower()}')
            
            conversion_tasks.append((file_path, str(output_file_path), is_video, is_image))
        
        # Start conversions with ThreadPoolExecutor (max 3 concurrent)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            for file_path, output_path, is_video, is_image in conversion_tasks:
                future = executor.submit(
                    _convert_worker_thread,
                    file_path,
                    output_path,
                    target_format,
                    is_video,
                    is_image,
                    None,  # No progress callback
                    self.chat_manager,
                    chat_id
                )
                futures.append(future)
            
            # Wait for all to complete
            completed = 0
            for future in futures:
                try:
                    future.result()
                    completed += 1
                except Exception as e:
                    logger.error(f"FileConverter: Conversion failed: {e}")
        
        return f"Started conversion of {len(conversion_tasks)} file(s) to {target_format}. Conversions running in background (max 3 at a time)."
    
    async def _arun(self, file_path: Optional[str] = None, target_format: str = "", convert_all: bool = False, **kwargs) -> str:
        """Async version of _run."""
        return self._run(file_path, target_format, convert_all)

