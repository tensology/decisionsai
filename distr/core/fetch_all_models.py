"""
Model Fetching Orchestrator

This module orchestrates fetching and caching of Ollama and OpenRouter models.
It runs at application startup and handles errors gracefully without corrupting cache.
"""

import logging
from datetime import datetime, timedelta
from distr.core.paths import MODELS_DIR
from distr.core.settings import load_settings_from_db

logger = logging.getLogger(__name__)


def fetch_all_models():
    """
    Fetch and cache all model lists (Ollama and OpenRouter).
    Runs synchronously at startup.
    
    Returns:
        dict with 'ollama' and 'openrouter' keys, each containing:
        - 'success': bool
        - 'models_count': int (0 if failed)
        - 'error': str (if failed)
    """
    results = {
        'ollama': {'success': False, 'models_count': 0, 'error': None},
        'openrouter': {'success': False, 'models_count': 0, 'error': None}
    }
    
    logger.info("Starting model fetching at startup...")
    
    # Clear OpenRouter cache so fresh models (with free-first sort) are fetched
    from distr.gui.utils.get_openrouter_models import clear_openrouter_cache
    clear_openrouter_cache()
    
    # Fetch Ollama models
    try:
        from distr.gui.utils.get_ollama_models import fetch_and_cache_ollama_models
        models = fetch_and_cache_ollama_models()
        if models:
            results['ollama'] = {
                'success': True,
                'models_count': len(models),
                'error': None
            }
            logger.info(f"✅ Ollama models fetched successfully: {len(models)} models")
        else:
            results['ollama'] = {
                'success': False,
                'models_count': 0,
                'error': 'No models returned'
            }
            logger.warning("⚠️  Ollama models fetch returned empty list")
    except Exception as e:
        results['ollama'] = {
            'success': False,
            'models_count': 0,
            'error': str(e)
        }
        logger.error(f"❌ Error fetching Ollama models: {e}", exc_info=True)
    
    # Fetch OpenRouter models (only if API key is available)
    try:
        settings = load_settings_from_db()
        openrouter_key = settings.get('openrouter_key', '').strip()
        openrouter_enabled = settings.get('openrouter_enabled', False)
        
        if openrouter_enabled and openrouter_key:
            from distr.gui.utils.get_openrouter_models import fetch_and_cache_openrouter_models
            models = fetch_and_cache_openrouter_models(openrouter_key)
            if models:
                results['openrouter'] = {
                    'success': True,
                    'models_count': len(models),
                    'error': None
                }
                logger.info(f"✅ OpenRouter models fetched successfully: {len(models)} models")
            else:
                results['openrouter'] = {
                    'success': False,
                    'models_count': 0,
                    'error': 'No models returned or API key invalid'
                }
                logger.warning("⚠️  OpenRouter models fetch returned empty list")
        else:
            results['openrouter'] = {
                'success': False,
                'models_count': 0,
                'error': 'OpenRouter not enabled or no API key'
            }
            logger.debug("⏭️  Skipping OpenRouter fetch (not enabled or no key)")
    except Exception as e:
        results['openrouter'] = {
            'success': False,
            'models_count': 0,
            'error': str(e)
        }
        logger.error(f"❌ Error fetching OpenRouter models: {e}", exc_info=True)
    
    # Log summary
    ollama_status = "✅" if results['ollama']['success'] else "❌"
    openrouter_status = "✅" if results['openrouter']['success'] else "⏭️"
    logger.info(f"Model fetching complete - Ollama: {ollama_status}, OpenRouter: {openrouter_status}")
    
    return results










