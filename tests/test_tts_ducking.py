"""
Test script for Text-to-Speech (TTS) and audio playback functionality.

This script tests the integration between the TTS engine and audio playback system.
It processes a series of test sentences, generates audio files, and plays them back
sequentially without interruption.

Key Features:
- Tests TTS generation with multiple sentences
- Implements audio playback
- Provides basic logging of the process
- Supports graceful cleanup on completion
- Tests volume ducking functionality

Usage:
    python test_tts_playback.py
"""

import time
import logging
import os
import sys
import random
from multiprocessing import Process, Queue, Event
from queue import Empty
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from distr.agent.distr.tts import TTSEngine
from distr.agent.distr.playback import Playback

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Set specific levels for some verbose modules
logging.getLogger("sounddevice").setLevel(logging.INFO)
logging.getLogger("soundfile").setLevel(logging.INFO)
logging.getLogger("distr.agent.distr.playback").setLevel(logging.DEBUG)
logging.getLogger("numba").setLevel(logging.WARNING)  # Suppress Numba messages
logging.getLogger("numpy").setLevel(logging.WARNING)  # Suppress NumPy messages

# Test configuration
TEST_SENTENCES = [
    "Once upon a time, in a friendly neighborhood, there lived a man named Joe who had a deep love for animals.",
    "Among his many pets was Max, a golden retriever with a heart of gold, who was adored by everyone in the community.",
    "Max's poker skills were so legendary that he was soon invited to underground games with the local raccoons.",
    "In the end, Joe realized that his pets were not just companions, but the most eccentric and hilarious family he could ever ask for."
]

def select_voice(tts):
    """
    Display available voices and let the user select one.
    
    Args:
        tts (TTSEngine): The TTS engine instance
        
    Returns:
        str: The selected voice name
    """
    voices = tts.get_available_voices()
    if not voices:
        logger.error("No voices available")
        sys.exit(1)
        
    print("\nAvailable voices:")
    for i, voice in enumerate(voices, 1):
        print(f"{i}. {voice}")
    
    while True:
        try:
            choice = int(input("\nSelect a voice (enter number): "))
            if 1 <= choice <= len(voices):
                return voices[choice - 1]
            print("Invalid choice. Please try again.")
        except ValueError:
            print("Please enter a valid number.")

def initialize_components():
    """
    Initialize TTS engine and playback system.
    
    Returns:
        tuple: (TTSEngine instance, Playback instance)
    """
    # Initialize TTS engine with default settings
    # engine = "elevenlabs"
    # api_key = "<your-api-key>"
    # voice_name = "Snoop Dogg"
    # tts = TTSEngine(engine=engine, api_key=api_key, voice_name=voice_name, voice_settings={"stability": 0.3, "similarity_boost": 0.5})

    models_dir = os.path.join(os.path.dirname(__file__), '..', 'distr', 'agent', 'models')
    
    # Initialize TTS engine and let user select voice
    tts = TTSEngine(model_dir=models_dir)
    tts.start()
    selected_voice = select_voice(tts)
    tts.stop()
    
    # Reinitialize with the selected voice
    tts = TTSEngine(model_dir=models_dir, voice_name=selected_voice)
    tts.start()
    
    logger.info(f"Selected voice: {selected_voice}")
    
    # Initialize playback with enhanced crossfading
    playback = Playback(
        crossfade_duration=3.0,  # Increased crossfade duration
        sample_rate=44100,
        channels=2,
        buffer_size=2048,  # Increased buffer size for smoother transitions
        queue_size=20,  # Increased queue size
        fade_in_duration=0.15,  # Add fade-in effect
        fade_out_duration=0.15,  # Add fade-out effect
        normalize_volume=True  # Enable volume normalization
    )
    logger.info(f"Using output device: {playback.output_device}")
    
    return tts, playback

def tts_generation_process(sentences, voice_name, models_dir, file_queue, ready_event, stop_event, logger):
    """
    Process function to process sentences and send generated file paths through queue.
    
    Args:
        sentences (list): List of sentences to process
        voice_name (str): Name of the voice to use
        models_dir (str): Path to models directory
        file_queue (Queue): Queue for sending file paths
        ready_event (Event): Event to signal TTS engine is ready
        stop_event (Event): Event to signal process termination
        logger: Logger instance
    """
    try:
        # Initialize TTS engine in the process
        tts = TTSEngine(model_dir=models_dir, voice_name=voice_name)
        tts.start()
        
        # Signal that TTS is ready
        ready_event.set()
        
        for sentence in sentences:
            if stop_event.is_set():
                break
            tts.process_text(sentence)
            logger.info(f"TTS: Queued sentence for generation: {sentence}")

        logger.info("TTS: Processing queued sentences...")
        generated_ids = set()
        total_sentences = len(sentences)
        
        while len(generated_ids) < total_sentences and not stop_event.is_set():
            with tts.generation_lock:
                for file_info in tts.generated_files.values():
                    if file_info.get('status') == 'generated' and file_info.get('file_path'):
                        audio_file = file_info.get('file_path')
                        if audio_file and os.path.exists(audio_file) and audio_file not in generated_ids:
                            file_queue.put(audio_file)
                            logger.info(f"TTS: Generated file: {audio_file}")
                            generated_ids.add(audio_file)
            time.sleep(0.1)
            
        logger.info("TTS: All sentences processed.")
        
        # Signal completion by putting None in the queue
        file_queue.put(None)
    except Exception as e:
        logger.error(f"TTS generation process error: {e}")
        # Signal error by putting None in the queue
        file_queue.put(None)
    finally:
        stop_event.set()
        tts.stop()

