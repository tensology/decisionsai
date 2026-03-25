#!/usr/bin/env python3
"""
Test Anthropic API to check for API key issues, quota errors, etc.
"""
import sys
import os
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_anthropic_api():
    """Test Anthropic API"""
    print("=" * 60)
    print("Testing Anthropic API")
    print("=" * 60)

    # Load API key from settings
    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        api_key = settings.get('anthropic_key', '').strip()

        if not api_key:
            print("❌ ERROR: No Anthropic API key found in settings")
            print("   Please add your Anthropic API key in Settings > Third Party Providers")
            return False

        print(f"✓ Anthropic API key found: {api_key[:8]}...{api_key[-4:]}")

    except Exception as e:
        print(f"❌ ERROR: Failed to load settings: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test the API
    try:
        from anthropic import Anthropic
        print("✓ Anthropic library imported successfully")

        client = Anthropic(api_key=api_key)
        print("✓ Anthropic client created")

        # Try a simple streaming request
        print("\nTesting streaming generation with prompt: 'Tell me a story about a dog named Spot.'")

        stream = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
            system="You are a helpful assistant.",
            messages=[
                {"role": "user", "content": "Tell me a story about a dog named Spot."}
            ],
            stream=True
        )

        print("\n✓ Stream created successfully")
        print("\nStreaming response:")
        print("-" * 60)

        full_content = ""
        event_count = 0
        text_chunk_count = 0

        async for event in stream:
            event_count += 1
            if hasattr(event, 'type'):
                print(f"Event #{event_count}: {event.type}")

                if event.type == "content_block_delta":
                    delta = event.delta
                    if hasattr(delta, 'type') and delta.type == "text_delta":
                        text_chunk = delta.text
                        full_content += text_chunk
                        text_chunk_count += 1
                        print(f"  Text chunk #{text_chunk_count}: {repr(text_chunk[:50])}")

        print("-" * 60)
        print(f"\n✓ Stream completed!")
        print(f"  - Total events: {event_count}")
        print(f"  - Text chunks: {text_chunk_count}")
        print(f"  - Full content length: {len(full_content)} characters")
        print(f"\nFirst 200 characters of response:")
        print(full_content[:200])

        if len(full_content) == 0:
            print("\n⚠️  WARNING: API returned 0 characters of text!")
            print("   This indicates a problem with the API response")
            return False

        print("\n" + "=" * 60)
        print("✅ Anthropic API is working correctly!")
        print("=" * 60)
        return True

    except Exception as e:
        error_str = str(e)
        print(f"\n❌ ERROR: Anthropic API failed")
        print(f"   Error: {error_str}")

        # Check for common errors
        if "401" in error_str or "Unauthorized" in error_str or "invalid" in error_str.lower():
            print("\n💡 DIAGNOSIS: Invalid API key")
            print("   → Your Anthropic API key is invalid or has been revoked")
            print("   → Get a new key at: https://console.anthropic.com/settings/keys")

        elif "429" in error_str or "rate_limit" in error_str.lower():
            print("\n💡 DIAGNOSIS: Rate limit exceeded")
            print("   → You're sending too many requests")
            print("   → Wait a moment and try again")

        elif "insufficient" in error_str.lower() or "quota" in error_str.lower() or "billing" in error_str.lower():
            print("\n💡 DIAGNOSIS: Insufficient quota / No credits")
            print("   → Your Anthropic account has no credits or quota")
            print("   → Add credits at: https://console.anthropic.com/settings/billing")

        elif "timeout" in error_str.lower() or "connection" in error_str.lower():
            print("\n💡 DIAGNOSIS: Network/connection issue")
            print("   → Check your internet connection")
            print("   → Anthropic servers might be down")

        else:
            print("\n💡 Unknown error - check the full traceback below")

        print("\n" + "=" * 60)
        import traceback
        traceback.print_exc()
        print("=" * 60)
        return False

if __name__ == "__main__":
    success = asyncio.run(test_anthropic_api())
    sys.exit(0 if success else 1)
