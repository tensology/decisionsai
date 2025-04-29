import pyaudio
import vosk
import json
import os
import torch
from TTS.api import TTS
from ollama import Client
from pathlib import Path
import hashlib
from collections import deque
import numpy as np
import threading
import subprocess
import re
from queue import Queue
import time
import scripts.sflow as sflow
from scripts.stranscribe import MicrophoneStream  # Import your existing MicrophoneStream class
import sys
import tty
import termios
import select
from openai import OpenAI

class AudioPlayer:
    def __init__(self, playlist_dir="./playlist"):
        self.playlist_dir = Path(playlist_dir)
        self.playlist_dir.mkdir(parents=True, exist_ok=True)
        self.current_playlist = []
        self.play_thread = None
        self.is_playing = False
        self.audio_queue = Queue()

    def add_to_playlist(self, audio_file):
        self.audio_queue.put(audio_file)
        if not self.is_playing:
            self.start_playback()

    def start_playback(self):
        self.is_playing = True
        self.play_thread = threading.Thread(target=self._playback_worker)
        self.play_thread.start()

    def _playback_worker(self):
        while self.is_playing:
            try:
                audio_file = self.audio_queue.get(timeout=1)
                subprocess.run(["afplay", str(audio_file)])
            except Queue.Empty:
                self.is_playing = False
            except Exception as e:
                print(f"Playback error: {e}")
                continue

class StreamingChatTTS:
    def __init__(self):
        self.client = OpenAI(api_key="<your-api-key>")
        self.ollama_client = Client()
        self.player = AudioPlayer()
        self.voice = "alloy"  # Options: alloy, echo, fable, onyx, nova, shimmer

        # Add new attributes for state management
        self.transcript_buffer = []
        self.should_stop = False
        
        # Initialize Speechmatics once
        self.ws = sflow.client.WebsocketClient(
            sflow.models.ConnectionSettings(
                url="wss://eu2.rt.speechmatics.com/v2",
                auth_token="l0ZZD0KhFv7wzS54zmobRuWevIRVvLL3",
            )
        )
        
        # Set up the configuration once
        self.speech_settings = sflow.models.AudioSettings()
        self.speech_config = sflow.models.TranscriptionConfig(
            language="en",
            operating_point="enhanced",
            diarization="speaker",
            enable_partials=True,
            max_delay=2,
            punctuation_overrides={"permitted_marks": [".", "?", "!", ","]},
            enable_entities=True
        )

        # Set up Speechmatics handlers
        self.ws.add_event_handler(
            event_name=sflow.models.ServerMessageType.AddPartialTranscript,
            event_handler=self.handle_transcript,
        )
        self.ws.add_event_handler(
            event_name=sflow.models.ServerMessageType.AddTranscript,
            event_handler=self.handle_transcript,
        )

    def generate_uid(self, text):
        return hashlib.md5(text.encode()).hexdigest()[:8]

    def process_response(self, response_text):
        sentences = self.split_into_sentences(response_text)
        
        for idx, sentence in enumerate(sentences):
            if not sentence.strip():
                continue

            uid = self.generate_uid(sentence)
            filename = f"{idx:04d}-{uid}.mp3"
            output_path = self.player.playlist_dir / filename

            if not output_path.exists():
                try:
                    response = self.client.audio.speech.create(
                        model="tts-1",
                        voice=self.voice,
                        input=sentence
                    )
                    response.stream_to_file(str(output_path))
                except Exception as e:
                    print(f"Error generating TTS for sentence {idx}: {e}")
                    continue

            self.player.add_to_playlist(output_path)

    def split_into_sentences(self, text):
        # Simple sentence splitting - you can use the more complex version from generate_tts.py
        return [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]

    def handle_transcript(self, msg):
        try:
            if msg.get('message') == 'AddPartialTranscript':
                metadata = msg.get('metadata', {})
                transcript = metadata.get('transcript', '').strip()
                if transcript:
                    print(f"\rPartial: {transcript}", end='', flush=True)
                    
            elif msg.get('message') == 'AddTranscript':
                metadata = msg.get('metadata', {})
                transcript = metadata.get('transcript', '').strip()
                if transcript:
                    self.transcript_buffer.append(transcript)
                    print(f"\nFinal: {transcript}")
        except Exception as e:
            print(f"\nError processing transcript: {e}")

    def process_input(self):
        """Process input when triggered"""
        user_input = " ".join(self.transcript_buffer)
        self.transcript_buffer = []  # Clear buffer
        
        if not user_input.strip():
            return True

        if user_input.lower() in ["exit", "quit", "goodbye"]:
            return False

        # Get AI response
        response = self.ollama_client.chat(model='gemma2:latest', 
                                  messages=[{"role": "user", "content": user_input}])
        ai_response = response['message']['content']
        print("\nAI:", ai_response)
        
        # Generate and play audio response
        self.process_response(ai_response)
        return True

    def chat_loop(self):
        print("\nListening... (Press Ctrl+C after speaking to get a response)")
        
        while True:
            try:
                # Start new recording session
                mic_stream = MicrophoneStream()
                
                # Run websocket for transcription
                self.ws.run_synchronously(mic_stream, self.speech_config, self.speech_settings)
                
            except KeyboardInterrupt:
                if not self.process_input():
                    break
            except Exception as e:
                print(f"\nError: {e}")
            finally:
                mic_stream.close()

def main():
    chat_tts = StreamingChatTTS()
    chat_tts.chat_loop()

if __name__ == "__main__":
    main() 