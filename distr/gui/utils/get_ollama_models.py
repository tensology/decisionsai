from datetime import datetime, timedelta
from distr.core.paths import MODELS_DIR
import subprocess
import requests
import logging
import json
import os
import time

logger = logging.getLogger(__name__)


def get_openai_models(api_key: str):
    """
    Fetch available OpenAI models from the API.
    Only returns supported chat models (no dated snapshots, no embeddings/tts/whisper).

    Args:
        api_key: OpenAI API key

    Returns:
        List of dicts with 'id' (model name for API) and 'name' (display name).
        Returns empty list if API call fails.
    """
    import re
    if not api_key or not api_key.strip():
        logger.warning("No OpenAI API key provided")
        return []

    try:
        response = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            all_models = data.get('data', [])

            # Date snapshot pattern (e.g., -2024-08-06, -20241022)
            date_pattern = re.compile(r'-\d{4}-?\d{2}-?\d{2}$')

            chat_models = []
            for model in all_models:
                model_id = model.get('id', '')
                if not model_id:
                    continue

                # Skip non-chat models
                if any(x in model_id for x in [
                    'embed', 'whisper', 'tts', 'dall-e', 'davinci', 'babbage',
                    'instruct', 'realtime', 'audio', 'moderation', 'search',
                    'similarity', 'code-', 'text-', 'curie', 'ada',
                    'canary', 'preview'
                ]):
                    continue

                # Only include known chat model families
                from distr.core.llm_factory import is_openai_model as _is_openai
                if not _is_openai(model_id):
                    continue

                # Skip dated snapshots (e.g., gpt-4o-2024-08-06)
                if date_pattern.search(model_id):
                    continue

                chat_models.append(model_id)

            # Sort: o-series first (reasoning), then gpt-4o, gpt-4, gpt-3.5
            def sort_key(m):
                if m.startswith('o4'):
                    return (0, m)
                elif m.startswith('o3'):
                    return (1, m)
                elif m.startswith('o1'):
                    return (2, m)
                elif 'gpt-4o' in m:
                    return (3, m)
                elif 'gpt-4' in m:
                    return (4, m)
                elif 'gpt-3.5' in m:
                    return (5, m)
                return (6, m)

            chat_models.sort(key=sort_key)

            models = []
            for model_id in chat_models:
                display_name = _format_openai_model_name(model_id)
                models.append({
                    'id': model_id,
                    'name': display_name
                })

            logger.info(f"Found {len(models)} OpenAI chat models (filtered from {len(all_models)} total)")
            return models
        else:
            logger.warning(f"OpenAI API returned status {response.status_code}: {response.text[:200]}")
            return []

    except requests.Timeout:
        logger.warning("Timeout fetching OpenAI models")
        return []
    except Exception as e:
        logger.error(f"Error fetching OpenAI models: {e}")
        return []


def _format_openai_model_name(model_id: str) -> str:
    """Format OpenAI model ID to display name."""
    name_map = {
        'gpt-4o': 'GPT-4o',
        'gpt-4o-mini': 'GPT-4o Mini',
        'gpt-4.1': 'GPT-4.1',
        'gpt-4.1-mini': 'GPT-4.1 Mini',
        'gpt-4.1-nano': 'GPT-4.1 Nano',
        'gpt-4-turbo': 'GPT-4 Turbo',
        'gpt-4': 'GPT-4',
        'gpt-3.5-turbo': 'GPT-3.5 Turbo',
        'o4-mini': 'o4-mini',
        'o3': 'o3',
        'o3-mini': 'o3-mini',
        'o3-pro': 'o3-pro',
        'o1': 'o1',
        'o1-mini': 'o1-mini',
        'chatgpt-4o-latest': 'ChatGPT-4o Latest',
    }

    if model_id in name_map:
        return name_map[model_id]

    for prefix, name in name_map.items():
        if model_id.startswith(prefix + '-'):
            suffix = model_id[len(prefix)+1:]
            return f"{name} ({suffix})"

    return model_id.upper().replace('-', ' ')


