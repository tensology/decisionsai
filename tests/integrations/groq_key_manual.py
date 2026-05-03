#!/usr/bin/env python3
"""Test Groq API key validation."""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Load API key from settings
try:
    from distr.core.settings import load_settings_from_db
    settings = load_settings_from_db()
    api_key = settings.get('groq_key', '').strip()
    
    if not api_key:
        print("❌ FAILED - Groq API key not found in settings")
        print("Please add your Groq API key in Settings > Third Party > Groq API Key")
        sys.exit(1)
except Exception as e:
    print(f"❌ FAILED - Error loading settings: {e}")
    sys.exit(1)

print("Testing Groq API Key")
print("=" * 60)
print(f"Key: {api_key[:10]}...{api_key[-10:]}")
print()

try:
    from groq import Groq
    print("✅ Groq library imported successfully")
    
    client = Groq(api_key=api_key)
    print("✅ Groq client created")
    
    print("\nTesting models.list()...")
    models = client.models.list()
    model_list = list(models.data)
    print(f"✅ SUCCESS - Found {len(model_list)} models")
    
    print("\nFirst 10 models:")
    for i, model in enumerate(model_list[:10], 1):
        print(f"  {i}. {model.id}")
    
    print("\n" + "=" * 60)
    print("✅ API KEY IS VALID!")
    print("The validation should work in the GUI now.")
    
except ImportError:
    print("❌ FAILED - Groq library not installed")
    print("Install with: pip install groq")
    sys.exit(1)
except Exception as e:
    error_msg = str(e)
    print(f"❌ FAILED - Error: {error_msg}")
    
    if "Invalid API Key" in error_msg or "invalid_api_key" in error_msg.lower() or "401" in error_msg:
        print("\nThe API key appears to be invalid or not activated yet.")
        print("Please check:")
        print("  1. The key is active in your Groq console")
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
