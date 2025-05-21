"""
TTS.py - Text-to-Speech Engine

This module provides a comprehensive text-to-speech (TTS) system with features including:
- Support for different TTS engines (Kokoro, ElevenLabs)
- Voice selection and cloning capabilities
- Multi-threaded audio generation
- Sentence queuing and prioritization
- File management and cleanup
- Voice settings customization

The system is designed to work with the Playback module for a complete audio pipeline,
offering high-quality voice synthesis with configurable voices and engines.

Key Features:
- Engine selection (Kokoro or ElevenLabs)
- Voice cloning from provided samples
- Text preprocessing and cleaning
- Efficient audio file generation and management
- Thread-safe operation with proper locking
- Comprehensive error handling and logging

Class Organization:
1. Initialization and Setup
2. Engine Management
3. Text Processing
4. Audio Generation
5. Queue Management
6. Resource Management
7. Utility Methods
"""

from typing import Optional, Dict, Any, List
from .utils import get_timestamp
from threading import Lock
import multiprocessing
import numpy as np
import tempfile
import logging
import glob
import uuid
import os
import re
import time
import threading
import queue

class TTSEngine:
    """
    Text-to-Speech engine for generating audio from text.
    
    This class manages the generation of speech audio with support for:
    - Multiple TTS engines (Kokoro, ElevenLabs)
    - Voice selection and cloning
    - Async generation with queuing
    - File management and cleanup
    - Voice customization
    """
    
    # ===========================================
    # 1. Initialization and Setup Text-to-Speech
    # ===========================================
    def __init__(self, 
                 engine: str = "kokoro", 
                 api_key: Optional[str] = None, 
                 voice_name: Optional[str] = None, 
                 clone_samples: Optional[List[str]] = None, 
                 delete_cloned_voices: Optional[bool] = False,
                 voice_settings: Optional[Dict[str, Any]] = None,
                 model_dir: Optional[str] = None,
                 playback=None):
        """
        Initialize the TTS engine with configuration options.
        Args:
            engine (str): TTS engine to use ('kokoro' or 'elevenlabs')
            api_key (str, optional): API key for cloud TTS services
            delete_cloned_voices (bool): Whether to delete existing cloned voices
            voice_name (str, optional): Name of the voice to use
            clone_samples (List[str], optional): List of audio sample paths for voice cloning
            voice_settings (Dict, optional): Engine-specific voice settings
            model_dir (str, optional): Path to model directory
            playback: Playback instance to queue generated files for playback (required)
        """
        # Initialize logger
        self.logger = logging.getLogger(__name__)
        
        # Store configuration parameters
        self.engine = engine
        self.api_key = api_key
        self.voice_name = voice_name
        self.voice_settings = voice_settings or {"stability": 0.3, "similarity_boost": 0.5}
        self.delete_cloned_voices = delete_cloned_voices
        self.clone_samples = clone_samples
        self.model_dir = model_dir
        self.playback = playback
        if self.playback is None:
            raise ValueError("TTSEngine requires a playback instance for direct file queueing.")
        
        # Text conditioning state
        self.previous_text = None
        
        # State tracking
        self.is_running = multiprocessing.Value('b', False)
        self.tts = None
        self.voice = None

        # Multiprocessing queues for inter-process communication
        self.input_queue = multiprocessing.Queue()
        self.output_queue = multiprocessing.Queue()
        self.signal_queue = multiprocessing.Queue()

        # Generation tracking structures
        self.generation_queue = multiprocessing.Queue()  # Queue for sentences to generate
        self.generated_files = {}  # Dict to track generated files by sentence ID
        self.generation_lock = Lock()  # Lock for thread-safe access to generated_files
        self.next_position = 0  # Track next position in sequence
        
        # Clean up any leftover temporary files
        self.cleanup()

    @staticmethod
    def create_queue_callback(tts_instance):
        """
        Create a picklable callback function for queueing sentences.
        
        Args:
            tts_instance: TTSEngine instance
            
        Returns:
            function: Callback function that queues text to the TTS engine
        """
        def queue_callback(text):
            return tts_instance.queue_sentence(text)
        return queue_callback

    # ===========================================
    # 2. Engine Management
    # ===========================================
    def _initialize_engine(self) -> None:
        """
        Initialize the selected TTS engine based on configuration.
        
        Raises:
            FileNotFoundError: If required model files aren't found
            ValueError: If no voices are available
        """
        if self.engine == "elevenlabs":
            self._initialize_elevenlabs()
        else:
            self._initialize_kokoro()

    def _initialize_elevenlabs(self) -> None:
        """Initialize the ElevenLabs TTS engine."""
        try:
            from elevenlabs.client import ElevenLabs
            self.tts = ElevenLabs(api_key=self.api_key)
            self.voice = self._get_elevenlabs_voice()
            self.logger.info(f"Successfully initialized ElevenLabs with voice {self.voice.name if hasattr(self.voice, 'name') else 'unknown'}")
        except Exception as e:
            self.logger.error(f"Error initializing ElevenLabs engine: {e}")
            raise

    def _initialize_kokoro(self) -> None:
        """Initialize the Kokoro TTS engine."""
        try:
            self.logger.info("Initializing Kokoro TTS engine...")
            from kokoro_onnx import Kokoro
            if self.model_dir:
                models_path = self.model_dir
            else:
                models_path = os.path.join(os.getcwd(), "distr", "agent", "models")
            self.logger.info(f"Models path: {models_path}")
            
            if not os.path.exists(models_path):
                raise FileNotFoundError(f"Models directory not found at {models_path}")
                
            self.logger.info("Loading Kokoro model files...")
            self.tts = Kokoro(
                os.path.join(models_path, "kokoro-v1.0.onnx"),
                os.path.join(models_path, "voices-v1.0.bin")
            )
            self.logger.info("Kokoro model files loaded successfully")
            
            # Get available voices and use the first one if none specified
            self.logger.info("Getting available voices...")
            available_voices = self.tts.get_voices()
            if not available_voices:
                raise ValueError("No voices available in Kokoro engine")
                
            self.voice = self.voice_name if self.voice_name in available_voices else available_voices[0]
            self.logger.info(f"Using voice: {self.voice}")
            self.logger.info("Kokoro TTS engine initialized successfully")
        except Exception as e:
            self.logger.error(f"Error initializing Kokoro engine: {e}")
            raise

    def _get_elevenlabs_voice(self):
        """
        Get or create an ElevenLabs voice.
        
        Returns:
            Voice object from ElevenLabs API
            
        Raises:
            Exception: If voice creation fails
        """
        agent_dir = os.path.join(os.getcwd(), "distr", 'agent', 'agents', self.voice_name)

        voices = self.tts.voices.get_all()

        # Delete cloned voices if requested
        if self.delete_cloned_voices:
            for voice in voices.voices:
                if voice.category == "cloned" and voice.name != self.voice_name:
                    self.logger.info(f"Deleting cloned voice: {voice.name}")
                    self.tts.voices.delete(voice.voice_id)

        # Check if voice already exists
        found_voice = False
        for v in voices.voices:
            if v.name == self.voice_name:
                found_voice = True
                return v

        # If voice doesn't exist, create it from samples
        if not found_voice:   
            voice_samples_dir = f"{agent_dir}/voice_samples"
            # Get voice samples from the voice_samples directory
            self.voice_samples = []
            if os.path.exists(voice_samples_dir):
                self.logger.info(f"[{get_timestamp()}] Loading voice samples from {voice_samples_dir}")
                # Get all mp3 files in the voice_samples directory
                self.voice_samples = glob.glob(f"{voice_samples_dir}/*.mp3")
                if self.voice_samples:
                    self.logger.info(f"[{get_timestamp()}] Found {len(self.voice_samples)} voice samples")
                else:
                    self.logger.error(f"[{get_timestamp()}] No voice samples found in {voice_samples_dir}")
            else:
                self.logger.error(f"[{get_timestamp()}] Voice samples directory not found: {voice_samples_dir}")

            return self.tts.clone(
                name=self.voice_name,
                description=f"{self.voice_name} voice clone",        
                files=self.voice_samples
            )
        else:
            return voices.voices[0]

    # ===========================================
    # 3. Text Processing
    # ===========================================
    def process_text(self, text: str, sentence_id=None, sentence_group=None, position=None) -> None:
        """
        Process text and add it to the generation queue with provided metadata.
        Args:
            text (str): Text to be processed and converted to audio (should be a single sentence or chunk)
            sentence_id (str): Unique ID for the sentence
            sentence_group (str): Group ID for this batch
            position (int): Order position
        """
        if isinstance(text, dict):
            # Accept dict from TTS input queue
            sentence_id = text.get('sentence_id', sentence_id)
            sentence_group = text.get('group_id', sentence_group)
            position = text.get('position', position)
            text = text.get('text', text)
        if not text or not isinstance(text, str):
            return
        # Deduplicate: Only add if sentence_id not in generated_files
        with self.generation_lock:
            if sentence_id and sentence_id in self.generated_files:
                return
        # Continue with normal processing
        if sentence_id is None:
            sentence_id = str(uuid.uuid4())
        if position is None:
            with self.generation_lock:
                position = self.next_position
                self.next_position += 1
        with self.generation_lock:
            self.generated_files[sentence_id] = {
                'text': text,
                'status': 'queued',
                'file_path': None,
                'position': position,
                'error': None,
                'sentence_group': sentence_group,
                'is_played': False,
                'is_generated': False
            }
        self.generation_queue.put({
            'id': sentence_id,
            'text': text,
            'position': position
        })
        self.logger.info(f"Queued text for TTS generation: {text} (group: {sentence_group}, pos: {position})")

    def _clean_text(self, text: str) -> str:
        """
        Clean text by removing special characters, emojis, and other problematic characters.
        
        Args:
            text (str): Text to be cleaned
            
        Returns:
            str: Cleaned text ready for TTS processing
        """
        # Remove emojis and special characters, but preserve apostrophes
        text = re.sub(r"[^\w\s.,!?'-]", '', text)
        
        # Remove multiple spaces
        text = re.sub(r'\s+', ' ', text)
        
        # Remove leading/trailing whitespace
        text = text.strip()        
        return text

    # ===========================================
    # 4. Audio Generation
    # ===========================================
    def generate_next(self) -> Optional[Dict[str, Any]]:
        """
        Generate the next queued sentence into an audio file and immediately queue it for playback.
        Returns:
            dict: Generated file information or None if nothing to generate
        """
        if not self.is_running.value:
            return None
        try:
            if self.generation_queue.empty():
                return None
            sentence_info = self.generation_queue.get_nowait()
            text = sentence_info.get('text')
            sentence_id = sentence_info.get('id')
            if not text or not sentence_id:
                self.logger.error("Invalid sentence information in queue")
                return None
            cleaned_text = self._clean_text(text)
            if not cleaned_text:
                self.logger.warning(f"Text '{text}' became empty after cleaning, skipping")
                return None
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                audio_file = temp_file.name
            try:
                if self.engine == "elevenlabs":
                    self._generate_elevenlabs_audio(cleaned_text, audio_file)
                else:
                    self._generate_kokoro_audio(cleaned_text, audio_file)
                with self.generation_lock:
                    self.generated_files[sentence_id] = {
                        'text': text,
                        'file_path': audio_file,
                        'status': 'generated',
                        'generated_at': time.time(),
                        'position': sentence_info.get('position', 0),
                        'is_played': False,
                        'is_generated': True
                    }
                self.logger.info(f"Generated audio file: {audio_file}")
                file_info = {
                    'id': sentence_id,
                    'text': text,
                    'file_path': audio_file,
                    'position': sentence_info.get('position', 0)
                }
                # Immediately queue for playback
                self.playback.queue_generated_tts_file(file_info)
                return file_info
            except Exception as e:
                self.logger.error(f"Error generating audio: {e}")
                if os.path.exists(audio_file):
                    try:
                        os.remove(audio_file)
                    except Exception:
                        pass
                with self.generation_lock:
                    self.generated_files[sentence_id] = {
                        'text': text,
                        'status': 'error',
                        'error': str(e),
                        'position': sentence_info.get('position', 0),
                        'is_played': False,
                        'is_generated': False
                    }
                return None
        except queue.Empty:
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error in generate_next: {e}")
            return None

    def _generate_elevenlabs_audio(self, text: str, audio_file: str) -> None:
        """
        Generate audio using the ElevenLabs engine with text conditioning.
        
        Args:
            text (str): Text to convert to speech
            audio_file (str): Path to save the audio file
            
        Raises:
            Exception: If audio generation fails
        """
        self.logger.info("Using ElevenLabs engine with text conditioning")
        
        # Generate audio with text conditioning
        audio_generator = self.tts.generate(
            text=text,
            voice=self.voice,
            model="eleven_multilingual_v2",
            # previous_text=self.previous_text,
            voice_settings=self.voice_settings,
        )
        
        # Update previous text for next generation
        self.previous_text = text
        
        # Write the audio chunks to file
        with open(audio_file, 'wb') as f:
            if isinstance(audio_generator, (bytes, bytearray)):
                f.write(audio_generator)
            else:
                # Handle generator output
                for chunk in audio_generator:
                    if chunk:
                        f.write(chunk)

    def _generate_kokoro_audio(self, text: str, audio_file: str) -> None:
        """
        Generate audio using the Kokoro engine.
        
        Args:
            text (str): Text to convert to speech
            audio_file (str): Path to save the audio file
            
        Raises:
            ValueError: If text is empty or audio generation fails
            Exception: If audio generation fails for other reasons
        """
        self.logger.info("Starting Kokoro audio generation...")
        import soundfile as sf
        from kokoro_onnx.config import SAMPLE_RATE
        
        # Clean the text
        text = self._clean_text(text)
        if not text:
            raise ValueError("Empty text after cleaning")
            
        self.logger.info(f"Generating audio for text: {text}")
        
        # Generate audio with error handling
        try:
            self.logger.info(f"Generating audio with voice: {self.voice}")
            result = self.tts.create(text, voice=self.voice, speed=1.2)
            self.logger.info("Audio generation completed")
            
            # Handle tuple return type from Kokoro
            if isinstance(result, tuple):
                audio_data = result[0]  # First element should be the audio data
                if len(result) > 1:
                    self.logger.debug(f"Kokoro sample rate: {result[1]} Hz")
            else:
                audio_data = result
            
            if audio_data is None:
                raise ValueError("Kokoro returned None audio data")
                
            if not isinstance(audio_data, np.ndarray):
                raise ValueError(f"Expected numpy array, got {type(audio_data)}")
                
            if len(audio_data) == 0:
                raise ValueError("Generated audio data is empty")
                
            # Ensure audio data is in the correct format
            if audio_data.ndim != 1:
                raise ValueError(f"Expected 1D audio data, got {audio_data.ndim}D")
                
            self.logger.info(f"Saving audio to file: {audio_file}")
            sf.write(audio_file, audio_data, SAMPLE_RATE)
            self.logger.info("Audio file saved successfully")
        except Exception as e:
            self.logger.error(f"Error in Kokoro audio generation: {str(e)}")
            self.logger.error(f"Text that caused error: {text}")
            raise

    # ===========================================
    # 5. Queue Management
    # ===========================================
    def get_generated_file(self, sentence_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the generated file information for a sentence ID.
        
        Args:
            sentence_id (str): ID of the sentence to retrieve
            
        Returns:
            Dict: Information about the generated file or None if not found
        """
        with self.generation_lock:
            return self.generated_files.get(sentence_id)

    def queue_sentence(self, text: str) -> str:
        """
        Queue a single sentence or text chunk for TTS generation. TTS expects pre-split text.
        Args:
            text (str): Text to be converted to speech (should be a single sentence or chunk)
        Returns:
            str: The sentence group ID for tracking
        """
        assert isinstance(text, str), "TTS expects a single sentence or chunk as a string. Sentence splitting must be handled upstream."
        sentence_group = str(uuid.uuid4())
        sentence_id = str(uuid.uuid4())
        with self.generation_lock:
            position = self.next_position
            self.next_position += 1
        self.process_text(
            text,
            sentence_id=sentence_id,
            sentence_group=sentence_group,
            position=position
        )
        return sentence_group

    def prune_played_files(self, max_kept=10):
        """
        Remove played files from generated_files, keeping only the most recent unplayed/generated ones.
        Args:
            max_kept (int): Maximum number of played files to keep for history
        """
        with self.generation_lock:
            played = [k for k, v in self.generated_files.items() if v.get('is_played', False)]
            for k in played[:-max_kept]:
                del self.generated_files[k]

    def clear_unplayed_files_from_previous_groups(self, keep_group=None):
        """
        Remove all unplayed/generated files from previous groups except the current one.
        Args:
            keep_group (str): The group ID to keep (current LLM response)
        """
        with self.generation_lock:
            to_delete = []
            for sid, info in self.generated_files.items():
                if info.get('status') != 'generated' or info.get('is_played', False):
                    # Only consider unplayed/generated files
                    continue
                if keep_group and info.get('sentence_group') != keep_group:
                    to_delete.append(sid)
            for sid in to_delete:
                file_path = self.generated_files[sid].get('file_path')
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        self.logger.info(f"[TTS] Removed old unplayed file: {file_path}")
                    except Exception as e:
                        self.logger.warning(f"[TTS] Failed to remove file {file_path}: {e}")
                del self.generated_files[sid]
            if to_delete:
                self.logger.info(f"[TTS] Cleared {len(to_delete)} unplayed/generated files from previous groups.")

    def get_playlist(self) -> List[Dict[str, Any]]:
        """
        Return files ready for playback as a playlist, sorted by group and position, no duplicates.
        Returns:
            List[Dict]: List of generated files ready for playback
        """
        with self.generation_lock:
            seen = set()
            playlist = []
            for sentence_id, file_info in self.generated_files.items():
                if file_info.get('status') == 'generated' and not file_info.get('is_played', False):
                    file_path = file_info.get('file_path')
                    key = (sentence_id, file_path)
                    if file_path and key not in seen:
                        playlist.append({
                            'sentence_id': sentence_id,
                            'file_path': file_info.get('file_path'),
                            'position': file_info.get('position'),
                            'status': file_info.get('status'),
                            'sentence_group': file_info.get('sentence_group'),
                            'is_played': file_info.get('is_played', False),
                            'text': file_info.get('text'),
                        })
                        seen.add(key)
            # Sort by group and position
            playlist.sort(key=lambda x: (x.get('sentence_group'), x.get('position', 0)))
            return playlist

    def wait_for_generation(self, timeout=30):
        """
        Wait for all queued sentences to be generated.
        
        Args:
            timeout (int): Maximum time to wait in seconds
            
        Returns:
            bool: True if all sentences were generated, False if timeout occurred
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self.generation_lock:
                # Check if all sentences are generated
                all_generated = True
                for file_info in self.generated_files.values():
                    if file_info.get('status') not in ['generated', 'error']:
                        all_generated = False
                        break
                
                if all_generated:
                    return True
                    
            time.sleep(0.1)
        return False

    # ===========================================
    # 6. Utility Methods
    # ===========================================
    def get_available_voices(self) -> list:
        """
        Get list of available voices.
        
        Returns:
            list: Names of available voices for the current engine
        """
        if self.engine == "elevenlabs":
            return [v.name for v in self.tts.voices.get_all().voices]
        return self.tts.get_voices() if hasattr(self.tts, 'get_voices') else ["default"]

    def get_queue_callback(self):
        """
        Get a picklable callback function for queueing sentences.
        
        Returns:
            function: Callback function that queues text to the TTS engine
        """
        return self.queue_sentence

    # ===========================================
    # 7. Resource Management
    # ===========================================
    def start(self) -> None:
        """
        Start the TTS engine.
        
        Initializes the selected TTS engine and sets the running state.
        """
        if not self.is_running.value:
            self.logger.info("Starting TTS engine...")
            try:
                self._initialize_engine()
                self.is_running.value = True
                
                # Start input queue processing thread
                self.input_thread = threading.Thread(target=self._process_input_queue)
                self.input_thread.daemon = True
                self.input_thread.start()
                self.logger.info("Started input queue processing thread")
                
                # Log engine status
                self.logger.info(f"TTS engine started successfully with engine: {self.engine}")
                if self.voice:
                    self.logger.info(f"Using voice: {self.voice_name}")
            except Exception as e:
                self.logger.error(f"Failed to start TTS engine: {e}")
                self.is_running.value = False
                raise
        else:
            self.logger.info("TTS engine is already running")

    def _process_input_queue(self):
        """Process items from the input queue and add them to the generation queue"""
        while self.is_running.value:
            try:
                # Process input queue items
                try:
                    item = self.input_queue.get_nowait()
                    self.logger.info(f"[TTS] Received from input queue: {item}")
                    if item and isinstance(item, dict):
                        text = item.get('text')
                        self.logger.info(f"[TTS] Calling process_text with: text={text}, sentence_id={item.get('sentence_id')}, group_id={item.get('group_id')}, position={item.get('position')}")
                        self.process_text(item)
                except queue.Empty:
                    # No new input items, try to generate next audio file
                    result = self.generate_next()
                    if result:
                        self.logger.info(f"[TTS] Generated audio file: {result}")
                        with self.generation_lock:
                            self.generated_files[result['id']] = {
                                'text': result['text'],
                                'file_path': result['file_path'],
                                'status': 'generated',
                                'generated_at': time.time(),
                                'position': result['position']
                            }
                        self.logger.info(f"[TTS] Added to generated_files: id={result['id']}, text={result['text']}, file_path={result['file_path']}, position={result['position']}")
                    else:
                        # No items to generate, small sleep to prevent CPU spinning
                        time.sleep(0.01)
                except Exception as e:
                    self.logger.error(f"[TTS] Error processing input queue: {e}")
                    time.sleep(0.01)
            except Exception as e:
                self.logger.error(f"[TTS] Unexpected error in input queue processing: {e}")
                time.sleep(0.01)
        self.logger.warning("[TTS] Input queue thread exiting!")

    def stop(self) -> None:
        """
        Stop the TTS engine.
        
        Stops the engine, drains all queues, and cleans up resources.
        """
        if self.is_running.value:
            self.logger.info("Stopping TTS engine...")
            self.is_running.value = False
            
            # Wait for input thread to stop
            if hasattr(self, 'input_thread') and self.input_thread and self.input_thread.is_alive():
                try:
                    self.input_thread.join(timeout=2.0)
                except Exception as e:
                    self.logger.warning(f"Error stopping input thread: {e}")
            
            # Empty all queues first
            for queue_attr in ['input_queue', 'output_queue', 'signal_queue', 'generation_queue']:
                if hasattr(self, queue_attr):
                    queue = getattr(self, queue_attr)
                    if queue:
                        try:
                            # Drain the queue
                            while True:
                                try:
                                    queue.get_nowait()
                                except:
                                    break
                        except Exception as e:
                            self.logger.warning(f"Error draining queue {queue_attr}: {e}")
            
            # Properly close and join all queues to release semaphores
            for queue_attr in ['input_queue', 'output_queue', 'signal_queue', 'generation_queue']:
                if hasattr(self, queue_attr):
                    queue = getattr(self, queue_attr)
                    if queue:
                        try:
                            queue.close()
                            queue.join_thread()
                        except Exception as e:
                            self.logger.warning(f"Error closing queue {queue_attr}: {e}")
            
            self.cleanup()
        else:
            self.logger.info("TTS engine is already stopped")

    def cleanup(self) -> None:
        """
        Clean up any temporary files created by the TTS engine.
        Removes all temporary audio files from the system temp directory.
        """
        try:
            # Clear the generation queue
            while not self.generation_queue.empty():
                try:
                    self.generation_queue.get_nowait()
                except:
                    break
            
            # Clear generated files
            with self.generation_lock:
                for sentence_id, file_info in list(self.generated_files.items()):
                    if file_info.get('status') == 'generated':
                        file_path = file_info.get('file_path')
                        if file_path and os.path.exists(file_path):
                            try:
                                os.remove(file_path)
                                self.logger.info(f"Cleaned up generated file: {file_path}")
                            except Exception as e:
                                self.logger.warning(f"Failed to remove generated file {file_path}: {e}")
                self.generated_files.clear()
            
            # Clean up any remaining temporary files
            tmp_dir = tempfile.gettempdir()
            for filename in os.listdir(tmp_dir):
                if filename.endswith('.wav') or filename.endswith('.mp3'):
                    file_path = os.path.join(tmp_dir, filename)
                    try:
                        os.remove(file_path)
                        self.logger.info(f"Cleaned up temporary file: {file_path}")
                    except Exception as e:
                        self.logger.warning(f"Failed to remove temporary file {file_path}: {e}")
                        
            self.logger.info("TTS cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during TTS cleanup: {e}")