def get_anthropic_models(api_key: str):
    """
    Fetch available Anthropic models from the API.
    Only returns the latest version of each model family (deduplicates dated variants).

    Args:
        api_key: Anthropic API key

    Returns:
        List of dicts with 'id' (model name for API) and 'name' (display name).
        Returns empty list if API call fails.
    """
    import re
    if not api_key or not api_key.strip():
        logger.warning("No Anthropic API key provided")
        return []

    try:
        response = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            all_models = data.get('data', [])

            # Group models by family, keep only the latest dated version per family
            # e.g., claude-3-5-sonnet-20241022 and claude-3-5-sonnet-20240620 -> keep 20241022
            date_pattern = re.compile(r'-(\d{8})$')
            family_map = {}  # family_name -> (model_id, date_str, display_name)

            for model in all_models:
                model_id = model.get('id', '')
                if not model_id:
                    continue
                display_name = model.get('display_name', '') or _format_anthropic_model_name(model_id)

                date_match = date_pattern.search(model_id)
                if date_match:
                    family = date_pattern.sub('', model_id)
                    date_str = date_match.group(1)
                else:
                    family = model_id
                    date_str = '99999999'  # non-dated aliases sort as newest

                if family not in family_map or date_str > family_map[family][1]:
                    family_map[family] = (model_id, date_str, display_name)

            models = []
            for family, (model_id, date_str, display_name) in family_map.items():
                models.append({
                    'id': model_id,
                    'name': display_name
                })

            # Sort: newest families first (4.x > 3.5 > 3), opus > sonnet > haiku
            def sort_key(m):
                mid = m['id']
                # Claude 4.x family
                if 'claude-opus-4' in mid or 'claude-sonnet-4' in mid or 'claude-haiku-4' in mid:
                    if 'opus' in mid:
                        return (0, mid)
                    elif 'sonnet' in mid:
                        return (1, mid)
                    elif 'haiku' in mid:
                        return (2, mid)
                    return (3, mid)
                # Claude 3.5 family
                elif 'claude-3-5' in mid or 'claude-3.5' in mid:
                    if 'sonnet' in mid:
                        return (4, mid)
                    elif 'haiku' in mid:
                        return (5, mid)
                    elif 'opus' in mid:
                        return (6, mid)
                    return (7, mid)
                # Claude 3 family
                elif 'claude-3' in mid:
                    if 'opus' in mid:
                        return (8, mid)
                    elif 'sonnet' in mid:
                        return (9, mid)
                    elif 'haiku' in mid:
                        return (10, mid)
                    return (11, mid)
                return (12, mid)

            models.sort(key=sort_key)
            logger.info(f"Found {len(models)} Anthropic models (deduplicated from {len(all_models)} total)")
            return models
        else:
            logger.warning(f"Anthropic API returned status {response.status_code}: {response.text[:200]}")
            return []

    except requests.Timeout:
        logger.warning("Timeout fetching Anthropic models")
        return []
    except Exception as e:
        logger.error(f"Error fetching Anthropic models: {e}")
        return []


def _format_anthropic_model_name(model_id: str) -> str:
    """Format Anthropic model ID to display name."""
    name_map = {
        'claude-opus-4': 'Claude Opus 4',
        'claude-sonnet-4': 'Claude Sonnet 4',
        'claude-haiku-4': 'Claude Haiku 4',
        'claude-3-5-sonnet': 'Claude 3.5 Sonnet',
        'claude-3-5-haiku': 'Claude 3.5 Haiku',
        'claude-3-opus': 'Claude 3 Opus',
        'claude-3-sonnet': 'Claude 3 Sonnet',
        'claude-3-haiku': 'Claude 3 Haiku',
    }

    for prefix, name in name_map.items():
        if model_id.startswith(prefix):
            return name

    return model_id.replace('-', ' ').title()


