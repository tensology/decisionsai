from .stt import STTEngine
from .llm import LLMEngine
from .tts import TTSEngine
from .playback import Playback
import signal
import sys
import time
import threading
from .utils import get_timestamp
import os
import glob
import json

class AgentSession:
    def __init__(self, agent_name, input_device=None, *args, **kwargs):
        print(f"\n[{get_timestamp()}] Initializing {agent_name} session...")
        
        # Initialize playback and get device info
        self.playback = Playback(input_device)
        self.device_info = self.playback.get_device_info()
        self.agent_name = agent_name

        # Initialize TTS engine first        
        ELEVENLABS_API_KEY = "sk_1a1f5f6826f2fd3c5e02279bcfbde22a193e0537572eecbe"
        
        # Check for role.txt in the agent directory
        agent_dir = f"./agents/{agent_name}"
        role_path = f"{agent_dir}/role.txt"
        voice_samples_dir = f"{agent_dir}/voice_samples"
        config_path = f"{agent_dir}/config.json"
        
        # Load voice configuration if available
        voice_settings = {
            "stability": 0.3,
            "similarity_boost": 1,
        }
        
        package = "elevenlabs"
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    if 'tts' in config:
                        package = config['tts']
                    if 'voice_settings' in config:
                        voice_settings = config['voice_settings']
            except Exception as e:
                print(f"[{get_timestamp()}] Error loading config: {e}")
        
        # Load role from file if it exists
        if os.path.exists(role_path):
            try:
                with open(role_path, 'r') as f:
                    role_text = f.read()
                print(f"[{get_timestamp()}] Loaded role from {role_path}")
                role = (role_text)
            except Exception as e:
                print(f"[{get_timestamp()}] Error loading role file: {e}")
                # Fallback to default role
                role = (f"""You are {agent_name}, an AI assistant.""")
        else:
            print(f"[{get_timestamp()}] No role file found at {role_path}, using default role")
            role = (f"""You are {agent_name}, an AI assistant.""")
        
        # Get voice samples from the voice_samples directory
        voice_samples = []
        if os.path.exists(voice_samples_dir):
            print(f"[{get_timestamp()}] Loading voice samples from {voice_samples_dir}")
            # Get all mp3 files in the voice_samples directory
            voice_samples = glob.glob(f"{voice_samples_dir}/*.mp3")
            if voice_samples:
                print(f"[{get_timestamp()}] Found {len(voice_samples)} voice samples")
            else:
                print(f"[{get_timestamp()}] No voice samples found in {voice_samples_dir}")
        else:
            print(f"[{get_timestamp()}] Voice samples directory not found: {voice_samples_dir}")
        
        # Initialize TTS engine without setting stt_engine to avoid circular reference
        self.tts_engine = TTSEngine(
            package=package, 
            api_key=ELEVENLABS_API_KEY, 
            voice_name=agent_name,
            clone_samples=voice_samples,
            voice_settings=voice_settings
        )

        self.llm_engine = LLMEngine(role=role)
        
        self.running = False
        self._stop_event = threading.Event()
        self._last_clear_time = 0  # Track the last time we cleared TTS and playback
        
        # Initialize STT with LLM callback
        self.stt_engine = STTEngine(
            engine_type="whisper.cpp",
            device_info=self.device_info,
            llm_callback=self.handle_transcription
        )

        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def handle_transcription(self, text, is_final=True):
        """Central method to handle transcriptions before sending to LLM"""
        if text and text.strip():
            print(f"\n[{get_timestamp()}] 🔄 Session received transcription: {text}")
            print(f"[{get_timestamp()}] LLM engine running: {self.llm_engine.running}, process alive: {self.llm_engine.process.is_alive() if self.llm_engine.process else False}")
            result = self.llm_engine.process_text(text)
            print(f"[{get_timestamp()}] LLM process_text result: {result}")
            return result
        return False

    def _signal_handler(self, sig, frame):
        print(f'\n[{get_timestamp()}] Gracefully shutting down...')
        self._stop_event.set()
        self.stop()
        # Give processes time to cleanup
        time.sleep(0.5)
        print(f"[{get_timestamp()}] Thank you for using DecisionsAI! We built this out of love for the community.")
        sys.exit(0)

    def start(self):
        """Start all processing engines concurrently"""
        print(f"[{get_timestamp()}] Starting {self.agent_name} session...")
        print(f"[{get_timestamp()}] Using input device: {self.device_info['name']}")
        self.running = True
        
        # Start all engines
        try:
            self.llm_engine.start()  # Start LLM first
            time.sleep(0.1)  # Small delay to ensure LLM is ready
            self.stt_engine.start()  # Then STT
            self.tts_engine.start()  # Then TTS
            
            # Send a welcome message to start the conversation
            time.sleep(1.0)  # Allow engines to fully initialize - longer delay to ensure all systems are ready
            print(f"[{get_timestamp()}] Sending welcome message from {self.agent_name}")
            
            # Send the welcome message
            self.llm_engine.send_welcome_message()
            
            # Actively check for and add TTS files to ensure welcome message is played
            print(f"[{get_timestamp()}] Beginning periodic checks for welcome message TTS files")
            self._check_and_add_tts_files(max_checks=15, delay=0.2)
            
        except Exception as e:
            print(f"[{get_timestamp()}] Error starting engines: {e}")
            self.stop()
            sys.exit(1)
        
        # Keep the session running and process LLM results
        try:
            last_check_time = time.time()
            check_interval = 0.5  # Check for new TTS files every 0.5 seconds
            
            while not self._stop_event.is_set():
                # Check for signals from LLM engine
                try:
                    if not self.llm_engine.signal_queue.empty():
                        signal_data = self.llm_engine.signal_queue.get_nowait()
                        if signal_data.get("action") == "clear_tts_and_playback":
                            self.clear_tts_and_playback()
                except Exception as e:
                    print(f"[{get_timestamp()}] Error processing LLM signal: {e}")
                
                # Process LLM results
                result = self.llm_engine.get_result()
                if result:
                    # Handle different types of LLM responses
                    if result.get('status') == 'sentence':
                        # This is a sentence fragment from streaming - send directly to TTS
                        sentence_text = result.get('text', '')
                        if sentence_text:
                            # Generate TTS for this sentence
                            audio_id = self.tts_engine.generate(sentence_text)
                            # Use the improved method to check for and add new files
                            self.playback.check_and_add_new_files(self.tts_engine)
                                
                    elif result.get('status') == 'success':
                        # This is the complete response
                        # No need to do anything as sentences were already processed
                        pass
                    elif result.get('status') == 'error':
                        error_msg = result.get('error', 'Unknown error')
                        print(f"\n[{get_timestamp()}] ❌ LLM Error: {error_msg}")
                    elif result.get('status') == 'interrupted':
                        print(f"\n[{get_timestamp()}] ⚠️ LLM Response interrupted")
                
                # Periodically check for new TTS files that might have been missed
                current_time = time.time()
                if current_time - last_check_time >= check_interval:
                    self.tts_engine._update_playlist_from_results()
                    self.playback.check_and_add_new_files(self.tts_engine)
                    last_check_time = current_time
                    
                time.sleep(0.1)
        except KeyboardInterrupt:
            self._signal_handler(signal.SIGINT, None)

    def stop(self):
        """Stop all processing engines"""
        print(f"[{get_timestamp()}] Stopping {self.agent_name} session...")
        self.running = False
        
        # Stop engines in reverse order
        try:
            self.tts_engine.stop()
            self.stt_engine.stop()
            self.llm_engine.stop()
        except Exception as e:
            print(f"[{get_timestamp()}] Error during shutdown: {e}")

    def get_voice_config(self):
        return {}

    def clear_tts_and_playback(self):
        """Clear TTS queue and playback"""
        current_time = time.time()
        
        # Only clear if it's been at least 1 second since last clear to prevent rapid consecutive clears
        if current_time - self._last_clear_time < 1.0:
            print(f"[{get_timestamp()}] Skipping clear operation - too soon after previous clear")
            return
            
        # Update last clear time
        self._last_clear_time = current_time
        
        # Clear TTS queue and temp files
        self.tts_engine.clear_queue_and_temp_files()
        
        # Clear playback playlist and stop audio
        # print(f"[{get_timestamp()}] Clearing playback playlist and stopping audio...")
        self.playback.stop_playback()
        self.playback.clear_playlist()

    def _check_and_add_tts_files(self, max_checks=10, delay=0.2):
        """Helper method to periodically check and add TTS files to playback"""
        for i in range(max_checks):
            # Force update from results
            self.tts_engine._update_playlist_from_results()
            
            # Check and add files
            files_added = self.playback.check_and_add_new_files(self.tts_engine)
            
            if files_added > 0:
                print(f"[{get_timestamp()}] Added {files_added} TTS files to playback during check {i+1}")
            
            # Short delay between checks
            time.sleep(delay)