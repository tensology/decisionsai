from .utils import get_timestamp

from .playback import Playback
from .stt import STTEngine
from .llm import LLMEngine
from .tts import TTSEngine
import sounddevice as sd
import threading
import logging
import signal
import time
import json
import os


class AgentSession:
    def __init__(self, 
                 input_device=None, output_device=None,
                 stt_engine="whisper.cpp", stt_api_key=None, 
                 llm_engine="ollama", llm_model="gemma3:4b", llm_api_key=None, 
                 tts_engine="kokoro", tts_api_key=None,
                 agent_name="Heart",
                 silence_threshold=0.03,
                 set_signal_handlers=True, 
                 settings=None, *args, **kwargs):
        """
        Initialize the AgentSession with the given agent name and input/output device.
        
        Args:
            input_device (str, optional): The name of the input device to use
            output_device (str, optional): The name of the output device to use
            agent_name (str): The name of the agent
            silence_threshold (float, optional): Energy threshold for silence detection
            set_signal_handlers (bool, optional): Whether to set signal handlers
            *args: Additional arguments
            **kwargs: Additional keyword arguments
        """

        self.logger = logging.getLogger(__name__)

        self.running = False
        self._stop_event = threading.Event()
        self._last_clear_time = 0  # Track the last time we cleared TTS and playback

        self.logger.info(f"\n[{get_timestamp()}] Initializing {agent_name} session...")

        self.agent_name = agent_name

        
        # Get input device info
        devices = sd.query_devices()
        input_device_info = None
        
                # If input device is specified, find it
        if input_device:
            for i, device in enumerate(devices):
                if device['name'] == input_device and device['max_input_channels'] > 0:
                    input_device_info = {
                        'index': i,
                        'name': device['name'],
                        'channels': device['max_input_channels'],
                        'default_samplerate': device['default_samplerate']
                    }
                    break
        
        # If no input device found or none specified, use first available input device
        if not input_device_info:
            for i, device in enumerate(devices):
                if device['max_input_channels'] > 0:
                    input_device_info = {
                        'index': i,
                        'name': device['name'],
                        'channels': device['max_input_channels'],
                        'default_samplerate': device['default_samplerate']
                    }
                    break
        
        if not input_device_info:
            raise RuntimeError("No input devices available")

        print(f"[DEBUG] Settings: {settings}")

        self.device_info = input_device_info
        self.logger.info(f"Using input device: {self.device_info['name']} ({self.device_info['channels']} channels)")

        self.role = f"""You are {self.agent_name}, an AI assistant."""

        if settings.get('elevenlabs_enabled'):
            self.tts_engine = "elevenlabs"
            self.tts_api_key = settings.get('elevenlabs_key')
            self.agent_name = settings.get('elevenlabs_voice')
            self.voice_name = settings.get('elevenlabs_voice')
            self.voice_samples = []
            self.voice_settings = {
                "stability": 0.5,
                "similarity_boost": 0.7,
            }
        else:
            self.tts_engine = tts_engine
            self.tts_api_key = tts_api_key
            self.voice_name = agent_name
            self.voice_samples = []

        self.get_voice_config()

        # Initialize playback for output
        self.playback = Playback(output_device=output_device)

        # Initialize TTS engine first - even though it's the last in the happy flow
        # STT -> LLM -> TTS | Playback
        self.tts = TTSEngine(
            engine=self.tts_engine,
            api_key=self.tts_api_key,
            voice_name=self.voice_name,
            clone_samples=self.voice_samples,
            voice_settings=self.voice_settings,
        )
        
        # Initialize LLM with role (but not TTS queue yet)
        self.llm = LLMEngine(
            agent_name=self.agent_name,
            role=self.role, 
            engine=llm_engine, 
            api_key=llm_api_key, 
            model_name=llm_model
        )
        
        # Initialize STT with LLM callback
        self.stt = STTEngine(
            engine_type=stt_engine,
            api_key=stt_api_key,
            device_info=self.device_info,
            llm_callback=self.llm.process_text,
            silence_threshold=silence_threshold,
            playback=self.playback
        )

        # Set up signal handlers if requested (should be disabled when run as a separate process)
        if set_signal_handlers:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)

        # After initializing self.tts and self.playback
        self.playback.set_tts_engine(self.tts)

    def get_voice_config(self):
        # Check for role.txt in the agent directory
        if self.tts_engine == "kokoro":
            agent_dir = os.path.join(os.path.dirname(__file__), "../", 'agents', "kokoro")
        else:
            agent_dir = os.path.join(os.path.dirname(__file__), "../", 'agents', self.agent_name)

        role_path = f"{agent_dir}/role.txt"
        config_path = f"{agent_dir}/config.json"
        config = {}

        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    if 'tts_engine' in config:
                        self.tts_engine = config['tts_engine']
                    if 'voice_settings' in config:
                        self.voice_settings = config['voice_settings']
            except Exception as e:
                self.logger.error(f"[{get_timestamp()}] Error loading config: {e}")
        
        # Load role from file if it exists
        if os.path.exists(role_path):
            try:
                with open(role_path, 'r') as f:
                    role_text = f.read()
                self.logger.info(f"[{get_timestamp()}] Loaded role from {role_path}")
                self.role = (role_text)
            except Exception as e:
                self.logger.error(f"[{get_timestamp()}] Error loading role file: {e}")
                # Fallback to default role
                self.role = (f"""You are {self.agent_name}, an AI assistant.""")
        else:
            self.logger.error(f"[{get_timestamp()}] No role file found at {role_path}, using default role")
            self.role = (f"""You are {self.agent_name}, an AI assistant.""")        
            self.voice_name = self.agent_name

        if self.tts_engine == "kokoro":
            self.voice_name = "af_heart"
            self.agent_name = "Heart"
            self.logger.info(f"[{get_timestamp()}] Using default voice {self.voice_name}")
        

    def _signal_handler(self, sig, frame):
        """Handle system signals for graceful shutdown"""
        self.logger.info(f'\n[{get_timestamp()}] Gracefully shutting down...')
        self._stop_event.set()
        self.clear_tts_and_playback()  # Ensure cleanup on shutdown
        self.stop()
        time.sleep(0.5)

    def clear_tts_and_playback(self):
        """
        Clear TTS and playback resources.
        This is called when speech is detected to interrupt current playback.
        """
        current_time = time.time()
        if current_time - self._last_clear_time < 0.1:  # Reduced debounce time
            return
            
        self._last_clear_time = current_time
        self.logger.info(f"[{get_timestamp()}] Clearing TTS and playback...")
        
        try:
            # First stop playback and clear playlist
            if hasattr(self, 'playback'):
                self.playback.stop_playback()
                self.playback.clear_playlist()
                self.logger.info(f"[{get_timestamp()}] Playback cleared")
            
            # Then clear TTS generation queue and files
            if hasattr(self, 'tts'):
                self.tts.cleanup()
                self.logger.info(f"[{get_timestamp()}] TTS cleared")
                
            # Finally reset LLM state
            if hasattr(self, 'llm'):
                self.llm.should_cancel_response = True
                self.llm.generating_reply = False
                self.llm.buffer = ""
                self.llm.current_stream = None  # Add this to ensure stream is cleared
                self.logger.info(f"[{get_timestamp()}] LLM state cleared")
                
            # Add a small delay to ensure state is fully reset
            time.sleep(0.05)
                
        except Exception as e:
            self.logger.error(f"[{get_timestamp()}] Error clearing TTS and playback: {e}")

    def start(self):
        """Start all processing engines in dependency order"""
        self.logger.info(f"[{get_timestamp()}] Starting {self.agent_name} session...")
        self.logger.info(f"[{get_timestamp()}] Using input device: {self.device_info['name']}")
        self.running = True
        
        try:
            # Start TTS first and wait for initialization
            self.logger.info(f"[{get_timestamp()}] 🎵 Initializing TTS engine with voice: {self.voice_name}")
            self.tts.start()
            self.logger.info(f"[{get_timestamp()}] ✅ TTS engine started successfully")
            
            # Connect playback to TTS
            self.logger.info(f"[{get_timestamp()}] 🔄 Connecting playback to TTS")
            self.playback.add_tts_playlist(self.tts)
            self.logger.info(f"[{get_timestamp()}] ✅ Playback connected to TTS")
            
            # Now that TTS is started, connect it to LLM
            self.logger.info(f"[{get_timestamp()}] 🔄 Connecting LLM to TTS input queue")
            self.llm.set_tts_queue(self.tts.input_queue)
            self.logger.info(f"[{get_timestamp()}] ✅ LLM connected to TTS input queue")
            
            # Start LLM second
            self.llm.start()
            self.logger.info(f"[{get_timestamp()}] ✅ LLM engine started")
            
            # Start STT last
            self.stt.start()
            self.logger.info(f"[{get_timestamp()}] ✅ STT engine started")
            
            # Initialize playback engine but don't start it yet
            self.logger.info(f"[{get_timestamp()}] 🎵 Initializing playback engine")
            self.playback._initialize_audio_system()
            self.logger.info(f"[{get_timestamp()}] ✅ Playback engine initialized")
            
            # Small delay to ensure all components are ready
            time.sleep(0.5)
            
            # Send welcome message
            self.logger.info(f"[{get_timestamp()}] {self.agent_name} will welcome you shortly")                    
            self.llm.send_welcome_message()
            # --- Ensure welcome message is played ---
            self.logger.info(f"[{get_timestamp()}] Checking for welcome message TTS files...")
            self.playback.check_and_add_new_files(self.tts)
            if self.playback.playlist and not self.playback.is_playing:
                self.logger.info(f"[{get_timestamp()}] Starting playback of welcome message ({len(self.playback.playlist)} files)")
                self.playback.start()
            
            # Main event loop
            while self.running and not self._stop_event.is_set():
                try:
                    # --- NEW: Process LLM signal queue for clearing TTS and playback ---
                    try:
                        while not self.llm.signal_queue.empty():
                            signal = self.llm.signal_queue.get_nowait()
                            if signal.get("action") == "clear_tts_and_playback":
                                print("[DEBUG] Clearing TTS and playback before new LLM response.")
                                self.tts.cleanup()
                                self.playback.clear_playlist()
                                # Send ack to LLM if queue exists
                                if hasattr(self.llm, 'tts_clear_ack_queue'):
                                    try:
                                        self.llm.tts_clear_ack_queue.put("ack")
                                        print("[DEBUG] Sent TTS/playback clear ack to LLM.")
                                    except Exception as e:
                                        self.logger.error(f"Error sending TTS clear ack: {e}")
                    except Exception as e:
                        self.logger.error(f"Error processing LLM signal queue: {e}")
                    # ---------------------------------------------------------------
                    # Check if all components are still running
                    if not self.stt.running or \
                       not self.llm.running or \
                       not self.tts.is_running.value:
                        self.logger.error(f"[{get_timestamp()}] One or more components stopped unexpectedly")
                        break

                    # Before queuing new LLM sentences, clear old unplayed/generated files
                    # NOTE: Do NOT call queue_sentence or clear_unplayed_files_from_previous_groups here with a placeholder.
                    # Instead, this logic should be placed where you actually queue a new LLM response to TTS.
                    # For example, in the LLM callback or wherever you process new LLM output:
                    #   group_id = self.tts.queue_sentence(llm_response)
                    #   self.tts.clear_unplayed_files_from_previous_groups(keep_group=group_id)
                    # This ensures only the current LLM response's files are kept.

                    # Handle TTS generation and playback
                    generated = self.tts.generate_next()
                    if generated:
                        self.logger.info(f"[{get_timestamp()}] Generated audio file: {generated.get('file_path')}")
                        self.playback.check_and_add_new_files(self.tts)
                        self.logger.debug(f"[{get_timestamp()}] Checked and added new TTS files to playlist.")
                        # Start playback immediately if not already playing and playlist has files
                        if self.playback.playlist and not self.playback.is_playing:
                            self.logger.info(f"[{get_timestamp()}] Starting playback immediately ({len(self.playback.playlist)} files)")
                            self.playback.start()
                    # Check playback status
                    if self.playback.is_playing:
                        self.logger.debug(f"[{get_timestamp()}] Currently playing audio")
                    # Small sleep to prevent CPU spinning
                    time.sleep(0.01)
                except Exception as e:
                    self.logger.error(f"[{get_timestamp()}] Error in main loop: {e}")
                    break
            
        except Exception as e:
            self.logger.error(f"[{get_timestamp()}] ❌ Error starting engines: {e}")
            self.stop()
            raise
        finally:
            self.stop()

    def stop(self):
        """Stop all processing engines"""
        self.logger.info(f"[{get_timestamp()}] Stopping {self.agent_name} session...")
        self.running = False
        self._stop_event.set()  # Make sure the stop event is set
        try:
            # First stop TTS and wait for it to complete
            if hasattr(self, 'tts'):
                self.logger.info(f"[{get_timestamp()}] Stopping TTS engine...")
                try:
                    self.tts.stop()
                    # Explicitly clear input/output queues
                    self._clear_queue(self.tts.input_queue)
                    self._clear_queue(self.tts.output_queue)
                    self._clear_queue(self.tts.signal_queue)
                    self._clear_queue(self.tts.generation_queue)
                    self.logger.info(f"[{get_timestamp()}] TTS engine stopped successfully")
                except Exception as e:
                    self.logger.error(f"[{get_timestamp()}] Error stopping TTS engine: {e}")
            # Then stop playback before STT to ensure no audio conflicts
            if hasattr(self, 'playback'):
                self.logger.info(f"[{get_timestamp()}] Stopping playback...")
                try:
                    self.playback.stop_playback()
                    self.playback.clear_playlist()
                    print("[DEBUG] Cleared playback playlist on session stop.")
                    self.logger.info(f"[{get_timestamp()}] Playback stopped successfully")
                except Exception as e:
                    self.logger.error(f"[{get_timestamp()}] Error stopping playback: {e}")
            # Then stop STT and wait for it to complete
            if hasattr(self, 'stt'):
                self.logger.info(f"[{get_timestamp()}] Stopping STT engine...")
                try:
                    self.stt.stop()
                    self.logger.info(f"[{get_timestamp()}] STT engine stopped successfully")
                except Exception as e:
                    self.logger.error(f"[{get_timestamp()}] Error stopping STT engine: {e}")
            # Finally stop LLM
            if hasattr(self, 'llm'):
                self.logger.info(f"[{get_timestamp()}] Stopping LLM engine...")
                try:
                    self.llm.stop()
                    # Explicitly clear input/output queues
                    self._clear_queue(self.llm.input_queue)
                    self._clear_queue(self.llm.output_queue)
                    self._clear_queue(self.llm.signal_queue)
                    self.logger.info(f"[{get_timestamp()}] LLM engine stopped successfully")
                except Exception as e:
                    self.logger.error(f"[{get_timestamp()}] Error stopping LLM engine: {e}")
            # Now do final cleanup of playback resources
            if hasattr(self, 'playback'):
                self.logger.info(f"[{get_timestamp()}] Performing final playback cleanup...")
                try:
                    self.playback.clear_playlist()
                    print("[DEBUG] Cleared playback playlist on final cleanup.")
                    self.playback.cleanup()
                    self.logger.info(f"[{get_timestamp()}] Playback cleanup completed successfully")
                except Exception as e:
                    self.logger.error(f"[{get_timestamp()}] Error during final playback cleanup: {e}")
            self.logger.info(f"[{get_timestamp()}] All engines stopped successfully")
            # Give a small delay to ensure all resources are properly released
            time.sleep(0.5)
        except Exception as e:
            self.logger.error(f"[{get_timestamp()}] Error during shutdown: {e}")
            import traceback
            traceback.print_exc()
        # Force garbage collection
        try:
            import gc
            gc.collect()
        except:
            pass
            
    def _clear_queue(self, queue):
        """Safely empty a queue to release resources"""
        if queue is None:
            return
            
        try:
            # Try to drain the queue
            while True:
                try:
                    # Use non-blocking get with a short timeout
                    queue.get(block=False)
                except Exception:
                    # Catch any exception from empty queue or other issues
                    break
            
            # Try to close queue resources
            try:
                if hasattr(queue, 'close'):
                    queue.close()
                if hasattr(queue, 'join_thread'):
                    queue.join_thread()
            except Exception as e:
                self.logger.debug(f"Error closing queue: {e}")
                
        except Exception as e:
            # Catch-all to prevent any failures during cleanup
            self.logger.error(f"[{get_timestamp()}] Error clearing queue: {e}")
            
    def __del__(self):
        """Destructor to ensure cleanup when object is garbage collected"""
        try:
            if hasattr(self, 'running') and self.running:
                self.stop()
                
            # Force close any left over resources
            for attr_name in ['tts', 'llm', 'stt', 'playback']:
                if hasattr(self, attr_name):
                    obj = getattr(self, attr_name)
                    if hasattr(obj, 'stop'):
                        try:
                            obj.stop()
                        except:
                            pass
                    if hasattr(obj, 'cleanup'):
                        try:
                            obj.cleanup()
                        except:
                            pass
        except:
            pass

    def clear_old_tts_files_before_llm_response(self, keep_group):
        """
        Clear all unplayed/generated TTS files from previous groups, keeping only the current group.
        Args:
            keep_group (str): The group_id to keep
        """
        if hasattr(self.tts, 'clear_unplayed_files_from_previous_groups'):
            self.tts.clear_unplayed_files_from_previous_groups(keep_group=keep_group)
            self.logger.info(f"[SESSION] Cleared old TTS files, keeping group {keep_group}")