# OpenRouter functions moved to distr/gui/utils/get_openrouter_models.py
# Import here for backward compatibility
def get_openrouter_models(api_key: str):
    """
    Fetch available OpenRouter models from the API.
    (Backward compatibility wrapper - delegates to new module)
    
    Args:
        api_key: OpenRouter API key
        
    Returns:
        List of dicts with 'id' (model name for API) and 'name' (display name).
        Returns empty list if API call fails.
    """
    from distr.gui.utils.get_openrouter_models import get_openrouter_models as _get_openrouter_models
    return _get_openrouter_models(api_key)


def get_groq_models(api_key: str):
    """
    Fetch available Groq models from the API.
    Only returns supported chat/completion models (no STT, embeddings, or guard models).
    All Groq models are free (rate-limited free tier).

    Args:
        api_key: Groq API key

    Returns:
        List of dicts with 'id' (model name for API) and 'name' (display name).
        Returns empty list if API call fails.
    """
    if not api_key or not api_key.strip():
        logger.warning("No Groq API key provided")
        return []

    try:
        response = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            all_models = data.get('data', [])

            # Skip non-chat models (STT, embeddings, guard/safety, tool-use variants)
            skip_patterns = [
                'embed', 'embedding', 'whisper', 'tts', 'guard',
                'moderation', 'distil-whisper'
            ]

            chat_models = []
            for model in all_models:
                model_id = model.get('id', '')
                if not model_id:
                    continue
                m_lower = model_id.lower()
                if any(x in m_lower for x in skip_patterns):
                    continue
                chat_models.append(model_id)

            # Sort: prioritize newer/larger models
            def sort_key(m):
                m_lower = m.lower()
                if 'llama-3.3' in m_lower or 'llama3.3' in m_lower:
                    return (0, m)
                elif 'llama-3.1' in m_lower or 'llama3.1' in m_lower:
                    return (1, m)
                elif 'llama-3' in m_lower or 'llama3' in m_lower:
                    return (2, m)
                elif 'deepseek' in m_lower:
                    return (3, m)
                elif 'mixtral' in m_lower:
                    return (4, m)
                elif 'gemma' in m_lower:
                    return (5, m)
                elif 'qwen' in m_lower:
                    return (6, m)
                return (7, m)

            chat_models.sort(key=sort_key)

            models = []
            for model_id in chat_models:
                display_name = f"{_format_groq_model_name(model_id)} (free)"
                models.append({
                    'id': model_id,
                    'name': display_name,
                    'is_free': True
                })

            logger.info(f"Found {len(models)} Groq chat models (filtered from {len(all_models)} total)")
            return models
        else:
            logger.warning(f"Groq API returned status {response.status_code}: {response.text[:200]}")
            return []

    except requests.Timeout:
        logger.warning("Timeout fetching Groq models")
        return []
    except Exception as e:
        logger.error(f"Error fetching Groq models: {e}")
        return []


