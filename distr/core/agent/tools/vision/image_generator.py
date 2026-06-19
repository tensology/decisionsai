"""
Image Generator Tool

A tool that generates images using image generation LLMs.
Supports commands like "create an image about X", "generate an image", "make a picture".
Can use dropped images as reference for image-to-image generation.
Handles file saving to desktop or default location.
"""

import logging
import os
import base64
import json
from typing import Optional, List
from pathlib import Path
from datetime import datetime

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ImageGeneratorInput(BaseModel):
    """Input schema for image_generator tool."""
    prompt: str = Field(description="The description of the image to generate (e.g., 'a sunset over mountains', 'a cat wearing a hat')")
    output_path: Optional[str] = Field(default=None, description="Optional: Where to save the generated image. If not specified, saves to desktop. Can be 'my desktop', 'desktop', or a full file path.")
    reference_images: Optional[str] = Field(default=None, description="Optional: Comma-separated list of image file paths to use as reference. If not provided, will use dropped images from the oracle ball if available.")
    style: Optional[str] = Field(default=None, description="Optional: Style for the image (e.g., 'photorealistic', 'cartoon', 'anime', 'oil painting')")


def is_image_file(file_path: str) -> bool:
    """Check if a file is an image based on extension."""
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.svg', '.ico', '.heic', '.heif'}
    ext = Path(file_path).suffix.lower()
    return ext in image_extensions


def get_dropped_images() -> List[str]:
    """Get all image files that were dropped on the oracle ball."""
    storage_dir = os.path.join(os.path.expanduser("~"), ".decisions", "dropped_files")
    storage_file = os.path.join(storage_dir, "current_files.json")
    
    if not os.path.exists(storage_file):
        return []
    
    try:
        with open(storage_file, 'r') as f:
            data = json.load(f)
            files = data.get("files", [])
            # Filter to only existing image files
            image_files = [f for f in files if os.path.exists(f) and os.path.isfile(f) and is_image_file(f)]
            return image_files
    except Exception as e:
        logger.error(f"Error reading dropped files: {e}")
        return []


def image_to_base64(image_path: str) -> Optional[str]:
    """Convert an image file to base64 string."""
    try:
        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')
            return base64_data
    except Exception as e:
        logger.error(f"Error converting image to base64: {e}")
        return None


