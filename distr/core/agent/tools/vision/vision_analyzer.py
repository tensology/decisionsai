"""
Vision Analyzer Tool

A tool that analyzes image files using vision-enabled LLMs.
Supports commands like "analyze this image", "what's in this picture", "describe this file".
Can work with dropped files or explicit file paths.
"""

import json
import logging
import os
from typing import Optional, Any
from pathlib import Path

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class VisionAnalyzerInput(BaseModel):
    """Input schema for vision_analyzer tool."""
    prompt: str = Field(description="The question or instruction about what to analyze in the image (e.g., 'What do you see?', 'Describe this image', 'What's in this picture?')")
    file_path: Optional[str] = Field(default=None, description="Optional: Path to the image file to analyze. If not provided, will use the most recently dropped file.")


def is_image_file(file_path: str) -> bool:
    """Check if a file is an image based on extension."""
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.svg', '.ico', '.heic', '.heif'}
    ext = Path(file_path).suffix.lower()
    return ext in image_extensions


def get_dropped_files() -> Optional[list]:
    """Get files that were dropped on the oracle ball."""
    import json
    storage_dir = os.path.join(os.path.expanduser("~"), ".decisions", "dropped_files")
    storage_file = os.path.join(storage_dir, "current_files.json")
    
    if not os.path.exists(storage_file):
        return None
    
    try:
        with open(storage_file, 'r') as f:
            data = json.load(f)
            files = data.get("files", [])
            # Only return files that still exist
            existing_files = [f for f in files if os.path.exists(f)]
            return existing_files if existing_files else None
    except Exception as e:
        logger.error(f"Error reading dropped files: {e}")
        return None


def get_last_dropped_image() -> Optional[str]:
    """Get the most recently dropped image file."""
    dropped_files = get_dropped_files()
    if not dropped_files:
        return None
    
    # Filter to only image files, return the last one (most recently dropped)
    image_files = [f for f in dropped_files if os.path.isfile(f) and is_image_file(f)]
    return image_files[-1] if image_files else None


def resolve_vision_llm_config(settings: dict) -> tuple[str, str]:
    """Resolve vision provider/model from global settings only."""
    provider = (
        (settings.get('vision_llm_provider') or '').strip()
        or (settings.get('conversational_llm_provider') or '').strip()
        or 'Ollama'
    )
    model = (
        (settings.get('vision_llm_model') or '').strip()
        or (settings.get('conversational_llm_model') or '').strip()
        or ''
    )
    return provider, model