def get_kilo_models(api_key: str):
    """
    Fetch available models from Kilo Gateway (OpenRouter-compatible API).
    Only returns supported chat models. Free models are tagged and sorted first.

    Args:
        api_key: Kilo API key from app.kilo.ai/profile

    Returns:
        List of dicts with 'id' (model name for API) and 'name' (display name).
        Returns empty list if API call fails.
    """
    if not api_key or not api_key.strip():
        logger.warning("No Kilo API key provided")
        return []
    try:
        response = requests.get(
            "https://api.kilo.ai/api/openrouter/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            all_models = data.get('data', [])

            # Skip non-chat models
            skip_patterns = ['embed', 'embedding', 'whisper', 'tts', 'dall-e', 'moderation']

            models = []
            for model in all_models:
                model_id = model.get('id', '')
                if not model_id:
                    continue
                m_lower = model_id.lower()
                if any(x in m_lower for x in skip_patterns):
                    continue

                name = model.get('name', '') or _format_kilo_model_name(model_id)

                # Check pricing (OpenRouter-compatible format)
                pricing = model.get('pricing') or {}
                prompt_cost = pricing.get('prompt') or '0'
                completion_cost = pricing.get('completion') or '0'
                is_free = (
                    (prompt_cost == '0' or float(prompt_cost or 0) == 0) and
                    (completion_cost == '0' or float(completion_cost or 0) == 0)
                )

                # Check tool support
                supported = model.get('supported_parameters') or []
                supports_tools = 'tools' in supported

                # Extract modality info for filtering by LLM type
                architecture = model.get('architecture') or {}
                input_modalities = architecture.get('input_modalities') or []
                output_modalities = architecture.get('output_modalities') or []

                # Build display name with tags
                display_parts = [name]
                if is_free:
                    display_parts.append("(free)")

                display_name = " ".join(display_parts)

                models.append({
                    'id': model_id,
                    'name': display_name,
                    'is_free': is_free,
                    'supports_tools': supports_tools,
                    'input_modalities': input_modalities,
                    'output_modalities': output_modalities,
                })

            # Sort: free first, then tool-supporting, then alphabetical
            def sort_key(m):
                free_prefix = 0 if m.get('is_free', False) else 1
                tools_prefix = 0 if m.get('supports_tools', False) else 1
                return (free_prefix, tools_prefix, m['id'].lower())

            models.sort(key=sort_key)

            if models:
                logger.info(f"Found {len(models)} Kilo Gateway models (filtered from {len(all_models)} total)")
            return models
        else:
            logger.warning(f"Kilo API returned status {response.status_code}: {response.text[:200]}")
            return []
    except requests.Timeout:
        logger.warning("Timeout fetching Kilo models")
        return []
    except Exception as e:
        logger.error(f"Error fetching Kilo models: {e}")
        return []


def _format_kilo_model_name(model_id: str) -> str:
    """Format Kilo/OpenRouter-style model ID to display name."""
    name = model_id
    if '/' in name:
        parts = name.split('/')
        if len(parts) > 1:
            name = parts[-1]
    name = name.replace('-', ' ').replace('_', ' ')
    return name.title()


def get_gemini_models(api_key: str):
    """
    Fetch available Google Gemini models from the API.

    Args:
        api_key: Google Gemini API key

    Returns:
        List of dicts with 'id' and 'name'.
        Returns empty list if API call fails.
    """
    if not api_key or not api_key.strip():
        logger.warning("No Gemini API key provided")
        return []

    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/openai/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            all_models = data.get('data', [])

            chat_models = []
            for model in all_models:
                model_id = model.get('id', '')
                if not model_id:
                    continue

                # Strip 'models/' prefix from Google's response
                if model_id.startswith('models/'):
                    model_id = model_id[7:]

                # Only include gemini chat models
                if not model_id.lower().startswith(('gemini-', 'gemma-')):
                    continue

                # Skip embedding, TTS, and image-only models
                if any(x in model_id.lower() for x in ['embed', '-tts', 'imagen']):
                    continue

                chat_models.append(model_id)

            # Sort: newer/larger models first
            def sort_key(m):
                ml = m.lower()
                if 'pro' in ml:
                    return (0, m)
                elif 'flash' in ml:
                    return (1, m)
                elif 'nano' in ml:
                    return (2, m)
                return (3, m)

            chat_models.sort(key=sort_key)

            models = []
            for model_id in chat_models:
                display_name = _format_gemini_model_name(model_id)
                models.append({
                    'id': model_id,
                    'name': display_name
                })

            logger.info(f"Found {len(models)} Gemini models (filtered from {len(all_models)} total)")
            return models
        else:
            logger.warning(f"Gemini API returned status {response.status_code}: {response.text[:200]}")
            return []
    except Exception as e:
        logger.error(f"Error fetching Gemini models: {e}")
        return []


def _format_gemini_model_name(model_id: str) -> str:
    """Format Gemini model ID to display name."""
    name = model_id
    # Remove 'models/' prefix if present
    if name.startswith('models/'):
        name = name[7:]
    # Capitalize and clean up
    name = name.replace('-', ' ').replace('_', ' ')
    return name.title()


def _format_groq_model_name(model_id: str) -> str:
    """Format Groq model ID to display name."""
    # Common Groq model patterns
    # Examples: "llama-3.3-70b-versatile", "mixtral-8x7b-32768", "gemma-7b-it"
    
    # Remove common prefixes/suffixes
    name = model_id
    
    # Handle common patterns
    name_map = {
        'llama-3.3-70b-versatile': 'Llama 3.3 70B Versatile',
        'llama-3.1-70b-versatile': 'Llama 3.1 70B Versatile',
        'llama-3.1-8b-instant': 'Llama 3.1 8B Instant',
        'mixtral-8x7b-32768': 'Mixtral 8x7B',
        'gemma-7b-it': 'Gemma 7B IT',
    }
    
    if model_id in name_map:
        return name_map[model_id]
    
    # Generic formatting: replace dashes with spaces, capitalize
    display = model_id.replace('-', ' ').replace('_', ' ')
    # Capitalize first letter of each word
    display = ' '.join(word.capitalize() for word in display.split())
    
    return display


def get_installed_ollama_models():
    """
    Get list of locally installed Ollama models, excluding embedding models.
    Enriches each model with capability tags (tools, vision, code, etc.) from the library cache.
    
    Returns:
        List of dicts with 'id' (model name for API), 'name' (display name), and capability fields.
        Returns empty list if Ollama is not available or no models installed.
    """
    try:
        # Get installed models from Ollama CLI
        result = subprocess.run(
            ['ollama', 'list'], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) <= 1:
                logger.warning("No Ollama models installed")
                return []
            
            # Build a lookup of capabilities from the library cache
            cap_lookup = {}
            try:
                library_models = get_ollama_models() or []
                for lm in library_models:
                    lm_name = (lm.get('name') or '').lower()
                    if lm_name:
                        cap_lookup[lm_name] = lm.get('capabilities') or []
            except Exception:
                pass
            
            models = []
            # Skip header line
            for line in lines[1:]:
                if line.strip():
                    parts = line.split()
                    if parts:
                        model_id = parts[0]  # e.g., "qwen3:8b" or "gemma3:4b"
                        
                        # Filter out embedding models - they don't support chat
                        model_id_lower = model_id.lower()
                        if any(exclude in model_id_lower for exclude in ['embed', 'embedding', 'nomic-embed']):
                            logger.debug(f"Skipping embedding model: {model_id}")
                            continue
                        
                        # Look up capabilities from library cache by base model name
                        base_name = model_id.split(':')[0].lower() if ':' in model_id else model_id.lower()
                        caps = cap_lookup.get(base_name, [])
                        
                        # Build display name with capability tags for Ollama
                        display_name = _format_model_display_name(model_id)
                        if caps:
                            cap_str = ", ".join(caps)
                            display_name = f"{display_name} ({cap_str})"
                        
                        entry = {
                            'id': model_id,
                            'name': display_name,
                        }
                        # Store capability flags for filtering
                        if 'tools' in caps:
                            entry['supports_tools'] = True
                        if 'vision' in caps:
                            entry['input_modalities'] = ['text', 'image']
                        if 'code' in caps:
                            entry['is_code'] = True
                        
                        models.append(entry)
            
            logger.info(f"Found {len(models)} installed Ollama chat models: {[m['id'] for m in models]}")
            return models
        else:
            logger.warning(f"Ollama list failed: {result.stderr}")
            return []
            
    except subprocess.TimeoutExpired:
        logger.warning("Timeout getting Ollama models")
        return []
    except FileNotFoundError:
        logger.warning("Ollama CLI not found - is Ollama installed?")
        return []
    except Exception as e:
        logger.error(f"Error getting installed Ollama models: {e}")
        return []


def _format_model_display_name(model_id: str) -> str:
    """
    Format a model ID into a human-readable display name.
    
    Examples:
        "llama3.1:8b" -> "Llama 3.1 (8B)"
        "gemma3:4b" -> "Gemma 3 (4B)"
        "mistral:latest" -> "Mistral"
        "qwen2.5:14b" -> "Qwen 2.5 (14B)"
    """
    # Split into name and tag
    if ':' in model_id:
        name, tag = model_id.split(':', 1)
    else:
        name = model_id
        tag = ''
    
    # Capitalize and format the name
    # Handle common patterns like "llama3.1" -> "Llama 3.1"
    display = name.replace('-', ' ').replace('_', ' ')
    
    # Insert space before numbers but keep decimals together
    import re
    display = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', display)
    
    # Capitalize first letter of each word
    display = display.title()
    
    # Format the tag (size)
    if tag and tag != 'latest':
        # Format size tags like "8b" -> "8B", "14b" -> "14B"
        tag_upper = tag.upper()
        display = f"{display} ({tag_upper})"
    
    return display


def scrape_ollama_library():
    """
    Scrape Ollama library website for available models.
    Returns list of models with name, description, and sizes.
    """
    output_file_path = os.path.join(MODELS_DIR, 'ollama_models.json')
    
    # Create the MODELS_DIR if it doesn't exist
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    logger.info("Fetching Ollama library from web...")
    
    try:
        url = "https://ollama.com/library"
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch Ollama library. Status: {response.status_code}")
            return []
        
        content = response.text
        
        # Parse the content
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, 'html.parser')
        models = parse_ollama_library(soup)
        
        # Save to JSON file for caching with timestamp
        cache_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'models': models
        }
        with open(output_file_path, 'w', encoding='utf-8') as file:
            json.dump(cache_data, file, indent=2)
        
        logger.info(f"Scraped {len(models)} models from Ollama library")
        return models
        
    except Exception as e:
        logger.error(f"Error scraping Ollama library: {e}")
        return []


