"""
OpenRouter Model Fetching and Caching

This module handles fetching and caching OpenRouter models with 24-hour timestamp validation.
"""

from datetime import datetime, timedelta
from distr.core.paths import MODELS_DIR
import requests
import logging
import json
import os

logger = logging.getLogger(__name__)


def clear_openrouter_cache():
    """Remove the OpenRouter models cache file so fresh data is fetched on next request."""
    cache_path = os.path.join(MODELS_DIR, 'openrouter_models.json')
    if os.path.exists(cache_path):
        os.remove(cache_path)
        logger.info("Cleared OpenRouter models cache")


def _format_openrouter_model_name(model_id: str) -> str:
    """Format OpenRouter model ID to display name."""
    # Remove common prefixes
    name = model_id
    
    # Handle common patterns
    if '/' in name:
        # e.g., "openai/gpt-4o" -> "GPT-4o"
        parts = name.split('/')
        if len(parts) > 1:
            name = parts[-1]
    
    # Format common model names
    name_map = {
        'gpt-4o': 'GPT-4o',
        'gpt-4o-mini': 'GPT-4o Mini',
        'gpt-4-turbo': 'GPT-4 Turbo',
        'gpt-4': 'GPT-4',
        'gpt-3.5-turbo': 'GPT-3.5 Turbo',
        'claude-3.5-sonnet': 'Claude 3.5 Sonnet',
        'claude-3-opus': 'Claude 3 Opus',
        'claude-3-sonnet': 'Claude 3 Sonnet',
        'claude-3-haiku': 'Claude 3 Haiku',
        'gemini-pro': 'Gemini Pro',
        'llama-3.1': 'Llama 3.1',
        'mistral-large': 'Mistral Large',
    }
    
    # Check exact match
    if name in name_map:
        return name_map[name]
    
    # Check prefix matches
    for prefix, display_name in name_map.items():
        if name.startswith(prefix):
            return display_name
    
    # Fallback: capitalize and clean up
    name = name.replace('-', ' ').replace('_', ' ')
    return name.title()


def _parse_openrouter_model(model: dict) -> dict | None:
    """Parse a single OpenRouter API model entry into our internal format.
    Returns None if the model should be skipped."""
    model_id = model.get('id', '')
    if not model_id:
        return None

    name = model.get('name', '') or _format_openrouter_model_name(model_id)

    context_length = model.get('context_length', 0)
    pricing = model.get('pricing') or {}
    prompt_cost = pricing.get('prompt') or '0'
    completion_cost = pricing.get('completion') or '0'
    is_free = (prompt_cost == '0' or float(prompt_cost or 0) == 0) and (
        completion_cost == '0' or float(completion_cost or 0) == 0
    )
    supported = model.get('supported_parameters') or []
    supports_tools = 'tools' in supported

    # Extract modality info for filtering by LLM type
    architecture = model.get('architecture') or {}
    input_modalities = architecture.get('input_modalities') or []
    output_modalities = architecture.get('output_modalities') or []

    # Build display name with additional info
    display_parts = [name]
    if is_free:
        display_parts.append("(free)")
    if context_length:
        context_k = context_length // 1000
        if context_k >= 1000:
            display_parts.append(f"({context_k // 1000}M ctx)")
        else:
            display_parts.append(f"({context_k}K ctx)")

    display_name = " ".join(display_parts)

    return {
        'id': model_id,
        'name': display_name,
        'context_window': int(context_length or 0),
        'is_free': is_free,
        'supports_tools': supports_tools,
        'input_modalities': input_modalities,
        'output_modalities': output_modalities,
    }


