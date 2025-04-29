from elevenlabs.client import ElevenLabs
import subprocess
import os
import threading
import tempfile
import shutil

# Initialize the client with your API key
client = ElevenLabs(
    api_key="<your-api-key>"
)

print("\nChecking for existing cloned voices...")
voices = client.voices.get_all()

created = False
the_voice = None
for voice in voices.voices:
    if voice.name == "Hayley Williams":
        the_voice = voice
        created = True
        break

if not created:
    # Delete all cloned voices
    for voice in voices.voices:
        if voice.category == "cloned" and voice.name != "Hayley Williams":
            print(f"Deleting cloned voice: {voice.name}")
            client.voices.delete(voice.voice_id)

    print("\nCreating new cloned voice...")
    the_voice = client.clone(
        name="Hayley Williams",
        description="Hayley Williams voice clone",        
        files=[
            "./clone/hayley/hayley1.mp3",
            "./clone/hayley/hayley2.0.mp3",
            "./clone/hayley/hayley2.1.mp3",
            "./clone/hayley/hayley2.2.mp3",
            "./clone/hayley/hayley2.3.mp3",
            "./clone/hayley/hayley2.4.mp3",
            "./clone/hayley/hayley2.5.mp3",
        ]
    )
else:
    print("Hayley Williams voice already exists, skipping creation.")

try:
    text = input("What would you like to say? ")
    
    if not text:
        raise ValueError("Text cannot be empty")

    print("\nGenerating audio...")
    
    # Create a temporary file for streaming
    temp_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
    temp_path = temp_file.name
    
    # Function to play the audio file while it's being written
    def play_audio():
        # Start playing as soon as we have the first chunk (which contains the MP3 header)
        while os.path.getsize(temp_path) < 512:  # Reduced from 1024 to 512 bytes
            continue
        # Play the file while it's being written
        subprocess.run(['afplay', '-q', '1', temp_path])  # Added -q 1 for faster startup
    
    # Start the playback thread
    player = threading.Thread(target=play_audio)
    player.start()
    
    # Generate and write audio chunks
    audio_stream = client.generate(
        text=text,
        voice=the_voice,
        model="eleven_monolingual_v1",
        stream=True
    )
    
    print("Starting playback...")
    for chunk in audio_stream:
        temp_file.write(chunk)
        temp_file.flush()  # Ensure data is written to disk
    
    # Close the file
    temp_file.close()
    
    # Wait for playback to complete
    player.join()
    print("Audio playback completed")
    
    # Save the final audio file
    output_file = "output_speech.mp3"
    shutil.copy2(temp_path, output_file)
    print(f"Audio saved to {output_file}")

except ValueError as e:
    print(f"Error: {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
finally:
    # Clean up temporary file
    if 'temp_path' in locals() and os.path.exists(temp_path):
        os.remove(temp_path)


