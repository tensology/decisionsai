#!/usr/bin/env python3
"""Test Kilo API key and KiloCodeLLM (Kilo Gateway)."""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Load API key from settings (database)
try:
    from distr.core.settings import load_settings_from_db
    settings = load_settings_from_db()
    api_key = settings.get("kilo_key", "").strip()
    
    if not api_key:
        print("❌ FAILED - Kilo API key not found in settings")
        print("Please add your KiloCode API key in Settings > Third Party Providers > KiloCode API Key")
        print("Get your key from: https://app.kilo.ai/profile")
        sys.exit(1)
except Exception as e:
    print(f"❌ FAILED - Error loading settings: {e}")
    sys.exit(1)

print("Testing KiloCode API Key (Kilo Gateway)")
print("=" * 60)
print(f"Key: {api_key[:10]}...{api_key[-10:]}")
print()

try:
    from distr.core.agent.services.kilo_code_llm import KiloCodeLLM, KILO_BASE_URL
    print("✅ KiloCodeLLM imported successfully")
    
    # Use key from DB (KiloCodeLLM loads from settings if api_key not passed)
    client = KiloCodeLLM(api_key=api_key)
    print("✅ KiloCodeLLM client created (base_url=%s)" % KILO_BASE_URL)
    
    print("\nTesting chat completion (minimal prompt)...")
    messages = [{"role": "user", "content": "Reply with exactly: OK"}]
    reply = client.complete(messages, temperature=0.0, max_tokens=20)
    print(f"✅ SUCCESS - Reply: {repr(reply)}")
    
    print("\n" + "=" * 60)
    print("✅ KILOCODE API KEY IS VALID!")
    print("Kilo Gateway is ready for use as Coding LLM.")
    
except ImportError as e:
    print("❌ FAILED - Import error:", e)
    print("Ensure openai is installed: pip install openai")
    sys.exit(1)
except ValueError as e:
    print(f"❌ FAILED - {e}")
    sys.exit(1)
except Exception as e:
    error_msg = str(e)
    print(f"❌ FAILED - Error: {error_msg}")
    
    if "Invalid API Key" in error_msg or "invalid_api_key" in error_msg.lower() or "401" in error_msg:
        print("\nThe API key appears to be invalid or not activated yet.")
        print("Please check:")
        print("  1. The key is active at https://app.kilo.ai/profile")
        print("  2. The key was copied correctly (no extra spaces)")
        print("  3. Wait a minute if you just created the key")
    elif "403" in error_msg or "forbidden" in error_msg.lower():
        print("\nThe API key is forbidden or invalid.")
    elif "429" in error_msg or "rate limit" in error_msg.lower():
        print("\nRate limit exceeded.")
    else:
        import traceback
        traceback.print_exc()
    
    sys.exit(1)
