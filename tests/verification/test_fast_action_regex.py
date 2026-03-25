
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from distr.core.agent.services.llm.fast_action_detector import FastActionDetector, ActionType

def test_summarize_regex():
    detector = FastActionDetector()
    
    test_cases = [
        # The failing case reported by user (now fixed)
        ("summarize from clipboard and read", "tts"),
        
        # Original working cases
        ("summarize and read from clipboard", "tts"),
        ("summarize and read this", "tts"),
        
        # Non-reading cases (should be "done")
        ("summarize from clipboard", "done"),
        ("summarize this", "done"),
        ("can you summarize this", "done"),
    ]
    
    print("--- Testing Fast Action Regex ---")
    all_passed = True
    
    for text, expected_type in test_cases:
        result = detector.detect(text)
        
        # Check action type
        if result.action_type != ActionType.CLIPBOARD_SUMMARIZE:
            print(f"❌ '{text}' -> Wrong Action: {result.action_type} (Expected CLIPBOARD_SUMMARIZE)")
            all_passed = False
            continue
            
        # Check response type
        if result.response_type != expected_type:
            print(f"❌ '{text}' -> Wrong Response Type: '{result.response_type}' (Expected '{expected_type}')")
            all_passed = False
        else:
            print(f"✅ '{text}' -> {result.response_type}")
            
    if all_passed:
        print("\nAll regex tests passed!")
    else:
        print("\nSome tests failed.")
        sys.exit(1)

if __name__ == "__main__":
    test_summarize_regex()
