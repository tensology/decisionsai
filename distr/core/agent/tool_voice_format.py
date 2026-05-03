"""
Voice-first formatting for agent tool results.

Tools return a short conversational ``voice`` block plus optional ``REFERENCE:`` detail.
The system prompt instructs the model never to speak anything at or below REFERENCE: aloud.
"""

REFERENCE_MARKER = "REFERENCE:"


def voice_then_reference(voice: str, reference: str) -> str:
    """Prefix human wording for TTS; append technical detail after REFERENCE:."""
    voice = (voice or "").strip()
    reference = (reference or "").strip()
    if not reference:
        return voice
    return f"{voice}\n\n{REFERENCE_MARKER}\n{reference}"
