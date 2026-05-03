"""Verify that clean_text_for_tts strips emojis before they reach TTS."""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "text_utils",
    _ROOT / "distr/core/agent/services/llm/text_utils.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
clean_text_for_tts = _mod.clean_text_for_tts

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

# spoken_prose: keep non-BMP letters, still drop emoji (So)
sp_ok = clean_text_for_tts("你好 😊 Day plan", spoken_prose=True)
assert "你好" in sp_ok and "😊" not in sp_ok, repr(sp_ok)
strict = clean_text_for_tts("你好 plan")
assert "你好" not in strict, repr(strict)
print("PASS: spoken_prose keeps CJK, strips emoji; strict BMP drops CJK")
