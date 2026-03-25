import inspect
try:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    print("\nSileroVADAnalyzer dir:")
    print(dir(SileroVADAnalyzer))
    print("\nSileroVADAnalyzer.set_params signature:")
    print(inspect.signature(SileroVADAnalyzer.set_params))
    print("\nSileroVADAnalyzer.set_params doc:")
    print(SileroVADAnalyzer.set_params.__doc__)
except ImportError as e:
    print(f"ImportError: {e}")
except Exception as e:
    print(f"Error: {e}")



