def fetch_and_cache_ollama_models():
    """
    Fetch and cache Ollama models with 24-hour timestamp validation.
    
    Returns:
        List of model dicts. Returns empty list on error (preserves existing cache).
    """
    output_file_path = os.path.join(MODELS_DIR, 'ollama_models.json')
    
    # Create the MODELS_DIR if it doesn't exist
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    # Check if cache exists and is valid (within 24 hours)
    cache_valid = False
    cached_models = []
    
    if os.path.exists(output_file_path):
        try:
            with open(output_file_path, 'r', encoding='utf-8') as file:
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
                            logger.info(f"Using cached Ollama models (age: {age})")
                        else:
                            logger.info(f"Ollama cache expired (age: {age}), fetching new models")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid timestamp in cache: {e}, fetching new models")
                else:
                    logger.warning("Cache missing timestamp, fetching new models")
            else:
                # Old format (just a list) - migrate it
                logger.info("Migrating old Ollama cache format to new format")
                if isinstance(cache_data, list):
                    cached_models = cache_data
                else:
                    cached_models = cache_data.get('models', [])
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error reading Ollama cache: {e}, fetching new models")
    
    # If cache is valid, return it
    if cache_valid:
        return cached_models
    
    # Cache is invalid or missing - fetch new models
    logger.info("Fetching Ollama models from web...")
    
    try:
        url = "https://ollama.com/library"
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            logger.warning(f"Failed to fetch Ollama library. Status: {response.status_code}")
            # Return cached models if available, otherwise empty list
            if cached_models:
                logger.info("Returning stale cache due to fetch failure")
                return cached_models
            return []
        
        content = response.text
        
        # Parse the content
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, 'html.parser')
        models = parse_ollama_library(soup)
        
        if not models:
            logger.warning("No models parsed from Ollama library")
            # Return cached models if available
            if cached_models:
                logger.info("Returning stale cache due to empty parse result")
                return cached_models
            return []
        
        # Save to JSON file for caching with timestamp
        cache_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'models': models
        }
        with open(output_file_path, 'w', encoding='utf-8') as file:
            json.dump(cache_data, file, indent=2)
        
        logger.info(f"✅ Successfully fetched and cached {len(models)} Ollama models")
        return models
        
    except requests.Timeout:
        logger.warning("Timeout fetching Ollama models")
        # Return cached models if available
        if cached_models:
            logger.info("Returning stale cache due to timeout")
            return cached_models
        return []
    except Exception as e:
        logger.error(f"Error fetching Ollama models: {e}", exc_info=True)
        # Return cached models if available
        if cached_models:
            logger.info("Returning stale cache due to error")
            return cached_models
        return []


