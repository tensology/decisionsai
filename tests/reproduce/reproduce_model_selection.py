
import os
import sys
import logging
import time
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Mock loguru and pipecat before importing AgentSession
sys.modules['loguru'] = MagicMock()
sys.modules['pipecat'] = MagicMock()
sys.modules['pipecat.transports'] = MagicMock()
sys.modules['pipecat.pipeline'] = MagicMock()
# Create a mock for pipeline module that has a pipeline submodule
mock_pipeline_module = MagicMock()
sys.modules['pipecat.pipeline.pipeline'] = mock_pipeline_module
sys.modules['pipecat.frames'] = MagicMock()
sys.modules['pipecat.processors'] = MagicMock()
sys.modules['pipecat.services'] = MagicMock()
sys.modules['pipecat.vad'] = MagicMock()

from distr.core.agent.session import AgentSession
from distr.core.db import get_session, Settings, Chat

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_model_selection():
    logger.info("Starting model selection reproduction test")
    
    # 1. Setup DB with specific model
    session = get_session()
    try:
        settings = session.query(Settings).first()
        if not settings:
            settings = Settings()
            session.add(settings)
        
        test_model = "llama3.2:latest"
        settings.agent_model = test_model
        settings.agent_provider = "Ollama"
        session.commit()
        logger.info(f"Set DB agent_model to: {test_model}")
    finally:
        session.close()
        
    # 2. Initialize AgentSession
    # We need to mock some things to avoid starting full audio pipeline
    with patch('distr.core.agent.session.HotSwappableLocalAudioTransport'), \
         patch('distr.core.agent.session.Pipeline'), \
         patch('distr.core.agent.session.PipelineRunner'):
        
        # Load settings as app would
        from distr.core.utils import load_settings_from_db
        app_settings = load_settings_from_db()
        
        logger.info(f"Loaded settings for AgentSession: {app_settings.get('agent_model')}")
        
        agent_session = AgentSession(settings=app_settings)
        
        # Trigger service creation
        agent_session._create_services()
        
        # 3. Verify OllamaLLMService model
        llm_service = agent_session.llm_service
        if llm_service:
            logger.info(f"OllamaLLMService model: {llm_service._model_name}")
            if llm_service._model_name == test_model:
                logger.info("✅ OllamaLLMService has correct model")
            else:
                logger.error(f"❌ OllamaLLMService has WRONG model: {llm_service._model_name} (expected {test_model})")
        else:
            logger.error("❌ OllamaLLMService not created")
            
        # 4. Verify ChatManager model
        chat_manager = agent_session.chat_manager
        if chat_manager:
            logger.info(f"ChatManager current_model: {chat_manager.current_model}")
            if chat_manager.current_model == test_model:
                logger.info("✅ ChatManager has correct model")
            else:
                logger.error(f"❌ ChatManager has WRONG model: {chat_manager.current_model} (expected {test_model})")
                
            # 5. Simulate New Chat
            logger.info("Simulating New Chat...")
            chat_id = chat_manager.create_chat("Test New Chat", is_new=True)
            logger.info(f"Created chat {chat_id}")
            
            # Check if ChatManager model is still correct
            logger.info(f"ChatManager current_model after new chat: {chat_manager.current_model}")
            
        else:
            logger.error("❌ ChatManager not created")

if __name__ == "__main__":
    test_model_selection()
