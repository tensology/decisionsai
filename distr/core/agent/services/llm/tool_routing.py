"""Request type detection, tool filtering, and clipboard context injection.

Routing pipeline (two clean layers):
  1. FastActionDetector (regex patterns) → bypass LLM entirely for unambiguous commands
  2. detect_request_type() + ToolRouter → decides if LLM gets tools, and which ones

The SAFE default is question_with_tools (give LLM tools). The DANGEROUS
default is conversational (strip all tools). We only classify as
conversational when we're confident it's pure chat.
"""

import logging
from typing import List, Dict

from distr.core.agent.services.llm.text_utils import normalize_text

logger = logging.getLogger(__name__)


def _build_tool_triggers() -> frozenset:
    """Build tool trigger words from the TOOL_REGISTRY.
    
    Auto-derives trigger words from tool class names. The semantic router
    handles fine-grained matching; this is just a fast gate for
    detect_request_type() to avoid classifying tool commands as conversational.
    """
    from distr.core.agent.tools.loader import TOOL_REGISTRY
    import re as _re
    auto_triggers = set()
    for class_name in TOOL_REGISTRY:
        words = _re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', class_name.replace('Tool', '')).lower()
        auto_triggers.add(words)
        for w in words.split():
            if len(w) > 3:
                auto_triggers.add(w)

    voice_triggers = {
        'copy', 'cut', 'paste', 'clipboard', 'click', 'right click', 'double click',
        'scroll', 'drag', 'press', 'open', 'focus', 'switch', 'bring', 'activate',
        'quit', 'exit', 'play', 'pause', 'mute',
        'volume', 'screenshot', 'snippet', 'snip it', 'snipit',
        'list files', 'show files', 'create file', 'read file', 'delete file',
        'open file', 'copy file', 'move file',
        # Ticket board and external integrations (Jira/Trello)
        'create ticket', 'create a ticket', 'make a ticket', 'add a ticket',
        'ticket board', 'kanban board', 'list boards', 'external board', 'external boards',
        'jira', 'trello', 'jira board', 'trello board', 'jira ticket', 'trello ticket',
        'jira tickets', 'trello tickets', 'list jira tickets', 'list trello tickets',
        'search the web', 'look up', 'search for',
        'new chat', 'clear chat', 'save as audio',
        'convert to pdf', 'convert to docx', 'make a pdf', 'export as pdf',
        'git commit', 'git push', 'git pull', 'git status',
        'workflow', 'run steps', 'automate',
        'send to telegram', 'execute code', 'run code',
        'start recording', 'stop recording', 'run action',
    }

    return frozenset(auto_triggers | voice_triggers)


# Built once at import time
_TOOL_TRIGGERS = _build_tool_triggers()

_CONVERSATIONAL_KEYWORDS = frozenset([
    'story', 'tell me a story', 'tell a story', 'share a story', 'give me a story',
    'what is', 'what are', 'what was', 'what were', 'what do', 'what does', 'what did',
    'how is', 'how are', 'how do', 'how does', 'how did', 'how can', 'how could',
    'why is', 'why are', 'why do', 'why does', 'why did',
    'when is', 'when are', 'when do', 'when does', 'when did', 'when was', 'when were',
    'where is', 'where are', 'where do', 'where does', 'where did', 'where was',
    'who is', 'who are', 'who do', 'who does', 'who did', 'who was', 'who were',
    'explain', 'describe', 'can you tell me', 'could you tell me', 'tell me about'
])

_SHORT_CONVERSATIONAL = frozenset([
    'yes', 'no', 'yeah', 'nope', 'sure', 'okay', 'ok', 'thanks', 'thank you',
    'cool', 'great', 'awesome', 'go ahead', 'please do', 'correct', 'right', 'continue',
    'hello', 'hi', 'hi there', 'hey', 'hey there', 'good morning', 'good afternoon',
    'good evening', 'good night', 'goodbye', 'bye', 'see you', 'later',
    'say that again', 'repeat that', 'come again',
])

_QUESTION_INDICATORS = frozenset([
    'can you', 'could you', 'would you', 'will you', 'should you',
    'what', 'how', 'why', 'when', 'where', 'who', 'which'
])


def get_clipboard_content_fast():
    """Fast clipboard content retrieval for context injection."""
    try:
        import platform
        import subprocess
        system = platform.system()
        
        if system == "Darwin":
            result = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=0.5)
            return result.stdout if result.returncode == 0 else None
        elif system == "Windows":
            result = subprocess.run(['powershell', '-command', 'Get-Clipboard'], capture_output=True, text=True, timeout=0.5)
            return result.stdout.strip() if result.returncode == 0 else None
        else:
            try:
                result = subprocess.run(['xclip', '-selection', 'clipboard', '-o'], capture_output=True, text=True, timeout=0.5)
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
            try:
                result = subprocess.run(['xsel', '--clipboard', '--output'], capture_output=True, text=True, timeout=0.5)
                return result.stdout if result.returncode == 0 else None
            except Exception:
                pass
            return None
    except Exception:
        return None


def should_inject_clipboard(text: str) -> bool:
    """Check if clipboard content should be injected into context."""
    if not text:
        return False
    text_normalized = normalize_text(text)
    clipboard_keywords = [
        'clipboard', 'what\'s in clipboard', 'what is in clipboard',
        'read clipboard', 'get clipboard', 'show clipboard',
        'explain this', 'elaborate this', 'rework this', 'reword this',
        'summarize this', 'read this'
    ]
    return any(kw in text_normalized for kw in clipboard_keywords)


def detect_request_type(text: str) -> str:
    """Detect request type: 'instruction', 'question_with_tools', or 'conversational'.
    
    DESIGN PRINCIPLE: Default to giving the LLM tools. Only strip tools when
    we're confident it's pure conversation.
    """
    if not text:
        return 'conversational'
    
    text_normalized = normalize_text(text)
    text_lower = text.lower().strip()
    words = text_lower.split()
    
    if text_lower in _SHORT_CONVERSATIONAL:
        return 'conversational'
    if len(words) <= 3:
        if all(w.rstrip('.,!?') in _SHORT_CONVERSATIONAL for w in words):
            return 'conversational'
    
    has_tool_trigger = any(kw in text_normalized for kw in _TOOL_TRIGGERS)
    
    if has_tool_trigger:
        has_question = (
            text_lower.endswith('?') or
            any(q in text_lower for q in _QUESTION_INDICATORS)
        )
        return 'question_with_tools' if has_question else 'instruction'
    
    is_conversational = (
        any(kw in text_lower for kw in _CONVERSATIONAL_KEYWORDS)
        or text_lower.endswith('?')
    )
    if is_conversational:
        return 'conversational'
    
    return 'question_with_tools'


def filter_tools_by_context(user_input: str, all_tools: List, previous_messages: List[Dict]) -> List:
    """Return all tools — capable models (GPT-4+, Claude, etc.) handle large tool sets fine.
    
    Tool filtering was causing missed tool calls across all providers.
    The LLM is better at deciding which tools to use than our heuristic filter.
    """
    return all_tools
