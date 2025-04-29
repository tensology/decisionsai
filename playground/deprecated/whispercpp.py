import pywhispercpp.model as pwc
import sounddevice as sd
import numpy as np
from queue import Queue
import threading
import time

class WhisperStream:
    def __init__(self, model_name="base.en"):
        # Disable progress output
        self.ctx = pwc.Model(model_name, print_progress=False)
        
        self.sample_rate = 16000
        self.block_size = 16000
        self.audio_queue = Queue()
        self.is_running = False
        self.is_speaking = False
        
        # Adjusted thresholds
        self.SILENCE_THRESHOLD = 0.003  # Very low threshold for silence
        self.VOICE_THRESHOLD = 0.005    # Slightly higher threshold for voice
        self.SILENCE_DURATION = 2.0
        
        # Select input device
        devices = sd.query_devices()
        print("\nAvailable input devices:")
        input_devices = []
        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:
                print(f"{len(input_devices)}: {device['name']}")
                input_devices.append(i)
        
        while True:
            try:
                selection = int(input("Select input device number: "))
                if 0 <= selection < len(input_devices):
                    self.device_index = input_devices[selection]
                    selected_device = devices[self.device_index]
                    print(f"\nUsing input device: {selected_device['name']}")
                    break
                else:
                    print("Invalid selection. Please try again.")
            except ValueError:
                print("Please enter a number.")
            
    def audio_callback(self, indata, frames, time, status):
        audio_data = indata.copy()
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32) / np.iinfo(audio_data.dtype).max
        self.audio_queue.put(audio_data)

    def is_silence(self, audio_data):
        # More sophisticated silence detection
        energy = np.abs(audio_data).mean()
        return energy < self.SILENCE_THRESHOLD

    def is_voice(self, audio_data):
        # Better voice detection using both energy and zero-crossing rate
        energy = np.abs(audio_data).mean()
        zero_crossings = np.sum(np.abs(np.diff(np.signbit(audio_data)))) / len(audio_data)
        return energy > self.VOICE_THRESHOLD and zero_crossings > 0.05

    def process_audio(self):
        audio_buffer = []
        silence_start = None
        consecutive_silence = 0
        
        while self.is_running:
            if not self.audio_queue.empty():
                audio_chunk = self.audio_queue.get()
                
                if audio_chunk.ndim > 1:
                    audio_chunk = audio_chunk.mean(axis=1)
                
                # If we detect voice
                if self.is_voice(audio_chunk):
                    self.is_speaking = True
                    silence_start = None
                    consecutive_silence = 0
                    audio_buffer.extend(audio_chunk)
                
                # If we detect silence and were previously speaking
                elif self.is_speaking:
                    if silence_start is None:
                        silence_start = time.time()
                    
                    audio_buffer.extend(audio_chunk)
                    consecutive_silence += len(audio_chunk) / self.sample_rate
                    
                    # If we've had enough silence
                    if consecutive_silence >= self.SILENCE_DURATION:
                        if len(audio_buffer) > self.sample_rate * 0.5:  # At least 0.5 seconds of audio
                            try:
                                audio_data = np.array(audio_buffer, dtype=np.float32)
                                if np.abs(audio_data).max() > 1.0:
                                    audio_data = audio_data / np.abs(audio_data).max()
                                
                                segments = self.ctx.transcribe(audio_data)
                                text = " ".join([segment.text.strip() for segment in segments if segment.text.strip()])
                                if text and text != "[BLANK_AUDIO]":
                                    print(f"\n{text}")
                                    print("EXECUTED")
                            except Exception as e:
                                pass
                        
                        # Reset for next utterance
                        audio_buffer = []
                        self.is_speaking = False
                        silence_start = None
                        consecutive_silence = 0
                
            time.sleep(0.01)

    def start_streaming(self):
        self.is_running = True
        process_thread = threading.Thread(target=self.process_audio)
        process_thread.start()
        
        try:
            with sd.InputStream(
                device=self.device_index,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                dtype=np.float32,
                callback=self.audio_callback
            ):
                print("\nListening... (Ctrl+C to stop)")
                while self.is_running:
                    time.sleep(0.1)
        except Exception as e:
            self.stop_streaming()

    def stop_streaming(self):
        self.is_running = False

def main():
    try:
        streamer = WhisperStream(model_name="base.en")
        streamer.start_streaming()
    except KeyboardInterrupt:
        print("\nStopping...")
        streamer.stop_streaming()
    except Exception as e:
        pass

if __name__ == "__main__":
    main()
