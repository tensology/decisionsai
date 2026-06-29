"""Text cleaning and parsing utilities for LLM services."""

import json
import re
import logging
import unicodedata
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Short neutral phrase for chat + TTS when a raw filesystem path would have
# appeared. This must not imply that the agent created or saved anything.
_PATH_REDACT_PLACEHOLDER = "a local path"

# Line ends with sentence-ending punctuation (., !, ?, ellipsis). Optional closers after.
_TTS_LINE_SENTENCE_END_RE = re.compile(r'[.!?…]["\'\)\]]*\s*$')
# Trailing clause punctuation to drop before appending a period.
_TTS_LINE_TRAILING_CLAUSE_RE = re.compile(r'[,;:\-–—]+\s*$')
_PROMPT_LEAK_RE = re.compile(
    r"""(?isx)
    ^\s*(?:
        (?:system|developer)\s+(?:prompt|message|instructions)\s*:
        |
        you\s+are\s+(?:codex|chatgpt|an?\s+ai\s+assistant|an?\s+voice\s+assistant|haley|heart)\b
        |
        (?:the\s+)?(?:system|developer)\s+(?:prompt|message|instructions)\s+(?:is|are|says|states)\b
    )
    |
    \b(?:valid\s+channels|available\s+tools|tool\s+namespace|knowledge\s+cutoff|current\s+date)\b
    |
    \bdo\s+not\s+(?:reveal|mention|disclose)\s+(?:the\s+)?(?:system\s+prompt|instructions)\b
    """
)


def looks_like_prompt_leak(text: str) -> bool:
    """Return True when model output appears to be internal prompt/instruction text."""
    if not text:
        return False
    sample = str(text).strip()
    if len(sample) < 12:
        return False
    return bool(_PROMPT_LEAK_RE.search(sample[:1500]))


def ensure_line_sentence_boundaries_for_tts(text: str) -> str:
    """Ensure each non-empty line ends with sentence punctuation before newline collapse.

    Copied email and clipboard text often breaks mid-thought at newlines without a
    full stop. Kokoro and other TTS engines then run lines together. Add a single
    period only when the line does not already end with ., !, or ? (no double stops).
    """
    if not text or "\n" not in text:
        return text

    lines = text.split("\n")
    out_lines: list[str] = []
    for line in lines:
        core = line.rstrip()
        if not core:
            out_lines.append(line)
            continue
        if _TTS_LINE_SENTENCE_END_RE.search(core):
            out_lines.append(line)
            continue
        core = _TTS_LINE_TRAILING_CLAUSE_RE.sub("", core).rstrip()
        if not core:
            out_lines.append(line)
            continue
        if _TTS_LINE_SENTENCE_END_RE.search(core):
            out_lines.append(core)
        else:
            out_lines.append(core + ".")
    return "\n".join(out_lines)


def redact_filesystem_paths_for_conversation(text: str) -> str:
    """Replace local filesystem paths with plain prose.

    Raw paths in chat are unreadable and break TTS (slash noise, long tokens, SSML-like
    artifacts). Use this for assistant bubbles, stream replacement text, and persisted
    replies — not for tool arguments or code sent to execute_code.
    """
    if not text:
        return ""
    out = text
    ph = _PATH_REDACT_PLACEHOLDER

    # file:// URIs
    out = re.sub(r'file://[^\s<>\'\"]+', ph, out, flags=re.IGNORECASE)
    # Windows absolute paths
    out = re.sub(
        r'(?<![\w/])([A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)+[^\\/:*?"<>|\r\n]+)',
        ph,
        out,
    )
    # Unix-style absolute paths: /a/b/c — at least two path segments after root.
    # Require the "/" not to be part of a URL scheme (https://) by rejecting "/" after ":" or another "/".
    out = re.sub(r'(?<![/:])(/(?:[^\s/]+/){2,}[^\s/]+)', ph, out)
    # Home-relative (~/, ~user/)
    out = re.sub(r'~[^\s]+', ph, out)

    # Merge duplicate placeholders from multi-pass or path + line boilerplate
    out = re.sub(r'(?:' + re.escape(ph) + r'\s*){2,}', ph + ' ', out)
    out = re.sub(
        r'(?i)\bfile\s+created\s*:\s*' + re.escape(ph),
        ph,
        out,
    )
    out = re.sub(r'(?i)\bsaved\s+to\s*:\s*' + re.escape(ph), ph, out)
    return out


