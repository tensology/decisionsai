from multiprocessing import Process, Queue
from queue import Queue
import numpy as np
import time
import threading
import sounddevice as sd
import tempfile
import wave
import os
import json
from ..utils import get_timestamp

# Import STT libraries
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
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class STTEngine:
    def __init__(self, device_info, engine_type="vosk", model_path=None, api_key=None, llm_callback=None):
        # Audio processing settings
        self.device_info = device_info
        self.audio_queue = Queue()
        self.transcription_queue = Queue()
        self.running = False
        self.is_speaking = False
        self.last_speech_time = None
        self.silence_duration = 0
        self.audio_buffer = []
        self.required_silence_duration = 0.8  # Required silence duration in seconds
        self.min_speech_duration = 0.5  # Minimum speech duration in seconds
        self.silence_threshold = 0.003  # Silence detection threshold
        self.max_speech_duration = 2.0  # Maximum speech duration before interrupting LLM
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
        
        # Start transcription handling thread
        self.transcription_thread = threading.Thread(target=self._handle_transcriptions)
        self.transcription_thread.daemon = False
        self.transcription_thread.start()

    def handle_transcribed_text(self, text, is_final=True):
        """Central method to handle all transcribed text"""
        if not text:
            return
            
        if is_final:
            # Send to LLM
            print(f"\n[{get_timestamp()}] 🎤 Final Transcription: {text}")
            
            # Call LLM callback the right way based on its type
            if self.llm_callback:
                print(f"[{get_timestamp()}] Calling LLM callback")
                
                # Check if the callback is a method bound to an object (AgentSession)
                if hasattr(self.llm_callback, '__self__'):
                    # Call the method on its object with the text argument
                    result = self.llm_callback(text, is_final=True)
                else:
                    # Direct function call
                    result = self.llm_callback(text)
                    
                print(f"[{get_timestamp()}] LLM callback result: {result}")
            else:
                print(f"[{get_timestamp()}] No LLM callback registered!")
        else:
            # Only show partial transcriptions
            print(f"\r[{get_timestamp()}] 🎤 (Partial) {text}", end="", flush=True)

    def signal_llm_interrupt(self):
        """Signal the LLM to interrupt its current response"""
        if self.llm_callback and not self.llm_interrupt_sent:
            print(f"\n[{get_timestamp()}] ⚠️ Speech continuing for more than 2 seconds, interrupting LLM...")
            # Call the interrupt method on the LLM callback
            if hasattr(self.llm_callback, 'interrupt'):
                self.llm_callback.interrupt()
                self.llm_interrupt_sent = True
                print(f"[{get_timestamp()}] LLM interrupt signal sent")

    def _handle_transcriptions(self):
        """Handle transcribed text from the queue and send to LLM"""
        print(f"[{get_timestamp()}] Starting transcription handling thread...")
        while self.running:
            try:
                # This method is now just a placeholder since we're calling the callback directly
                time.sleep(0.1)
            except Exception as e:
                print(f"[{get_timestamp()}] Error handling transcription: {e}")
                time.sleep(0.1)

    def _setup_stt_engine(self):
        """Initialize the selected STT engine"""
        if self.engine_type == "whisper.cpp":
            if not WHISPER_CPP_AVAILABLE:
                raise ImportError("pywhispercpp is not installed")
            model = self.model_path or "base.en"
            self.stt = pwc.Model(model, print_progress=False)
            
        elif self.engine_type == "openai":
            if not OPENAI_AVAILABLE or not self.api_key:
                raise ValueError("OpenAI API key required or openai package not installed")
            self.stt = OpenAI(api_key=self.api_key)
            
        else:  # default to vosk
            if not VOSK_AVAILABLE:
                raise ImportError("vosk is not installed")
            model_path = self.model_path or "./models/vosk-model-en-us-0.22"
            self.stt = vosk.Model(model_path)
            self.recognizer = vosk.KaldiRecognizer(self.stt, 16000)

    def start(self):
        """Start the audio stream and processing"""
        self.running = True
        
        # Start audio stream using device info from Playback
        try:
            self.stream = sd.InputStream(
                device=self.device_info['index'],
                channels=1,
                samplerate=16000,
                dtype=np.float32,
                blocksize=16000,
                callback=self.audio_callback
            )
            self.stream.start()
            print(f"\n[{get_timestamp()}] Starting audio stream with device {self.device_info['name']}")
            
            # Start processing thread
            self.audio_thread = threading.Thread(target=self.process_audio)
            self.audio_thread.daemon = False  # Changed to False
            self.audio_thread.start()
            
        except Exception as e:
            print(f"[{get_timestamp()}] Error starting audio stream: {e}")
            self.stop()
            raise

    def stop(self):
        """Stop the audio stream and processing"""
        print(f"[{get_timestamp()}] Stopping STT engine...")
        self.running = False
        
        # Stop and wait for the audio processing thread
        if self.audio_thread and self.audio_thread.is_alive():
            try:
                self.audio_thread.join(timeout=2.0)
            except Exception as e:
                print(f"[{get_timestamp()}] Error stopping audio thread: {e}")
        
        # Stop and wait for the transcription thread
        if self.transcription_thread and self.transcription_thread.is_alive():
            try:
                self.transcription_thread.join(timeout=2.0)
            except Exception as e:
                print(f"[{get_timestamp()}] Error stopping transcription thread: {e}")
        
        # Stop and close the audio stream
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
                self.stream = None
            except Exception as e:
                print(f"[{get_timestamp()}] Error stopping audio stream: {e}")
            
        # Clear queues and buffers
        try:
            while not self.audio_queue.empty():
                self.audio_queue.get_nowait()
            while not self.transcription_queue.empty():
                self.transcription_queue.get_nowait()
            self.audio_buffer = []
        except Exception as e:
            print(f"[{get_timestamp()}] Error clearing queues: {e}")
            
        print(f"[{get_timestamp()}] STT engine stopped")

    def audio_callback(self, indata, frames, time, status):
        """Callback function for the audio stream"""
        if status:
            print(f"[{get_timestamp()}] Status: {status}")
        if len(indata) > 0:
            self.audio_queue.put(indata.copy())

    def is_silence(self, audio_data):
        """Check if the audio data is silence based on energy level"""
        energy = np.abs(audio_data).mean()
        is_silent = energy < self.silence_threshold
        if is_silent and self.is_speaking:
            print(f"\r[{get_timestamp()}] Silence energy: {energy:.6f}", end="", flush=True)
        return is_silent

    def process_audio(self):
        """Process audio data from the queue and convert to text"""
        print(f"[{get_timestamp()}] Starting audio processing thread")
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
                            print(f"\n[{get_timestamp()}] Speech detected...")
                            self.llm_interrupt_sent = False  # Reset interrupt flag when new speech starts
                            speech_start_time = current_time  # Record when speech started
                            ducking_applied = False  # Reset ducking flag
                            playback_cleared = False  # Reset playback cleared flag
                            
                            # Try to get parent session to access playback
                            session = self._get_session_from_callback()
                            if session and hasattr(session, 'playback'):
                                # Apply volume ducking immediately when speech is detected
                                session.playback.set_volume(0.5)
                                ducking_applied = True
                                
                        self.is_speaking = True
                        self.last_speech_time = current_time
                        self.silence_duration = 0
                        self.audio_buffer.extend(audio_data.flatten())
                        
                        # Calculate speech duration if we have a start time
                        if speech_start_time:
                            speech_duration = current_time - speech_start_time
                            
                            # If speech has continued for at least 0.5 seconds, clear the playback
                            if (speech_duration >= 0.5 and 
                                not playback_cleared):
                                print(f"\n[{get_timestamp()}] Speech continued for {speech_duration:.2f}s, clearing playback...")
                                session = self._get_session_from_callback()
                                if session and hasattr(session, 'clear_tts_and_playback'):
                                    session.clear_tts_and_playback()
                                    playback_cleared = True
                        
                        # Check if speech has been going on for more than max_speech_duration
                        if speech_duration >= self.max_speech_duration and not self.llm_interrupt_sent:
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
                                    
                                    print(f"\n[{get_timestamp()}] Processing {len(audio_chunk)/16000:.1f}s of audio...")
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
                                session = self._get_session_from_callback()
                                if session and hasattr(session, 'playback'):
                                    session.playback.set_volume(1.0)
                                    ducking_applied = False
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"\n[{get_timestamp()}] Error in audio processing: {e}")
                import traceback
                traceback.print_exc()
                if not self.running:
                    break
                time.sleep(0.1)
        
        print(f"[{get_timestamp()}] Audio processing thread stopped")
        
    def _get_session_from_callback(self):
        """Helper method to get the parent session from callback"""
        if not self.llm_callback:
            return None
            
        # Check if callback is a method of AgentSession
        if hasattr(self.llm_callback, '__self__'):
            return self.llm_callback.__self__
        return None

    def transcribe_audio(self, audio_data):
        """Transcribe audio using the selected engine"""
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
                        
            elif self.engine_type == "openai":
                # Save audio data to a temporary file
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                    with wave.open(temp_file.name, 'wb') as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(16000)
                        wf.writeframes((audio_data * 32767).astype(np.int16).tobytes())
                    
                    # Transcribe using OpenAI
                    with open(temp_file.name, 'rb') as audio_file:
                        response = self.stt.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_file
                        )
                        os.unlink(temp_file.name)
                        text = response.text
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
            print(f"\n[{get_timestamp()}] Error during transcription: {e}")
        
        return None