def parse_ollama_library(soup):
    """Parse Ollama library HTML to extract model information."""
    models = []
    
    # Find all model links
    model_links = soup.select('a[href*="/library/"]')
    
    # Tags that represent capabilities (not sizes)
    capability_tags = {'tools', 'vision', 'cloud', 'thinking', 'embedding', 'code'}
    
    for a in model_links:
        href = a.get('href', '')
        if '/library/' not in href:
            continue
            
        model_name = href.replace('/library/', '').strip()
        if not model_name or '/' in model_name:
            continue
        
        model = {'name': model_name}
        
        # Get full text and extract description
        full_text = a.get_text(separator=' ', strip=True)
        
        # Description is everything after the model name until sizes/stats
        if model_name in full_text:
            parts = full_text.split(model_name, 1)
            if len(parts) > 1:
                desc_text = parts[1].strip()
                # Take first sentence or up to first size marker
                desc_words = []
                for word in desc_text.split():
                    # Stop at size markers or stats
                    if word.lower().endswith('b') and word[:-1].replace('.', '').isdigit():
                        break
                    if word.lower().endswith('m') and word[:-1].replace('.', '').isdigit():
                        break
                    if 'Pulls' in word or word.isdigit():
                        break
                    desc_words.append(word)
                model['description'] = ' '.join(desc_words[:30])  # Limit length
        
        # Find size/capability spans
        size_spans = a.find_all('span', class_=lambda c: c and 'inline-flex' in c)
        sizes = []
        capabilities = []
        for s in size_spans:
            size_text = s.get_text(strip=True).lower()
            if size_text in capability_tags:
                capabilities.append(size_text)
            else:
                # Validate it looks like a size (number + b/m)
                if any(size_text.endswith(x) for x in ['b', 'm']):
                    if any(c.isdigit() for c in size_text):
                        sizes.append(size_text.upper())
        
        model['sizes'] = sizes if sizes else ['latest']
        if capabilities:
            model['capabilities'] = capabilities
        
        models.append(model)
    
    return models


