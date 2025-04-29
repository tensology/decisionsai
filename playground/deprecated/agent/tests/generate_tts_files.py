#!/usr/bin/env python3
import os
import sys
import time
import logging
import threading
import signal
import shutil
from pathlib import Path

ELEVENLABS_API_KEY = "<your-api-key>"


# Ensure the current directory is in the path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TTS-Generator")

# Import the TTSEngine and Playback from agent v2
from distr.tts import TTSEngine
from distr.playback import Playback

# Global variables for hard stop functionality
tts_engine = None
playback_engine = None
tmp_directory = None
should_exit = False
current_volume = 1.0
command_thread = None

def hard_stop():
    """Immediately stop all processes, clear playlists, delete temp files, and exit"""
    global tts_engine, playback_engine, tmp_directory, should_exit
    
    logger.info("HARD STOP TRIGGERED - Cleaning up and exiting immediately")
    
    # Set exit flag to stop all threads
    should_exit = True
    
    # Stop TTS engine and clear its playlist
    if tts_engine:
        logger.info("Stopping TTS engine...")
        tts_engine.stop()
        tts_engine.playlist.clear()
        logger.info("TTS playlist cleared")
    
    # Stop playback and clear its playlist
    if playback_engine:
        logger.info("Stopping playback...")
        playback_engine.stop_playback()
        playback_engine.clear_playlist()
        logger.info("Playback playlist cleared")
    
    # Delete all temporary files
    if tmp_directory and os.path.exists(tmp_directory):
        logger.info(f"Deleting all temporary files in {tmp_directory}")
        try:
            # Delete all files in the tmp directory
            for file in os.listdir(tmp_directory):
                file_path = os.path.join(tmp_directory, file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")
            
            # Optionally, remove the tmp directory itself
            # os.rmdir(tmp_directory)
        except Exception as e:
            logger.error(f"Error cleaning up tmp directory: {e}")
    
    logger.info("Cleanup complete, exiting")
    # Force exit the script
    os._exit(0)

def signal_handler(sig, frame):
    """Handle Ctrl+C (SIGINT) to trigger hard stop"""
    logger.info("Ctrl+C pressed, initiating hard stop")
    hard_stop()

def volume_toggle_handler():
    """Toggle volume between 1.0 and 0.5"""
    global playback_engine, current_volume
    
    if playback_engine:
        # Toggle between 1.0 and 0.5
        new_volume = 0.5 if current_volume == 1.0 else 1.0
        current_volume = new_volume
        
        logger.info(f"Toggling volume to {new_volume}")
        try:
            # Apply volume change directly
            playback_engine.set_volume(new_volume)
            logger.info(f"Volume successfully set to {new_volume}")
        except Exception as e:
            logger.error(f"Error setting volume: {e}")

def command_listener_thread():
    """Thread to listen for command input"""
    global should_exit
    
    logger.info("Command listener started")
    logger.info("Commands:")
    logger.info("  'v' - Toggle volume between 1.0 and 0.5")
    logger.info("  'q' or 'exit' - Stop and exit")
    
    while not should_exit:
        try:
            # Get command from user
            command = input("Enter command (v/q/exit): ").strip().lower()
            
            if command == 'v':
                volume_toggle_handler()
            elif command in ['q', 'exit']:
                logger.info("Exit command received, initiating hard stop")
                hard_stop()
                break
            else:
                logger.info("Unknown command. Available commands: v, q, exit")
        except Exception as e:
            logger.error(f"Error in command listener: {e}")
            time.sleep(0.5)
    
    logger.info("Command listener stopped")

def start_command_listener():
    """Start the command listener thread after initial setup is complete"""
    global command_thread
    
    # Start command listener thread
    command_thread = threading.Thread(target=command_listener_thread)
    command_thread.daemon = True
    command_thread.start()
    logger.info("Command listener started after initial setup")

def split_into_sentences(text):
    """Split text into sentences using simple splitting"""
    sentences = []
    for line in text.split('\n'):
        for sentence in line.split('. '):
            sentence = sentence.strip()
            if sentence:
                sentences.append(sentence + ('.' if not sentence.endswith(('.', '!', '?')) else ''))
    return sentences

def monitor_and_play(tts, playback, total_sentences):
    """Monitor TTS generation and play files as they become available"""
    global should_exit
    
    logger.info("Starting monitoring and playback thread")
    generated_count = 0
    error_count = 0
    
    while generated_count + error_count < total_sentences and not should_exit:
        # Update playlist from results
        tts._update_playlist_from_results()
        
        # Check for newly generated files
        with playback.playlist_lock:
            tts_playlist = tts.get_playlist()
            for item in tts_playlist:
                if item.get('status') == 'generated' and item.get('file_path'):
                    file_path = item['file_path']
                    
                    # Check if this file is already in our playback playlist
                    already_queued = False
                    for pb_item in playback.get_playlist():
                        if pb_item.get('file_path') == file_path:
                            already_queued = True
                            break
                    
                    # If not already queued, add it to playback
                    if not already_queued:
                        logger.info(f"Adding newly generated file to playback: {file_path}")
                        playback.add_to_playlist(file_path, auto_delete=True)
                        
                        # Start playback if not already playing
                        if not playback.is_playing:
                            logger.info("Starting playback")
                            playback.play_playlist()
        
        # Count current status
        generating = 0
        generated = 0
        error = 0
        
        for item in tts.get_playlist():
            status = item.get('status')
            if status == 'generating':
                generating += 1
            elif status == 'generated':
                generated += 1
            elif status == 'error':
                error += 1
        
        # Update counts
        generated_count = generated
        error_count = error
        
        # Log progress
        logger.info(f"Status: Generating={generating}, Generated={generated}, Errors={error}")
        
        # Check if we're done
        if generated_count + error_count >= total_sentences:
            break
            
        time.sleep(0.5)
    
    # Log final results
    logger.info(f"Generation complete: {generated_count} successful, {error_count} errors")
    
    # Wait for playback to complete
    logger.info("Waiting for playback to complete...")
    while playback.is_playing and not should_exit:
        time.sleep(0.5)
    
    logger.info("Monitoring and playback thread completed")

def generate_tts_files():
    """Generate TTS audio files from the sample text using multiprocessing"""
    global tts_engine, playback_engine, tmp_directory
    
    # Register signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("Press Ctrl+C at any time to stop generation and playback")
    
    # Initialize TTS engine with coqui package
    logger.info("Initializing TTS engine...")
    start_time = time.time()
    # Initialize TTS engine without setting stt_engine to avoid circular reference
    tts_engine = TTSEngine(
        # package="elevenlabs", 
        # api_key=ELEVENLABS_API_KEY, 
        # voice_name="Hayley Williams",
        # clone_samples=[
        #     "./agents/Hayley Williams/voice_samples/01.mp3",
        #     "./agents/Hayley Williams/voice_samples/02.mp3",
        #     "./agents/Hayley Williams/voice_samples/03.mp3",
        #     "./agents/Hayley Williams/voice_samples/04.mp3",
        #     "./agents/Hayley Williams/voice_samples/05.mp3",
        #     "./agents/Hayley Williams/voice_samples/06.mp3",
        #     "./agents/Hayley Williams/voice_samples/07.mp3",
        # ],
        # voice_settings={
        #     "stability": 0.3,
        #     "similarity_boost": 1,
        # }
    )
    tts_engine.start()
    init_time = time.time() - start_time
    logger.info(f"TTS Engine initialization time: {init_time:.2f} seconds")
    
    # Get the tmp directory - this will now be in the agent v2 root
    tmp_directory = tts_engine.mktmp()
    logger.info(f"Using tmp directory: {tmp_directory}")
    
    # Initialize playback
    logger.info("Initializing playback...")
    playback_engine = Playback()
    
    # Start command listener after input device selection is complete
    start_command_listener()
    
    # Load sample text
    sample_text_path = os.path.join(current_dir, "sample_text.txt")
    with open(sample_text_path, 'r') as f:
        sample_text = f.read()
    
    # Split text into sentences
    sentences = split_into_sentences(sample_text)
    logger.info(f"Processing {len(sentences)} sentences")
      
    # Start monitoring thread
    monitor_thread = threading.Thread(
        target=monitor_and_play,
        args=(tts_engine, playback_engine, len(sentences))
    )
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # Queue all sentences for generation
    start_time = time.time()
    audio_ids = []
    for i, sentence in enumerate(sentences):
        if should_exit:
            break
            
        logger.info(f"Queueing sentence {i+1}/{len(sentences)}: {sentence[:30]}...")
        audio_id = tts_engine.generate(sentence)
        audio_ids.append(audio_id)
        # Small delay to ensure queue positions are distinct
        time.sleep(0.01)
    
    queue_time = time.time() - start_time
    logger.info(f"Time to queue all sentences: {queue_time:.4f} seconds")
    
    # Wait for monitoring thread to complete
    logger.info("Waiting for monitoring thread to complete...")
    monitor_thread.join()
    
    # Print file paths for successful generations
    logger.info("Generated audio files:")
    for item in tts_engine.get_playlist():
        if item.get('status') == 'generated' and item.get('file_path'):
            logger.info(f"  - {item.get('file_path')}")
    
    # Stop the TTS engine when done
    logger.info("Stopping TTS engine...")
    tts_engine.stop()
    
    logger.info("TTS file generation and playback completed")
                
if __name__ == "__main__":
    try:
        generate_tts_files()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, initiating hard stop")
        hard_stop()
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        hard_stop() 