"""File metadata utility functions for gathering file information."""
import os
import platform
import subprocess
import mimetypes
import logging

logger = logging.getLogger(__name__)


def get_file_metadata(file_path):
    """Get comprehensive metadata for a file.
    
    Returns dict with:
    - size: file size in bytes
    - size_human: human-readable size
    - mime_type: MIME type
    - default_app: default application to open (macOS only)
    - type_description: file type description
    - image_info: dict with width, height (if image)
    - audio_info: dict with duration, sample_rate, channels, format (if audio)
    """
    metadata = {
        'size': 0,
        'size_human': '0 B',
        'mime_type': 'unknown',
        'default_app': None,
        'type_description': 'unknown',
        'image_info': None,
        'audio_info': None
    }
    
    try:
        if not os.path.exists(file_path):
            return metadata
        
        # File size
        size = os.path.getsize(file_path)
        metadata['size'] = size
        # Human-readable size
        size_for_format = size
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_for_format < 1024.0:
                metadata['size_human'] = f"{size_for_format:.1f} {unit}"
                break
            size_for_format /= 1024.0
        
        # MIME type
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            metadata['mime_type'] = mime_type
            # Type description from MIME type
            if mime_type.startswith('image/'):
                metadata['type_description'] = f"Image ({mime_type.split('/')[1].upper()})"
            elif mime_type.startswith('audio/'):
                metadata['type_description'] = f"Audio ({mime_type.split('/')[1].upper()})"
            elif mime_type.startswith('video/'):
                metadata['type_description'] = f"Video ({mime_type.split('/')[1].upper()})"
            elif mime_type.startswith('text/'):
                metadata['type_description'] = f"Text ({mime_type.split('/')[1]})"
            elif mime_type == 'application/pdf':
                metadata['type_description'] = "PDF Document"
            elif mime_type.startswith('application/'):
                metadata['type_description'] = f"Application ({mime_type.split('/')[1]})"
            else:
                metadata['type_description'] = mime_type
        
        # Default application (macOS)
        if platform.system() == 'Darwin':
            try:
                # Use LaunchServices to get default app
                result = subprocess.run(
                    ['mdls', '-name', 'kMDItemContentType', file_path],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    uti = result.stdout.strip().split('=')[-1].strip().strip('"')
                    if uti:
                        # Get default app for UTI
                        result2 = subprocess.run(
                            ['duti', '-x', uti],
                            capture_output=True,
                            text=True,
                            timeout=2
                        )
                        if result2.returncode == 0 and result2.stdout.strip():
                            app_name = result2.stdout.strip().split('\n')[0]
                            # Clean up app name (remove .app extension)
                            if app_name.endswith('.app'):
                                app_name = app_name[:-4]
                            metadata['default_app'] = app_name
            except Exception:
                pass
        
        # Image metadata
        if metadata['mime_type'].startswith('image/'):
            try:
                from PIL import Image
                with Image.open(file_path) as img:
                    metadata['image_info'] = {
                        'width': img.width,
                        'height': img.height,
                        'format': img.format,
                        'mode': img.mode
                    }
            except ImportError:
                pass
            except Exception:
                pass
        
        # Audio metadata
        if metadata['mime_type'].startswith('audio/'):
            try:
                # Try soundfile first
                try:
                    import soundfile as sf
                    with sf.SoundFile(file_path) as f:
                        duration = len(f) / f.samplerate
                        metadata['audio_info'] = {
                            'duration': duration,
                            'duration_formatted': f"{int(duration // 60)}:{int(duration % 60):02d}",
                            'sample_rate': f.samplerate,
                            'channels': f.channels,
                            'format': f.format,
                            'subtype': f.subtype
                        }
                except ImportError:
                    # Fallback to pydub
                    try:
                        from pydub import AudioSegment
                        audio = AudioSegment.from_file(file_path)
                        duration = len(audio) / 1000.0  # pydub returns milliseconds
                        metadata['audio_info'] = {
                            'duration': duration,
                            'duration_formatted': f"{int(duration // 60)}:{int(duration % 60):02d}",
                            'sample_rate': audio.frame_rate,
                            'channels': audio.channels,
                            'format': os.path.splitext(file_path)[1][1:].upper() if os.path.splitext(file_path)[1] else 'unknown'
                        }
                    except ImportError:
                        pass
            except Exception:
                pass
        
    except Exception as e:
        logger.debug(f"Error getting metadata for {file_path}: {e}")
    
    return metadata











