"""Backward-compatible re-exports from split modules.

This file is kept so existing imports like ``from ...utils import X`` continue
to work.  New code should import from the specific module directly:
  - prompt.py          → load_system_prompt_template, build_tools_description
  - text_utils.py      → clean_text_for_tts, normalize_text, parse_tool_calls_from_content
  - tool_routing.py    → detect_request_type, filter_tools_by_context, get_clipboard_content_fast,
                          should_inject_clipboard, _build_tool_triggers, _TOOL_TRIGGERS, etc.
"""

from distr.core.agent.services.llm.prompt import (
    build_tools_description,
    load_system_prompt_template,
)
from distr.core.agent.services.llm.text_utils import (
    brief_tool_completion_message,
    clean_model_text_for_chat,
    clean_text_for_tts,
    humanize_silent_navigation_json,
    normalize_text,
    parse_tool_calls_from_content,
    redact_filesystem_paths_for_conversation,
)
from distr.core.agent.services.llm.tool_routing import (
    _TOOL_TRIGGERS,
    _build_tool_triggers,
    detect_request_type,
    filter_tools_by_context,
    get_clipboard_content_fast,
    should_inject_clipboard,
)

__all__ = [
    "build_tools_description",
    "load_system_prompt_template",
    "brief_tool_completion_message",
    "clean_model_text_for_chat",
    "clean_text_for_tts",
    "humanize_silent_navigation_json",
    "normalize_text",
    "parse_tool_calls_from_content",
    "redact_filesystem_paths_for_conversation",
    "_TOOL_TRIGGERS",
    "_build_tool_triggers",
    "detect_request_type",
    "filter_tools_by_context",
    "get_clipboard_content_fast",
    "should_inject_clipboard",
]
