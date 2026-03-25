"""
Image utilities for LLM vision support

Shared utilities for converting images to WebP format and encoding as base64
for use across all LLM services that support vision.
"""

import base64
import logging
import mimetypes
import os
import tempfile
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


def convert_image_to_base64(
    image_path: str,
    convert_to_webp: bool = True,
    quality: int = 80
) -> Tuple[str, str]:
    """
    Convert an image to base64 encoding, optionally converting to WebP first.

    Args:
        image_path: Path to the image file
        convert_to_webp: Whether to convert to WebP format for better compression
        quality: WebP quality (1-100), default 80

    Returns:
        Tuple of (base64_encoded_data, mime_type)

    Raises:
        FileNotFoundError: If image file doesn't exist
        Exception: For other image processing errors
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    original_size = os.path.getsize(image_path)

    if convert_to_webp:
        try:
            from PIL import Image

            # Open and convert to WebP
            img = Image.open(image_path)

            # Convert RGBA/LA/P to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            else:
                img = img.convert('RGB')

            # Save to temporary WebP file
            with tempfile.NamedTemporaryFile(suffix='.webp', delete=False) as tmp_file:
                tmp_webp_path = tmp_file.name

            # Save as WebP with specified quality
            img.save(tmp_webp_path, 'WEBP', quality=quality, method=6)

            # Read the WebP file
            with open(tmp_webp_path, 'rb') as webp_file:
                image_data = webp_file.read()

            # Clean up temp file
            try:
                os.unlink(tmp_webp_path)
            except OSError:
                pass

            base64_image = base64.b64encode(image_data).decode('utf-8')
            mime_type = 'image/webp'

            # Log conversion stats
            webp_size = len(image_data)
            compression_ratio = (1 - (webp_size / original_size)) * 100 if original_size > 0 else 0

            orig_str = _format_size(original_size)
            webp_str = _format_size(webp_size)

            logger.info(f"[Vision] ✅ Converted image to WebP: {orig_str} → {webp_str} ({compression_ratio:.1f}% smaller)")

            return base64_image, mime_type

        except Exception as e:
            logger.warning(f"Failed to convert image to WebP, using original: {e}")
            # Fall through to original image handling

    # Use original image (fallback or if WebP conversion disabled)
    with open(image_path, 'rb') as image_file:
        image_data = image_file.read()
        base64_image = base64.b64encode(image_data).decode('utf-8')

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith('image/'):
            mime_type = 'image/jpeg'

        logger.info(f"[Vision] Using original image format: {mime_type}")

        return base64_image, mime_type


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def check_vision_model_support(model_name: str, provider: str) -> bool:
    """
    Check if a model supports vision based on model name and provider.

    Args:
        model_name: Name of the model
        provider: Provider name (OpenAI, Anthropic, OpenRouter, Groq, KiloCode)

    Returns:
        True if model supports vision, False otherwise
    """
    model_lower = model_name.lower()

    # OpenAI vision models
    if provider == "OpenAI":
        return any(v in model_lower for v in ['gpt-4', 'o1', 'o3'])

    # Anthropic Claude vision models (all Claude 3+ models support vision)
    elif provider == "Anthropic":
        return any(v in model_lower for v in ['claude-3', 'claude-opus', 'claude-sonnet', 'claude-haiku'])

    # OpenRouter (depends on underlying model)
    elif provider == "OpenRouter":
        return any(v in model_lower for v in ['gpt-4', 'claude-3', 'vision', 'llava', 'moondream'])

    # Groq vision models
    elif provider == "Groq":
        return any(v in model_lower for v in ['llama-3.2', 'llama-vision', 'vision'])

    # KiloCode (routes to various providers)
    elif provider == "KiloCode":
        return any(v in model_lower for v in ['gpt-4', 'claude-3', 'vision', 'llava'])

    # Ollama vision models
    elif provider == "Ollama":
        # Known vision models
        vision_models = ['llava', 'bakllava', 'llama-vision', 'moondream', 'minicpm-v', 'vision']
        if any(v in model_lower for v in vision_models):
            return True

        # Known non-vision models
        non_vision_models = ['llama', 'mistral', 'phi', 'gemma', 'qwen', 'codellama']
        is_non_vision = any(nv in model_lower for nv in non_vision_models)

        # For Ollama, assume vision support unless it's a known non-vision model
        return not is_non_vision

    # Unknown provider or no vision support
    return False


def get_image_path_from_context(uploaded_image_path: Optional[str] = None) -> Optional[str]:
    """
    Get image path from either direct parameter or thread-local storage.

    Args:
        uploaded_image_path: Directly provided image path

    Returns:
        Image path if found, None otherwise
    """
    import threading

    # Check direct parameter first
    if uploaded_image_path and uploaded_image_path.strip():
        return uploaded_image_path.strip()

    # Fall back to thread-local storage (for Telegram uploads)
    image_path = getattr(threading.current_thread(), 'telegram_uploaded_image', None)
    if image_path:
        return image_path

    return None


async def analyze_image_with_vision_llm(image_path: str, user_text: str, settings: dict = None) -> str:
    """
    Analyze an image using the configured Vision LLM (from global settings).

    Loads vision provider/model from DB settings, makes the appropriate API call
    (OpenAI or Ollama), and returns the analysis text. This replaces ~200 lines
    of inline vision code that was duplicated across providers.

    Args:
        image_path: Path to the image file
        user_text: The user's question/prompt about the image
        settings: Optional pre-loaded settings dict. If None, loads from DB.

    Returns:
        Analysis text from the vision LLM, or a fallback error message.
    """
    if not settings:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()

    vision_provider = (
        (settings.get('vision_llm_provider') or '').strip()
        or (settings.get('conversational_llm_provider') or '').strip()
        or 'OpenAI'
    )
    vision_model = (
        (settings.get('vision_llm_model') or '').strip()
        or (settings.get('conversational_llm_model') or '').strip()
        or 'gpt-4o'
    )

    provider_key = vision_provider.lower()
    model_lower = vision_model.lower() if vision_model else ""

    # Check if the vision model actually supports vision
    vision_capable = any(v in model_lower for v in [
        'gpt-4', 'o1', 'o3', 'llava', 'vision', 'moondream',
        'claude-3', 'minicpm', 'qwen3-vl', 'qwen-vl',
    ])

    if not vision_capable:
        logger.warning(f"[Vision] No valid vision model configured: {vision_provider}/{vision_model}")
        return ""

    logger.info(f"[Vision] Analyzing image with {vision_provider}/{vision_model}")

    try:
        base64_image, mime_type = convert_image_to_base64(image_path)
    except Exception as e:
        logger.error(f"[Vision] Failed to encode image: {e}", exc_info=True)
        return f"[Image analysis failed: {e}]"

    prompt_text = f"Please analyze this image. The user asked: {user_text}"

    # --- OpenAI-compatible providers ---
    if provider_key in ('openai', 'openrouter', 'groq', 'kilocode'):
        try:
            from openai import AsyncOpenAI

            api_key = settings.get('openai_key', '')
            base_url = None
            if provider_key == 'openrouter':
                api_key = settings.get('openrouter_key', api_key)
                base_url = 'https://openrouter.ai/api/v1'
            elif provider_key == 'groq':
                api_key = settings.get('groq_key', api_key)
                base_url = 'https://api.groq.com/openai/v1'
            elif provider_key == 'kilocode':
                api_key = settings.get('kilocode_key', api_key)
                base_url = settings.get('kilocode_url', None)

            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url

            client = AsyncOpenAI(**client_kwargs)
            vision_messages = [
                {"role": "system", "content": "You are a helpful assistant that analyzes images. Describe what you see in detail, including any text, objects, people, or notable elements. Be thorough but concise."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                ]}
            ]

            response = await client.chat.completions.create(
                model=vision_model, messages=vision_messages, max_tokens=1024
            )
            analysis = response.choices[0].message.content.strip()
            logger.info(f"[Vision] ✅ Got analysis ({len(analysis)} chars)")
            return analysis

        except Exception as e:
            logger.error(f"[Vision] OpenAI-compatible vision call failed: {e}", exc_info=True)
            return f"[Image analysis failed: {e}]"

    # --- Anthropic ---
    elif provider_key == 'anthropic':
        try:
            import anthropic as _anthropic

            api_key = settings.get('anthropic_key', '')
            client = _anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=vision_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": base64_image}}
                ]}],
            )
            analysis = response.content[0].text.strip()
            logger.info(f"[Vision] ✅ Got Anthropic analysis ({len(analysis)} chars)")
            return analysis

        except Exception as e:
            logger.error(f"[Vision] Anthropic vision call failed: {e}", exc_info=True)
            return f"[Image analysis failed: {e}]"

    # --- Ollama ---
    elif provider_key == 'ollama':
        try:
            import aiohttp

            ollama_url = settings.get('ollama_url', 'http://localhost:11434')
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": vision_model,
                    "prompt": prompt_text,
                    "images": [base64_image],
                    "stream": False,
                }
                async with session.post(f"{ollama_url}/api/generate", json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        analysis = result.get('response', '')
                        logger.info(f"[Vision] ✅ Got Ollama analysis ({len(analysis)} chars)")
                        return analysis
                    else:
                        raise Exception(f"Ollama returned HTTP {resp.status}")

        except Exception as e:
            logger.error(f"[Vision] Ollama vision call failed: {e}", exc_info=True)
            return f"[Image analysis failed: {e}]"

    logger.warning(f"[Vision] Unsupported vision provider: {vision_provider}")
    return ""
