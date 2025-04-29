"""
This script implements a continuous speech recognition system using the Vosk library.
It initializes a speech recognition model, sets up audio input, and processes speech
in real-time.

Key components:
1. Model initialization
2. Continuous audio listening
3. Speech recognition
5. Action handling for recognized speech
"""
from distr.core.constants import MODELS_DIR, DEFAULT_SILENCE_TIMER, TMP_DIR, WHISPER_MODEL_PATH
from distr.core.utils import load_settings_from_db, load_actions_config
from distr.core.signals import signal_manager 
from distr.core.constants import TMP_DIR
from PyQt6 import QtCore
import numpy as np
import pyaudio
import logging
import queue
import json
import vosk
import time
import hashlib
import wave
import os
import importlib
import whisper
import threading
from typing import Set


# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Set Vosk logger to WARNING to suppress its debug messages
vosk_logger = logging.getLogger("vosk")
vosk_logger.setLevel(logging.WARNING)

# Global variables for the model and recognizer
model = None
recognizer = None

def initialize_model():
    global model, recognizer
    if model is None:
        # model_path = os.path.join(MODELS_DIR, "vosk-model-small-en-us-0.15") # Using a smaller model  
        model_path = os.path.join(MODELS_DIR, "vosk-model-en-us-0.22")  # Using a larger model
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found at {model_path}")
        
        model = vosk.Model(model_path)        
        # Kaldi is an open-source speech recognition toolkit used by Vosk
        # It provides the core speech recognition functionality
        
        # Options for speech recognition:
        # 1. vosk.KaldiRecognizer: Default Kaldi-based recognizer (current choice)
        # 2. speech_recognition library with various backends:
        #    - recognizer = speech_recognition.Recognizer()
        # 3. DeepSpeech:
        #    - model = deepspeech.Model("path_to_model")
        #    - recognizer = model.createStream()
        # 4. wav2vec:
        #    - processor = Wav2Vec2Processor.from_pretrained("facebook/wav2vec2-base-960h")
        #    - model = Wav2Vec2ForCTC.from_pretrained("facebook/wav2vec2-base-960h")
        
        recognizer = vosk.KaldiRecognizer(model, 16000)
        # - vosk.KaldiRecognizer: Creates a new speech recognizer object
        # - model: The acoustic model loaded earlier, used for speech recognition
        # - 16000: The sample rate of the audio input in Hz (16 kHz)
        # - 8000: 8 kHz, suitable for telephone-quality audio
        # - 22050: 22.05 kHz, often used for lower-quality music
        # - 44100: 44.1 kHz, CD-quality audio
        # - 48000: 48 kHz, commonly used in professional audio equipment

        # This recognizer will be used to convert audio input into text,
        # allowing the system to understand and process spoken commands or speech.

        recognizer.SetWords(True)  # Enable word timings
        # Word timings provide the start and end time for each word in the transcription.
        # This allows for precise alignment of the transcribed text with the audio,
        # which can be useful for features like highlighting words as they are spoken
        # or for accurate audio segmentation based on speech content.

        recognizer.SetPartialWords(True)  # Enable partial results
        # This setting allows the recognizer to return partial transcriptions
        # as it processes the audio in real-time. It's useful for providing
        # immediate feedback to the user, enabling features like live
        # transcription display or early trigger word detection. However,
        # these partial results may be less accurate than the final result.
        print("ASR Model loaded successfully.")

        recognizer.SetPartialWords(True)  # Enable partial results