class VisionAnalyzerTool(BaseTool):
    """
    Tool to analyze image files using vision-enabled LLMs.
    
    When called, this tool:
    1. Locates the image file (from dropped files or explicit path)
    2. Converts it to base64
    3. Calls the LLM service with the image and user's prompt
    4. Returns the model's analysis
    """
    
    name: str = "vision_analyzer"
    description: str = (
        "🎯 USE THIS TOOL when the user wants to analyze an image file or picture. "
        "This tool can analyze images that were dropped onto the oracle ball or images specified by file path. "
        ""
        "Use this tool when: "
        "- User says 'analyze this image', 'what's in this picture', 'describe this image', 'what do you see in this file' "
        "- User asks about a dropped file: 'what did I just drop', 'analyze what I dropped', 'what's in the file I gave you' "
        "- User mentions an image file: 'analyze this photo', 'what's in this picture', 'describe this image' "
        "- User wants to understand visual content: 'what does this show', 'what can you see', 'tell me about this image' "
        ""
        "The tool automatically finds the most recently dropped image file if no path is specified. "
        "It supports common image formats: PNG, JPG, JPEG, GIF, BMP, WEBP, TIFF, SVG, etc."
    )
    args_schema: type[BaseModel] = VisionAnalyzerInput
    _last_telegram_image: Optional[str] = None  # Shared storage for cross-thread Telegram send

    llm_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self.llm_service = llm_service
    
    def _run(self, prompt: str = "", file_path: Optional[str] = None, **kwargs) -> str:
        """
        Analyze an image file.
        
        Args:
            prompt: The question or instruction about what to analyze
            file_path: Optional path to the image file. If not provided, uses most recently dropped image.
        
        Returns:
            Analysis result from the vision model
        """
        # Extract actual prompt from user text if needed
        original_text = kwargs.get('text', '') or kwargs.get('transcription', '')
        if not prompt or prompt == "__ORIGINAL_TEXT__":
            if original_text:
                # Extract a meaningful prompt from the user's text
                text_lower = original_text.lower()
                prompt = original_text
                
                # Remove trigger phrases to get the actual question
                triggers = [
                    'analyze this image', 'analyze this picture', 'analyze this file',
                    'what\'s in this image', 'what\'s in this picture', 'what\'s in this file',
                    'what do you see', 'describe this image', 'describe this picture',
                    'what did i drop', 'what did i just drop', 'analyze what i dropped',
                    'what\'s in the file i gave you', 'what\'s in the file i dropped'
                ]
                
                for trigger in triggers:
                    if trigger in text_lower:
                        prompt = original_text.replace(trigger, '').strip()
                        break
                
                # If prompt is empty or just trigger words, use a default
                if not prompt or len(prompt) < 5:
                    prompt = "What do you see in this image? Provide a detailed description."
            else:
                prompt = "What do you see in this image? Provide a detailed description."
        
        # Determine which file to analyze
        image_path = None
        
        if file_path:
            # Explicit path provided
            if not os.path.exists(file_path):
                return f"Error: File not found: {file_path}"
            if not is_image_file(file_path):
                return f"Error: File is not an image file: {file_path}. Supported formats: PNG, JPG, JPEG, GIF, BMP, WEBP, TIFF, SVG, etc."
            image_path = file_path
            logger.info(f"VisionAnalyzer: Using explicit file path: {image_path}")
        else:
            # Try to find the most recently dropped image
            image_path = get_last_dropped_image()
            if not image_path:
                return "Error: No image file specified and no image files were found in recently dropped files. Please specify a file path or drop an image file onto the oracle ball first."
            logger.info(f"VisionAnalyzer: Using most recently dropped image: {image_path}")
        
        # Verify file exists and is readable
        if not os.path.exists(image_path):
            return f"Error: Image file not found: {image_path}"
        
        if not os.access(image_path, os.R_OK):
            return f"Error: Cannot read image file: {image_path} (permission denied)"
        
        # IMPORTANT: Only store analyzed image if it was captured locally (screenshot), NOT if it came FROM Telegram
        # Check if this image is from a Telegram download (we don't want to send it back)
        import threading
        is_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request
        is_telegram_image = False
        
        if is_telegram_request:
            # Check if the image path is from a Telegram download
            # Telegram downloads are stored in temp directory with "telegram_" prefix
            image_path_str = str(image_path)
            if "telegram" in image_path_str.lower() and ("temp" in image_path_str.lower() or "decisions_ai_telegram" in image_path_str.lower()):
                is_telegram_image = True
                logger.info(f"📸 Image is from Telegram download - will NOT include in response: {image_path}")
        
        # Only store image if it's NOT from Telegram (i.e., it was captured locally or from dropped files)
        telegram_image_path = None
        if is_telegram_request and not is_telegram_image:
            try:
                import shutil
                from pathlib import Path
                import tempfile
                # Copy image to persistent temp directory
                persistent_temp_dir = Path(tempfile.gettempdir()) / "decisions_ai_telegram_analyzed"
                persistent_temp_dir.mkdir(parents=True, exist_ok=True)
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                file_ext = Path(image_path).suffix or '.png'
                telegram_image_path = persistent_temp_dir / f"analyzed_image_{timestamp}{file_ext}"
                shutil.copy2(image_path, telegram_image_path)
                # Store in thread and shared storage for cross-thread retrieval
                threading.current_thread().telegram_analyzed_image = str(telegram_image_path)
                VisionAnalyzerTool._last_telegram_image = str(telegram_image_path)
                logger.info(f"📸 Stored analyzed image for Telegram: {telegram_image_path}")
            except Exception as e:
                logger.warning(f"Failed to store image for Telegram: {e}")
        
        # Get vision LLM settings from database
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        vision_provider, vision_model = resolve_vision_llm_config(settings)
        vision_provider_key = (vision_provider or "").strip().lower()
        
        logger.info(f"VisionAnalyzer: Using vision LLM - Provider: {vision_provider}, Model: {vision_model}")
        
        # Just check something is configured — model validation happens in settings UI
        if not vision_model or not vision_model.strip():
            return "Error: No vision model configured. Please select one in the LLMs settings tab."
        
        from distr.core.agent.services.llm.image_utils import convert_image_to_base64

        base64_image, image_mime = convert_image_to_base64(image_path, convert_to_webp=True)
        if not base64_image:
            return f"Error: Failed to process image file: {image_path}"
        
        # Build enhanced prompt
        enhanced_prompt = f"""{prompt}

Please provide a detailed analysis of this image. Describe what you see, any text visible, objects, people, scenes, colors, layout, and any other relevant details."""
        
        # Build OpenAI-compatible vision messages (used by openai, openrouter, kilocode)
        content_items = [
            {"type": "text", "text": enhanced_prompt},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{base64_image}"}},
        ]
        vision_messages = [{"role": "user", "content": content_items}]
        
        # Call vision API directly
        try:
            if vision_provider_key == "openai":
                try:
                    from openai import OpenAI
                    from distr.core.settings import load_settings_from_db
                    
                    settings = load_settings_from_db()
                    openai_key = settings.get('openai_key', '')
                    if not openai_key:
                        return "Error: OpenAI API key not configured. Please set it in settings."
                    
                    client = OpenAI(api_key=openai_key)
                    
                    if not vision_model:
                        vision_model = "gpt-4o"
                    
                    create_kwargs = {
                        "model": vision_model,
                        "messages": vision_messages,
                        "max_tokens": 2000,
                        "timeout": 60.0
                    }
                    
                    logger.info(f"VisionAnalyzer: Calling vision API with model: {vision_model}")
                    try:
                        vision_response = client.chat.completions.create(**create_kwargs)
                    except Exception as api_error:
                        logger.error(f"VisionAnalyzer: Vision API call failed: {api_error}", exc_info=True)
                        error_msg = str(api_error)
                        if "Connection" in error_msg or "timeout" in error_msg.lower() or "network" in error_msg.lower():
                            return "Error: Connection issue with OpenAI API. Please check your internet connection and try again."
                        elif "rate_limit" in error_msg.lower() or "429" in error_msg:
                            return "Error: OpenAI API rate limit exceeded. Please wait a moment and try again."
                        elif "401" in error_msg or "unauthorized" in error_msg.lower():
                            return "Error: Invalid OpenAI API key. Please check your API key in settings."
                        else:
                            return f"Error calling vision API: {error_msg}"
                    
                    # Check if response is valid
                    if not vision_response or not vision_response.choices:
                        logger.error("VisionAnalyzer: Vision API returned empty response")
                        return "Error: Vision API returned empty response. Please try again."
                    
                    vision_result = vision_response.choices[0].message.content
                    
                    # Check if content is empty
                    if not vision_result:
                        logger.error("VisionAnalyzer: Vision API returned empty content")
                        return "Error: Vision API returned empty content. Please try again."
                    
                    # Log preview for debugging
                    preview = vision_result[:200] if len(vision_result) > 200 else vision_result
                    logger.info(f"VisionAnalyzer: Analysis complete ({len(vision_result)} chars). Preview: {preview}")
                    
                    return vision_result
                    
                except ImportError:
                    logger.error("OpenAI library not available for direct vision API call")
                    return "Error: OpenAI library not available. Please install it: pip install openai"
                except Exception as e:
                    logger.error(f"Error calling vision API directly: {e}", exc_info=True)
                    error_msg = str(e)
                    if "Connection" in error_msg or "timeout" in error_msg.lower():
                        return "Error: Connection issue with OpenAI API. Please check your internet connection and try again."
                    return f"Error analyzing image: {error_msg}"
            elif vision_provider_key == "ollama":
                # Ollama vision API - supports vision models like llava, bakllava, moondream, etc.
                try:
                    import requests
                    from distr.core.settings import load_settings_from_db
                    
                    settings = load_settings_from_db()
                    ollama_url = settings.get('ollama_url', 'http://localhost:11434/')
                    if not ollama_url.endswith('/'):
                        ollama_url += '/'
                    
                    # Use vision model from settings, or fallback
                    if not vision_model or vision_model == "":
                        vision_model = "llava"  # Default Ollama vision model
                    
                    logger.info(f"VisionAnalyzer: Calling Ollama vision API with model: {vision_model}")
                    
                    # Ollama vision API uses /api/chat endpoint with images
                    response = requests.post(
                        f"{ollama_url}api/chat",
                        json={
                            "model": vision_model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": enhanced_prompt,
                                    "images": [base64_image]  # Ollama accepts base64 images
                                }
                            ],
                            "stream": False
                        },
                        timeout=60
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if 'message' in result and 'content' in result['message']:
                            vision_result = result['message']['content']
                            logger.info(f"VisionAnalyzer: Analysis complete ({len(vision_result)} chars)")
                            return vision_result
                        else:
                            return "Error: Ollama vision API returned unexpected response format."
                    else:
                        error_msg = f"Ollama vision API call failed. Status: {response.status_code}"
                        if response.text:
                            error_msg += f" - {response.text[:200]}"
                        logger.error(error_msg)
                        return f"Error: {error_msg}"
                except Exception as e:
                    logger.error(f"Error calling Ollama vision API: {e}")
                    return f"Error calling Ollama vision API: {str(e)}"
            elif vision_provider_key == "openrouter":
                # OpenRouter can route to various vision models
                try:
                    import requests
                    from distr.core.settings import load_settings_from_db
                    
                    settings = load_settings_from_db()
                    openrouter_key = settings.get('openrouter_key', '')
                    if not openrouter_key:
                        error_msg = "OpenRouter API key not configured. Please set it in settings."
                        logger.error(f"OpenRouter vision API call failed: {error_msg}")
                        return f"Error: {error_msg}"
                    
                    # OpenRouter uses OpenAI-compatible API for vision
                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {openrouter_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": vision_model or "openai/gpt-4o",
                            "messages": vision_messages,
                            "max_tokens": 2000
                        },
                        timeout=60
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if 'choices' in result and len(result['choices']) > 0:
                            vision_result = result['choices'][0]['message']['content']
                            logger.info(f"OpenRouter vision API call successful: model={vision_model or 'openai/gpt-4o'}")
                            return vision_result
                        else:
                            error_msg = "OpenRouter vision API returned empty choices"
                            logger.error(f"OpenRouter vision API call failed: {error_msg}. Response: {result}")
                            return f"Error: {error_msg}"
                    
                    # Log detailed error for non-200 status
                    error_details = f"Status: {response.status_code}"
                    try:
                        error_body = response.json()
                        error_details += f", Response: {error_body}"
                        if 'error' in error_body:
                            error_msg = error_body.get('error', {})
                            if isinstance(error_msg, dict):
                                error_details += f", Error message: {error_msg.get('message', 'Unknown error')}"
                            else:
                                error_details += f", Error: {error_msg}"
                    except (json.JSONDecodeError, ValueError, KeyError):
                        error_details += f", Response text: {response.text[:500]}"
                    
                    logger.error(f"OpenRouter vision API call failed: {error_details}")
                    return f"Error: OpenRouter vision API call failed. Status: {response.status_code}"
                except requests.exceptions.Timeout:
                    error_msg = "OpenRouter vision API call timed out after 60 seconds"
                    logger.error(f"OpenRouter vision API call failed: {error_msg}")
                    return f"Error: {error_msg}"
                except requests.exceptions.RequestException as e:
                    error_msg = f"Network/request error: {type(e).__name__}: {str(e)}"
                    logger.error(f"OpenRouter vision API call failed: {error_msg}", exc_info=True)
                    return f"Error calling OpenRouter vision API: {error_msg}"
                except Exception as e:
                    error_msg = f"Unexpected error: {type(e).__name__}: {str(e)}"
                    logger.error(f"OpenRouter vision API call failed: {error_msg}", exc_info=True)
                    return f"Error calling OpenRouter vision API: {error_msg}"
            elif vision_provider_key == "anthropic":
                # Anthropic Claude supports vision
                try:
                    from anthropic import Anthropic
                    from distr.core.settings import load_settings_from_db
                    
                    settings = load_settings_from_db()
                    anthropic_key = settings.get('anthropic_key', '')
                    if not anthropic_key:
                        return "Error: Anthropic API key not configured. Please set it in settings."
                    
                    client = Anthropic(api_key=anthropic_key)
                    
                    if not vision_model:
                        vision_model = "claude-3-5-sonnet-20241022"
                    
                    # Anthropic uses different message format
                    anthropic_messages = [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": enhanced_prompt
                                },
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": image_mime,
                                        "data": base64_image
                                    }
                                }
                            ]
                        }
                    ]
                    
                    logger.info(f"VisionAnalyzer: Calling Anthropic vision API with model: {vision_model}")
                    vision_response = client.messages.create(
                        model=vision_model,
                        max_tokens=2000,
                        messages=anthropic_messages
                    )
                    
                    if vision_response and vision_response.content:
                        vision_result = vision_response.content[0].text
                        logger.info(f"VisionAnalyzer: Analysis complete ({len(vision_result)} chars)")
                        return vision_result
                    
                    return "Error: Anthropic vision API returned empty response."
                except ImportError:
                    return "Error: Anthropic library not available. Please install it: pip install anthropic"
                except Exception as e:
                    logger.error(f"Error calling Anthropic vision API: {e}")
                    return f"Error calling Anthropic vision API: {str(e)}"
            elif vision_provider_key in ("kilocode", "groq", "gemini"):
                # KiloCode / Groq / Gemini — OpenAI-compatible API
                try:
                    import requests
                    from distr.core.settings import load_settings_from_db
                    settings = load_settings_from_db()
                    if vision_provider_key == "kilocode":
                        api_key = settings.get('kilocode_key', '')
                        base_url = (settings.get('kilocode_url') or "https://api.kilo.ai/api/gateway").rstrip('/') + "/chat/completions"
                    elif vision_provider_key == "gemini":
                        api_key = settings.get('gemini_key', '')
                        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                    else:
                        api_key = settings.get('groq_key', '')
                        base_url = "https://api.groq.com/openai/v1/chat/completions"
                    if not api_key:
                        return f"Error: {vision_provider_key.title()} API key not configured."
                    response = requests.post(
                        base_url,
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": vision_model or "openai/gpt-4o", "messages": vision_messages, "max_tokens": 2000},
                        timeout=120,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        choices = data.get('choices', [])
                        if choices:
                            vision_result = choices[0].get('message', {}).get('content', '')
                            if vision_result:
                                return vision_result
                        return f"Error: {vision_provider_key.title()} vision API returned empty response."
                    return f"Error: {vision_provider_key.title()} vision API failed (status {response.status_code}): {response.text[:200]}"
                except Exception as e:
                    return f"Error calling {vision_provider_key.title()} vision API: {e}"
            else:
                return f"Error: Vision provider '{vision_provider}' not yet supported for image analysis. Supported providers: OpenAI, Ollama, Anthropic, OpenRouter, KiloCode, Groq, Google Gemini."
                
        except Exception as e:
            logger.error(f"Error in VisionAnalyzer: {e}", exc_info=True)
            return f"Error processing image: {str(e)}"
    
    async def _arun(self, prompt: str = "", file_path: Optional[str] = None, **kwargs) -> str:
        """Async version - calls sync version."""
        return self._run(prompt=prompt, file_path=file_path, **kwargs)


