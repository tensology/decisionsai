
from distr.core.agent.services.llm.utils import clean_text_for_tts

def test_streaming_chunks():
    print("Testing streaming chunks behavior with clean_text_for_tts...")
    
    # Simulate valid LLM streaming output
    chunks = ["Here is ", "a simple ", "sentence."]
    
    reconstructed_full = ""
    reconstructed_cleaned = ""
    
    print(f"Original chunks: {chunks}")
    
    for chunk in chunks:
        # Pass strip_whitespace=False to mimic the fix in ollama.py
        cleaned = clean_text_for_tts(chunk, strip_whitespace=False)
        reconstructed_full += chunk
        reconstructed_cleaned += cleaned
        print(f"Chunk: '{chunk}' -> Cleaned: '{cleaned}' (strip_whitespace=False)")
        
    print("-" * 30)
    print(f"Full Original: '{reconstructed_full}'")
    print(f"Full Cleaned : '{reconstructed_cleaned}'")
    
    expected = "Here is a simple sentence."
    if reconstructed_cleaned == expected:
        print("PASS: Text preserved correctly.")
    else:
        print(f"FAIL: Text mismatch! Expected '{expected}', got '{reconstructed_cleaned}'")

if __name__ == "__main__":
    test_streaming_chunks()
