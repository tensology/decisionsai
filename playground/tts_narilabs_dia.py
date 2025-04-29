import soundfile as sf
import os
import time
import logging
import torch
import queue
import uuid
from multiprocessing import Process, Queue, Value, Event
from datetime import datetime
import sounddevice as sd
import resampy

from dia.model import Dia

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class AsyncDiaGenerator:
    def __init__(self, model_name="nari-labs/Dia-1.6B"):
        logger.info("Initializing Async Dia Generator...")
        self.model_name = model_name
        self.playlist = []
        self.voice_cache = {}  # Cache for processed voice samples
        self.text_cache = {}   # Cache for encoded clone text
        
        # Create shared resources for multiprocessing
        self.generation_queue = Queue()
        self.result_queue = Queue()
        self.command_queue = Queue()
        self.is_running = Value('b', True)
        self.stop_playback_flag = Event()
        
        # Process reference
        self.generator_process = None
        
        # Audio playback settings
        self.target_samplerate = 44100
        self.is_playing = Value('b', False)
        
        # Timing information
        self.start_time = None
        self.end_time = None
        
    def start(self):
        """Start the generator process"""
        self.start_time = time.time()
        logger.info(f"Starting generation process at {datetime.fromtimestamp(self.start_time)}")
        
        self.generator_process = Process(
            target=self._generator_process_wrapper,
            args=(
                self.model_name,
                self.generation_queue,
                self.result_queue,
                self.command_queue,
                self.is_running
            ),
            daemon=True
        )
        self.generator_process.start()
        logger.info("Generator process started")
        
    def _generator_process_wrapper(self, *args, **kwargs):
        """Wrapper to catch any exceptions in the generator process"""
        try:
            self._generator_process(*args, **kwargs)
        except Exception as e:
            logger.error(f"Generator process crashed: {e}")
            
    def _generator_process(self, model_name, generation_queue, result_queue, command_queue, is_running):
        """Main generator process that handles audio generation"""
        try:
            # Initialize model in this process
            model = Dia.from_pretrained(model_name)
            
            # Cache for processed audio prompts
            audio_prompt_cache = {}
            
            while is_running.value:
                try:
                    # Check for commands
                    try:
                        cmd = command_queue.get_nowait()
                        if cmd.get('command') == 'stop':
                            logger.info("Generator received stop command")
                            break
                    except queue.Empty:
                        pass
                        
                    # Check for generation requests
                    try:
                        request = generation_queue.get_nowait()
                        
                        entry_id = request.get('id')
                        clone_text = request.get('clone_text', '')
                        generate_text = request.get('generate_text', '')
                        audio_path = request.get('audio_path', '')
                        output_path = request.get('output_path', '')
                        
                        # Update status
                        result_queue.put({
                            'type': 'status',
                            'id': entry_id,
                            'status': 'generating',
                            'start_time': time.time()
                        })
                        
                        try:
                            # Check if we have cached the audio prompt
                            if audio_path not in audio_prompt_cache:
                                logger.info(f"Processing and caching audio prompt: {audio_path}")
                                # Process the audio prompt once and cache it
                                audio_prompt_cache[audio_path] = audio_path
                            
                            # Generate the audio
                            output = model.generate(
                                clone_text + generate_text + "  . . ",
                                audio_prompt_path=audio_prompt_cache[audio_path]
                            )
                            
                            # Save the output
                            sf.write(output_path, output, 44100)
                            
                            generation_time = time.time() - request.get('start_time', time.time())
                            
                            # Send success result
                            result_queue.put({
                                'type': 'status',
                                'id': entry_id,
                                'status': 'generated',
                                'file_path': output_path,
                                'generated_at': time.time(),
                                'generation_time': generation_time
                            })
                            
                        except Exception as e:
                            logger.error(f"Error generating audio: {e}")
                            result_queue.put({
                                'type': 'status',
                                'id': entry_id,
                                'status': 'error',
                                'error': str(e)
                            })
                            
                    except queue.Empty:
                        pass
                        
                    time.sleep(0.01)
                    
                except Exception as e:
                    logger.error(f"Error in generator process: {e}")
                    time.sleep(0.1)
                    
        except Exception as e:
            logger.error(f"Fatal error in generator process: {e}")
            
    def generate_async(self, clone_text, generate_text, audio_path, output_path):
        """Queue a generation request"""
        entry_id = str(uuid.uuid4())
        
        # Create playlist entry
        entry = {
            'id': entry_id,
            'clone_text': clone_text,
            'generate_text': generate_text,
            'created_at': time.time(),
            'status': 'queued',
            'file_path': output_path,
            'audio_path': audio_path,
            'error': None,
            'generated_at': None,
            'generation_time': None,
            'start_time': time.time()
        }
        
        # Add to playlist
        self.playlist.append(entry)
        
        # Send to generator process
        self.generation_queue.put({
            'id': entry_id,
            'clone_text': clone_text,
            'generate_text': generate_text,
            'audio_path': audio_path,
            'output_path': output_path,
            'start_time': time.time()
        })
        
        return entry_id
        
    def _update_playlist_from_results(self):
        """Update playlist entries with results from generator process"""
        try:
            while not self.result_queue.empty():
                result = self.result_queue.get_nowait()
                if result.get('type') == 'status':
                    entry_id = result.get('id')
                    status = result.get('status')
                    
                    for item in self.playlist:
                        if item['id'] == entry_id:
                            item['status'] = status
                            for key in ['file_path', 'generated_at', 'generation_time', 'error']:
                                if key in result:
                                    item[key] = result[key]
                            break
        except queue.Empty:
            pass
            
    def wait_for_all_generations(self, timeout=120):
        """Wait for all generations to complete"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            self._update_playlist_from_results()
            
            all_complete = True
            for item in self.playlist:
                if item['status'] not in ['generated', 'error']:
                    all_complete = False
                    break
                    
            if all_complete:
                self.end_time = time.time()
                logger.info(f"All generations completed at {datetime.fromtimestamp(self.end_time)}")
                logger.info(f"Total generation time: {self.end_time - self.start_time:.2f} seconds")
                return True
                
            time.sleep(0.1)
        return False
        
    def play_generated_audio(self):
        """Play all generated audio files in sequence"""
        self._update_playlist_from_results()
        
        # Filter only successfully generated files
        generated_files = [item for item in self.playlist if item['status'] == 'generated']
        
        if not generated_files:
            logger.info("No generated files to play")
            return
            
        logger.info(f"Playing {len(generated_files)} generated files")
        
        self.is_playing.value = True
        self.stop_playback_flag.clear()
        
        play_start_time = time.time()
        logger.info(f"Starting playback at {datetime.fromtimestamp(play_start_time)}")
        
        for item in generated_files:
            if self.stop_playback_flag.is_set():
                break
                
            try:
                logger.info(f"Playing file: {item['file_path']} (Generation time: {item['generation_time']:.2f}s)")
                audio_data, samplerate = sf.read(item['file_path'])
                
                # Resample if needed
                if samplerate != self.target_samplerate:
                    audio_data = resampy.resample(audio_data, samplerate, self.target_samplerate)
                    samplerate = self.target_samplerate
                    
                # Play the audio
                sd.play(audio_data, samplerate)
                sd.wait()
                
            except Exception as e:
                logger.error(f"Error playing file {item['file_path']}: {e}")
                
        play_end_time = time.time()
        logger.info(f"Playback completed at {datetime.fromtimestamp(play_end_time)}")
        logger.info(f"Total playback time: {play_end_time - play_start_time:.2f} seconds")
        
        self.is_playing.value = False
        
    def stop_playback(self):
        """Stop current playback"""
        if self.is_playing.value:
            self.stop_playback_flag.set()
            try:
                sd.stop()
            except Exception as e:
                logger.error(f"Error stopping playback: {e}")
                
    def cleanup(self):
        """Clean up resources"""
        self.stop_playback()
        
        # Stop generator process
        self.is_running.value = False
        self.command_queue.put({'command': 'stop'})
        
        if self.generator_process and self.generator_process.is_alive():
            self.generator_process.join(timeout=2.0)
            if self.generator_process.is_alive():
                self.generator_process.terminate()
                
def interactive_tts(generator, voice_sample_path, clone_from_text):
    """Interactive TTS generation loop"""
    try:
        while True:
            # Get user input
            user_input = input("\nWhat would you like me to say? (or type 'quit' to exit): ")
            
            if user_input.lower() == 'quit':
                logger.info("Exiting interactive mode...")
                break
                
            if not user_input.strip():
                logger.info("No input provided, please try again.")
                continue
                
            # Format the input with speaker tag
            formatted_input = f"[S1] {user_input}. ."
            
            # Generate output path
            output_path = f"interactive_cloned_{int(time.time())}.mp3"
            
            # Clear any existing entries in the playlist
            generator.playlist.clear()
            
            # Queue the generation
            generator.generate_async(
                clone_from_text,
                formatted_input,
                voice_sample_path,
                output_path
            )
            
            # Wait for generation to complete
            if generator.wait_for_all_generations():
                # Play the generated audio
                generator.play_generated_audio()
                
                # Clean up the generated file
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                        logger.info(f"Cleaned up generated file: {output_path}")
                except Exception as e:
                    logger.error(f"Error cleaning up file {output_path}: {e}")
            else:
                logger.error("Failed to generate audio")
                
            # Clear the queues
            while not generator.generation_queue.empty():
                try:
                    generator.generation_queue.get_nowait()
                except queue.Empty:
                    break
                    
            while not generator.result_queue.empty():
                try:
                    generator.result_queue.get_nowait()
                except queue.Empty:
                    break
                
    except KeyboardInterrupt:
        logger.info("\nReceived keyboard interrupt, cleaning up...")
    finally:
        generator.cleanup()

def main():
    # Initialize the async generator
    generator = AsyncDiaGenerator()
    generator.start()
    
    # Path to your voice sample
    voice_sample_path = os.path.join(os.path.dirname(__file__), "sample.mp3")
    logger.info(f"Using voice sample from: {voice_sample_path}")
    
    # Base transcription for voice cloning
    clone_from_text = """[S1] I'm ready to get over it, but I mean I'm 28 and I've been in a band for 13 years and I'm just now realizing that that's really what it is, you know?"""
    
    # Start interactive mode
    interactive_tts(generator, voice_sample_path, clone_from_text)

if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("Starting interactive voice generation script")
    logger.info("=" * 80)
    main()
