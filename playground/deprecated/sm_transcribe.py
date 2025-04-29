import scripts.sflow as sflow
import pyaudio
import wave
import io

class MicrophoneStream:
    def __init__(self, chunk=1024):
        self.chunk = chunk
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=self.chunk,
        )
        
        # Create a wave header
        self.wav_header = io.BytesIO()
        with wave.open(self.wav_header, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit audio
            wav.setframerate(16000)
            wav.writeframes(b'')  # Write empty frames to generate header
        self.header_written = False

    def read(self, size):
        # First send the WAV header
        if not self.header_written:
            self.header_written = True
            return self.wav_header.getvalue()
        
        # Then send the audio data
        return self.stream.read(size, exception_on_overflow=False)

    def close(self):
        self.stream.stop_stream()
        self.stream.close()
        self.p.terminate()

def main():
    API_KEY = "<your-api-key>"
    CONNECTION_URL = "wss://eu2.rt.speechmatics.com/v2"

    # Create a transcription client
    ws = sflow.client.WebsocketClient(
        sflow.models.ConnectionSettings(
            url=CONNECTION_URL,
            auth_token=API_KEY,
        )
    )

    def print_transcript(msg):
        try:
            # For partial results
            if msg.get('message') == 'AddPartialTranscript':
                metadata = msg.get('metadata', {})
                transcript = metadata.get('transcript', '')
                # print(f"[Partial] {transcript}")
            
            # For final results
            elif msg.get('message') == 'AddTranscript':
                metadata = msg.get('metadata', {})
                transcript = metadata.get('transcript', '')
                speaker = metadata.get('speaker', '')
                if speaker:
                    print(f"[Speaker {speaker}] {transcript}")
                else:
                    if transcript.strip():
                        print(f"[Final] {transcript}")

        except Exception as e:
            print(f"Debug - Message structure: {msg}")
            print(f"Error processing message: {e}")

    # Register both partial and final transcript handlers
    ws.add_event_handler(
        event_name=sflow.models.ServerMessageType.AddPartialTranscript,
        event_handler=print_transcript,
    )
    ws.add_event_handler(
        event_name=sflow.models.ServerMessageType.AddTranscript,
        event_handler=print_transcript,
    )

    # Audio settings
    settings = sflow.models.AudioSettings()

    # Transcription config with diarization enabled
    conf = sflow.models.TranscriptionConfig(
        language="en",
        operating_point="enhanced",
        diarization="speaker",
        enable_partials=True,
        max_delay=5,
        punctuation_overrides={"permitted_marks": [".", "?", "!", ","]},
        enable_entities=True
    )

    # Create microphone stream
    mic_stream = MicrophoneStream()

    print("Starting transcription (press Ctrl-C to stop):")
    try:
        ws.run_synchronously(mic_stream, conf, settings)
    except KeyboardInterrupt:
        print("\nTranscription stopped.")
    finally:
        mic_stream.close()

if __name__ == "__main__":
    main()