# Legacy function for backwards compatibility
def parse_content(soup):
    """Legacy parser - redirects to new parser."""
    return parse_ollama_library(soup)

def is_file_older_than_a_day(file_path):
    file_time = os.path.getmtime(file_path)
    current_time = time.time()
    return (current_time - file_time) > timedelta(days=1).total_seconds()

def get_ollama_models():
    """Get models from Ollama library (scraped from website). 
    For installed models, use get_installed_ollama_models() instead.
    
    This function reads from cache with timestamp validation (24-hour validity).
    Backward compatible with old format.
    """
    output_file_path = os.path.join(MODELS_DIR, 'ollama_models.json')
    
    if not os.path.exists(output_file_path):
        return []
    
    try:
        with open(output_file_path, 'r', encoding='utf-8') as file:
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
                        logger.debug(f"Using cached Ollama models ({len(models)} models, age: {age})")
                        return models
                    else:
                        logger.debug(f"Ollama cache expired (age: {age})")
                        return []
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid timestamp in cache: {e}")
                    return []
            else:
                # Has timestamp field but empty - invalid
                logger.warning("Cache has timestamp field but it's empty")
                return []
        elif isinstance(cache_data, dict) and 'models' in cache_data:
            # Old format without timestamp - use it but log warning
            models = cache_data.get('models', [])
            logger.debug(f"Using cached Ollama models (no timestamp, {len(models)} models)")
            return models
        # Handle old format (just a list)
        elif isinstance(cache_data, list):
            logger.debug(f"Using cached Ollama models (legacy format, {len(cache_data)} models)")
            return cache_data
        else:
            logger.warning("Unexpected cache format for Ollama models")
            return []
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error reading Ollama models cache: {e}")
        return []


