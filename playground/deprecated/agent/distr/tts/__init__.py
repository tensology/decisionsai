from multiprocessing import Process, Queue, Value
import tempfile
import time
import os
import queue
import uuid
from ..utils import get_timestamp


class TTSEngine:
    """
    Text-to-Speech engine focused on audio generation
    Uses multiprocessing for audio generation to prevent blocking the main thread
    """
    def __init__(self, package="coqui", api_key=None, voice_name=None, 
                 clone_samples=None, voice_settings=None, delete_other_voices=False, volume=1.0):
        # Configuration
        self.api_key = api_key              
        self.package = package
        self.voice_name = voice_name
        self.clone_samples = clone_samples or []
        self.voice_settings = voice_settings or {}
        self.delete_other_voices = delete_other_voices        
        self.volume = volume
        self.voice = None
        self.tts = None
        self.interrupted = False
        
        # Initialize TTS engine
        self.setup_tts(package)
        
        # Create shared resources
        self.playlist = []  # Keep playlist in main process
        self.generation_queue = Queue()  # Queue for generation requests
        self.result_queue = Queue()      # Queue for results/status updates
        self.command_queue = Queue()     # Queue for commands
        
        # Shared flags
        self.is_running = Value('b', True)
        
        # Process reference
        self.generator_process = None

    def start(self):
        """Start the TTS engine process"""
        # Start generator process
        self.generator_process = Process(
            target=self._generator_process_wrapper, 
            args=(
                self.package,
                self.api_key,
                self.voice_name,
                self.clone_samples,
                self.voice_settings,
                self.delete_other_voices,
                self.generation_queue,
                self.result_queue,
                self.command_queue,
                self.is_running
            ),
            daemon=True
        )
        self.generator_process.start()
        
        print("TTS Engine started")
        
    def _generator_process_wrapper(self, *args, **kwargs):
        """Wrapper function for the generator process to catch any exceptions"""
        try:
            self._generator_process(*args, **kwargs)
        except Exception as e:
            print(f"Generator process crashed: {e}")
            import traceback
            traceback.print_exc()
    
    def _generator_process(self, package, api_key, voice_name, clone_samples, voice_settings, 
                          delete_other_voices, generation_queue, result_queue, command_queue, is_running):
        """
        Separate process that handles audio generation
        All parameters are explicitly passed to avoid pickling issues
        """
        try:            
            
            # Track local state
            interrupted = False
            
            # Initialize the TTS engine in this process
            tts = None
            if package == "elevenlabs":
                from elevenlabs.client import ElevenLabs
                tts = ElevenLabs(api_key=api_key)
                voices = tts.voices.get_all()
                voice = None
                for v in voices.voices:
                    if v.name == voice_name:
                        voice = v
                        break
                if not voice and len(clone_samples) > 0:
                    voice = tts.clone(
                        name=voice_name,
                        description="Voice clone",
                        files=clone_samples
                    )
                if not voice:
                    voice_name = voices.voices[0].name
                    voice = voices.voices[0]
                    
            elif package == "openai":
                from openai import OpenAI
                tts = OpenAI(api_key=api_key)
                voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
                if not voice_name or voice_name not in voices:
                    voice_name = voices[0]
                    
            else:
                from TTS.api import TTS as CoquiTTS
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                tts = CoquiTTS("tts_models/en/vctk/vits").to(device)
                if not voice_name:
                    voice_name = "p318"  # Default voice
            
            # Create tmp directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            distr_dir = os.path.dirname(current_dir)
            agent_dir = os.path.dirname(distr_dir)
            tmp_dir = os.path.join(agent_dir, 'tmp')
            if not os.path.exists(tmp_dir):
                os.makedirs(tmp_dir)
                
            # Main processing loop
            while is_running.value:
                try:
                    # Check for commands with short timeout
                    try:
                        cmd = command_queue.get(timeout=0.1)
                        if cmd.get('command') == 'stop':
                            print("Generator received stop command")
                            break
                        elif cmd.get('command') == 'interrupt':
                            interrupted = True
                            print("Generator received interrupt command")
                        elif cmd.get('command') == 'reset_interrupt':
                            interrupted = False
                            # print("Generator received reset interrupt command")
                    except (queue.Empty, EOFError):
                        pass
                        
                    # Check for generation requests
                    try:
                        request = generation_queue.get_nowait()
                        
                        # Skip if interrupted
                        if interrupted:
                            print(f"Skipping generation due to interrupt: {request.get('text', '')[:30]}...")
                            result_queue.put({
                                'type': 'status',
                                'id': request.get('id'),
                                'status': 'interrupted',
                                'error': 'Generation interrupted'
                            })
                            continue
                            
                        # Process the generation request
                        entry_id = request.get('id')
                        text = request.get('text', '')
                        text = text.replace("*", "-")
                        vs = request.get('voice_settings', {})
                        
                        # Update status
                        result_queue.put({
                            'type': 'status',
                            'id': entry_id,
                            'status': 'generating'
                        })
                        
                        generation_start = time.time()
                        
                        # Set file extension
                        if package in ["elevenlabs", "openai"]:
                            suffix = ".mp3"
                        else:
                            suffix = ".wav"
                            
                        # Create temp file
                        output_file = None
                        try:
                            with tempfile.NamedTemporaryFile(
                                suffix=suffix,
                                delete=False,
                                dir=tmp_dir,
                                prefix=f"{voice_name.lower().replace(' ', '-')}-{time.time()}"
                            ) as temp_file:
                                file_path = temp_file.name
                                
                                # Generate based on TTS engine
                                generation_complete = False
                                
                                if package == "elevenlabs" and tts:
                                    try:
                                        audio = tts.generate(
                                            text=text,
                                            voice=voice,
                                            model="eleven_monolingual_v1",
                                            stream=True,
                                            voice_settings=vs
                                        )
                                        
                                        for chunk in audio:
                                            if interrupted:
                                                break
                                            temp_file.write(chunk)
                                            temp_file.flush()
                                            
                                        generation_complete = True
                                    except Exception as e:
                                        print(f"Error generating audio with Elevenlabs: {e}")
                                        
                                elif package == "openai" and tts:
                                    try:
                                        # Close file first
                                        temp_file.close()
                                        output_file = None
                                        
                                        with tts.audio.speech.with_streaming_response.create(
                                            model="gpt-4o-mini-tts",
                                            voice=voice_name,
                                            input=text,
                                            instructions="Speak in a cheerful and positive tone."
                                        ) as response:
                                            response.stream_to_file(file_path)
                                            
                                        generation_complete = True
                                    except Exception as e:
                                        print(f"Error generating audio with OpenAI: {e}")
                                        
                                else:
                                    try:
                                        # Close file first
                                        temp_file.close()
                                        output_file = None
                                        
                                        with suppress_stdout():
                                            tts.tts_to_file(text=text, file_path=file_path, speaker=voice_name)
                                            
                                        generation_complete = True
                                    except Exception as e:
                                        print(f"Error generating audio with Coqui: {e}")                                    
                                    
                            # Check if generation was successful
                            if generation_complete and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                                generation_time = time.time() - generation_start
                                
                                # Send success result
                                result_queue.put({
                                    'type': 'status',
                                    'id': entry_id,
                                    'status': 'generated',
                                    'file_path': file_path,
                                    'generated_at': time.time(),
                                    'generation_time': generation_time
                                })
                            else:
                                # Failed generation
                                print(f"Audio generation failed or produced empty file")
                                if os.path.exists(file_path):
                                    try:
                                        os.remove(file_path)
                                    except Exception:
                                        pass
                                    
                                result_queue.put({
                                    'type': 'status',
                                    'id': entry_id,
                                    'status': 'error',
                                    'error': 'Generation failed or produced empty file'
                                })
                                
                        except Exception as e:
                            print(f"Error in generation process: {e}")
                            if output_file:
                                try:
                                    output_file.close()
                                except Exception:
                                    pass
                                    
                            result_queue.put({
                                'type': 'status',
                                'id': entry_id,
                                'status': 'error',
                                'error': str(e)
                            })
                            
                    except (queue.Empty, EOFError):
                        pass
                        
                    # Short sleep to prevent tight loop
                    time.sleep(0.01)
                    
                except Exception as e:
                    print(f"Unexpected error in generator process: {e}")
                    time.sleep(0.1)
                    
            print("Generator process exiting")
            
        except Exception as e:
            print(f"Fatal error in generator process: {e}")
    
    def generate(self, text, voice_settings=None, voice_name=None, play=True):
        """Generate audio from text"""
        if not text or text.strip() == "":
            return None
            
        # Create unique ID
        entry_id = str(uuid.uuid4())
        
        # Create playlist entry
        entry = {
            'id': entry_id,
            'text': text,
            'created_at': time.time(),
            'status': 'queued',
            'file_path': None,
            'voice_name': voice_name or self.voice_name,
            'play': play,
            'auto_delete': True,
            'queue_position': len(self.playlist),
            'error': None,
            'generated_at': None,
            'played_at': None,
            'generation_time': None
        }
        
        # Add to playlist - thread-safe in main thread
        self.playlist.append(entry)
            
        # Send to generator process
        self.generation_queue.put({
            'id': entry_id,
            'text': text,
            'voice_settings': voice_settings or self.voice_settings
        })
        
        return entry_id
    
    def queue_in_playlist(self, text):
        """Add text to playlist and generate audio"""
        if not text or text.strip() == "":
            return None
            
        entry_id = self.generate(text)
        return entry_id
    
    def set_interrupt(self, interrupted=True):
        """Set the interrupted flag to stop current generation"""
        # Send command to process
        cmd = {
            'command': 'interrupt' if interrupted else 'reset_interrupt'
        }
        self.command_queue.put(cmd)
    
    def reset_interrupt(self):
        """Reset the interrupted flag"""
        self.set_interrupt(False)

    def stop(self):
        """Stop the generator process"""
        print("Stopping TTSEngine...")
        
        # Set running flag to false
        self.is_running.value = False
        
        # Send stop command
        self.command_queue.put({'command': 'stop'})
        
        # Wait for process to finish
        if self.generator_process and self.generator_process.is_alive():
            try:
                self.generator_process.join(timeout=2.0)
                if self.generator_process.is_alive():
                    print("Generator process did not exit cleanly, terminating")
                    self.generator_process.terminate()
            except Exception as e:
                print(f"Error stopping generator process: {e}")
                
        print("TTSEngine stopped")
    
    def set_voice_name(self, voice_name):
        """Set the voice name"""
        if voice_name == self.voice_name:
            return
            
        print(f"Changing voice from {self.voice_name} to {voice_name}")
        self.voice_name = voice_name
        self.setup_tts(self.package)
    
    def setup_tts(self, package=None):
        """Initialize the TTS engine based on the selected package"""
        # Just store the package name, don't initialize the client here
        # The actual initialization happens in the child process
        self.package = package or self.package
    
    def get_all_voices(self):
        """Get all available voices"""
        if self.package == "elevenlabs" and self.tts:
            voices = self.tts.voices.get_all()
            return [v.name for v in voices.voices]
        elif self.package == "openai":
            return ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        elif self.package == "coqui":
            return ["p318"]  # Default voice
        return ["default"]
    
    def set_volume(self, volume):
        """Set the volume (kept for compatibility)"""
        self.volume = max(0.0, min(1.0, volume))
    
    def cleanup_text(self, text):
        """Clean up text for better TTS output"""
        return text.replace(".", ".\n\n").replace("!", "!\n\n").replace("?", "?!\n\n").replace("*", "'")
        
    def wait_for_generation(self, entry_id, timeout=30):
        """Wait for an audio entry to be generated"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            # Update playlist from results
            self._update_playlist_from_results()
            
            # Thread-safe in main thread
            for item in self.playlist:
                if item['id'] == entry_id:
                    if item['status'] in ['generated', 'playing', 'played']:
                        return True
                    elif item['status'] == 'error':
                        return False
            time.sleep(0.1)
        return False

    def mktmp(self):
        """Create temporary directory"""
        # Get the path to the agent v2 root directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        distr_dir = os.path.dirname(current_dir)
        agent_dir = os.path.dirname(distr_dir)
        
        # Create tmp directory in the agent v2 root
        tmp_dir = os.path.join(agent_dir, 'tmp')
        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir)
        return tmp_dir

    def get_playlist(self):
        """Return the current playlist"""
        # Thread-safe copy in main thread
        return self.playlist.copy()

    def _update_playlist_from_results(self):
        """Update playlist entries based on results from the generator process"""
        try:
            # Check result queue for status updates
            while not self.result_queue.empty():
                result = self.result_queue.get_nowait()
                if result.get('type') == 'status':
                    entry_id = result.get('id')
                    status = result.get('status')
                    
                    # Find matching playlist entry
                    for item in self.playlist:
                        if item['id'] == entry_id:
                            # Update status
                            item['status'] = status
                            
                            # Update other fields
                            for key in ['file_path', 'generated_at', 'generation_time', 'error']:
                                if key in result:
                                    item[key] = result[key]
                            break
        except queue.Empty:
            pass

    def clear_playlist(self):
        """Clear the playlist"""
        self.playlist.clear()
        print(f"[{get_timestamp()}] Playlist cleared")
            
    def clear_queue_and_temp_files(self):
        """Clear the TTS queue and remove temp files"""
        print(f"[{get_timestamp()}] Clearing TTS queue and removing temp files...")
        
        # Clear the playlist
        self.clear_playlist()
        
        # Set interrupt flag to stop current generation
        self.set_interrupt(True)
        
        # Reset interrupt flag after a short delay
        # Add a small delay to avoid multiple rapid interrupt/reset cycles
        time.sleep(0.2)
        self.reset_interrupt()
        
        # Remove temp files
        tmp_dir = self.mktmp()
        if os.path.exists(tmp_dir):
            for file in os.listdir(tmp_dir):
                file_path = os.path.join(tmp_dir, file)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                except Exception as e:
                    print(f"[{get_timestamp()}] Error removing temp file {file_path}: {e}")
