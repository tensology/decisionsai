import logging
import regex
import re
import spacy
from pathlib import Path

pattern = regex.compile(r'\b(\p{Lu}\w{0,3})\.(?!\w*\.)', regex.UNICODE)

# Load spaCy model
def load_spacy_model():
    """
    Loads the large spaCy model for sentence segmentation.
    If not available, returns None and falls back to regex-based splitting.
    """
    try:
        return spacy.load("en_core_web_lg")
    except (OSError, IOError):
        logging.warning("Large spaCy model not found. Using regex-based sentence splitting.")
        return None

# Load the spaCy model when module is imported
nlp = load_spacy_model()

async def chunk_text(text: str, chunk_size=128):
    """
    Yields successive 'chunk_size' pieces from 'text'.
    """
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
            
def merge_adjacent_sentences(sentences, min_length=15):
    """
    Merges small sentences together, so "OK! I will do that!" 
    will remain as one sentence instead of ["OK!", "I will do that!"]
    """
    merged = []
    for s in sentences:
        s = s.strip()
        if not merged:
            merged.append(s)
        else:
            # If either the previous sentence or the current sentence is too short,
            # merge them.
            if len(merged[-1]) < min_length or len(s) < min_length:
                merged[-1] = merged[-1] + " " + s
            else:
                merged.append(s)
    return merged
  
def pre_process_text(text: str):
    """
    Pre-processes text to protect special patterns from being split incorrectly.
    Handles abbreviations, URLs, and other special cases.
    """
    # Protect URLs (e.g., example.com, http://example.com)
    url_pattern = r'(?:https?://)?(?:[\w-]+\.)+[\w-]+(?:/[^\s]*)?'
    urls = list(re.finditer(url_pattern, text))  # Convert to list
    protected_text = text
    
    # Replace URLs with markers
    for i, match in enumerate(urls):
        url = match.group()
        marker = f"__URL{i}__"
        protected_text = protected_text.replace(url, marker)
    
    # Protect common abbreviations
    abbreviations = [
        "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.",
        "Inc.", "Ltd.", "Corp.", "Co.",
        "i.e.", "e.g.", "etc.", "vs.", "v.",
        "Jan.", "Feb.", "Mar.", "Apr.", "Aug.", "Sept.", "Oct.", "Nov.", "Dec.",
        ".com", ".org", ".net", ".edu", ".gov"
    ]
    for i, abbr in enumerate(abbreviations):
        marker = f"__ABBR{i}__"
        protected_text = protected_text.replace(abbr, marker)
    
    return protected_text, urls, abbreviations

def post_process_text(text: str, urls, abbreviations):
    """
    Restores protected patterns in the text.
    """
    restored = text
    
    # Restore URLs
    url_pattern = r'(?:https?://)?(?:[\w-]+\.)+[\w-]+(?:/[^\s]*)?'
    for i, match in enumerate(urls):
        url = match.group()
        marker = f"__URL{i}__"
        restored = restored.replace(marker, url)
    
    # Restore abbreviations
    for i, abbr in enumerate(abbreviations):
        marker = f"__ABBR{i}__"
        restored = restored.replace(marker, abbr)
    
    return restored

def split_sentences_regex(text: str):
    """
    Fallback regex-based sentence splitter for when spaCy is not available.
    Uses common sentence boundary patterns to split text while preserving URLs.
    """
    if not text:
        return []
    
    # First, protect URLs and domains
    url_pattern = r'(?:https?://)?(?:[\w-]+\.)+[\w-]+(?:/[^\s]*)?'
    urls = list(re.finditer(url_pattern, text))
    protected_text = text
    
    # Replace URLs with markers
    for i, match in enumerate(urls):
        url = match.group()
        marker = f"__URL{i}__"
        protected_text = protected_text.replace(url, marker)
    
    # Split into sentences using a pattern that looks for:
    # 1. A period, exclamation mark, or question mark
    # 2. Followed by whitespace
    # 3. Followed by a capital letter or number
    # 4. But not if it's part of a URL marker
    sentence_pattern = r'(?<!__URL\d+__)(?<=[.!?])\s+(?=[A-Z0-9])'
    parts = re.split(sentence_pattern, protected_text)
    
    # Process each part and restore URLs
    sentences = []
    for part in parts:
        if part and part.strip():
            # Restore URLs
            restored = part.strip()
            for i, match in enumerate(urls):
                url = match.group()
                marker = f"__URL{i}__"
                restored = restored.replace(marker, url)
            
            # Add proper sentence ending if missing
            if not restored.endswith(('.', '!', '?')):
                restored += '.'
            
            # Only add if it's not too short
            if len(restored) > 3:
                sentences.append(restored)
    
    return sentences

def split_sentences_spacy(text: str):
    """
    Splits text into sentences using spaCy's sentence segmentation.
    """
    if not text or not nlp:
        return []

    doc = nlp(text)
    sentences = [sent.text for sent in doc.sents]
    return sentences

def split_sentences(text: str, locale_str="en_US"):
    """
    Splits text into sentences using spaCy's sentence segmentation if available,
    or falls back to a regex-based approach if spaCy is not available.
    """
    if not text:
        return []
    
    if nlp:
        return split_sentences_spacy(text)
    else:
        return split_sentences_regex(text)

