# Audio Tests

Audio diagnostic tests for DecisionsAI.

## Quick Microphone Test

To test if your microphone is working:

```bash
# From project root
cd /path/to/DecisionsAI
source ~/.virtualenvs/decisions/bin/activate

# Run microphone test
python tests/audio/test_microphone.py

# Or specify a device index
python tests/audio/test_microphone.py 2
```

## Available Tests

### test_microphone.py
Simple microphone test that:
- Lists all audio devices
- Records a 3-second sample
- Analyzes audio levels
- Provides diagnostics

### test_stt_diagnostic.py
Full STT diagnostic that tests:
- Microphone detection
- Audio capture
- Whisper STT service
- Audio processing

### test_transcription_live.py
Live transcription test that:
- Records audio continuously
- Shows transcriptions in real-time
- Tests the full STT pipeline

### test_transcription.py
Basic transcription test.

### test_kokoro.py
Tests the Kokoro TTS service.

### test_device_combinations.py
Tests different audio device combinations.

## Common Issues

### No audio detected
1. Check microphone is connected
2. Check system permissions (Privacy > Microphone)
3. Check correct device is selected in Settings > Audio

### Garbage transcription
1. Speak clearly and close to the microphone
2. Reduce background noise
3. Check microphone sensitivity in system settings

### "Model corrupted" error
Remove the corrupted model file:
```bash
rm ~/.local/share/pywhispercpp/models/ggml-base.en.bin
# Or on macOS:
rm ~/Library/Application\ Support/pywhispercpp/models/ggml-base.en.bin
```
Then run the app again - the model will be re-downloaded.
