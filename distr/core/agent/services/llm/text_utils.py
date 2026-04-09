"""Text cleaning and parsing utilities for LLM services."""

import json
import re
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def clean_text_for_tts(text: str, strip_whitespace: bool = True) -> str:
    """
    Clean text for Text-to-Speech processing.
    Removes markdown, emojis, and unpronounceable characters.
    """
    if not text:
        return ""
        
    # CRITICAL FIX: Remove hallucinated Chain-of-Thought or Prompt Leakage
    text = re.sub(r'Your tool output was:.*?Your response should be:\s*', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'^Your response should be:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r"^Here's (?:the|a) natural response:\s*", '', text, flags=re.IGNORECASE)

    # Strip leaked tool call artifacts from TTS
    text = re.sub(r'to=functions\.\w+\s*\{[^}]*\}\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'to=functions\.\w+\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'functions\.\w+\s*\([^)]*\)\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\{"name"\s*:\s*"\w+".*?"arguments"\s*:.*?\}\s*', '', text, flags=re.IGNORECASE | re.DOTALL)

    # Strip XML blocks
    text = re.sub(r'<tool_call>\s*.*?\s*</tool_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>\s*.*?\s*</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<tool_call>.*', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)

    # Normalize smart/curly quotes
    text = re.sub(r"[\u2018\u2019\u0060\u00B4]", "'", text)
    text = re.sub(r"[\u201C\u201D]", '"', text)

    # Remove emojis and non-speakable unicode characters
    sanitized_chars = []
    for char in text:
        try:
            code = ord(char)
            if (
                (0x20 <= code <= 0x24F) or
                code == 0x0A or code == 0x0D or code == 0x09 or
                code == 0x2026 or code == 0x2013 or code == 0x2014
            ):
                sanitized_chars.append(char)
        except (ValueError, TypeError):
            continue
    text = ''.join(sanitized_chars)

    # Remove markdown formatting
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'_+', '', text)
    text = re.sub(r'`+', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[•\-\*→]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'[✓❌✅⚠️🚨]', '', text)
    text = re.sub(r'\[([^\]]+)\]', r'\1', text)
    # Strip lone # characters that arrive in streaming chunks
    text = re.sub(r'(?:^|\n)#{1,6}\s', '\n', text)

    # Normalize whitespace
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    if strip_whitespace:
        if '\n' in text:
            lines = text.split('\n')
            lines = [line.strip() for line in lines]
            text = '\n'.join(lines)
        else:
            text = text.strip()
    
    return text


def normalize_text(text: str) -> str:
    """Normalize text to fix common transcription errors."""
    if not text:
        return ""
    text_lower = text.lower().strip()
    text_lower = text_lower.replace("snip it", "snippet").replace("snipit", "snippet")
    text_lower = text_lower.replace("right click", "rightclick")
    text_lower = text_lower.replace("double click", "doubleclick")
    return text_lower


def parse_tool_calls_from_content(content: str) -> List[Dict]:
    """Parse tool calls from LLM response content that may contain JSON.
    
    Args:
        content: LLM response content that may contain JSON tool calls
        
    Returns:
        List of parsed tool call dictionaries
    """
    tool_calls = []
    
    if not content:
        return tool_calls
    
    # Patterns to find JSON in text
    json_patterns = [
        r'\{[^{}]*"name"[^{}]*\}',
        r'\{[^{}]*"function"[^{}]*\}',
        r'\{[^{}]*"tools"[^{}]*\}',
        r'\{[^{}]*"action"[^{}]*\}',
        r'\{[^{}]*"operation"[^{}]*\}',
    ]
    
    json_matches = []
    for pattern in json_patterns:
        matches = re.findall(pattern, content)
        json_matches.extend(matches)
    
    if content.strip().startswith('{'):
        try:
            test_json = json.loads(content.strip())
            if isinstance(test_json, dict) and any(key in test_json for key in ['name', 'function', 'tools', 'action', 'operation', 'params', 'param', 'parameters']):
                json_matches.append(content.strip())
        except (json.JSONDecodeError, ValueError):
            pass
    
    for json_str in json_matches:
        try:
            parsed_json = json.loads(json_str)
            tool_name = None
            params = {}
            
            if 'name' in parsed_json:
                tool_name = parsed_json.get('name', '')
                if 'class' in parsed_json and not tool_name:
                    tool_name = parsed_json.get('class', '')
                params = parsed_json.get('parameters', parsed_json.get('params', {}))
            elif 'tools' in parsed_json:
                tools_obj = parsed_json.get('tools', {})
                if isinstance(tools_obj, dict):
                    if 'action' in tools_obj:
                        action = tools_obj.get('action', '')
                        if action in ['copy', 'cut', 'paste']:
                            tool_name = 'text_editing'
                            params = {'operation': action}
                    elif 'operation' in tools_obj:
                        tool_name = 'text_editing'
                        params = {'operation': tools_obj.get('operation', '')}
            elif 'function' in parsed_json:
                func_obj = parsed_json.get('function', {})
                if isinstance(func_obj, dict):
                    tool_name = func_obj.get('name', '')
                    args_str = func_obj.get('arguments', '')
                    if args_str:
                        try:
                            params = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except (json.JSONDecodeError, ValueError):
                            params = {}
            elif 'params' in parsed_json:
                params_obj = parsed_json.get('params', {})
                if isinstance(params_obj, dict):
                    if 'action' in params_obj:
                        action = params_obj.get('action', '')
                        if action in ['copy', 'cut', 'paste']:
                            tool_name = 'text_editing'
                            params = {'operation': action}
                    elif 'name' in parsed_json and parsed_json.get('name') == 'clipboard_action':
                        tool_name = 'clipboard_action'
                        params = params_obj
                    else:
                        tool_name = parsed_json.get('name', 'unknown')
                        params = params_obj
            elif 'param' in parsed_json:
                raw_tool_name = parsed_json.get('param', '')
                if raw_tool_name:
                    tool_name = raw_tool_name
                    params = {}
            elif 'children' in parsed_json:
                children = parsed_json.get('children', [])
                if children and isinstance(children, list):
                    first_child = children[0] if children else {}
                    if isinstance(first_child, dict):
                        tool_name = first_child.get('name', '')
                        params = first_child.get('parameters', {})
            elif 'parameters' in parsed_json:
                params_obj = parsed_json.get('parameters', {})
                if isinstance(params_obj, dict):
                    tool_name = parsed_json.get('name', 'unknown')
                    params = params_obj
            
            if tool_name and tool_name != 'unknown':
                tool_calls.append({
                    'name': tool_name,
                    'arguments': json.dumps(params) if isinstance(params, dict) else str(params)
                })
                content = re.sub(r'.*?' + re.escape(json_str), '', content, flags=re.DOTALL)
        except json.JSONDecodeError:
            continue
        except Exception as e:
            logger.warning(f"Error parsing tool call JSON: {e}")
            continue
    
    return tool_calls
