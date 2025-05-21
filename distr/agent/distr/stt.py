"""
STT.py - Speech-to-Text Engine

This module provides speech recognition capabilities with multiple engine support including:
- Whisper.cpp for local, fast speech recognition
- Vosk for lightweight offline recognition
- AssemblyAI for high-accuracy cloud-based recognition

The system handles audio input, speech detection, and transcription with features including:
- Real-time speech detection
- Silence detection with configurable thresholds
- Speech duration tracking
- LLM interaction including interruption capabilities
- Audio playback management integration (volume ducking)
- Multi-threaded processing

Key Features:
- Support for multiple STT engines
- Speech/silence detection with energy thresholds
- Automatic playback volume ducking during speech
- LLM interruption for continuous speech
- Threaded audio processing
- Error handling and cleanup

Class Organization:
1. Initialization and Setup
2. Speech Processing 
3. Transcription Handling
4. Playback Interaction
5. Audio Stream Management
6. Engine Management
7. Utility Methods
"""

from multiprocessing import Queue
from .utils import get_timestamp
import sounddevice as sd
from queue import Queue
import numpy as np
import threading
import time
import json
import logging
import os

# Optional dependency imports with availability flags
try:
    import pywhispercpp.model as pwc
    WHISPER_CPP_AVAILABLE = True
except ImportError:
    WHISPER_CPP_AVAILABLE = False

try:
    import vosk
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

try:
    import assemblyai as aai
    ASSEMBLYAI_AVAILABLE = True
except ImportError:
    ASSEMBLYAI_AVAILABLE = False