def _sort_openrouter_models(models: list) -> list:
    """Sort OpenRouter models: free first, then tool-supporting, then by popularity."""
    def sort_key(m):
        is_free = m.get('is_free', False)
        supports_tools = m.get('supports_tools', False)
        mid = m['id'].lower()
        free_prefix = 0 if is_free else 1
        tools_prefix = 0 if supports_tools else 1
        if 'gpt-4o' in mid:
            return (free_prefix, tools_prefix, 0, mid)
        elif 'claude' in mid:
            return (free_prefix, tools_prefix, 1, mid)
        elif 'gpt-4' in mid:
            return (free_prefix, tools_prefix, 2, mid)
        elif 'gemini' in mid:
            return (free_prefix, tools_prefix, 3, mid)
        elif 'llama' in mid:
            return (free_prefix, tools_prefix, 4, mid)
        elif 'deepseek' in mid:
            return (free_prefix, tools_prefix, 5, mid)
        elif 'mistral' in mid:
            return (free_prefix, tools_prefix, 6, mid)
        elif 'qwen' in mid:
            return (free_prefix, tools_prefix, 7, mid)
        return (free_prefix, tools_prefix, 8, mid)

    models.sort(key=sort_key)
    return models


def get_openrouter_models(api_key: str):
    """
    Fetch available OpenRouter models from the API.
    This function reads from cache if available and valid (within 24 hours), otherwise fetches from API.
    
    Args:
        api_key: OpenRouter API key
        
    Returns:
        List of dicts with 'id' (model name for API) and 'name' (display name).
        Returns empty list if API call fails.
    """
    if not api_key or not api_key.strip():
        logger.warning("No OpenRouter API key provided")
        return []
    
    # Try to read from cache first (with timestamp validation)
    cache_path = os.path.join(MODELS_DIR, 'openrouter_models.json')
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as file:
                cache_data = json.load(file)
            
            # Handle new format (with timestamp)
            if isinstance(cache_data, dict) and 'timestamp' in cache_data:
                timestamp_str = cache_data.get('timestamp', '')
                if timestamp_str:
                    try:
                        # Parse timestamp (ISO format with Z)
                        timestamp_str = timestamp_str.rstrip('Z')
                        cache_time = datetime.fromisoformat(timestamp_str)
                        current_time = datetime.utcnow()
                        age = current_time - cache_time
                        
                        # Check if cache is less than 24 hours old
                        if age < timedelta(hours=24):
                            models = cache_data.get('models', [])
                            if models:
                                logger.debug(f"Using cached OpenRouter models ({len(models)} models, age: {age})")
                                return models
                        else:
                            logger.debug(f"OpenRouter cache expired (age: {age}), fetching from API")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid timestamp in cache: {e}, fetching from API")
                else:
                    logger.debug("Cache missing timestamp, fetching from API")
            elif isinstance(cache_data, dict) and 'models' in cache_data:
                # Old format without timestamp - use it but log warning
                models = cache_data.get('models', [])
                if models:
                    logger.debug(f"Using cached OpenRouter models (no timestamp, {len(models)} models)")
                    return models
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error reading OpenRouter cache: {e}")
    
    # Cache miss, invalid, or expired - fetch from API
    return _fetch_openrouter_models_from_api(api_key)


def _fetch_openrouter_models_from_api(api_key: str):
    """
    Fetch OpenRouter models directly from API (without cache check).
    
    Args:
        api_key: OpenRouter API key
        
    Returns:
        List of model dicts, or empty list on error.
    """
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            all_models = data.get('data', [])
            
            models = []
            for model in all_models:
                parsed = _parse_openrouter_model(model)
                if parsed:
                    models.append(parsed)

            _sort_openrouter_models(models)
            logger.info(f"Found {len(models)} OpenRouter models")
            return models
        else:
            logger.warning(f"OpenRouter API returned status {response.status_code}: {response.text[:200]}")
            return []
            
    except requests.Timeout:
        logger.warning("Timeout fetching OpenRouter models")
        return []
    except Exception as e:
        logger.error(f"Error fetching OpenRouter models: {e}")
        return []