def playback_process(playback, tts, stop_event, logger):
    """
    Process function to start and monitor playback as files are added to the playlist.
    
    Args:
        playback (Playback): The playback system instance
        tts (TTSEngine): The TTS engine instance
        stop_event (Event): Event to signal process termination
        logger: Logger instance
    """
    try:
        logger.info("Playback: Starting playback process.")
        playback.start()
        
        while not stop_event.is_set():
            with playback.lock:
                playlist_empty = len(playback.playlist) == 0
            is_playing = playback.is_playing
            
            if playlist_empty and not is_playing:
                # Check if TTS is done
                with tts.generation_lock:
                    tts_done = all(
                        file_info.get('status') in ['generated', 'error']
                        for file_info in tts.generated_files.values()
                    )
                if tts_done:
                    logger.info("Playback: All files played and TTS is done.")
                    break
            time.sleep(0.1)
    except Exception as e:
        logger.error(f"Playback process error: {e}")
    finally:
        stop_event.set()

def trigger_volume_ducking(playback, min_delay=5.0, max_delay=10.0):
    """
    Trigger volume ducking after a random delay between min_delay and max_delay seconds.
    The delay ensures playback has been running for at least 5 seconds.
    
    Args:
        playback (Playback): The playback system instance
        min_delay (float): Minimum delay in seconds (default: 5.0)
        max_delay (float): Maximum delay in seconds (default: 10.0)
    """
    # Calculate random delay
    delay = random.uniform(min_delay, max_delay)
    logger.info(f"Volume ducking will trigger in {delay:.1f} seconds")
    
    # Wait for the delay
    time.sleep(delay)
    
    # Get current volume before ducking
    current_volume = playback.volume
    logger.info(f"Triggering volume ducking at {time.time():.1f} seconds")
    logger.info(f"Current volume before ducking: {current_volume:.2f}")
    
    # Duck volume to 30% with 1 second transition, wait 2 seconds, then 1 second fade-out
    playback.duck_volume(
        volume_ratio=0.3,
        wait_time=2.0,
        transition_duration=0.5,
        fallout_duration=0.5
    )
    logger.info("Volume ducking initiated")

def main():
    """
    Main test function that orchestrates the TTS and playback testing process.
    """
    # Initialize components for voice selection
    tts, playback = initialize_components()
    voice_name = tts.voice_name
    models_dir = tts.model_dir
    
    # Stop the initial TTS instance since we'll create a new one in the process
    tts.stop()
    
    try:
        # Create communication channels
        file_queue = Queue()
        stop_event = Event()
        ready_event = Event()
        
        # Create TTS process
        tts_proc = Process(
            target=tts_generation_process,
            args=(TEST_SENTENCES, voice_name, models_dir, file_queue, ready_event, stop_event, logger)
        )
        
        # Start TTS process and wait for it to be ready
        tts_proc.start()
        ready_event.wait(timeout=10.0)  # Wait up to 10 seconds for TTS to be ready
        
        if not ready_event.is_set():
            logger.error("TTS process failed to start")
            return
        
        # Start playback in main process
        logger.info("Starting playback...")
        playback.start()
        
        # Start volume ducking in a separate thread
        ducking_thread = threading.Thread(
            target=trigger_volume_ducking,
            args=(playback,)
        )
        ducking_thread.daemon = True
        ducking_thread.start()
        
        # Monitor queue for new files and play them
        last_file_received = False
        while not stop_event.is_set() or not file_queue.empty():
            try:
                # Try to get a new file from the queue
                audio_file = file_queue.get(timeout=0.1)
                
                # None signals end of processing
                if audio_file is None:
                    logger.info("Received end of processing signal")
                    last_file_received = True
                    break
                    
                if audio_file and os.path.exists(audio_file):
                    if playback.add_to_playlist(audio_file):
                        logger.info(f"Added to playlist: {audio_file}")
                        
                    # Start playback if not already playing
                    if not playback.is_playing:
                        playback.start()
                        
            except Empty:
                # Check if TTS process is still running
                if not tts_proc.is_alive():
                    logger.info("TTS process has completed")
                    last_file_received = True
                    break
                continue
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                break
        
        # Wait for playback to complete with proper delays
        logger.info("Waiting for final playback to complete...")
        while playback.is_playing or len(playback.playlist) > 0:
            time.sleep(0.1)
        
        # Add a final delay to ensure audio completion
        if last_file_received:
            logger.info("Adding final delay for audio completion...")
            time.sleep(2.0)  # 2-second delay after playback finishes
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, stopping processes...")
        stop_event.set()
        # Add a small delay before cleanup on interrupt
        time.sleep(0.5)
    finally:
        # Ensure TTS process is stopped
        stop_event.set()
        if tts_proc.is_alive():
            tts_proc.join(timeout=2.0)
            
        # Add a final delay before cleanup
        time.sleep(0.5)
            
        # Cleanup resources
        print("\nCleaning up resources...")
        playback.cleanup()
        logger.info("Test completed")

if __name__ == "__main__":
    main()