class STTEngine:
    """
    Speech-to-Text engine that provides real-time transcription capabilities
    with configurable speech detection parameters and multiple backend options.
    
    This class manages audio input, speech detection, and transcription with support for:
    - Multiple speech recognition backends (Whisper.cpp, Vosk, AssemblyAI)
    - Real-time speech detection with silence thresholds
    - Integration with LLM callback for transcription handling
    - Audio playback volume control during speech
    - Thread-safe audio processing
    """
    
    # ===========================================
    # 1. Initialization and Setup
    # ===========================================
    def __init__(self, device_info, engine_type="whisper.cpp", model_path="base.en", api_key=None, llm_callback=None, silence_threshold=0.03, playback=None):
        """
        Initialize the STT engine with device and engine settings.
        
        Args:
            device_info (dict): Information about the audio input device to use
            engine_type (str): Type of STT engine to use ("whisper.cpp", "vosk", or "assemblyai")
            model_path (str, optional): Path to the model file for local engines
            api_key (str, optional): API key for cloud-based engines (e.g., AssemblyAI)
            llm_callback (callable): Callback function to handle transcribed text
            silence_threshold (float, optional): Energy threshold for silence detection
            playback (object, optional): Playback object for volume ducking
        """
        # Initialize logger
        self.logger = logging.getLogger(__name__)
        
        print(f"[DEBUG] STTEngine __init__ called with device_info: {device_info}, engine_type: {engine_type}")
        self.logger.info(f"[DEBUG] STTEngine __init__ called with device_info: {device_info}, engine_type: {engine_type}")
        
        # Audio processing settings
        self.device_info = device_info
        self.audio_queue = Queue()
        self.transcription_queue = Queue()
        self.running = False
        self.is_speaking = False
        self.last_speech_time = None
        self.silence_duration = 0
        self.audio_buffer = []
        
        # Speech detection configuration
        self.required_silence_duration = 1.5  # Required silence duration in seconds
        self.min_speech_duration = 0.5  # Minimum speech duration in seconds
        self.silence_threshold = silence_threshold
        self._debug_energy_prints = 0
        self._debug_energy_start_time = None
        self.max_debug_energy_prints = 100  # Print 100 energies (~5s at 50Hz)
        self.max_speech_duration = 1.5  # Maximum speech duration before interrupting LLM
        self.llm_interrupt_sent = False  # Flag to track if we've sent an interrupt
        
        # Speech ducking settings
        self.speech_start_time = None  # When speech was first detected
        self.ducking_applied = False   # Whether we've already applied volume ducking
        self.playback_cleared = False  # Whether we've already cleared playback
        self.speech_continue_duration = 0.5  # Duration of speech before clearing playback
        
        # STT Engine setup
        self.engine_type = engine_type
        self.model_path = model_path
        self.api_key = api_key
        self.stt = None
        self.recognizer = None  # Only for Vosk
        self.llm_callback = llm_callback
        
        # Initialize the selected STT engine
        self._setup_stt_engine()
        
        # Audio stream
        self.stream = None
        
        self.playback = playback
        
        self.last_energy = 0.0
        self._status_thread = None
        self._status_thread_stop = threading.Event()
        
    def _setup_stt_engine(self):
        """
        Initialize the selected STT engine based on engine_type.
        Handles different initialization requirements for each engine type.
        Raises ImportError if required dependencies are not available.
        """
        print(f"[DEBUG] _setup_stt_engine: engine_type={self.engine_type}")
        self.logger.info(f"[DEBUG] _setup_stt_engine: engine_type={self.engine_type}")
        if self.engine_type == "whisper.cpp":
            if not WHISPER_CPP_AVAILABLE:
                print("[FATAL] pywhispercpp is not installed!")
                self.logger.error("[FATAL] pywhispercpp is not installed!")
                raise ImportError("pywhispercpp is not installed")
            model = self.model_path or os.path.join(os.getcwd(), "distr", "agent", "models", "base.en")
            self.stt = pwc.Model(model, print_progress=False)                        
        elif self.engine_type == "assemblyai":
            if not ASSEMBLYAI_AVAILABLE or not self.api_key:
                print("[FATAL] AssemblyAI API key required or assemblyai package not installed!")
                self.logger.error("[FATAL] AssemblyAI API key required or assemblyai package not installed!")
                raise ValueError("AssemblyAI API key required or assemblyai package not installed")
            self.stt = aai.Transcriber(api_key=self.api_key)
        else:  # fallback is vosk
            if not VOSK_AVAILABLE:
                print("[FATAL] vosk is not installed!")
                self.logger.error("[FATAL] vosk is not installed!")
                raise ImportError("vosk is not installed")
            model_path = self.model_path or os.path.join(os.getcwd(), "distr", "agent", "models", "vosk-model-en-us-0.22")
            self.stt = vosk.Model(model_path)
            self.recognizer = vosk.KaldiRecognizer(self.stt, 16000)

    # ===========================================
    # 2. Speech Processing
    # ===========================================
    def is_silence(self, audio_data):
        """
        Determine if audio contains silence based on energy threshold.
        
        Args:
            audio_data (numpy.ndarray): Audio data to analyze
            
        Returns:
            bool: True if the audio is below the silence threshold
        """
        energy = np.abs(audio_data).mean()
        self.last_energy = energy  # Track last energy for status reporting
        self.logger.debug(f"[DEBUG] is_silence: energy={energy:.6f}, threshold={self.silence_threshold}")
        # Debug print for first 5 seconds
        if self._debug_energy_prints < self.max_debug_energy_prints:
            if self._debug_energy_start_time is None:
                self._debug_energy_start_time = time.time()
            self.logger.debug(f"[DEBUG] Input energy: {energy:.6f} (threshold: {self.silence_threshold})")
            self._debug_energy_prints += 1
        is_silent = energy < self.silence_threshold
        if is_silent and self.is_speaking:
            self.logger.debug(f"[DEBUG] Silence detected: energy={energy:.6f}, threshold={self.silence_threshold}")
        return is_silent

    def process_audio(self):
        """
        Main audio processing loop that detects speech, accumulates audio data, 
        and sends it for transcription when speech ends.
        
        Handles speech detection, silence detection, audio buffering, and 
        manages integration with playback system for volume ducking.
        """
        self.logger.info(f"[{get_timestamp()}] Starting audio processing thread")
        buffer_size = 16000  # 1 second of audio at 16kHz
        
        # Speech tracking variables
        speech_start_time = None
        speech_duration = 0
        ducking_applied = False
        playback_cleared = False
        
        while self.running:
            try:
                if not self.audio_queue.empty():
                    audio_data = self.audio_queue.get()
                    current_time = time.time()
                    
                    # Check for speech or silence
                    if not self.is_silence(audio_data):
                        # Speech detected
                        if not self.is_speaking:
                            self.logger.info(f"\n[{get_timestamp()}] Speech detected...")
                            self.llm_interrupt_sent = False  # Reset interrupt flag when new speech starts
                            speech_start_time = current_time  # Record when speech started
                            ducking_applied = False  # Reset ducking flag
                            playback_cleared = False  # Reset playback cleared flag
                            
                            # Try to get parent session to access playback
                            session = self._get_session_from_callback()
                            if session and hasattr(session, 'playback'):
                                # Apply volume ducking immediately when speech is detected
                                session.playback.duck_volume(True)
                                ducking_applied = True
                                
                        self.is_speaking = True
                        self.last_speech_time = current_time
                        self.silence_duration = 0
                        self.audio_buffer.extend(audio_data.flatten())
                        
                        # Calculate speech duration if we have a start time
                        if speech_start_time:
                            speech_duration = current_time - speech_start_time
                            
                            # If speech has continued for at least 0.5 seconds, clear the playback
                            if (speech_duration and not playback_cleared):
                                self.logger.warning(f"\n[{get_timestamp()}] Speech continued for {speech_duration:.2f}s, clearing playback... (energy: {np.abs(audio_data).mean():.6f}, threshold: {self.silence_threshold})")
                                session = self._get_session_from_callback()
                                if session and hasattr(session, 'clear_tts_and_playback'):
                                    session.clear_tts_and_playback()
                                    playback_cleared = True
                        
                        # Check if speech has been going on for more than max_speech_duration
                        if speech_duration >= self.max_speech_duration:
                            if not ducking_applied:
                                if self.playback and hasattr(self.playback, 'duck_volume'):
                                    self.logger.warning(f"[{get_timestamp()}] Ducking playback volume after {speech_duration:.2f}s of speech...")
                                    self.playback.duck_volume(volume_ratio=0.3, wait_time=0.5, transition_duration=0.5, fallout_duration=0.5)
                                    self.logger.warning(f"[{get_timestamp()}] Playback volume ducked due to long speech.")
                                    ducking_applied = True
                            if not self.llm_interrupt_sent:
                                self.logger.warning(f"[{get_timestamp()}] ⚠️ Speech continuing for more than {self.max_speech_duration} seconds, interrupting LLM... (energy: {np.abs(audio_data).mean():.6f}, threshold: {self.silence_threshold})")
                                self.signal_llm_interrupt()
                        
                        # For Vosk, process continuously
                        if self.engine_type == "vosk":
                            self.transcribe_audio(audio_data)
                            
                    elif self.is_speaking:
                        self.silence_duration = current_time - self.last_speech_time
                        self.audio_buffer.extend(audio_data.flatten())
                        
                        # Process after required silence duration and minimum speech duration
                        if speech_start_time:
                            speech_duration = current_time - speech_start_time
                        
                        if (self.silence_duration >= self.required_silence_duration and 
                            len(self.audio_buffer) > 0 and 
                            speech_duration >= self.min_speech_duration):
                            
                            # For Vosk, get final result
                            if self.engine_type == "vosk":
                                final_result = json.loads(self.recognizer.FinalResult())
                                final_text = final_result.get("text", "").strip()
                                if final_text:
                                    self.handle_transcribed_text(final_text, is_final=True)
                            else:
                                # For Whisper, ensure we have enough audio data
                                audio_chunk = np.array(self.audio_buffer, dtype=np.float32)
                                if len(audio_chunk) >= buffer_size:  # Ensure at least 1 second
                                    # Pad if needed to reach exact multiple of buffer_size
                                    if len(audio_chunk) % buffer_size != 0:
                                        pad_length = buffer_size - (len(audio_chunk) % buffer_size)
                                        audio_chunk = np.pad(audio_chunk, (0, pad_length), 'constant')
                                    
                                    self.logger.info(f"\n[{get_timestamp()}] Processing {len(audio_chunk)/16000:.1f}s of audio...")
                                    transcribed_text = self.transcribe_audio(audio_chunk)
                                    if transcribed_text:
                                        self.handle_transcribed_text(transcribed_text, is_final=True)
                            
                            # Reset buffers
                            self.audio_buffer = []
                            self.is_speaking = False
                            speech_start_time = None
                            speech_duration = 0
                            
                            # Restore volume to normal after processing speech
                            if ducking_applied:
                                if self.playback and hasattr(self.playback, 'duck_volume'):
                                    self.playback.duck_volume(False)
                                    ducking_applied = False
                else:
                    time.sleep(0.01)
            except Exception as e:
                self.logger.error(f"\n[{get_timestamp()}] Error in audio processing: {e}")
                import traceback
                traceback.print_exc()
                if not self.running:
                    break
                time.sleep(0.1)
        
        self.logger.info(f"[{get_timestamp()}] Audio processing thread stopped")

    def transcribe_audio(self, audio_data):
        """
        Transcribe audio data using the selected engine.
        
        Args:
            audio_data (numpy.ndarray): Audio data to transcribe
            
        Returns:
            str: Transcribed text or None if transcription failed
        """
        try:
            if self.engine_type == "whisper.cpp":
                # Ensure audio is in the correct range [-1, 1]
                if audio_data.max() > 1.0 or audio_data.min() < -1.0:
                    audio_data = np.clip(audio_data, -1.0, 1.0)
                
                segments = self.stt.transcribe(audio_data)
                for segment in segments:
                    text = segment.text.strip()
                    if text:
                        return text
                        
            elif self.engine_type == "vosk":
                audio_data = (audio_data * 32767).astype(np.int16)
                if self.recognizer.AcceptWaveform(audio_data.tobytes()):
                    result = json.loads(self.recognizer.Result())
                    text = result.get("text", "").strip()
                    if text:
                        return text
                else:
                    # Handle partial results
                    partial = json.loads(self.recognizer.PartialResult())
                    if partial.get("partial", "").strip():
                        self.handle_transcribed_text(partial["partial"], is_final=False)

        except Exception as e:
            self.logger.error(f"\n[{get_timestamp()}] Error during transcription: {e}")
        
        return None

    # ===========================================
    # 3. Transcription Handling
    # ===========================================
    def handle_transcribed_text(self, text, is_final=True):
        """
        Process transcribed text and send to the LLM callback if available.
        
        Args:
            text (str): Transcribed text to process
            is_final (bool): Whether this is a final transcription or partial
        """
        if not text:
            return
            
        if is_final:
            # Send to LLM
            self.logger.info(f"🎤 Final Transcription: {text}")
            
            # Call LLM callback the right way based on its type
            if self.llm_callback:            
                self.logger.info(f"Calling LLM callback")
                # Make sure to call the callback correctly depending on whether it's a method or function
                if hasattr(self.llm_callback, '__self__'):
                    # It's a method, call it as is
                    result = self.llm_callback(text)                    
                else:
                    # It's a function or instance with __call__, call it with text argument
                    result = self.llm_callback(text)
                
                self.logger.info(f"LLM callback result: {result}")
            else:
                self.logger.warning(f"No LLM callback registered!")
        else:
            # Only show partial transcriptions
            self.logger.info(f"🎤 (Partial) {text}")

    # ===========================================
    # 4. Playback Interaction
    # ===========================================
    def signal_llm_interrupt(self):
        """
        Signal the LLM to interrupt its current response when speech continues
        longer than max_speech_duration. Only sends interrupt once per speech segment.
        """
        if self.llm_callback and not self.llm_interrupt_sent:
            self.logger.warning(f"⚠️ Speech continuing for more than {self.max_speech_duration} seconds, interrupting LLM...")
            try:
                # Duck playback volume immediately
                if self.playback and hasattr(self.playback, 'duck_volume'):
                    self.logger.info(f"Calling playback.duck_volume for LLM interrupt...")
                    self.playback.duck_volume(volume_ratio=0.3, wait_time=2.0, transition_duration=0.5, fallout_duration=0.5)
                    self.logger.info(f"Playback volume ducked due to long speech.")
                # Then interrupt LLM
                if hasattr(self.llm_callback, 'interrupt'):
                    self.llm_callback.interrupt()
                    self.llm_interrupt_sent = True
                    self.logger.info(f"LLM interrupt signal sent")
                # Reset speech tracking
                self.speech_start_time = None
                self.speech_duration = 0
                self.ducking_applied = False
                self.playback_cleared = False
            except Exception as e:
                self.logger.error(f"Error during interruption: {e}")

    def _get_session_from_callback(self):
        """
        Extract the parent session object from the callback if available.
        This is used to access the playback system for volume control.
        
        Returns:
            object: Parent session object or None if not available
        """
        if not self.llm_callback:
            return None
            
        # Check if callback is a method of AgentSession
        if hasattr(self.llm_callback, '__self__'):
            return self.llm_callback.__self__
        return None

    def _status_reporter(self):
        while self.running and not self._status_thread_stop.is_set():
            try:
                print(f"[STT STATUS] Energy: {self.last_energy:.6f}, VAD: {self.is_speaking}")
                self.logger.info(f"[STT STATUS] Energy: {self.last_energy:.6f}, VAD: {self.is_speaking}")
            except Exception as e:
                self.logger.error(f"[STT STATUS] Error: {e}")
            self._status_thread_stop.wait(3.0)

    # ===========================================
    # 5. Audio Stream Management
    # ===========================================
    def start(self):
        """
        Start the audio input stream and processing threads.
        Sets up audio capture from the configured device and begins processing.
        """
        self.running = True
        
        # Start audio stream using device info from Playback
        try:
            # Get the number of channels from device info
            channels = self.device_info.get('channels', 1)
            
            # Ensure we have a valid number of channels
            if channels <= 0:
                channels = 1
            
            self.stream = sd.InputStream(
                device=self.device_info['index'],
                channels=channels,  # Use device's channel count
                samplerate=16000,
                dtype=np.float32,
                blocksize=16000,
                callback=self.audio_callback
            )
            self.stream.start()
            self.logger.info(f"Starting audio stream with device {self.device_info['name']} ({channels} channels)")
            
            # Start processing thread
            self.audio_thread = threading.Thread(target=self.process_audio)
            self.audio_thread.daemon = False  # Non-daemon thread to ensure proper cleanup
            self.audio_thread.start()
            
            # Start status reporter thread
            self._status_thread_stop.clear()
            self._status_thread = threading.Thread(target=self._status_reporter)
            self._status_thread.daemon = True
            self._status_thread.start()
            
        except Exception as e:
            self.logger.error(f"Error starting audio stream: {e}")
            self.stop()
            raise

    def stop(self):
        """
        Stop all audio processing and clean up resources.
        Terminates threads, closes audio streams, and clears queues and buffers.
        """
        self.logger.info(f"Stopping STT engine...")
        self.running = False
        
        # Stop and wait for the audio processing thread
        if hasattr(self, 'audio_thread') and self.audio_thread and self.audio_thread.is_alive():
            try:
                self.audio_thread.join(timeout=2.0)
            except Exception as e:
                self.logger.error(f"Error stopping audio thread: {e}")
        
        # Stop and wait for the transcription thread
        if hasattr(self, 'transcription_thread') and self.transcription_thread and self.transcription_thread.is_alive():
            try:
                self.transcription_thread.join(timeout=2.0)
            except Exception as e:
                self.logger.error(f"Error stopping transcription thread: {e}")
        
        # Stop and close the audio stream
        if hasattr(self, 'stream') and self.stream:
            try:
                self.stream.stop()
                self.stream.close()
                self.stream = None
            except Exception as e:
                self.logger.error(f"Error stopping audio stream: {e}")
            
        # Clear queues and buffers
        try:
            # Clear audio queue
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except:
                    pass
                    
            # Clear transcription queue
            while not self.transcription_queue.empty():
                try:
                    self.transcription_queue.get_nowait()
                except:
                    pass
                    
            # Clear audio buffer
            self.audio_buffer = []
            
        except Exception as e:
            self.logger.error(f"Error clearing queues: {e}")
            
        # Terminate PortAudio resources
        try:
            sd._terminate()
            self.logger.info(f"PortAudio resources terminated in STT engine")
        except Exception as e:
            pass
            
        # Stop status reporter thread
        if self._status_thread and self._status_thread.is_alive():
            self._status_thread_stop.set()
            self._status_thread.join(timeout=2.0)
            
        self.logger.info(f"STT engine stopped")

    def audio_callback(self, indata, frames, time, status):
        """
        Callback function for the audio stream.
        Receives audio data from the input device and puts it in the processing queue.
        
        Args:
            indata (numpy.ndarray): Input audio data from the device
            frames (int): Number of frames
            time (CData): Timing information
            status (CallbackFlags): Status flags
        """
        if status:
            self.logger.warning(f"Status: {status}")
        if len(indata) > 0:
            # If we have multiple channels, average them
            if indata.shape[1] > 1:
                indata = np.mean(indata, axis=1, keepdims=True)
            self.audio_queue.put(indata.copy())


