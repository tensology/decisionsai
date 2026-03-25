import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from distr.core.agent.session import AgentSession
from distr.core.agent.services.openai_llm import OpenAILLMService

class MockSettings(dict):
    def get(self, key, default=None):
        if key == 'agent_provider':
            return 'OpenAI'
        if key == 'agent_model':
            return 'gpt-4o-test'
        if key == 'openai_key':
            return 'sk-mock-key'
        return super().get(key, default)

class TestOpenAISwap(unittest.TestCase):
    @patch('distr.core.db.get_session')
    @patch('distr.core.db.Settings')
    @patch('distr.core.agent.services.openai_llm.AsyncOpenAI') # Mock OpenAI client
    @patch('distr.core.agent.session.ChatManager') # Mock ChatManager
    def test_openai_instantiation(self, mock_chat_manager, mock_openai, mock_settings_cls, mock_get_session):
        # Setup mocks
        mock_settings_instance = MockSettings({'dummy': 'value'}) # Make it truthy so "settings or {}" doesn't fail
        # Mock the session.query(Settings).first() chain
        mock_db_session = MagicMock()
        mock_get_session.return_value = mock_db_session
        mock_db_session.query.return_value.first.return_value = mock_settings_instance
        
        print(f"DEBUG TEST: mock_settings_instance type: {type(mock_settings_instance)}")
        print(f"DEBUG TEST: mock_settings_instance.get('agent_provider'): {mock_settings_instance.get('agent_provider')}")
        
        # Instantiate AgentSession
        session = AgentSession(settings=mock_settings_instance, input_speech_engine="Whisper")
        
        # Manually trigger service creation since __init__ doesn't do it
        session._create_services()
        
        # Assert Configuration
        print("Config LLM Engine:", session.config['llm']['engine'])
        self.assertEqual(session.config['llm']['engine'], 'openai')
        self.assertEqual(session.config['llm']['model_name'], 'gpt-4o-test')
        self.assertEqual(session.config['llm']['api_key'], 'sk-mock-key')
        
        # Assert Service Creation
        print("LLM Service Type:", type(session.llm_service))
        self.assertIsInstance(session.llm_service, OpenAILLMService)
        self.assertEqual(session.llm_service._model_name, 'gpt-4o-test')
        
        print("Verification Successful!")

if __name__ == '__main__':
    unittest.main()
