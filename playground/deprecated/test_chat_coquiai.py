import pyperclip
from ollama import Client
import pyaudio
import json
import vosk
import os
import time
import numpy as np
from collections import deque
from TTS.api import TTS
import torch
import subprocess
import re
import threading

# Global variable to hold the TTS model
tts_model = None

# Add these color codes at the top of your file
GREEN = '\033[92m'
RESET = '\033[0m'

def initialize_tts_model():
    global tts_model
    print("Initializing TTS model...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts_model = TTS("tts_models/en/vctk/vits").to(device)
    print("TTS model initialized.")

def detect_silence(audio_data, sample_rate, silence_threshold=0.03, silence_duration=3.0):
    audio_data = np.frombuffer(audio_data, dtype=np.int16)
    amplitudes = np.abs(audio_data).astype(np.float32) / 32768.0

    silence_frames = deque(maxlen=int(silence_duration * sample_rate // 4000))
    for amplitude in amplitudes:
        if amplitude < silence_threshold:
            silence_frames.append(True)
        else:
            silence_frames.clear()

    return len(silence_frames) == silence_frames.maxlen

def listen_for_speech():
    # model_path = os.path.join(os.getcwd(), "..", "models", "vosk-model-small-en-us-0.15")
    model_path = os.path.join(os.getcwd(), "..", "models", "vosk-model-en-us-0.22")
    print(f"Looking for model at: {model_path}")
    if not os.path.exists(model_path):
        print(f"Model not found at {model_path}")
        print("Current directory contents:")
        print(os.listdir(os.getcwd()))
        raise FileNotFoundError(f"Model not found at {model_path}")
    
    model = vosk.Model(model_path)
    recognizer = vosk.KaldiRecognizer(model, 16000)

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=8000)
    stream.start_stream()

    print("Listening... Speak your request.")
    
    full_speech = ""
    silent_chunks = 0
    speech_started = False
    silence_threshold = 5  # Increased from 3 to 5 seconds

    while True:
        audio = stream.read(4000, exception_on_overflow=False)
        
        if recognizer.AcceptWaveform(audio):
            result = json.loads(recognizer.Result())
            if result["text"]:
                speech_started = True
                full_speech += " " + result["text"]
                print(result["text"], end=" ", flush=True)
                silent_chunks = 0  # Reset silent chunks counter when speech is detected
        elif speech_started:
            silent_chunks += 1
            if silent_chunks >= silence_threshold:  # 5 chunks of silence (5 seconds)
                break
        
    stream.stop_stream()
    stream.close()
    p.terminate()

    return full_speech.strip()

def play_audio(output_file):
    subprocess.run(["afplay", "-r", "1.25", output_file])

def speak_output(ai_response):
    print("Generating speech...")
    
    try:
        # Generate speech and save to file
        output_file = "output.wav"
        speaker = "p261"  # best chick voice
        tts_model.tts_to_file(text=ai_response, file_path=output_file, speaker=speaker)

        print(f"Audio saved to {output_file}")

        # Start audio playback in a separate thread
        audio_thread = threading.Thread(target=play_audio, args=(output_file,))
        audio_thread.start()

        print("Press Enter to interrupt playback and continue...")
        input()  # Wait for user to press Enter

        if audio_thread.is_alive():
            print("\nPlayback interrupted. Listening for new input...")
            subprocess.run(["killall", "afplay"])  # Stop audio playback

        audio_thread.join()  # Wait for the audio thread to finish

    except Exception as e:
        print(f"Error in speech generation or playback: {e}")

def cleanup_response(response):
    """Clean up the markdown response from Ollama."""
    # Remove code blocks
    response = re.sub(r'```[\s\S]*?```', '', response)
    
    # Remove markdown links
    response = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', response)
    
    # Remove bullet points and numbering
    response = re.sub(r'^\s*[-*+]\s+', '', response, flags=re.MULTILINE)
    response = re.sub(r'^\s*\d+\.\s+', '', response, flags=re.MULTILINE)
    
    # Remove emphasis markers
    response = re.sub(r'[*_]{1,2}([^*_]+)[*_]{1,2}', r'\1', response)
    
    # Remove any remaining special characters
    response = re.sub(r'[#>`]', '', response)
    
    # Collapse multiple newlines into a single one
    response = re.sub(r'\n+', '\n', response)
    
    return response.strip()

def color_code_blocks(text):
    """Colorize code blocks in green."""
    def repl(match):
        return f"{GREEN}{match.group(0)}{RESET}"
    return re.sub(r'```[\s\S]*?```', repl, text)

def main():
    # Initialize Ollama client
    print("Loading Ollama model...")
    client = Client()
    print("Model loaded successfully.")

    # Initialize TTS model
    initialize_tts_model()

    # Initialize conversation history
    conversation_history = []

    while True:
        print("\nHow can I help you today? (Say 'exit' to quit)")
        
        user_input = listen_for_speech()
        print(f"\nYou said: {user_input}")

        if user_input.lower() == "exit":
            print("Exiting...")
            break

        # Add user input to conversation history
        conversation_history.append({"role": "user", "content": user_input})

        # Create the messages list with conversation history
        messages = conversation_history + [{"role": "user", "content": user_input}]

        # Generate response using the Ollama model
        response = client.chat(model='gemma2:latest', messages=messages)

        # Print the generated response
        print("Full response:")
        full_response = json.dumps(response, indent=2)
        print(color_code_blocks(full_response))  # Use color_code_blocks for full response

        ai_response = response['message']['content']
        print(ai_response)
        # Clean up the response
        # cleaned_response = cleanup_response(ai_response)
        # print(f"\nCleaned response:\n{color_code_blocks(cleaned_response)}\n")  # Use color_code_blocks for cleaned response

        # Add AI response to conversation history
        conversation_history.append({"role": "assistant", "content": ai_response})

        cleaned_response = cleanup_response(ai_response)
        speak_output(cleaned_response)

        # Limit conversation history to last 10 exchanges (20 messages)
        if len(conversation_history) > 20:
            conversation_history = conversation_history[-20:]

    # ... rest of the code ...

if __name__ == "__main__":
    main()


