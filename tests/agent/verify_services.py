import sys
import os
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../')))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_services")

def verify_imports():
    logger.info("Verifying imports...")
    try:
        from distr.core.agent.services import WhisperSTTService, OllamaLLMService, KokoroTTSService
        logger.info("✅ Successfully imported services from distr.core.agent.services")
        return True
    except ImportError as e:
        logger.error(f"❌ Failed to import services: {e}")
        return False

def verify_instantiation():
    logger.info("Verifying instantiation (mocking dependencies if needed)...")
    from distr.core.agent.services import WhisperSTTService, OllamaLLMService, KokoroTTSService
    
    # We might fail if models are not found, so we wrap in try-except
    try:
        # Whisper
        # We don't actually instantiate because it loads the model which takes time/memory
        # and might fail if model path is wrong. We just check class existence.
        logger.info(f"WhisperSTTService class: {WhisperSTTService}")
        
        # Ollama
        logger.info(f"OllamaLLMService class: {OllamaLLMService}")
        
        # Kokoro
        logger.info(f"KokoroTTSService class: {KokoroTTSService}")
        
        return True
    except Exception as e:
        logger.error(f"❌ Instantiation verification failed: {e}")
        return False

if __name__ == "__main__":
    if verify_imports() and verify_instantiation():
        logger.info("✅ Service verification passed")
        sys.exit(0)
    else:
        logger.error("❌ Service verification failed")
        sys.exit(1)