# Broader than legacy BMP-only whitelist: letters, numbers, marks, punctuation, symbols, spaces.
# Excludes category C (controls/format/surrogate) and So (emoji / pictographs) for speakability.
_ALLOWED_SPOKEN_TTS_CATEGORIES = frozenset({
    "Lu", "Ll", "Lt", "Lm", "Lo",
    "Nd", "Nl", "No",
    "Mc", "Mn",
    "Pc", "Pd", "Pe", "Pf", "Pi", "Po", "Ps",
    "Sk", "Sm", "Sc",
    "Zs",
})


def _char_allowed_for_spoken_tts(char: str) -> bool:
    """Keep multilingual prose; drop emoji (So), controls, and format characters."""
    if char in "\n\r\t":
        return True
    code = ord(char)
    if code < 0x20:
        return False
    cat = unicodedata.category(char)
    if cat.startswith("C"):
        return False
    if cat == "So":
        return False
    return cat in _ALLOWED_SPOKEN_TTS_CATEGORIES





def _make_symbols_speakable_for_tts(text: str) -> str:
    """Turn copied symbol-heavy text into prose without changing the source text."""
    if not text:
        return ""

    def _email_to_words(match: re.Match) -> str:
        local, domain = match.group(1), match.group(2)
        local = re.sub(r"[._-]+", " ", local).strip()
        domain = re.sub(r"[._-]+", " dot ", domain).strip()
        return f"{local} at {domain}"

    text = re.sub(
        r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
        _email_to_words,
        text,
    )
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = re.sub(r"\(([^)]+)\)", r"\1", text)
    text = re.sub(r"\{([^}]+)\}", r". \1.", text)
    text = re.sub(r"\s*\|\s*", ". ", text)
    text = re.sub(r"(?<!\w)@(?=\w)", "at ", text)
    text = re.sub(r"\s*->\s*", " to ", text)
    text = re.sub(r"\s*=>\s*", " becomes ", text)
    text = re.sub(r"\s*&\s*", " and ", text)
    text = re.sub(r"\s*=\s*", " equals ", text)
    text = re.sub(r"\s*\+\s*", " plus ", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"([.!?])\s*([.!?])+", r"\1", text)
    return text


