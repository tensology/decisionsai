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
    """Return True when the chat model accepts image input.

    Mirrors the Development composer's modelSupportsImages() heuristic
    (threads/composer/index.js) so the Oracle and the UI agree. Provider
    matching is case-insensitive (service names are e.g. "OpenAI").
    """
    import re as _re

    model_lower = (model_name or "").lower()
    provider_key = (provider or "").lower().replace(" ", "")
    if provider_key == "ollama":
        vision_models = ['llava', 'bakllava', 'llama-vision', 'moondream', 'minicpm-v', 'vision', 'gemma3', 'gemma-3', 'qwen2.5vl', 'qwen3-vl', 'llama4']
        if any(v in model_lower for v in vision_models):
            return True
        non_vision_models = ['llama', 'mistral', 'phi', 'gemma', 'qwen', 'codellama', 'deepseek']
        return not any(nv in model_lower for nv in non_vision_models)
    if provider_key == "openai" and _re.search(r"gpt|o1|o3|o4", model_lower):
        return True
    if provider_key == "anthropic" and _re.search(r"claude-(3|4|opus|sonnet|haiku)", model_lower):
        return True
    return bool(_re.search(r"(gpt-4|gpt-5|claude-3|claude-4|gemini|gemma-3|llama-4|qwen.*vl|vision|pixtral|llava|moondream)", model_lower))


def resolve_vision_model(settings: dict = None) -> Optional[Tuple[str, str]]:
    """Return the configured (provider, model) for image description, or None.

    Uses vision_llm_provider/vision_llm_model; falls back to the conversational
    model only when that model can itself read images.
    """
    if settings is None:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
    for prov_key, model_key in (("vision_llm_provider", "vision_llm_model"),
                                ("conversational_llm_provider", "conversational_llm_model")):
        provider = (settings.get(prov_key) or "").strip()
        model = (settings.get(model_key) or "").strip()
        if provider and model and check_vision_model_support(model, provider):
            return provider, model
    return None


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

    resolved = resolve_vision_model(settings)
    if not resolved:
        logger.warning("[Vision] No vision-capable model configured")
        return ""
    vision_provider, vision_model = resolved
    provider_key = vision_provider.lower()

    logger.info(f"[Vision] Analyzing image with {vision_provider}/{vision_model}")

    try:
        base64_image, mime_type = convert_image_to_base64(image_path)
    except Exception as e:
        logger.error(f"[Vision] Failed to encode image: {e}", exc_info=True)
        return f"[Image analysis failed: {e}]"

    prompt_text = (
        "Describe this image in detail: all visible text (verbatim), objects, layout, "
        f"colors and anything relevant to the user's message. The user asked: {user_text}"
    )

    # --- OpenAI-compatible providers ---
    if provider_key in ('openai', 'openrouter', 'groq', 'kilocode', 'gemini', 'nvidia'):
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
            elif provider_key == 'gemini':
                api_key = settings.get('gemini_key', api_key)
                base_url = 'https://generativelanguage.googleapis.com/v1beta/openai/'
            elif provider_key == 'nvidia':
                api_key = settings.get('nvidia_key', api_key)
                base_url = 'https://integrate.api.nvidia.com/v1'

            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url

            # One client per call: bound it and close it so httpx connection
            # pools are not leaked (SDK default is 600s x 2 retries).
            client_kwargs.setdefault("timeout", 120.0)
            client = AsyncOpenAI(**client_kwargs)
            vision_messages = [
                {"role": "system", "content": "You are a helpful assistant that analyzes images. Describe what you see in detail, including any text, objects, people, or notable elements. Be thorough but concise."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                ]}
            ]

            try:
                response = await client.chat.completions.create(
                    model=vision_model, messages=vision_messages, max_tokens=1024
                )
            finally:
                await client.close()
            analysis = (response.choices[0].message.content or "").strip()
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
            client = _anthropic.AsyncAnthropic(api_key=api_key, timeout=120.0)
            try:
                response = await client.messages.create(
                    model=vision_model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": base64_image}}
                    ]}],
                )
            finally:
                await client.close()
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
