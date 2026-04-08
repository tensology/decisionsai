"""Verify that clean_text_for_tts strips emojis before they reach TTS."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from distr.core.agent.services.llm.text_utils import clean_text_for_tts

cases = [
    ("Hello 😊 world", "Hello world"),
    ("Great job! 🎉🎊 Let's go", "Great job! Let's go"),
    ("Check ✅ this ❌ out", "Check this out"),
    ("Fire 🔥 emoji", "Fire emoji"),
    ("No emojis here.", "No emojis here."),
    ("👋 Hey there 👍", "Hey there"),
    ("Stars ⭐⭐⭐ rating", "Stars rating"),
    ("Heart ❤️ love 💕", "Heart love"),
    ("Thinking 🤔 face", "Thinking face"),
    ("Code: `print('hi')` done", "Code: print('hi') done"),
]

passed = 0
failed = 0
for raw, expected in cases:
    result = clean_text_for_tts(raw)
    # Normalize whitespace for comparison
    result_norm = ' '.join(result.split())
    expected_norm = ' '.join(expected.split())
    ok = result_norm == expected_norm
    status = "PASS" if ok else "FAIL"
    if not ok:
        failed += 1
        print(f"  {status}: {repr(raw)}")
        print(f"    expected: {repr(expected_norm)}")
        print(f"    got:      {repr(result_norm)}")
    else:
        passed += 1
        print(f"  {status}: {repr(raw)} -> {repr(result_norm)}")

print(f"\n{passed}/{passed+failed} passed")
if failed:
    sys.exit(1)