def get_available_ollama_models_with_sizes():
    """
    Get available Ollama models from the library with their available sizes.
    Also marks which models/sizes are already installed.
    
    Returns:
        List of dicts with model info including 'name', 'description', 'sizes', 
        and 'installed_sizes' (list of sizes already installed).
    """
    import re
    
    # Get available models from library
    available_models = get_ollama_models() or []
    
    # Get installed models
    installed = get_installed_ollama_models()
    installed_ids = set(m['id'] for m in installed)
    
    # Parse installed model names to get base name and size variants
    # e.g., "qwen2.5:7b-instruct" -> base="qwen2.5", sizes={"7b-instruct", "7b"}
    installed_by_base = {}
    for model_id in installed_ids:
        if ':' in model_id:
            base, tag = model_id.split(':', 1)
        else:
            base = model_id
            tag = 'latest'
        
        base_lower = base.lower()
        if base_lower not in installed_by_base:
            installed_by_base[base_lower] = set()
        
        tag_lower = tag.lower()
        installed_by_base[base_lower].add(tag_lower)
        
        # Also extract just the size portion (e.g., "7b" from "7b-instruct")
        size_match = re.match(r'^(\d+\.?\d*[bm])', tag_lower)
        if size_match:
            installed_by_base[base_lower].add(size_match.group(1))
    
    # Enrich available models with installation status
    for model in available_models:
        model_name = model.get('name', '').lower()
        sizes = model.get('sizes', [])
        
        # Check which sizes are installed
        installed_sizes = []
        if model_name in installed_by_base:
            installed_tags = installed_by_base[model_name]
            for size in sizes:
                size_lower = size.lower()
                # Direct match
                if size_lower in installed_tags:
                    installed_sizes.append(size)
                    continue
                # Check if any installed tag starts with this size
                for tag in installed_tags:
                    if tag.startswith(size_lower):
                        installed_sizes.append(size)
                        break
        
        model['installed_sizes'] = list(set(installed_sizes))
        model['any_installed'] = len(model['installed_sizes']) > 0
    
    return available_models


def pull_ollama_model(model_name: str, size: str = None, progress_callback=None):
    """
    Download/pull an Ollama model.
    
    Args:
        model_name: Base model name (e.g., "llama3.1")
        size: Size tag (e.g., "8b", "70b"). If None, pulls "latest"
        progress_callback: Optional callback function(line: str) for progress updates
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    # Construct the full model tag
    if size and size.lower() != 'latest':
        model_tag = f"{model_name}:{size.lower()}"
    else:
        model_tag = model_name
    
    logger.info(f"Starting download of Ollama model: {model_tag}")
    
    try:
        # Use subprocess.Popen to stream output
        process = subprocess.Popen(
            ['ollama', 'pull', model_tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Stream output
        output_lines = []
        for line in process.stdout:
            line = line.strip()
            if line:
                output_lines.append(line)
                if progress_callback:
                    progress_callback(line)
                logger.debug(f"Ollama pull: {line}")
        
        process.wait()
        
        if process.returncode == 0:
            logger.info(f"Successfully downloaded model: {model_tag}")
            return True, f"Successfully downloaded {model_tag}"
        else:
            error_msg = '\n'.join(output_lines[-5:]) if output_lines else "Unknown error"
            logger.error(f"Failed to download model {model_tag}: {error_msg}")
            return False, f"Failed to download {model_tag}: {error_msg}"
            
    except FileNotFoundError:
        logger.error("Ollama CLI not found")
        return False, "Ollama is not installed. Please install Ollama first."
    except Exception as e:
        logger.error(f"Error downloading model {model_tag}: {e}")
        return False, f"Error: {str(e)}"


def get_installed_model_ids():
    """Get a set of installed model IDs for quick lookup."""
    models = get_installed_ollama_models()
    return set(m['id'] for m in models)