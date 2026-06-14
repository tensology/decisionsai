"""Backward-compatible re-exports from split modules.

This file is kept so existing imports like ``from ...utils import X`` continue
to work.  New code should import from the specific module directly:
  - prompt.py          → load_system_prompt_template, build_tools_description
  - text_utils.py      → clean_text_for_tts, normalize_text, parse_tool_calls_from_content
  - tool_routing.py    → detect_request_type, filter_tools_by_context, get_clipboard_content_fast,
                          should_inject_clipboard, _build_tool_triggers, _TOOL_TRIGGERS, etc.
"""

# prompt

# text utilities

# tool routing
