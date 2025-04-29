from contextlib import contextmanager
import sys
import io
import os
import warnings
import time
import datetime
import re
import importlib.util
import logging
import regex

# Suppress specific warnings
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)

@contextmanager
def suppress_stdout():
    """Context manager to temporarily suppress stdout and stderr"""
    # Save the current stdout and stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    # Redirect stdout and stderr to devnull
    with open(os.devnull, 'w') as devnull:
        sys.stdout = devnull
        sys.stderr = devnull
        try:
            yield
        finally:
            # Restore stdout and stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

@contextmanager
def suppress_vosk_logs():
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    os.dup2(devnull, 2)
    try:
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(devnull)

def get_timestamp():
    """Return a formatted timestamp for logging"""
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

# Text Processing Utilities
class TextProcessor:
    """
    Utility class for text processing operations including:
    - Handling abbreviations
    - Cleaning and normalizing text
    - Handling special formatting and markdown
    - Filtering irrelevant audio artifacts
    
    This centralizes all text processing logic for consistent handling.
    """
    
    # Sound descriptions and audio artifacts to filter out
    AUDIO_ARTIFACTS = [
        "(clears throat)", "[blank audio]", "[no audio]", 
        "[clapping]", "(clapping)", "[laughter]", "[laugh]", 
        "(laughter)", "(laugh)", "[music]", "(music)", 
        "[bleep]", "(bleep)", "[beep]", "(beep)", 
        "[bell]", "(bell)", "[static]", "[popping]", 
        "(popping)", "[silence]", "(silence)", "[sigh]",
        "(sighs)", "[sighing]", "(sighing)", "[applause]", 
        "(applause)", "(bell ringing)", "(clicking)",
        "(coughing)", "(knocking)", "[coughing]",
        "[tapping]", "(beatboxing)", "(tapping)",
        "[dog barks]", "(cough)", "(breathing heavily)"
    ]
    
    @classmethod
    def is_audio_artifact(cls, text):
        """Check if text is an audio artifact that should be filtered out."""
        if not text:
            return True
            
        text = text.strip().lower()
        return text in [artifact.lower() for artifact in cls.AUDIO_ARTIFACTS]
    
    @classmethod
    def clean_text(cls, text):
        """
        Clean and normalize text by:
        - Handling markdown formatting
        - Normalizing whitespace
        - Fixing common contractions
        - Handling URL formatting
        - Converting Unicode characters to ASCII equivalents
        
        Args:
            text (str): The text to clean
            
        Returns:
            str: The cleaned and normalized text
        """
        if not text:
            return text
        
        # Convert Unicode characters to ASCII equivalents
        text = text.replace('’', "'")  # Curly apostrophe to straight apostrophe
        text = text.replace('"', '"')  # Smart quotes to straight quotes
        text = text.replace('"', '"')
        text = text.replace('–', '-')  # En dash to hyphen
        text = text.replace('—', '-')  # Em dash to hyphen
        text = text.replace('…', '...')  # Ellipsis to three dots
        
        # Direct fix for Tensology.com split issue
        if text.strip().lower() == "com" or text.strip().lower() == "com.":
            return "com"
            
        if text.strip().endswith("Tensology") or text.strip().endswith("Tensology."):
            return text.strip().rstrip(".") + ".com"
        
        # Fix URLs in markdown format: convert [text](url) to just url
        text = re.sub(r'\[(.*?)\]\((https?://[^\s]+)\)', r'\2', text)
        
        # Fix URLs with asterisks: *text* to text
        text = re.sub(r'\*(https?://[^\s]+)\*', r'\1', text)
        
        # Handle markdown formatting
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
        text = re.sub(r'`(.*?)`', r'\1', text)        # Code
        text = re.sub(r'~~(.*?)~~', r'\1', text)      # Strikethrough
        
        # Remove horizontal rules
        text = re.sub(r'---+', '', text)
        
        # Fix common contractions that might get split
        text = re.sub(r'(\w) \' (\w)', r'\1\'\2', text)  # Fix "I ' m" → "I'm"
        text = re.sub(r'(\w) \' (\w)', r'\1\'\2', text)  # Fix "I 'm" → "I'm"
        
        # Clean up URL formatting
        text = re.sub(r'http(s)? : / / (\S+)', r'http\1://\2', text)  # Fix "http : / /" → "http://"
        text = re.sub(r'(\S+) \. (\w{2,6})', r'\1.\2', text)  # Fix "example . com" → "example.com"
        
        # Fix punctuation with spaces
        text = re.sub(r' \.', '.', text)  # Fix " ." → "."
        text = re.sub(r' ,', ',', text)   # Fix " ," → ","
        text = re.sub(r' !', '!', text)   # Fix " !" → "!"
        text = re.sub(r' \?', '?', text)  # Fix " ?" → "?"
        text = re.sub(r' :', ':', text)   # Fix " :" → ":"
        
        # Join URL pieces that might still be broken
        # Match domain name followed by dot and TLD with possible space or newline between
        url_patterns = [
            (r'(https?://)([^\s]+)(\s+)(\S+\.\S+)', r'\1\2\4'),  # http://www. example.com → http://www.example.com
            (r'(https?://)([^\s]+\.)(\s+)(\S+)', r'\1\2\4'),     # http://example. com → http://example.com
            (r'(\S+\.)(\s+)(com|org|net|edu|gov|io|ai)', r'\1\3')  # example. com → example.com
        ]
        
        for pattern, replacement in url_patterns:
            text = re.sub(pattern, replacement, text)
        
        # Normalize whitespace (compress multiple spaces to single space)
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    @classmethod
    def clean_sentence_for_tts(cls, sentence):
        """
        Clean a sentence for TTS output.
        
        Args:
            sentence (str): The sentence to process
            
        Returns:
            str: Cleaned sentence suitable for TTS
        """
        if not sentence or not isinstance(sentence, str):
            return ""
        
        # Apply general text cleaning
        processed = cls.clean_text(sentence)
        
        return processed.strip()
