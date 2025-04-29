import os
import subprocess
import torch
import time
from TTS.api import TTS
import random
import signal
import sys
import termios
import tty

# Global variables
tts_model = None
nltk_initialized = False

def initialize_tts_model():
    global tts_model
    print("Initializing TTS model...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts_model = TTS("tts_models/en/vctk/vits").to(device)
    print("TTS model initialized.")

def initialize_nltk():
    global nltk_initialized
    if not nltk_initialized:
        import nltk
        nltk.download('gutenberg')
        nltk.download('punkt')
        nltk.download('punkt_tab')
        nltk_initialized = True

def generate_audio_file(text, speaker):
    output_file = f"speakers/{speaker}.wav"
    text_file = f"speakers/{speaker}.txt"
    
    if not os.path.exists(output_file):
        print(f"Generating audio for speaker {speaker}...")
        tts_model.tts_to_file(text=text, file_path=output_file, speaker=speaker)
        
        # Write the text to a file
        with open(text_file, 'w') as f:
            f.write(' '.join(text.split()))  # Join the text as it might be a list
        print(f"Audio generated for speaker {speaker}")

def play_audio_file(speaker):
    output_file = f"speakers/{speaker}.wav"
    process = subprocess.Popen(["afplay", "-r", "1.20", output_file])
    return process

def control_playback(speaker, process):
    paused = False
    while process.poll() is None:
        char = get_char()
        if char == ' ':  # Spacebar
            if paused:
                process.send_signal(signal.SIGCONT)
                paused = False
            else:
                process.send_signal(signal.SIGSTOP)
                paused = True
        elif char in ['n', 'p', 'q', 'r']:  # Added 'p' for previous
            process.terminate()
            return char
    return 'n'  # Default to next if playback finishes

def play_and_control(speaker):
    process = play_audio_file(speaker)
    return control_playback(speaker, process)

def get_char():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

def list_available_speakers():
    global tts_model
    if tts_model is None:
        initialize_tts_model()
    return tts_model.speakers

def clear_speakers_folder():
    speakers_folder = "./speakers/"
    if os.path.exists(speakers_folder):
        for file in os.listdir(speakers_folder):
            if file.endswith(".wav"):
                os.remove(os.path.join(speakers_folder, file))
        print("Speakers folder cleared.")
    else:
        print("Speakers folder does not exist.")

def generate_random_paragraph():
    global nltk_initialized
    if not nltk_initialized:
        initialize_nltk()
    
    import nltk
    from nltk.corpus import gutenberg
    
    while True:
        # Choose a random book from the Gutenberg corpus
        book = random.choice(gutenberg.fileids())
        
        # Get the text of the book
        text = gutenberg.raw(book)
        
        # Tokenize the text into sentences
        sentences = nltk.sent_tokenize(text)
        
        # Choose a random starting point
        start = random.randint(0, len(sentences) - 6)
        
        # Select 5 consecutive sentences
        paragraph = ' '.join(sentences[start:start+5])
        
        # Check if the paragraph meets our criteria
        words = paragraph.split()
        if 50 <= len(words) <= 100 and all(len(word) <= 15 for word in words):
            return paragraph
    
    return paragraph

def need_to_generate_voices():
    speakers_folder = "./speakers/"
    if not os.path.exists(speakers_folder) or not os.listdir(speakers_folder):
        return True
    return False

def create_progress_bar(percentage):
    if not 0 <= percentage <= 100:
        raise ValueError("Percentage must be between 0 and 100")

    fill = '█'
    empty = '-'
    total_width = 5

    filled_width = int(percentage / 20)  # Adjusted for 5 total characters

    bar = fill * filled_width + empty * (total_width - filled_width)
    return f"[{bar}]"

if __name__ == "__main__":
    clear_choice = input("Do you want to clear the ./speakers/ folder? (y/n): ").strip().lower()
    if clear_choice == 'y':
        clear_speakers_folder()

    if need_to_generate_voices() or clear_choice == 'y':
        initialize_nltk()
        initialize_tts_model()
        speakers = list_available_speakers()
        
        total_speakers = len(speakers)
        for i, speaker in enumerate(speakers, 1):
            progress = (i / total_speakers) * 100
            progress_bar = create_progress_bar(progress)
            print(f"\nProcessing speaker {i}/{total_speakers}: {speaker} {progress_bar}")
            text_to_speak = generate_random_paragraph()
            generate_audio_file(text_to_speak, speaker)
            print(f"Completed {i}/{total_speakers} speakers")
    else:
        speakers = [f.split('.')[0] for f in os.listdir('./speakers/') if f.endswith('.wav')]

    # Sort the speakers list alphabetically
    speakers.sort()

    ready = input("Are you ready to listen to the generated speakers? (y/n): ").strip().lower()
    
    if ready == 'y' or ready == '':
        total_speakers = len(speakers)
        i = 0
        while i < total_speakers:
            speaker = speakers[i]
            progress = ((i + 1) / total_speakers) * 100
            progress_bar = create_progress_bar(progress)
            print(f"\nNow listening to speaker: {speaker} ({i+1}/{total_speakers}) {progress_bar}")
            
            # Load and print the text file content
            text_file = f"speakers/{speaker}.txt"
            if os.path.exists(text_file):
                with open(text_file, 'r') as f:
                    print(f"Transcription: {f.read()}")
            else:
                print("No transcription present")
            
            print("Press Enter or 'n' for next, 'p' for previous, Spacebar to pause/resume, 'r' to restart, 'q' to quit.")
            
            choice = play_and_control(speaker)
            
            if choice in ['n', '\n', '\r']:  # Add Enter key options
                i += 1
            elif choice == 'p':
                i = max(0, i - 1)  # Ensure i doesn't go below 0
            elif choice == 'r':
                continue
            elif choice == 'q':
                print("Exiting the application.")
                exit()
    else:
        print("Exiting the application.")

# Add this at the end of the file
def signal_handler(sig, frame):
    print("\nExiting the application.")
    exit(0)

signal.signal(signal.SIGINT, signal_handler)