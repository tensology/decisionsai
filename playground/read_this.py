import os
import sys
import time
import logging
from multiprocessing import Process, Queue, Event
from queue import Empty

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from distr.agent.distr.tts import TTSEngine
from distr.agent.distr.playback import Playback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOC_PATH = os.path.join(os.path.dirname(__file__), 'tmp', 'doc.txt')

# Clean up text: remove excessive blank lines, collapse spaces, join lines into paragraphs
def clean_text(text):
    import re
    # Remove leading/trailing whitespace from each line
    lines = [line.strip() for line in text.splitlines()]
    # Remove empty lines
    lines = [line for line in lines if line]
    # Join lines into a single string, separating paragraphs by two newlines
    cleaned = '\n'.join(lines)
    # Collapse multiple spaces/tabs into a single space
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # Optionally, collapse multiple newlines to two (paragraph separation)
    cleaned = re.sub(r'\n{2,}', '\n\n', cleaned)
    # Remove any leading/trailing whitespace again
    cleaned = cleaned.strip()
    return cleaned

# Helper to split text into sentences (simple, can be improved)
def split_sentences(text):
    import re
    # Split on period, exclamation, or question mark followed by space or end of string
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]

def tts_generation_process(sentences, voice_name, models_dir, file_queue, ready_event, stop_event, logger):
    try:
        tts = TTSEngine(model_dir=models_dir, voice_name=voice_name)
        tts.start()
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
        file_queue.put(None)
    except Exception as e:
        logger.error(f"TTS generation process error: {e}")
        file_queue.put(None)
    finally:
        stop_event.set()
        tts.stop()

def main():
    # Read and clean document
    if not os.path.exists(DOC_PATH):
        logger.error(f"File not found: {DOC_PATH}")
        return
    with open(DOC_PATH, 'r') as f:
        text = f.read()
    text = clean_text(text)
    if not text:
        logger.error("Document is empty after cleanup.")
        return
    sentences = split_sentences(text)
    if not sentences:
        logger.error("No sentences found in document after cleanup.")
        return

    # Initialize TTS and playback for voice selection
    models_dir = os.path.join(os.path.dirname(__file__), '..', 'distr', 'agent', 'models')
    voice_name = "af_heart"

    tts = TTSEngine(model_dir=models_dir, voice_name=voice_name)
    tts.start()
    # voices = tts.get_available_voices() if hasattr(tts, 'get_available_voices') else tts.get_all_voices()
    # voice_name = voices[0] if voices else None
    tts.stop()

    # Setup output device selection
    dummy_playback = Playback()  # Temporary instance to access device listing
    devices = dummy_playback._get_output_devices()
    print("\nAvailable output devices:")
    for i, device in enumerate(devices):
        print(f"{i}: {device['name']}")
    print("--------------------------------")
    try:
        selection = int(input("Select output device number: "))
        if 0 <= selection < len(devices):
            output_device = devices[selection]['name']
        else:
            print("Invalid selection. Using default device.")
            output_device = None
    except (ValueError, EOFError):
        print("Invalid input. Using default device.")
        output_device = None

    # Setup playback
    playback = Playback(
        output_device=output_device,
        crossfade_duration=1.0,
        sample_rate=44100,
        channels=2,
        buffer_size=1024,
        queue_size=10,
        fade_in_duration=0.1,
        fade_out_duration=0.1,
        normalize_volume=True
    )
    logger.info(f"Using output device: {playback.output_device}")

    # Setup multiprocessing
    file_queue = Queue()
    stop_event = Event()
    ready_event = Event()

    # Start TTS process
    tts_proc = Process(
        target=tts_generation_process,
        args=(sentences, voice_name, models_dir, file_queue, ready_event, stop_event, logger)
    )
    tts_proc.start()
    ready_event.wait(timeout=10.0)
    if not ready_event.is_set():
        logger.error("TTS process failed to start")
        return

    # Start playback
    playback.start()
    last_file_received = False
    try:
        while not stop_event.is_set() or not file_queue.empty():
            try:
                audio_file = file_queue.get(timeout=0.1)
                if audio_file is None:
                    logger.info("Received end of processing signal")
                    last_file_received = True
                    break
                if audio_file and os.path.exists(audio_file):
                    if playback.add_to_playlist(audio_file):
                        logger.info(f"Added to playlist: {audio_file}")
                    if not playback.is_playing:
                        playback.start()
            except Empty:
                if not tts_proc.is_alive():
                    logger.info("TTS process has completed")
                    last_file_received = True
                    break
                continue
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                break
        logger.info("Waiting for final playback to complete...")
        while playback.is_playing or len(playback.playlist) > 0:
            time.sleep(0.1)
        if last_file_received:
            logger.info("Adding final delay for audio completion...")
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, stopping processes...")
        stop_event.set()
        time.sleep(0.5)
    finally:
        stop_event.set()
        if tts_proc.is_alive():
            tts_proc.join(timeout=2.0)
        time.sleep(0.5)
        playback.cleanup()
        logger.info("Done.")

if __name__ == "__main__":
    main()