class ContinuousListener(QtCore.QThread):
    def vad(self, audio_data, threshold=0.03): 
        energy = np.abs(np.frombuffer(audio_data, dtype=np.int16)).mean()
        return energy > threshold

    def __init__(self, action_handler, chat_manager):

        super().__init__()
        self.config = {}

        self.chat_manager = chat_manager
        self.action_handler = action_handler
        self.audio = pyaudio.PyAudio()

        # Start loading Whisper model in the background
        self.whisper_model = None
        self.whisper_buffer = []
        self.whisper_model_ready = threading.Event()    
        # self.load_whisper_model()

        self.stream = None
        self.frames = []
        self.running = True

        self.is_listening = True
        self.is_transcribing = False
        self.is_speaking = False

        self.exit_words = set()
        self.stop_listening_words = set()
        self.start_listening_words = set()
        self.filler_words = set()

        self.transcription_stream = None
        self.transcription_frames = []

        self.transcription_buffer = []

        self.audio_queue = queue.Queue()

        self.action = {}
        self.previous_action = {}

        self.silence_timer = DEFAULT_SILENCE_TIMER
        self.silence_start_time = None
        self.last_speech_time = None

        self.audio_filename = None
        self.source_language = None
        self.transcription = None

        # connect signals
        signal_manager.voice_set_action.connect(self.set_action)

        signal_manager.voice_set_is_transcribing.connect(self.set_is_transcribing)
        signal_manager.voice_set_is_listening.connect(self.set_is_listening)
        signal_manager.voice_set_is_speaking.connect(self.set_is_speaking)

        signal_manager.voice_set_transcription_buffer.connect(self.set_transcription_buffer)

        signal_manager.voice_stop_speaking.connect(self.stop_speaking)

        signal_manager.voice_start_transcribing.connect(self.start_transcribing)
        signal_manager.voice_stop_transcribing.connect(self.stop_transcribing)

        signal_manager.voice_update_last_speech_time.connect(self.update_last_speech_time)

        # Add these debug variables
        self.debug_counter = 0
        self.last_debug_time = time.time()

        self.filler_words: Set[str] = set(self.config.get("filler_words", []))


    def set_transcription_buffer(self, buffer):
        self.transcription_buffer += buffer

    def set_action(self, action):
        self.action = action


    def set_is_transcribing(self, value):
        self.is_transcribing = value

    def set_is_listening(self, value):
        self.is_listening = value

    def set_is_speaking(self, value):
        self.is_speaking = value


    def run(self):
        print("ContinuousListener.run() method called")
        self.get_config()
        initialize_model()            
        self.start_continuous_stream()        
        print("Listening... Just speak")                
        while self.running:
            try:
                if self.stream and self.stream.is_active():
                    audio_data = self.stream.read(512, exception_on_overflow=False)
                    time_since_last_speech = self.get_time_since_last_speech(audio_data)
                    if len(audio_data) > 0:
                        try:
                            if recognizer.AcceptWaveform(audio_data):
                                result = json.loads(recognizer.Result())
                                if result.get("text"):
                                    print("result:", result["text"])
                                    self.process_speech(result["text"], time_since_last_speech=time_since_last_speech)
                                # else:
                                    # print("Empty audio data received")
                                    # pass
                        except IOError as e:
                            if e.errno == pyaudio.paInputOverflowed:
                                print("Input overflowed, ignoring")
                            else:
                                print(f"IOError: {e}")
                        except Exception as e:
                            print(f"Error processing audio: {e}")
                else:
                    print("Stream is not active or not initialized")
                    time.sleep(0.1)
            except Exception as e:
                print(f"Error in run method: {str(e)}")
                logger.error(f"Error in run method: {str(e)}", exc_info=True)
                time.sleep(0.1)


    def stop_speaking(self):
        self.is_speaking = False
        signal_manager.action_set_is_speaking.emit(False)
        signal_manager.reset_voice_box.emit()
        signal_manager.stop_sound_player.emit()


    def start_continuous_stream(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=1024
        )
        print("Continuous listening stream started")


    def process_continuous_audio(self, audio_data):
        if recognizer.AcceptWaveform(audio_data):
            result = json.loads(recognizer.Result())
            if result["text"]:
                self.action_handler.process_speech(result["text"])


    def audio_callback(self, in_data, frame_count, time_info, status):
        current_time = time.time()
        data_length = len(in_data)
        print(f"Audio callback at {current_time:.2f}: {data_length} bytes, frame_count: {frame_count}")
        if status:
            print(f"Status: {status}")
        if data_length > 0:
            try:
                self.audio_queue.put_nowait(in_data)
                print(f"Put audio data in queue: {data_length} bytes")
                self.check_audio_levels(in_data)
            except queue.Full:
                print("Queue is full, discarding audio data")
        else:
            print("Warning: Received empty audio data")
        return (in_data, pyaudio.paContinue)  # Changed from (None, pyaudio.paContinue)

    def get_config(self):
        if self.config == {}:
            self.config = load_settings_from_db()
            self.startup_listening_state = self.config.get("startup_listening_state", "remember")
            self.last_listening_state = self.config.get("last_listening_state", True)

            if self.startup_listening_state == "remember":
                self.is_listening = self.last_listening_state
            elif self.startup_listening_state == "stop":
                self.is_listening = False
            elif self.startup_listening_state == "start":
                self.is_listening = True

            self.action_config = load_actions_config()
            self.exit_words = set(self.action_config.get("exit_words", []))
            self.stop_listening_words = set(self.action_config.get("stop_listening", []))
            self.start_listening_words = set(self.action_config.get("start_listening", []))                        
            self.filler_words = set(self.action_config.get("filler_words", []))
        return self.config

    def clean_speech(self, speech):
        # Split into words and convert to lowercase
        if speech:
            words = str(speech.strip().lower()).split()
            if not words:
                return None

        # Remove consecutive duplicate words
        words = [words[i] for i in range(len(words)) 
                if i == 0 or words[i] != words[i-1]]

        # Initialize cleaned words list
        cleaned_words = []
        
        for i, word in enumerate(words):
            # Skip if it's a filler word AND:
            # 1. It's at the start/end of the sentence OR
            # 2. It's surrounded by other filler words
            if word in self.filler_words:
                prev_word = words[i-1] if i > 0 else None
                next_word = words[i+1] if i < len(words)-1 else None
                
                # Skip if it's at the edges or surrounded by filler words
                if (i == 0 or i == len(words)-1 or 
                    (prev_word in self.filler_words and next_word in self.filler_words)):
                    continue
                
                # Skip if it's part of a repeated sequence
                if (prev_word == word or next_word == word):
                    continue
            
            cleaned_words.append(word)
        
        cleaned_speech = ' '.join(cleaned_words)
        
        if cleaned_speech:
            return cleaned_speech
        else:
            print(f"Speech consisted only of filler words: {speech}")
            return None
        

    def process_speech(self, speech: str, time_since_last_speech: float) -> None:
        cleaned_speech = self.clean_speech(speech)
        if cleaned_speech:
            self.update_last_speech_time()

        print("time_since_last_speech:", time_since_last_speech)
        print("cleaned_speech:", cleaned_speech)        
        print("is_listening:", self.is_listening)
        if not self.is_speaking:

            if self.is_listening:
                print("is_transcribing:", self.is_transcribing)
                
                #first check if the user wants to exit the app
                if not self.is_transcribing:
                    if cleaned_speech in self.exit_words:
                        signal_manager.exit_app.emit()

                    if cleaned_speech in self.stop_listening_words:
                        self.is_listening = False
                        signal_manager.disable_tray.emit()
                        signal_manager.voice_set_is_listening.emit(False)                    
                        signal_manager.action_set_is_listening.emit(False)
                        return

                    if self.action_handler:    
                        self.action_handler.process_speech(self.chat_manager, cleaned_speech)
                else:
                    if cleaned_speech in self.confirm_words:
                        self.is_transcribing = False
                        signal_manager.disable_tray.emit()
                        signal_manager.voice_set_is_transcribing.emit(False)                    
                        signal_manager.action_set_is_transcribing.emit(False)
                        return
                    self.transcription_buffer.append(cleaned_speech)
            else:
                if cleaned_speech in self.exit_words:
                    signal_manager.exit_app.emit()

                if cleaned_speech in self.start_listening_words:
                    self.is_listening = True
                    print("started listening again")
                    signal_manager.action_set_is_listening.emit(True)
                    signal_manager.voice_set_is_listening.emit(True)
                    signal_manager.enable_tray.emit()
                    return
        else:
            print("DECISIONS IS SPEAKING")
            return 

    def get_time_since_last_speech(self, audio_data):
        if self.last_speech_time is not None:
            current_time = time.time()
            time_since_last_speech = current_time - self.last_speech_time
            # print(f"Time since last speech: {time_since_last_speech:.2f} seconds")
            return time_since_last_speech
        else:
            return None


    def update_last_speech_time(self):        
        timestamp = time.time()
        self.last_speech_time = timestamp
        self.silence_start_time = None
        signal_manager.action_update_last_speech_time.emit()


    def update_silence_timer(self):
        if self.action:
            if isinstance(self.action, dict) and isinstance(self.action.get("end"), dict):
                silence_config = self.action["end"].get("silence", {})
                if isinstance(silence_config, dict):
                    self.current_silence_timer = silence_config.get("timer", DEFAULT_SILENCE_TIMER)
                elif isinstance(silence_config, bool):
                    self.current_silence_timer = DEFAULT_SILENCE_TIMER if silence_config else None
            else:
                self.current_silence_timer = DEFAULT_SILENCE_TIMER            
            print(f"Updated silence timer to {self.current_silence_timer} seconds")


    def audio_callback(self, in_data, frame_count, time_info, status):
        current_time = time.time()
        data_length = len(in_data)
        print(f"Audio callback at {current_time:.2f}: {data_length} bytes, frame_count: {frame_count}")
        if status:
            print(f"Status: {status}")
        if data_length > 0:
            try:
                self.audio_queue.put_nowait(in_data)
                print(f"Put audio data in queue: {data_length} bytes")
                self.check_audio_levels(in_data)
            except queue.Full:
                print("Queue is full, discarding audio data")
        else:
            print("Warning: Received empty audio data")
        return (in_data, pyaudio.paContinue)  # Changed from (None, pyaudio.paContinue)


    def start_transcribing(self):
        signal_manager.set_oracle_green.emit()
        if self.is_transcribing:
            print("Transcription already in progress")
            return
        
        self.transcription_frames = []
        self.transcription_stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=44100,
            input=True,
            frames_per_buffer=1024,
            stream_callback=self.transcription_callback
        )
        self.transcription_stream.start_stream()

        self.is_transcribing = True
        signal_manager.action_set_is_transcribing.emit(True)
        
        print("Transcription stream started")


    def transcription_callback(self, in_data, frame_count, time_info, status):
        self.transcription_frames.append(in_data)
        return (in_data, pyaudio.paContinue)
    

    def stop_transcribing(self, cut=False):
        if not self.is_transcribing:
            return
        
        if self.transcription_stream:
            self.transcription_stream.stop_stream()
            self.transcription_stream.close()
            self.transcription_stream = None

        if cut:
            self.transcription_frames = []
            self.transcription_buffer = []
            self.update_action_variables()
            return

        # Generate MD5 hash for the filename
        timestamp = int(time.time())
        filename = f"audio_{timestamp}.wav"
        md5_hash = hashlib.md5(filename.encode()).hexdigest()
        self.audio_filename = os.path.join(TMP_DIR, f"{md5_hash}.wav")

        # Save the recorded audio to a file
        print("Saving audio file:", self.audio_filename)
        wf = wave.open(self.audio_filename, 'wb')
        wf.setnchannels(1)
        wf.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
        wf.setframerate(44100)
        wf.writeframes(b''.join(self.transcription_frames))
        wf.close()

        # Reset transcription_frames after saving
        self.transcription_frames = []

        print("action:", self.action)
        print("previous_action:", self.previous_action)
        print("Transcription stopped and saved to file")

        print(f"Transcribing audio file: {self.audio_filename}")
        print(f"Captured Speech Buffer: {self.transcription_buffer}")

        self.is_transcribing = False
        signal_manager.action_set_is_transcribing.emit(False)

        translate = self.action.get('params', {}).get("method") == 'translate'
        if translate:
            # result = whisper_model.transcribe(audio_file, task="translate")
            print("SOURCE LANGUAGE:", source_language)
            print("TRANSCRIPTION:'n", transcription)
        else:
            whisper_model = self.get_whisper_model()  # This will wait if the model is not yet loaded
            result = whisper_model.transcribe(str(self.audio_filename), task="transcribe")
            source_language = result["language"]
            transcription = result["text"]

            print("TRANSCRIPTION:\n", transcription)

        self.execute_action({
            "text": " ".join(self.transcription_buffer),
            'transcription': transcription,
            'trigger_sentence': self.transcription_buffer,
            'audio_file': self.audio_filename
        })

        # print("deleting audio file:", self.audio_filename)
        self.update_action_variables()
        os.remove(str(self.audio_filename))

    def update_action_variables(self):
        self.previous_action = self.action
        self.action = {}
        signal_manager.action_set_action.emit(self.action)

    def execute_action(self, data):       
        print(f"Full Speech Sent to Transcription: {self.transcription_buffer}")
        
        print(f"Executing action: {self.action.get('trigger', 'Unknown action')}")
        method = self.action.get('method')
        if method:
            module_name, function_name = method.rsplit('.', 1)
            module = importlib.import_module(f"distr.actions.{module_name}")
            function = getattr(module, function_name)
            function(self.chat_manager, self.action, data)
        else:
            self.logger.warning(f"No method specified for action: {self.trigger_phrase}")
            return False


    def load_whisper_model(self):
        print("Starting to load Whisper model...")
        try:
            self.whisper_model = whisper.load_model(WHISPER_MODEL_PATH)
            print("Whisper model (base.en) loaded successfully")
            self.whisper_model_ready.set()
        except Exception as e:
            print(f"Error loading Whisper model: {str(e)}")
        print("Whisper model initialization complete")


    def get_whisper_model(self):
        self.whisper_model_ready.wait() 
        return self.whisper_model

    def stop(self):
        self.running = False
        self.wait()

    def __del__(self):
        try:
            if hasattr(self, 'audio') and self.audio:
                self.audio.terminate()
            if hasattr(self, 'stream') and self.stream:
                self.stream.stop_stream()
                self.stream.close()
        except:
            pass  # Ignore any errors during cleanup


    def check_audio_devices(self):
        p = pyaudio.PyAudio()
        info = p.get_host_api_info_by_index(0)
        num_devices = info.get('deviceCount')
        for i in range(0, num_devices):
            if (p.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels')) > 0:
                print(f"Input Device id {i} - {p.get_device_info_by_host_api_device_index(0, i).get('name')}")
        p.terminate()

    def check_pyaudio_version(self):
        print(f"PyAudio version: {pyaudio.__version__}")

    def check_stream_status(self):
        if self.stream:
            print(f"Stream is {'active' if self.stream.is_active() else 'inactive'}")
            print(f"Stream time: {self.stream.get_time()}")
            print(f"Stream CPU load: {self.stream.get_cpu_load()}")
        else:
            print("Stream is not initialized")

    def test_microphone(self):
        print("Testing microphone...")
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        RECORD_SECONDS = 5

        p = pyaudio.PyAudio()

        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)

        print("* recording")

        frames = []

        for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
            data = stream.read(CHUNK)
            frames.append(data)
            print(f"Recorded chunk: {len(data)} bytes")

        print("* done recording")

        stream.stop_stream()
        stream.close()
        p.terminate()

        print(f"Recorded {len(frames)} chunks of audio data")

    def check_recognizer(self):
        if recognizer is None:
            print("Error: Recognizer is not initialized")
        else:
            print("Recognizer is initialized")

    def check_audio_levels(self, data):
        audio_data = np.frombuffer(data, dtype=np.int16)
        rms = np.sqrt(np.mean(np.square(audio_data)))
        peak = np.max(np.abs(audio_data))
        print(f"Audio level - RMS: {rms:.2f}, Peak: {peak}")
        if rms > 500:  # Adjust this threshold as needed
            print("Speech detected!")

    def check_microphone(self):
        print("Checking microphone...")
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        RECORD_SECONDS = 5

        p = pyaudio.PyAudio()

        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)

        print("* recording")

        frames = []

        for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
            data = stream.read(CHUNK)
            frames.append(data)
            print(f"Recorded chunk: {len(data)} bytes")

        print("* done recording")

        stream.stop_stream()
        stream.close()
        p.terminate()

        print(f"Recorded {len(frames)} chunks of audio data")

        # Save the recorded audio to a file
        wf = wave.open("microphone_test.wav", 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()

        print("Saved recorded audio to microphone_test.wav")
