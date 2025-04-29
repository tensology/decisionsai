from elevenlabs.client import ElevenLabs
import requests
import json
import subprocess
import os

# Store the API key in a variable
API_KEY = "<your-api-key>"

# Initialize the client with your API key
client = ElevenLabs(
    api_key=API_KEY
)

print("\nChecking for existing cloned voices...")
voices = client.voices.get_all()

created = False
the_voice = None
for voice in voices.voices:
    if voice.name == "Sample Test":
        the_voice = voice
        created = True
        break

if not created:
    # Delete all cloned voices
    for voice in voices.voices:
        if voice.category == "cloned":
            print(f"Deleting cloned voice: {voice.name}")
            client.voices.delete(voice.voice_id)

    print("\nCreating new cloned voice...")
    the_voice = client.clone(
        name="Sample Test",
        description="voice clone",
        files=[
            "./clone/sample1.mp3",
            "./clone/sample2.mp3",
            "./clone/sample3.mp3",
            "./clone/sample4.mp3",
            "./clone/sample5.mp3",
            "./clone/sample6.mp3",
            "./clone/sample7.mp3",
        ]
    )
else:
    print("Sample Test voice already exists, skipping creation.")

try:
    text = input("What would you like to say in Afrikaans? ")
    
    if not text:
        raise ValueError("Text cannot be empty")

    print("\nGenerating audio...")
    
    # Define the API endpoint
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{the_voice.voice_id}"
    
    # Define the headers
    headers = {
        "Content-Type": "application/json",
        "xi-api-key": API_KEY
    }
    
    # Define the payload with the language_id
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.5
        },
        "language_id": "af"  # Afrikaans language ID
    }
    
    # Make the POST request
    print("Sending request to ElevenLabs API...")
    response = requests.post(url, headers=headers, json=payload)
    
    # Check if the request was successful
    if response.status_code == 200:
        # Save the audio content to a file
        output_file = "output_speech_af.mp3"
        with open(output_file, "wb") as audio_file:
            audio_file.write(response.content)
        print(f"Audio saved to {output_file}")
        
        # Play the audio
        print("Playing audio...")
        subprocess.run(['afplay', output_file])
        print("Audio playback completed")
    else:
        print(f"Error: {response.status_code}, {response.text}")

except ValueError as e:
    print(f"Error: {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")