def clean_text_for_tts(
    text: str,
    strip_whitespace: bool = True,
    *,
    spoken_prose: bool = False,
    speakable_symbols: bool = False,
) -> str:
    """
    Clean text for Text-to-Speech processing.
    Removes markdown, emojis, and unpronounceable characters.

    If ``spoken_prose`` is True (e.g. ``speak_text_directly``, planner readouts), use a
    Unicode-category allowlist so Latin-extended, Greek, Cyrillic, CJK, etc. are kept
    instead of the legacy BMP-only filter (which gutted long planner paragraphs).
    Emoji (category So) and controls are still removed.
    """
    if not text:
        return ""

    from distr.core.llm_errors import is_formatted_model_error_message

    if looks_like_prompt_leak(text):
        logger.warning("Suppressing probable prompt leak in model text")
        return ""

    if is_formatted_model_error_message(text):
        return ""

    # Voice-first tools append a technical block after REFERENCE: — never speak that tail.
    _ref = "\n\nREFERENCE:\n"
    if _ref in text:
        text = text.split(_ref, 1)[0]
    else:
        # Be permissive when model emits variant spacing/casing around marker.
        text = re.split(r'\n\s*REFERENCE\s*:\s*\n', text, maxsplit=1, flags=re.IGNORECASE)[0]

    # Strip machine-only suffixes from vision tools (avoid JSON-ish noise in chat/TTS).
    text = re.sub(r'\nPOINTER_RESULT:.*', '', text, flags=re.DOTALL)

    # Strip filesystem paths early so sanitization never preserves slash-heavy tokens for TTS.
    text = redact_filesystem_paths_for_conversation(text)

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
    # Strip any remaining HTML/XML-like tags — angle brackets trigger ElevenLabs SSML parsing
    # and can produce the characteristic elongated "aaaaaaaahhhhh" artifact.
    text = re.sub(r'<[^>]{0,80}>', '', text)
    if speakable_symbols:
        text = _make_symbols_speakable_for_tts(text)
    # Any surviving bare < or > could still confuse ElevenLabs; replace with parens.
    text = text.replace('<', '(').replace('>', ')')

    # Strip leaked Instruction tags from workflow reports that may surface in LLM output
    text = re.sub(r'\[Instruction:.*?\]', '', text, flags=re.IGNORECASE | re.DOTALL)

    # Normalize smart/curly quotes (do not map U+0060 grave — Markdown/code backticks)
    text = re.sub(r"[\u2018\u2019\u00B4]", "'", text)
    text = re.sub(r"[\u201C\u201D]", '"', text)

    # Remove common filler words/interjections that sound unnatural in TTS.
    text = re.sub(r'\b(?:uh+|um+|ah+|er+|hmm+)\b[\s,.;:!?-]*', '', text, flags=re.IGNORECASE)
    # Clamp exaggerated letter elongations ("soooo", "ahhhh") to keep speech natural.
    text = re.sub(r'([A-Za-z])\1{2,}', r'\1\1', text)

    # Remove emojis / unspeakable characters (strict BMP) or category-based (spoken prose)
    sanitized_chars = []
    for char in text:
        try:
            if spoken_prose:
                if _char_allowed_for_spoken_tts(char):
                    sanitized_chars.append(char)
            else:
                code = ord(char)
                if (
                    (0x20 <= code <= 0x24F) or
                    code == 0x0A or code == 0x0D or code == 0x09 or
                    code == 0x2026 or code == 0x2013 or code == 0x2014
                ):
                    sanitized_chars.append(char)
        except (ValueError, TypeError):
            continue
    text = "".join(sanitized_chars)

    # Remove markdown formatting
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'_+', '', text)
    text = re.sub(r'`+', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Inline headings after whitespace collapse (e.g. "# Pick up brief ## Handoff").
    text = re.sub(r'#{1,6}\s+', '', text)
    # Markdown numbered-list markers require whitespace after the dot. In live
    # streaming, a chunk can start mid-version/decimal ("2.3"), and the old
    # zero-or-more whitespace pattern stripped the leading "2." from speech.
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[•\-\*→]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'[✓❌✅⚠️🚨]', '', text)
    text = re.sub(r'\[([^\]]+)\]', r'\1', text)
    # Strip lone # characters that arrive in streaming chunks
    text = re.sub(r'(?:^|\n)#{1,6}\s', '\n', text)

    # Suppress noisy path/URL readouts in spoken output.
    # Replace with concise placeholders so users do not hear slash-heavy strings.
    text = re.sub(r'https?://\S+', 'a web link', text, flags=re.IGNORECASE)
    text = re.sub(r'file://\S+', 'a file link', text, flags=re.IGNORECASE)
    # Paths already redacted via redact_filesystem_paths_for_conversation above.
    # Residual slash/backslash separators from copied snippets are unpleasant
    # in TTS ("slash slash slash", "forward slash"). Treat them as spacing.
    text = re.sub(r'\s*[\\/]+\s*', ' ', text)

    # Email/clipboard paste: newline breaks without terminal punctuation should
    # become sentence boundaries before we collapse lines into one utterance.
    text = ensure_line_sentence_boundaries_for_tts(text)

    # Normalize whitespace — collapse newlines to spaces so TTS never receives bare
    # newline characters (Kokoro/espeak-ng phonemizer drops whitespace at utterance
    # boundaries on \n, producing merged words; ElevenLabs can also stall on them).
    text = text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
    text = re.sub(r'[ \t]{2,}', ' ', text)

    if strip_whitespace:
        text = text.strip()

    # Inject natural pause markers for speech rhythm
    # DISABLED: newline-based pause hints cause espeak-ng phonemizer (used by Kokoro)
    # to drop whitespace at utterance boundaries, producing merged words like
    # "Hello.World" instead of "Hello. World".
    # text = inject_prosody_hints(text)

    return text


