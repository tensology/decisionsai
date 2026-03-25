
import re

def clean_text_for_tts(text: str) -> str:
    """Remove markdown, asterisks, emojis, and other formatting that shouldn't be spoken.
    
    Args:
        text: Text to clean
        
    Returns:
        Cleaned text suitable for TTS
    """
    if not text:
        return text
    
    # Remove emojis and non-speakable unicode characters
    # Keep only basic Latin, Latin-1 Supplement, and Latin Extended characters
    # Also keep common punctuation and symbols that can be spoken
    # CRITICAL: Space (0x20) MUST be preserved - it's essential for word separation
    sanitized_chars = []
    for char in text:
        try:
            code = ord(char)
            # ALLOWLIST: Basic Latin, Latin-1 Supplement, Latin Extended-A/B
            # Ranges: 0x20-0x7E (Basic Latin), 0xA0-0xFF (Latin-1), 0x0100-0x024F (Latin Extended)
            # Also allow some specific useful symbols that can be spoken
            # CRITICAL: 0x20 is SPACE - must be included to preserve word spacing
            if (
                (0x20 <= code <= 0x24F) or  # Latin characters and basic punctuation (includes space 0x20)
                code == 0x2026 or           # Ellipsis …
                code == 0x2013 or           # En dash
                code == 0x2014             # Em dash
            ):
                sanitized_chars.append(char)
            # Skip everything else (emojis, complex scripts, etc.)
        except:
            continue
    
    text = ''.join(sanitized_chars)
    
    # Remove asterisks (single and double) - used for bold/emphasis
    text = re.sub(r'\*+', '', text)
    
    # Remove underscores used for emphasis
    text = re.sub(r'_+', '', text)
    
    # Remove backticks (code blocks)
    text = re.sub(r'`+', '', text)
    
    # Remove markdown links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    
    # Remove markdown headers (# Header -> Header)
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    
    # Remove numbered list markers (1. 2. 3. etc.) at start of lines
    # Pattern: start of line, optional whitespace, digits, period, optional space
    text = re.sub(r'^\s*\d+\.\s*', '', text, flags=re.MULTILINE)
    
    # Remove bullet point markers (•, -, *, →) at start of lines
    # Pattern: start of line, optional whitespace, bullet char, optional space
    text = re.sub(r'^\s*[•\-\*→]\s*', '', text, flags=re.MULTILINE)
    
    # Remove common formatting symbols that shouldn't be spoken
    # Remove checkmarks, X marks, warning symbols, etc. (but keep if they're part of words)
    text = re.sub(r'[✓❌✅⚠️🚨]', '', text)
    
    # Remove brackets and parentheses used for emphasis (but keep if they're part of natural speech)
    # Only remove standalone brackets/parentheses, not those around words
    # This is tricky - we'll be conservative and only remove obvious formatting cases
    text = re.sub(r'\[([^\]]+)\]', r'\1', text)  # Remove square brackets but keep content
    
    # CRITICAL: Preserve ALL spaces between words - only collapse excessive whitespace
    # Collapse multiple consecutive spaces/tabs to single space (preserve single spaces)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    
    # Normalize newlines (convert \r\n to \n)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Convert 3+ consecutive newlines to double newline (preserve paragraph breaks)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Clean up trailing/leading whitespace on each line ONLY
    # CRITICAL: This preserves spaces between words - strip() only removes leading/trailing
    if '\n' in text:
        # Only process line-by-line if there are actual newlines
        lines = text.split('\n')
        lines = [line.strip() for line in lines]
        text = '\n'.join(lines)
    else:
        # No newlines - just strip leading/trailing from the whole text
        # This preserves all spaces between words
        text = text.strip()
    
    return text

def test_clean_text():
    # Test case 1: Normal sentence with spaces
    text1 = "This is a test sentence."
    cleaned1 = clean_text_for_tts(text1)
    print(f"Test 1 (Normal): '{text1}' -> '{cleaned1}'")
    
    # Test case 2: Sentence with newlines (the bug I suspect)
    text2 = "This\nis\na\ntest\nsentence."
    cleaned2 = clean_text_for_tts(text2)
    print(f"Test 2 (Newlines): '{repr(text2)}' -> '{cleaned2}'")
    
    # Test case 3: Sentence with tabs
    text3 = "This\tis\ta\ttest."
    cleaned3 = clean_text_for_tts(text3)
    print(f"Test 3 (Tabs): '{repr(text3)}' -> '{cleaned3}'")
    
    # Test case 4: The user's specific example text (approximated)
    # If the LLM generates with newlines instead of spaces?
    text4 = "Alright,\nhere's\na\nquick\nstory."
    cleaned4 = clean_text_for_tts(text4)
    print(f"Test 4 (User approx): '{repr(text4)}' -> '{cleaned4}'")

    # Test case 5: Unicode spaces (non-breaking, etc)
    # 0xA0 is NBSP.
    text5 = "This\u00A0is\u00A0a\u00A0test."
    cleaned5 = clean_text_for_tts(text5)
    print(f"Test 5 (NBSP): '{repr(text5)}' -> '{cleaned5}'")

if __name__ == "__main__":
    test_clean_text()