def resolve_output_path(output_path: Optional[str] = None, prompt: str = "", output_format: str = "png") -> str:
    """Resolve the output path for saving the generated image.
    
    Args:
        output_path: Where to save the image (folder name or full path)
        prompt: The prompt used to generate the image (for filename)
        output_format: File extension (png, jpg, svg, etc.)
    """
    from distr.core.settings import resolve_folder_path
    
    # Ensure format doesn't have a leading dot
    output_format = output_format.lstrip('.')
    
    if output_path:
        # Check if it's a folder reference
        if output_path.lower() in ['my desktop', 'desktop', 'my documents', 'documents', 'my downloads', 'downloads']:
            folder_path = resolve_folder_path(output_path)
            # Generate filename from prompt
            safe_prompt = "".join(c for c in prompt[:50] if c.isalnum() or c in (' ', '-', '_')).strip()
            safe_prompt = safe_prompt.replace(' ', '_')
            if not safe_prompt:
                safe_prompt = "generated_image"
            filename = f"{safe_prompt}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format}"
            return os.path.join(folder_path, filename)
        else:
            # Assume it's a full path
            if os.path.isdir(output_path):
                # If it's a directory, add filename
                safe_prompt = "".join(c for c in prompt[:50] if c.isalnum() or c in (' ', '-', '_')).strip()
                safe_prompt = safe_prompt.replace(' ', '_')
                if not safe_prompt:
                    safe_prompt = "generated_image"
                filename = f"{safe_prompt}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format}"
                return os.path.join(output_path, filename)
            else:
                # Full file path
                return output_path
    else:
        # Default to desktop
        desktop_path = resolve_folder_path("my desktop")
        safe_prompt = "".join(c for c in prompt[:50] if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_prompt = safe_prompt.replace(' ', '_')
        if not safe_prompt:
            safe_prompt = "generated_image"
        filename = f"{safe_prompt}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format}"
        return os.path.join(desktop_path, filename)


def save_base64_image(base64_data: str, output_path: str) -> bool:
    """Save a base64-encoded image to a file."""
    try:
        # Decode base64
        image_data = base64.b64decode(base64_data)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Write image
        with open(output_path, 'wb') as f:
            f.write(image_data)
        
        logger.info(f"Image saved to: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Error saving image: {e}")
        return False


class ImageGeneratorTool(BaseTool):
    """
    Tool to generate images using image generation LLMs.
    
    When called, this tool:
    1. Gets the Image LLM provider and model from settings
    2. Generates an image from the prompt (optionally using reference images)
    3. Saves the image to the specified location (or desktop by default)
    4. Returns the file path of the generated image
    """
    
    name: str = "image_generator"
    description: str = (
        "🎨 USE THIS TOOL when the user wants to create, generate, or make an image. "
        "This tool can generate images from text descriptions and optionally use dropped images as reference. "
        "Examples: 'create an image about X', 'generate an image of Y', 'make a picture of Z', "
        "'create an image using the images I dropped as reference', 'generate an image and put it on my desktop', "
        "'create an SVG icon that looks like X', 'move the file to my downloads folder'. "
        "The tool automatically saves generated images to your desktop unless you specify a different location. "
        "Note: SVG requests will generate PNG images (most APIs don't support true vector SVG generation)."
    )
    args_schema: type[BaseModel] = ImageGeneratorInput
    
    # Class variable for storing the last generated image path (accessible cross-thread)
    _last_generated_image: Optional[str] = None
    
    def __init__(self, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self._llm_service = llm_service
        self._last_error = None  # Store last error message for user-friendly reporting
    
    def _get_image_llm_config(self) -> tuple[Optional[str], Optional[str]]:
        """Get Image LLM provider and model from settings."""
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            image_provider = settings.get('image_llm_provider', 'Ollama')
            image_model = settings.get('image_llm_model', '').strip()
            
            if image_model:
                logger.info(f"ImageGeneratorTool: Found Image LLM - Provider: {image_provider}, Model: {image_model}")
                return (image_provider, image_model)
            else:
                logger.warning(f"ImageGeneratorTool: Image LLM model not configured")
                return (None, None)
        except Exception as e:
            logger.warning(f"ImageGeneratorTool: Error loading image LLM settings: {e}")
            return (None, None)
    
    def _generate_with_openai(
        self,
        prompt: str,
        model: str,
        reference_images: Optional[List[str]] = None,
        style: Optional[str] = None,
    ) -> Optional[str]:
        """Generate image using the selected OpenAI image model."""
        try:
            from openai import OpenAI
            from distr.core.settings import load_settings_from_db
            
            settings = load_settings_from_db()
            openai_key = settings.get('openai_key', '')
            if not openai_key:
                return None
            
            client = OpenAI(api_key=openai_key)
            
            # Enhance prompt with style if provided
            enhanced_prompt = prompt
            if style:
                enhanced_prompt = f"{prompt}, {style} style"
            
            # For image-to-image, we'd need to use the images API with reference images
            # DALL-E 3 doesn't support image-to-image directly, but we can include reference in prompt
            if reference_images:
                enhanced_prompt = f"{enhanced_prompt}. Use the provided reference images as inspiration for style, composition, and elements."
                # Note: DALL-E 3 doesn't support direct image input, so we describe the reference
                # For true image-to-image, would need DALL-E 2 or other models
            
            model_id = (model or "gpt-image-1").strip() or "gpt-image-1"
            logger.info(f"Generating image with OpenAI {model_id}: {enhanced_prompt[:100]}...")

            response = client.images.generate(
                model=model_id,
                prompt=enhanced_prompt,
                n=1,
                size="1024x1024",  # Options: "1024x1024", "1024x1792", "1792x1024"
                quality="standard",  # or "hd"
                response_format="b64_json"  # Get base64 response
            )
            
            if response.data and len(response.data) > 0:
                image_b64 = response.data[0].b64_json
                return image_b64
            
            return None
        except Exception as e:
            logger.error(f"Error generating image with OpenAI: {e}")
            return None
    
    def _generate_with_ollama(self, prompt: str, model: str, reference_images: Optional[List[str]] = None, style: Optional[str] = None) -> Optional[str]:
        """Generate image using Ollama image models."""
        try:
            import requests
            from distr.core.settings import load_settings_from_db
            
            settings = load_settings_from_db()
            ollama_url = settings.get('ollama_url', 'http://localhost:11434/')
            if not ollama_url.endswith('/'):
                ollama_url += '/'
            
            # Enhance prompt with style if provided
            enhanced_prompt = prompt
            if style:
                enhanced_prompt = f"{prompt}, {style} style"
            
            # For image-to-image with Ollama, we need to send reference images
            # Check if model supports image input (e.g., flux, stable-diffusion variants)
            if reference_images and len(reference_images) > 0:
                # Convert reference images to base64
                ref_images_b64 = []
                for ref_img in reference_images:
                    b64 = image_to_base64(ref_img)
                    if b64:
                        ref_images_b64.append(b64)
                
                if ref_images_b64:
                    enhanced_prompt = f"{enhanced_prompt}. Use these reference images: {', '.join([os.path.basename(img) for img in reference_images])}"
                    # Note: Ollama's image generation API may vary by model
                    # Some models support image input, others don't
            
            logger.info(f"Generating image with Ollama {model}: {enhanced_prompt[:100]}...")
            
            # Call Ollama generate endpoint
            # Note: Ollama image generation endpoint may be different - check model capabilities
            response = requests.post(
                f"{ollama_url}api/generate",
                json={
                    "model": model,
                    "prompt": enhanced_prompt,
                    "stream": False
                },
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                # Ollama image models return base64 or URL - adjust based on actual response
                if 'image' in result:
                    return result['image']
                elif 'response' in result:
                    # Some models return base64 in response
                    return result['response']
            
            return None
        except Exception as e:
            logger.error(f"Error generating image with Ollama: {e}")
            return None
    
    def _generate_with_openrouter(self, prompt: str, model: str, reference_images: Optional[List[str]] = None, style: Optional[str] = None) -> Optional[str]:
        """Generate image using OpenRouter chat/completions with modalities.
        
        Per OpenRouter docs: https://openrouter.ai/docs/guides/overview/multimodal/image-generation
        - Use /api/v1/chat/completions endpoint (NOT /api/v1/images/generations)
        - Include "modalities": ["image", "text"] in request
        - Images returned in message.images[].image_url.url as base64 data URLs
        """
        try:
            import requests
            from distr.core.settings import load_settings_from_db
            
            settings = load_settings_from_db()
            openrouter_key = settings.get('openrouter_key', '')
            if not openrouter_key:
                logger.error("OpenRouter image generation failed: API key not configured in settings")
                return None
            
            # Enhance prompt with style if provided
            enhanced_prompt = prompt
            if style:
                enhanced_prompt = f"{prompt}, {style} style"
            
            # Build message content - can include reference images
            message_content = enhanced_prompt
            if reference_images and len(reference_images) > 0:
                # For image-to-image, include reference images in the message
                content_parts = [{"type": "text", "text": enhanced_prompt}]
                for ref_img in reference_images:
                    b64 = image_to_base64(ref_img)
                    if b64:
                        import mimetypes
                        mime_type, _ = mimetypes.guess_type(ref_img)
                        if not mime_type or not mime_type.startswith('image/'):
                            mime_type = 'image/jpeg'
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"}
                        })
                message_content = content_parts
            
            logger.info(f"Generating image with OpenRouter {model}: {enhanced_prompt[:100]}...")
            
            # Build request payload per OpenRouter docs
            # Only include simple text content - no reference images unless explicitly provided
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": message_content
                    }
                ],
                "modalities": ["image", "text"]  # Required for image generation
            }
            
            # Add image_config for aspect ratio (optional, for Gemini models)
            if 'gemini' in model.lower():
                payload["image_config"] = {
                    "aspect_ratio": "1:1",
                    "image_size": "1K"
                }
            
            # Log payload size for debugging
            payload_json = json.dumps(payload)
            payload_size = len(payload_json)
            has_images = isinstance(message_content, list) and any(p.get('type') == 'image_url' for p in message_content)
            
            logger.info(f"IMAGE: Calling OpenRouter chat/completions")
            logger.info(f"IMAGE: Model: {model}")
            logger.info(f"IMAGE: Payload size: {payload_size} bytes, Has reference images: {has_images}")
            logger.info(f"OpenRouter request: model={model}, payload_size={payload_size}, has_images={has_images}")
            
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",  # Correct endpoint
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://decisionsai.app",  # Optional but recommended
                    "X-Title": "DecisionsAI"  # Optional but recommended
                },
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.debug(f"OpenRouter response: {json.dumps(result, indent=2)[:1000]}")
                
                # Extract image from response per OpenRouter format
                # Images are in choices[0].message.images[].image_url.url
                if 'choices' in result and len(result['choices']) > 0:
                    message = result['choices'][0].get('message', {})
                    images = message.get('images', [])
                    
                    if images and len(images) > 0:
                        image_url = images[0].get('image_url', {}).get('url', '')
                        if image_url.startswith('data:'):
                            # Extract base64 from data URL (data:image/png;base64,...)
                            if ';base64,' in image_url:
                                base64_data = image_url.split(';base64,')[1]
                                logger.info(f"OpenRouter image generation successful: model={model}")
                                return base64_data
                            else:
                                logger.error(f"OpenRouter: Unexpected image URL format: {image_url[:100]}")
                        else:
                            logger.error(f"OpenRouter: Image URL is not a data URL: {image_url[:100]}")
                    else:
                        # Check if there's text content indicating why no image
                        content = message.get('content', '')
                        logger.warning(f"OpenRouter: No images in response. Content: {content[:200]}")
                        self._last_error = f"Model {model} did not generate an image. Response: {content[:200]}"
                else:
                    logger.error(f"OpenRouter: Response missing 'choices' field. Response: {result}")
                    self._last_error = f"Unexpected response format from OpenRouter"
                
                return None
            else:
                # Log detailed error information
                error_details = f"Status: {response.status_code}"
                error_message_for_user = None
                try:
                    error_body = response.json()
                    error_details += f", Response: {error_body}"
                    if 'error' in error_body:
                        error_msg = error_body.get('error', {})
                        if isinstance(error_msg, dict):
                            error_details += f", Error message: {error_msg.get('message', 'Unknown error')}"
                            error_message_for_user = error_msg.get('message', 'Unknown error')
                        else:
                            error_details += f", Error: {error_msg}"
                            error_message_for_user = str(error_msg)
                except (json.JSONDecodeError, ValueError, KeyError):
                    error_details += f", Response text: {response.text[:500]}"
                    error_message_for_user = response.text[:200] if response.text else f"HTTP {response.status_code}"
                
                logger.error(f"OpenRouter image generation failed: {error_details}")
                
                # Store error message for user-friendly reporting
                if response.status_code == 400:
                    self._last_error = f"HTTP 400 Bad Request - The model ({model}) may not support image generation. Check that it has 'image' in output_modalities. Error: {error_message_for_user}"
                elif response.status_code == 401:
                    self._last_error = f"HTTP 401 Unauthorized - Invalid OpenRouter API key. Please check your API key in settings."
                elif response.status_code == 413:
                    self._last_error = f"HTTP 413 Payload Too Large - The request was too big. This can happen if reference images are included. Try without reference images."
                elif response.status_code == 429:
                    self._last_error = f"HTTP 429 Rate Limited - Too many requests. Please wait a moment and try again."
                elif response.status_code == 404:
                    self._last_error = f"HTTP 404 Not Found - Model '{model}' not found. Try 'google/gemini-2.5-flash-image-preview' or 'black-forest-labs/flux.2-pro'."
                elif error_message_for_user:
                    self._last_error = f"HTTP {response.status_code}: {error_message_for_user}"
                else:
                    self._last_error = f"HTTP {response.status_code} - {error_details}"
                
                return None
        except requests.exceptions.Timeout:
            logger.error(f"OpenRouter image generation failed: Request timeout after 120 seconds (model: {model})")
            self._last_error = f"Request timeout after 120 seconds. Image generation can be slow - try again."
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter image generation failed: Network/request error - {type(e).__name__}: {str(e)}", exc_info=True)
            self._last_error = f"Network error: {str(e)}"
            return None
        except Exception as e:
            logger.error(f"OpenRouter image generation failed: Unexpected error - {type(e).__name__}: {str(e)}", exc_info=True)
            self._last_error = f"Unexpected error: {str(e)}"
            return None
    
    def _generate_with_anthropic(self, prompt: str, reference_images: Optional[List[str]] = None, style: Optional[str] = None) -> Optional[str]:
        """Generate image using Anthropic - Note: Anthropic doesn't support image generation."""
        # Anthropic doesn't have an image generation API
        # They only support vision (image analysis), not image generation
        logger.warning("Anthropic doesn't support image generation - only vision (image analysis)")
        return None
    
    def _generate_with_pixazo(self, prompt: str, model: str, style: Optional[str] = None) -> Optional[str]:
        """Generate image via Pixazo gateway (async job + poll)."""
        try:
            from distr.core.third_party_keys import pixazo_api_key
            from distr.core.pixazo_client import download_url_to_bytes, pixazo_generate_media_urls

            api_key = pixazo_api_key()
            if not api_key:
                self._last_error = "Pixazo API key not configured. Add it in Settings → API Keys."
                return None

            enhanced = f"{prompt}, {style} style" if style else prompt
            urls = pixazo_generate_media_urls(api_key, model, enhanced, timeout_sec=180)
            if not urls:
                return None
            data = download_url_to_bytes(urls[0])
            return base64.b64encode(data).decode("utf-8")
        except Exception as e:
            logger.error("Error generating image with Pixazo: %s", e)
            self._last_error = str(e)
            return None

    def _run(self, prompt: str = "", output_path: Optional[str] = None, reference_images: Optional[str] = None, style: Optional[str] = None, **kwargs) -> str:
        """
        Generate an image based on the prompt.
        
        Args:
            prompt: Description of the image to generate
            output_path: Where to save the image (defaults to desktop)
            reference_images: Comma-separated list of image paths, or None to use dropped images
            style: Optional style for the image
            **kwargs: Additional arguments (ignored)
        """
        if not prompt:
            return "Error: No prompt provided. Please describe what image you want to generate."
        
        # Get Image LLM configuration
        image_provider, image_model = self._get_image_llm_config()
        
        if not image_provider or not image_model:
            return "Error: Image LLM not configured. Please set the Image LLM provider and model in settings."
        
        logger.info(f"ImageGenerator: Using Image LLM - Provider: {image_provider}, Model: {image_model}")
        
        # Get reference images - ONLY if explicitly provided or user asks to use reference
        # Do NOT auto-include images - they might be for vision/analysis, not generation reference
        ref_images = []
        prompt_lower = prompt.lower()
        wants_reference = any(word in prompt_lower for word in ['reference', 'based on', 'like this', 'similar to', 'use the image', 'use this image', 'using the image'])
        
        if reference_images:
            # Parse comma-separated list - explicitly provided
            ref_images = [img.strip() for img in reference_images.split(',') if img.strip()]
            logger.info(f"Using {len(ref_images)} explicitly provided reference image(s)")
        elif wants_reference:
            # Only check for dropped images if user asks for reference
            dropped_images = get_dropped_images()
            if dropped_images:
                ref_images = dropped_images
                logger.info(f"Using {len(ref_images)} dropped image(s) as reference (user requested)")
            else:
                logger.info("User requested reference but no dropped images found")
        
        # Validate reference images exist
        valid_ref_images = []
        for img_path in ref_images:
            if os.path.exists(img_path) and is_image_file(img_path):
                valid_ref_images.append(img_path)
            else:
                logger.warning(f"Reference image not found or not an image: {img_path}")
        
        # Check if SVG is requested (note: most APIs generate raster images)
        prompt_lower = prompt.lower()
        is_svg_request = "svg" in prompt_lower or ("icon" in prompt_lower and "svg" in prompt_lower)
        output_format = "svg" if is_svg_request else "png"
        
        # Generate image based on provider
        # Note: OpenRouter models like google/gemini-2.5-flash-image ARE image generation models
        # when used with modalities: ["image", "text"] - don't confuse with vision/analysis models
        from distr.core.chat import provider_slug

        provider_key = provider_slug(image_provider)
        image_b64 = None

        if provider_key == "openai":
            image_b64 = self._generate_with_openai(prompt, image_model, valid_ref_images, style)
        elif provider_key == "ollama":
            image_b64 = self._generate_with_ollama(prompt, image_model, valid_ref_images, style)
        elif provider_key == "openrouter":
            image_b64 = self._generate_with_openrouter(prompt, image_model, valid_ref_images, style)
        elif provider_key == "pixazo":
            if valid_ref_images:
                return "Error: Pixazo image-to-image is not wired in image_generator yet. Use MCP pixazo-media or a text-only prompt."
            image_b64 = self._generate_with_pixazo(prompt, image_model, style)
        elif provider_key == "anthropic":
            return "Error: Anthropic doesn't support image generation. Anthropic only supports vision (image analysis), not image creation. Please use OpenAI, Ollama, OpenRouter, or Pixazo for image generation."
        else:
            return f"Error: Unsupported image generation provider: {image_provider}. Supported providers: OpenAI, Ollama, OpenRouter, Pixazo."
        
        if not image_b64:
            # Log detailed error for debugging
            logger.error(f"ImageGenerator: Failed to generate image with {image_provider} (model: {image_model}). Check logs above for detailed error information.")
            
            # Use stored error message if available
            if hasattr(self, '_last_error') and self._last_error:
                error_msg = f"Error: {self._last_error}"
                # Clear the stored error
                self._last_error = None
                return error_msg
            
            # Fallback to generic error messages
            error_msg = f"Error: Failed to generate image using {image_provider} (model: {image_model}). "
            if provider_key == "openrouter":
                error_msg += "Check that the model has 'image' in output_modalities. "
                error_msg += "Compatible models include: google/gemini-2.5-flash-image-preview, black-forest-labs/flux.2-pro, etc."
            elif provider_key == "openai":
                error_msg += "Please check your OpenAI API key and ensure it has access to DALL-E."
            elif provider_key == "ollama":
                error_msg += "Please ensure Ollama is running and the image model is available."
            elif provider_key == "pixazo":
                error_msg += "Check Pixazo API key in Settings → API Keys and pick a model under LLMs → Image."
            else:
                error_msg += "Please check your API key and model configuration."
            return error_msg
        
        # Resolve output path
        final_output_path = resolve_output_path(output_path, prompt, output_format)
        
        # Note about SVG: Most image generation APIs produce raster images (PNG/JPG)
        # True SVG generation would require specialized tools
        svg_note = ""
        if is_svg_request and output_format == "png":
            svg_note = "\n\nNote: Most image generation APIs produce raster images (PNG), not true vector SVG. The image has been saved as PNG. For true SVG icons, consider using specialized SVG generation tools."
        
        # Save image
        if save_base64_image(image_b64, final_output_path):
            result_message = f"✅ Image generated successfully and saved to: {final_output_path}\n\nPrompt: {prompt}" + (f"\nStyle: {style}" if style else "") + (f"\nReference images: {len(valid_ref_images)} image(s)" if valid_ref_images else "") + svg_note
            
            # ALWAYS copy generated image for potential Telegram sending
            # Thread-local storage might not have telegram_request flag due to thread pool execution
            # So we copy the image regardless and let the Telegram handler decide if it needs it
            import threading
            try:
                import shutil
                import tempfile
                from datetime import datetime as dt
                # Copy to temp directory for Telegram sending
                temp_dir = Path(tempfile.gettempdir()) / "decisions_ai_telegram_analyzed"
                temp_dir.mkdir(parents=True, exist_ok=True)
                timestamp = dt.now().strftime("%Y%m%d_%H%M%S_%f")
                temp_copy = temp_dir / f"generated_image_{timestamp}.png"
                shutil.copy2(final_output_path, temp_copy)
                
                # Store in thread-local for any thread that might need it
                threading.current_thread().telegram_analyzed_image = str(temp_copy)
                
                # Also store in a global location for cross-thread access
                # This is used by the Telegram response handler
                ImageGeneratorTool._last_generated_image = str(temp_copy)
                
                logger.info(f"📸 Generated image stored for sending: {temp_copy}")
            except Exception as e:
                logger.warning(f"Failed to copy generated image: {e}")
            
            # Check if user wants to move the file
            prompt_lower_check = prompt.lower()
            if "move" in prompt_lower_check or "move the file" in prompt_lower_check:
                # Extract destination from prompt
                move_dest = None
                if "downloads" in prompt_lower_check:
                    move_dest = "my downloads"
                elif "documents" in prompt_lower_check:
                    move_dest = "my documents"
                elif "desktop" in prompt_lower_check:
                    move_dest = "my desktop"
                
                if move_dest:
                    try:
                        from distr.core.settings import resolve_folder_path
                        dest_folder = resolve_folder_path(move_dest)
                        dest_path = os.path.join(dest_folder, os.path.basename(final_output_path))
                        
                        import shutil
                        shutil.move(final_output_path, dest_path)
                        result_message += f"\n\n✅ File moved to: {dest_path}"
                        final_output_path = dest_path
                    except Exception as e:
                        logger.error(f"Error moving file: {e}")
                        result_message += f"\n\n⚠️  Could not move file: {str(e)}"
            
            return result_message
        else:
            return f"Error: Image was generated but failed to save to {final_output_path}. Please check file permissions."
