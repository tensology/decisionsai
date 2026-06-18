#!/usr/bin/env python3
"""
Test OpenAI TTS API to check for API key issues, quota errors, etc.
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_openai_tts():
    """Test OpenAI TTS API"""
    print("=" * 60)
    print("Testing OpenAI TTS API")
    print("=" * 60)

    # Load API key from settings
    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        api_key = settings.get('openai_key', '').strip()

        if not api_key:
            print("❌ ERROR: No OpenAI API key found in settings")
            print("   Please add your OpenAI API key in Settings > Third Party Providers")
            return False

        print(f"✓ OpenAI API key found: {api_key[:8]}...{api_key[-4:]}")

    except Exception as e:
        print(f"❌ ERROR: Failed to load settings: {e}")
        return False

    # Test the API
    try:
        from openai import OpenAI
        print("✓ OpenAI library imported successfully")

        client = OpenAI(api_key=api_key)
        print("✓ OpenAI client created")

        # Try a simple TTS request
        print("\nTesting TTS generation with text: 'Hello, this is a test.'")
        response = client.audio.speech.create(
            model="tts-1",
            voice="nova",
            input="Hello, this is a test."
        )

        audio_bytes = response.content
        print(f"✓ TTS generation successful! Generated {len(audio_bytes)} bytes of audio")

        # Check if we can decode it
        try:
            from pydub import AudioSegment
            import io
            audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
            print(f"✓ Audio decoded successfully:")
            print(f"  - Duration: {len(audio_segment)/1000:.2f} seconds")
            print(f"  - Sample rate: {audio_segment.frame_rate} Hz")
            print(f"  - Channels: {audio_segment.channels}")

        except Exception as e:
            print(f"⚠️  Warning: Could not decode audio: {e}")
            print("   (Audio generation worked, but pydub failed)")

        print("\n" + "=" * 60)
        print("✅ OpenAI TTS API is working correctly!")
        print("=" * 60)
        return True

    except Exception as e:
        error_str = str(e)
        print(f"\n❌ ERROR: OpenAI TTS API failed")
        print(f"   Error: {error_str}")

        # Check for common errors
        if "401" in error_str or "Unauthorized" in error_str or "invalid" in error_str.lower():
            print("\n💡 DIAGNOSIS: Invalid API key")
            print("   → Your OpenAI API key is invalid or has been revoked")
            print("   → Get a new key at: https://platform.openai.com/api-keys")

        elif "429" in error_str or "rate_limit" in error_str.lower():
            print("\n💡 DIAGNOSIS: Rate limit exceeded")
            print("   → You're sending too many requests")
            print("   → Wait a moment and try again")

        elif "insufficient" in error_str.lower() or "quota" in error_str.lower() or "billing" in error_str.lower():
            print("\n💡 DIAGNOSIS: Insufficient quota / No credits")
            print("   → Your OpenAI account has no credits or quota")
            print("   → Add credits at: https://platform.openai.com/account/billing")
            print("   → Check usage at: https://platform.openai.com/usage")

        elif "timeout" in error_str.lower() or "connection" in error_str.lower():
            print("\n💡 DIAGNOSIS: Network/connection issue")
            print("   → Check your internet connection")
            print("   → OpenAI servers might be down")

        else:
            print("\n💡 Unknown error - check the full traceback above")

        print("\n" + "=" * 60)
        import traceback
        traceback.print_exc()
        print("=" * 60)
        return False

if __name__ == "__main__":
    success = test_openai_tts()
    sys.exit(0 if success else 1)
