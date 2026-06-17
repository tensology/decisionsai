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
    """Validate Anthropic API key via GET /v1/models.

    Previously this called ``/v1/messages`` with a fixed Haiku model id; Anthropic
    retires model ids over time, so a valid key could still get **404** and fail
    validation. Listing models only checks auth and stays model-id-agnostic.
    """
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models?limit=1",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        snippet = (body[:280] + "…") if len(body) > 280 else body
        if e.code == 401:
            return False, "Invalid API key"
        if e.code == 403:
            return False, (
                "Forbidden (403): key rejected or lacks access — "
                "confirm billing and workspace in console.anthropic.com"
            )
        if e.code == 429:
            # Key is accepted; rate-limited
            return True, ""
        if e.code >= 500:
            return False, f"Anthropic server error ({e.code})"
        return False, f"HTTP {e.code}" + (f": {snippet}" if snippet.strip() else "")
    except Exception as e:
        return False, str(e)


def validate_cursor(api_key: str) -> tuple[bool, str]:
    """Validate Cursor API key by listing available Background Agent models."""
    try:
        req = urllib.request.Request(
            "https://api.cursor.com/v0/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        snippet = (body[:280] + "…") if len(body) > 280 else body
        if e.code == 401:
            return False, "Invalid API key"
        if e.code == 403:
            return False, "Forbidden (403): key rejected or lacks Cursor API access"
        if e.code == 429:
            return True, ""
        if e.code >= 500:
            return False, f"Cursor server error ({e.code})"
        return False, f"HTTP {e.code}" + (f": {snippet}" if snippet.strip() else "")
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




def validate_gemini(api_key: str) -> tuple[bool, str]:
    """Validate Google Gemini API key."""
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/openai/models",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "Invalid API key"
        # Google returns 400 for invalid keys — check the body
        if e.code == 400:
            try:
                body = e.read().decode("utf-8", errors="replace")
                if "API_KEY_INVALID" in body or "API key not valid" in body:
                    return False, "Invalid API key"
            except Exception:
                pass
            return False, "Invalid API key"
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)




def validate_nvidia(api_key: str) -> tuple[bool, str]:
    """Validate NVIDIA NIM API key (build.nvidia.com)."""
    try:
        req = urllib.request.Request(
            "https://integrate.api.nvidia.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status == 200:
                return True, ""
        return False, "Invalid API key"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        if e.code == 403:
            return False, (
                "Forbidden (403): generate a new key at build.nvidia.com with "
                "the Public API Endpoints scope"
            )
        return False, f"HTTP Error: {e.code}"
    except Exception as e:
        return False, str(e)


def validate_masko(api_key: str) -> tuple[bool, str]:
    """Validate Masko API key by checking credits."""
    try:
        req = urllib.request.Request(
            "https://api.masko.ai/v1/credits",
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
        "cursor": validate_cursor,
        "elevenlabs": validate_elevenlabs,
        "assemblyai": validate_assemblyai,
        "openrouter": validate_openrouter,
        "groq": validate_groq,
        "kilocode": validate_kilocode,
        "gemini": validate_gemini,
        "nvidia": validate_nvidia,
        "masko": validate_masko,
    }

    validator = validators.get(provider.lower())
    if not validator:
        return False, f"Unknown provider: {provider}"

    normalized_key = _normalize_api_key(key)
    if not normalized_key:
        return False, "API key is required"

    return validator(normalized_key)
