import asyncio
import io
import ssl
import sys

import pyaudio

from speechmatics_flow.client import WebsocketClient
from speechmatics_flow.models import (
    ConnectionSettings,
    Interaction,
    AudioSettings,
    ConversationConfig,
    ServerMessageType,
)

AUTH_TOKEN = "<your-api-key>"


ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
# Create a websocket client
client = WebsocketClient(
    ConnectionSettings(
        url="wss://flow.api.speechmatics.com/v1/flow",
        auth_token=AUTH_TOKEN,
        ssl_context=ssl_context,
    )
)

# Create a buffer to store binary messages sent from the server
audio_buffer = io.BytesIO()


# Create callback function which adds binary messages to audio buffer
def binary_msg_handler(msg: bytes):
    if isinstance(msg, (bytes, bytearray)):
        audio_buffer.write(msg)


# Register the callback which will be called
# when the client receives an audio message from the server
client.add_event_handler(ServerMessageType.audio, binary_msg_handler)


async def audio_playback(buffer):
    """Read from buffer and play audio back to the user"""
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, output=True)
    try:
        while True:
            # Get the current value from the buffer
            audio_to_play = buffer.getvalue()
            # Only proceed if there is audio data to play
            if audio_to_play:
                # Write the audio to the stream
                stream.write(audio_to_play)
                buffer.seek(0)
                buffer.truncate(0)
            # Pause briefly before checking the buffer again
            await asyncio.sleep(0.05)
    finally:
        stream.close()
        stream.stop_stream()
        p.terminate()


async def main():
    tasks = [
        # Use the websocket to connect to Flow Service and start a conversation
        asyncio.create_task(
            client.run(
                interactions=[Interaction(sys.stdin.buffer)],
                audio_settings=AudioSettings(),
                conversation_config=ConversationConfig(),
            )
        ),
        # Run audio playback handler which streams audio from audio buffer
        asyncio.create_task(audio_playback(audio_buffer)),
    ]

    await asyncio.gather(*tasks)


asyncio.run(main())