def _truncate_spoken_words(text: str, max_len: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip().strip(" -:")
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rsplit(" ", 1)[0].rstrip() + "..."


def spoken_task_summary(
    instruction: str,
    *,
    ticket_title: str = "",
    max_len: int = 150,
) -> str:
    """Short spoken task label from CLI/workflow instruction blobs (not raw markdown)."""
    title = clean_text_for_tts(str(ticket_title or "").strip(), spoken_prose=True)
    title = re.sub(r"\s+", " ", title).strip()
    if title:
        return _truncate_spoken_words(title, max_len)

    raw = str(instruction or "").strip()
    if not raw:
        return ""

    if "--- PRIMARY TASK ---" in raw:
        primary = raw.rsplit("--- PRIMARY TASK ---", 1)[-1].strip()
        first_line = primary.splitlines()[0].strip() if primary else ""
        if first_line:
            spoken = clean_text_for_tts(first_line, spoken_prose=True)
            spoken = re.sub(r"\s+", " ", spoken).strip()
            if spoken:
                return _truncate_spoken_words(spoken, max_len)

    if "## Instruction" in raw:
        chunk = raw.split("## Instruction", 1)[1]
        for stop in ("## Return contract", "## Workspace memory", "## Agent map", "## Pick up brief"):
            if stop in chunk:
                chunk = chunk.split(stop, 1)[0]
        spoken = clean_text_for_tts(chunk, spoken_prose=True)
        spoken = re.sub(r"\s+", " ", spoken).strip()
        if spoken:
            return _truncate_spoken_words(spoken, max_len)

    spoken = clean_text_for_tts(raw, spoken_prose=True)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    low = spoken.lower()
    for boilerplate in (
        "pick up brief",
        "linked entities",
        "read-only pickup",
        "kanban ticket context",
    ):
        if low.startswith(boilerplate):
            spoken = ""
            break
    if spoken:
        return _truncate_spoken_words(spoken, max_len)
    return ""


def spoken_result_summary(value: str, *, max_len: int = 170) -> str:
    """Compress CLI/backend result text for voice without model-list noise."""
    clean = clean_text_for_tts(str(value or ""), spoken_prose=True)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
    low = clean.lower()
    if "available models:" in low:
        clean = clean.split("Available models:", 1)[0].strip().rstrip(".")
    if "cannot use this model" in low or "can't use this model" in low:
        clean = "The selected coding model isn't available."
    return _truncate_spoken_words(clean, max_len)


def clean_model_text_for_chat(text: str, strip_whitespace: bool = True) -> str:
    """Normalize model prose before it is streamed or persisted in chat.

    Some cheaper/free OpenAI-compatible models ignore the no-markdown prompt and
    emit bold markers, headings, code fences, or XML-like tool artifacts. Chat
    should still look like a conversational transcript, so reuse the robust TTS
    sanitizer while keeping broad Unicode prose.
    """
    return clean_text_for_tts(
        text,
        strip_whitespace=strip_whitespace,
        spoken_prose=True,
    )


def humanize_silent_navigation_json(result: str) -> Optional[str]:
    """Turn legacy open_page JSON into conversational text.

    Previously ``open_page`` returned ``{"status","page","url","silent"}``. That string was
    shown in chat and passed through TTS cleaning, which replaces ``https://`` with
    ``a web link`` and corrupts the JSON. Parse and return a plain sentence instead.
    """
    s = (result or "").strip()
    if not s.startswith("{") or '"silent"' not in s:
        return None
    try:
        d = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("silent") or d.get("status") != "success":
        return None
    page = (d.get("page") or "").strip().lower()
    url = (d.get("url") or "").strip()
    path = ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path or ""
        if parsed.fragment:
            path = f"{path}#{parsed.fragment}"
    except Exception:
        pass
    # Mirror open_page routes (labels may be fuzzy matched keys)
    if "kanban" in page or page in ("board", "ticket board", "ticketboard"):
        return "I've opened the Ticket Board in your browser."
    if "chat" in page or page in ("new chat", "new conversation", "chats"):
        return "I've opened Chat in your browser."
    if "project" in page:
        return "I've opened Projects in your browser."
    if "workflow" in page:
        return "I've opened Workflows in your browser."
    if page == "actions":
        return "I've opened Actions in your browser."
    if page in ("skills", "snippets"):
        return "I've opened Skills in your browser."
    if "doc" in page:
        return "I've opened the API documentation in your browser."
    if "/tickets" in path or "/kanban" in path:
        return "I've opened the Ticket Board in your browser."
    if "/chat" in path:
        return "I've opened Chat in your browser."
    if "/projects" in path:
        return "I've opened Projects in your browser."
    if "/workflows" in path:
        return "I've opened Workflows in your browser."
    if "/actions" in path:
        return "I've opened Actions in your browser."
    if "/skills" in path:
        return "I've opened Skills in your browser."
    if "/docs" in path:
        return "I've opened the API documentation in your browser."
    if "/settings" in path:
        return "I've opened Settings in your browser."
    return "I've opened that page in your browser."


def brief_tool_completion_message(tool_name: Optional[str]) -> str:
    """Short spoken acknowledgement after a tool when there is no richer assistant reply.

    Avoids bare \"Done\", which sounds abrupt in voice-first mode.
    """
    key = (tool_name or "").strip().lower()
    table = {
        "open_page": "I've opened that in your browser.",
        "kanban_ticket": "I've updated the ticket board.",
        "create_cursor_ticket": "I've saved that ticket.",
        "find_skill": "I've opened that skill.",
        "push_skill": "I've pushed that skill.",
        "type_text": "I've typed that for you.",
        "summarize_clipboard": "Here's your summary.",
        "rework_clipboard": "I've reworked that text.",
        "clipboard_action": "Finished with the clipboard.",
        "text_editing": "Finished that edit.",
        "create_action": "I've set that action up.",
        "start_recording": "Recording started.",
        "stop_recording": "Recording stopped.",
        "file_operations": "I've applied those file changes.",
        "execute_code": "I've run that code.",
        "play_action": "Playing that action.",
        "stop_action": "Stopped the action.",
        "pause_action": "Paused the action.",
        "resume_action": "Resumed the action.",
    }
    return table.get(key, "I've finished that step.")


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