def extract_sentences(text, buffer):
    """
    Extract sentences from text and manage buffer.
    
    Args:
        text (str): Text chunk received from LLM response stream
        buffer (str): Existing buffer of incomplete sentences
        
    Returns:
        tuple: (List of complete sentences, Updated buffer)
    """
    # Add new text to buffer
    updated_buffer = buffer + text
    
    # Nothing to process yet
    if not updated_buffer.strip():
        return [], updated_buffer
    
    # Pre-process text to protect URLs and abbreviations
    protected_text, urls, abbreviations = pre_process_text(updated_buffer)
    
    # Split into sentences
    sentences = split_sentences(protected_text)
    
    # If no complete sentences yet, return empty list and keep buffer
    if not sentences:
        return [], updated_buffer
    
    # Process all but the last sentence (which might be incomplete)
    complete_sentences = []
    for i, sent in enumerate(sentences[:-1]):
        restored = post_process_text(sent, urls, abbreviations)
        complete_sentences.append(restored.strip())
    
    # Keep the last sentence in the buffer as it might be incomplete
    last_sentence = sentences[-1] if sentences else ""
    new_buffer = post_process_text(last_sentence, urls, abbreviations)
    
    # Merge short sentences
    complete_sentences = merge_adjacent_sentences(complete_sentences)
    
    return complete_sentences, new_buffer

def process_buffer(buf):
    """
    Processes the given text buffer and returns a tuple:
    (list_of_complete_sentences, remaining_buffer)
    """
    # Nothing to process
    if not buf or not buf.strip():
        return [], buf
    
    # Protect URLs and domains from being split
    protected_text = buf
    url_pattern = r'(?:https?://)?(?:[\w-]+\.)+[\w-]+(?:/[^\s]*)?'
    urls = list(re.finditer(url_pattern, buf))
    
    # Replace URLs with markers
    for i, match in enumerate(urls):
        url = match.group()
        marker = f"__URL{i}__"
        protected_text = protected_text.replace(url, marker)
    
    # Split into sentences
    possible_sentences = split_sentences(protected_text)
    if not possible_sentences:
        return [], buf

    # Process sentences
    sentences = []
    if buf and buf[-1] in {'.', '?', '!'}:
        # All sentences are complete
        for sentence in possible_sentences:
            # Restore URLs
            restored = sentence.strip()
            for i, match in enumerate(urls):
                url = match.group()
                marker = f"__URL{i}__"
                restored = restored.replace(marker, url)
            if restored and len(restored) > 3:  # Skip very short fragments
                sentences.append(restored)
        new_buf = ""
    else:
        # The last sentence might be incomplete
        for sentence in possible_sentences[:-1]:
            # Restore URLs
            restored = sentence.strip()
            for i, match in enumerate(urls):
                url = match.group()
                marker = f"__URL{i}__"
                restored = restored.replace(marker, url)
            if restored and len(restored) > 3:  # Skip very short fragments
                sentences.append(restored)
        # Keep the last sentence as buffer
        new_buf = possible_sentences[-1] if possible_sentences else ""
        # Restore URLs in buffer
        for i, match in enumerate(urls):
            url = match.group()
            marker = f"__URL{i}__"
            new_buf = new_buf.replace(marker, url)
    
    return sentences, new_buf
        
async def stream_sentence_generator(chunks, target_size=128, min_length=15):
    """
    Accumulates chunks until at least 'target_size' bytes of characters 
    have been buffered, then generates sentences from them
    with at least `min_length` bytes of characters.
    """
    buffer = "" # Our main buffer
    chunk_buffer = []  # Temporary buffer to collect chunks until we hit the target_size 
    current_size = 0  # Running total of the length of buffered chunks

    # Loop over each incoming chunk of text.
    async for chunk in chunks:
        chunk_buffer.append(chunk)
        current_size += len(chunk.encode('utf-8'))

        # Once we've accumulated at least target_size bytes of characters, process the buffered text.
        if current_size >= target_size:
            combined_text = ''.join(chunk_buffer)
            chunk_buffer.clear() 
            current_size = 0

            # Pre-process the combined text (workaround for titles, initials, etc.)
            preprocessed_chunk = pre_process_text(combined_text)
            buffer += preprocessed_chunk

            # Process the main buffer to split it into complete sentences,
            # and update the buffer with any leftover incomplete sentence.
            sentences, buffer = process_buffer(buffer)
            # Merge adjacent sentences if one of them is shorter than min_length.
            sentences = merge_adjacent_sentences(sentences, min_length)
            for sentence in sentences:
                yield sentence

    # After processing all full buffers, check if there are any leftover chunks.
    if chunk_buffer:
        combined_text = ''.join(chunk_buffer)
        preprocessed_chunk = pre_process_text(combined_text)
        buffer += preprocessed_chunk
        chunk_buffer.clear()
        sentences, buffer = process_buffer(buffer)
        # Yield each complete sentence obtained from the leftover text.
        for sentence in sentences:
            yield sentence

    # Finally, if any text remains in the main buffer (an incomplete sentence, perhaps),
    # post-process and yield it as the final sentence.
    if buffer:
        yield post_process_text(buffer.strip())