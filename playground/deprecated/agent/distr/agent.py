from .stt import STTEngine
from .llm import LLMEngine
from .tts import TTSEngine
from .utils import get_timestamp

class Agent:
    def __init__(self):
        self.stt_engine = STTEngine()
        self.llm_engine = LLMEngine()
        self.tts_engine = TTSEngine()
        self.running = False
        
    def start(self):
        """Start all engines"""
        self.running = True
        
        # Start STT engine
        self.stt_engine.start()
        
        # Start LLM engine
        self.llm_engine.start()
        
        # Start TTS engine
        self.tts_engine.start()
        
        # Connect LLM output to TTS input
        self.tts_engine.set_input_queue(self.llm_engine.get_tts_queue())
        
        print(f"[{get_timestamp()}] Agent started")
        
    def stop(self):
        """Stop all engines"""
        self.running = False
        
        # Stop engines in reverse order
        self.tts_engine.stop()
        self.llm_engine.stop()
        self.stt_engine.stop()
        
        print(f"[{get_timestamp()}] Agent stopped")
        
    def process_audio(self, audio_data):
        """Process audio data through the pipeline"""
        if not self.running:
            return False
            
        # Send audio to STT
        if not self.stt_engine.process_audio(audio_data):
            return False
            
        # Get transcription from STT
        transcription = self.stt_engine.get_result()
        if not transcription:
            return False
            
        # Send transcription to LLM
        if not self.llm_engine.process_text(transcription["text"]):
            return False
            
        # Get response from LLM
        response = self.llm_engine.get_result()
        if not response:
            return False
            
        # TTS will automatically process the response from its input queue
        
        return True
        
    def get_playlist(self):
        """Get the current TTS playlist"""
        return self.tts_engine.get_playlist() 