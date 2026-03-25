"""
Shared API validation utilities for third-party providers.
Can be used by both Qt GUI and web interface.
"""
import logging
import urllib.request
import urllib.error
import json

logger = logging.getLogger(__name__)


def _normalize_api_key(key: str) -> str:
    """Normalize pasted API keys to token-only format."""
    normalized = (key or "").strip()
    if normalized.lower().startswith("bearer "):
        return normalized[7:].strip()
    return normalized


def validate_openai(api_key: str) -> tuple[bool, str]:
    """Validate OpenAI API key."""
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)


def validate_anthropic(api_key: str) -> tuple[bool, str]:
    """Validate Anthropic API key."""
    try:
        data = json.dumps({
            "model": "claude-3-haiku-20240307",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}]
        }).encode('utf-8')
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        elif e.code == 400:
            # Bad request might still mean valid key
            return True, ""
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)


def validate_elevenlabs(api_key: str) -> tuple[bool, str]:
    """Validate ElevenLabs API key."""
    try:
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user",
            headers={"xi-api-key": api_key}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)


def validate_assemblyai(api_key: str) -> tuple[bool, str]:
    """Validate AssemblyAI API key."""
    try:
        req = urllib.request.Request(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": api_key}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            # Any response means the key is accepted
            return True, ""
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        elif e.code in [400, 404, 405]:
            # These errors indicate the API accepted the key
            return True, ""
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)


def validate_rube(token: str) -> tuple[bool, str]:
    """Validate Rube MCP token."""
    # For Rube, we just check if the token is non-empty and has minimum length
    # Actual validation would require MCP connection which is complex
    if len(token.strip()) >= 20:
        return True, ""
    return False, "Token too short (minimum 20 characters)"


def validate_openrouter(api_key: str) -> tuple[bool, str]:
    """Validate OpenRouter API key."""
    try:
        data = json.dumps({
            "model": "openrouter/auto",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        body_lower = body.lower()
        if e.code == 401 or "user not found" in body_lower or "invalid api key" in body_lower:
            return False, "Invalid API key"
        # Authentication passed but request failed for a non-auth reason
        # (e.g. quota/rate limit/model availability).
        if e.code in (400, 402, 404, 405, 409, 422, 429):
            return True, ""
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)


def validate_groq(api_key: str) -> tuple[bool, str]:
    """Validate Groq API key using the Groq SDK."""
    try:
        from groq import Groq
        # Use Groq SDK to validate the API key
        client = Groq(api_key=api_key)
        # Try to list models to validate the key
        models = client.models.list()
        # If we get here without exception, the key is valid
        return True, ""
    except ImportError:
        return False, "Groq library not installed. Run: pip install groq"
    except Exception as e:
        error_msg = str(e)
        # Check for common error patterns
        if "Invalid API Key" in error_msg or "invalid_api_key" in error_msg.lower() or "401" in error_msg:
            return False, "Invalid API key"
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            return False, "API key forbidden or invalid"
        elif "429" in error_msg or "rate limit" in error_msg.lower():
            return False, "Rate limit exceeded"
        else:
            logger.error(f"Groq validation error: {e}", exc_info=True)
            return False, f"Validation error: {error_msg}"


def validate_kilocode(api_key: str) -> tuple[bool, str]:
    """Validate Kilo API key via Kilo Gateway (OpenRouter-compatible API)."""
    try:
        req = urllib.request.Request(
            "https://api.kilo.ai/api/openrouter/models",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)




def validate_provider(provider: str, key: str) -> tuple[bool, str]:
    """
    Validate API key for any provider.

    Args:
        provider: Provider name (lowercase)
        key: API key or token

    Returns:
        Tuple of (is_valid, error_message)
    """
    validators = {
        "openai": validate_openai,
        "anthropic": validate_anthropic,
        "elevenlabs": validate_elevenlabs,
        "assemblyai": validate_assemblyai,
        "rube": validate_rube,
        "openrouter": validate_openrouter,
        "groq": validate_groq,
        "kilocode": validate_kilocode,
    }

    validator = validators.get(provider.lower())
    if not validator:
        return False, f"Unknown provider: {provider}"

    normalized_key = _normalize_api_key(key)
    if not normalized_key:
        return False, "API key is required"

    return validator(normalized_key)
