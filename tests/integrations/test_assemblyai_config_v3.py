
import sys
import os
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 1. Mock the specific LEAF modules that assemblyai_stt.py imports.
# We do NOT mock the intermediate packages (distr.core.agent) because we want Python
# to find the real assemblyai_stt.py file.

# Mock libs
mock_libs = MagicMock()
mock_libs.PIPECAT_AVAILABLE = True
mock_libs.STTService = object 
# Make sure we mock the module path exactly as it is imported
sys.modules['distr.core.agent.libs'] = mock_libs

# Mock other dependencies
sys.modules['numpy'] = MagicMock()
sys.modules['assemblyai'] = MagicMock()

# Now import the class under test
from distr.core.agent.services.assemblyai_stt import AssemblyAISTTService
import assemblyai as aai

class TestAssemblyAIRealCode(unittest.TestCase):
    def setUp(self):
        aai.reset_mock()
        aai.settings = MagicMock()
    
    def test_default_model(self):
        service = AssemblyAISTTService(api_key="key")
        self.assertEqual(service.speech_model, "universal-2")
        
    def test_slam_model(self):
        service = AssemblyAISTTService(api_key="key", model="slam-1")
        self.assertEqual(service.speech_model, "slam-1")
        
    def test_transcribe_passes_model(self):
        service = AssemblyAISTTService(api_key="key", model="slam-1")
        
        # Setup mocks for transcribe_file
        mock_transcriber = MagicMock()
        aai.Transcriber.return_value = mock_transcriber
        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.text = "foo"
        mock_transcriber.transcribe.return_value = mock_result
        
        service.transcribe_file("audio.wav")
        
        # Verify speech_model was passed in config
        args, kwargs = aai.TranscriptionConfig.call_args
        self.assertEqual(kwargs['speech_model'], 'slam-1')

if __name__ == '__main__':
    unittest.main()
