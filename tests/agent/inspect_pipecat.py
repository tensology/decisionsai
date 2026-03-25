import inspect
from pipecat.transports.local.audio import LocalAudioTransport

print("\nLocalAudioTransport.input source:")
try:
    print(inspect.getsource(LocalAudioTransport.input))
except Exception as e:
    print(f"Could not get source for LocalAudioTransport.input: {e}")

print("\nLocalAudioTransport.output source:")
try:
    print(inspect.getsource(LocalAudioTransport.output))
except Exception as e:
    print(f"Could not get source for LocalAudioTransport.output: {e}")


print("\nLLMService.process_frame source:")
try:
    print(inspect.getsource(LLMService.process_frame))
except Exception as e:
    print(f"Could not get source for LLMService.process_frame: {e}")

print("\nSTTService.process_frame source:")
try:
    print(inspect.getsource(STTService.process_frame))
except Exception as e:
    print(f"Could not get source for STTService.process_frame: {e}")