def fetch_and_cache_openrouter_models(api_key: str = None):
    """
    Fetch and cache OpenRouter models with 24-hour timestamp validation.
    
    Args:
        api_key: OpenRouter API key. If None, reads from settings.
        
    Returns:
        List of model dicts. Returns empty list on error (preserves existing cache).
    """
    # Get API key from settings if not provided
    if not api_key:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        api_key = settings.get('openrouter_key', '').strip()
        if not api_key:
            logger.warning("No OpenRouter API key available")
            return []
    
    cache_path = os.path.join(MODELS_DIR, 'openrouter_models.json')
    
    # Create the MODELS_DIR if it doesn't exist
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    # Check if cache exists and is valid (within 24 hours)
    cache_valid = False
    cached_models = []
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as file:
                cache_data = json.load(file)
                
            # Check if it's the new format with timestamp
            if isinstance(cache_data, dict) and 'timestamp' in cache_data:
                timestamp_str = cache_data.get('timestamp', '')
                if timestamp_str:
                    try:
                        # Parse timestamp (ISO format with Z)
                        timestamp_str = timestamp_str.rstrip('Z')
                        cache_time = datetime.fromisoformat(timestamp_str)
                        current_time = datetime.utcnow()
                        age = current_time - cache_time
                        
                        # Check if cache is less than 24 hours old
                        if age < timedelta(hours=24):
                            cache_valid = True
                            cached_models = cache_data.get('models', [])
                            logger.info(f"Using cached OpenRouter models (age: {age})")
                        else:
                            logger.info(f"OpenRouter cache expired (age: {age}), fetching new models")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid timestamp in cache: {e}, fetching new models")
                else:
                    logger.warning("Cache missing timestamp, fetching new models")
            else:
                # Old format (just a list) - migrate it
                logger.info("Migrating old OpenRouter cache format to new format")
                if isinstance(cache_data, list):
                    cached_models = cache_data
                else:
                    cached_models = cache_data.get('models', [])
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error reading OpenRouter cache: {e}, fetching new models")
    
    # If cache is valid, return it
    if cache_valid:
        return cached_models
    
    # Cache is invalid or missing - fetch new models
    logger.info("Fetching OpenRouter models from API...")
    
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )
        
        # Handle specific error codes that shouldn't update cache
        if response.status_code in [502, 504]:
            logger.warning(f"OpenRouter API returned {response.status_code} (Bad Gateway) - preserving existing cache")
            if cached_models:
                logger.info("Returning stale cache due to 502/504 error")
                return cached_models
            return []
        
        if response.status_code == 200:
            data = response.json()
            all_models = data.get('data', [])
            
            if not all_models:
                logger.warning("OpenRouter API returned empty model list")
                if cached_models:
                    logger.info("Returning stale cache due to empty API response")
                    return cached_models
                return []
            
            models = []
            for model in all_models:
                parsed = _parse_openrouter_model(model)
                if parsed:
                    models.append(parsed)

            _sort_openrouter_models(models)

            # Save to JSON file for caching with timestamp
            cache_data = {
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'models': models
            }
            with open(cache_path, 'w', encoding='utf-8') as file:
                json.dump(cache_data, file, indent=2)
            
            logger.info(f"✅ Successfully fetched and cached {len(models)} OpenRouter models")
            return models
        else:
            logger.warning(f"OpenRouter API returned status {response.status_code}: {response.text[:200]}")
            # Return cached models if available
            if cached_models:
                logger.info("Returning stale cache due to API error")
                return cached_models
            return []
            
    except requests.Timeout:
        logger.warning("Timeout fetching OpenRouter models")
        # Return cached models if available
        if cached_models:
            logger.info("Returning stale cache due to timeout")
            return cached_models
        return []
    except Exception as e:
        logger.error(f"Error fetching OpenRouter models: {e}", exc_info=True)
        # Return cached models if available
        if cached_models:
            logger.info("Returning stale cache due to error")
            return cached_models
        return []
