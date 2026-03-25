
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock modules that might not be available or problematic
sys.modules['distr.core.agent.libs'] = MagicMock()
sys.modules['distr.core.agent.libs'].PIPECAT_AVAILABLE = True
sys.modules['distr.core.agent.libs'].STTService = object  # Mock base class

# Mock assemblyai
mock_aai = MagicMock()
sys.modules['assemblyai'] = mock_aai

from distr.core.agent.services.assemblyai_stt import AssemblyAISTTService

class TestAssemblyAIConfig(unittest.TestCase):
    def setUp(self):
        # Reset mocks
        mock_aai.reset_mock()
        # Mock settings
        mock_aai.settings = MagicMock()
        
    def test_init_default_model(self):
        """Test initialization with default model"""
        service = AssemblyAISTTService(api_key="test_key")
        self.assertEqual(service.speech_model, "universal-2")
        self.assertEqual(mock_aai.settings.api_key, "test_key")
        
    def test_init_specific_model(self):
        """Test initialization with specific model"""
        service = AssemblyAISTTService(api_key="test_key", model="slam-1")
        self.assertEqual(service.speech_model, "slam-1")
        
    def test_transcription_config_usage(self):
        """Test that speech_model is passed to TranscriptionConfig"""
        service = AssemblyAISTTService(api_key="test_key", model="slam-1")
        
        # Test transcribe_file since it's synchronous and easier to test than the async run_stt
        # Mock Transcriber
        mock_transcriber = MagicMock()
        mock_aai.Transcriber.return_value = mock_transcriber
        mock_transcript = MagicMock()
        mock_transcript.status = "completed"
        mock_transcript.text = "test transcript"
        mock_transcriber.transcribe.return_value = mock_transcript
        
        # Run method
        service.transcribe_file("dummy.wav")
        
        # Verify Config was created with correct params
        mock_aai.TranscriptionConfig.assert_called_with(
            language_code="en",
            punctuate=True,
            format_text=True,
            speech_model="slam-1"
        )

if __name__ == '__main__':
    unittest.main()
