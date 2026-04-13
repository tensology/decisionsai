"""System prompt loading and tool description building."""

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Module-level cache for system prompt template
_template_cache: dict = {}


def load_system_prompt_template(template_path: Optional[Path] = None) -> str:
    """Load the system prompt template from file.
    
    Caches the result after first read — the template file doesn't change at runtime.
    
    Args:
        template_path: Optional path to template file. If None, uses default location.
        
    Returns:
        Template string with {agent_name} and {tools_description} placeholders
    """
    cache_key = str(template_path) if template_path else "__default__"
    if cache_key in _template_cache:
        return _template_cache[cache_key]

    try:
        if template_path is None:
            current_dir = Path(__file__).parent
            template_path = current_dir / "system_prompt.txt"
        
        if template_path.exists():
            with open(template_path, 'r', encoding='utf-8') as f:
                template = f.read()
            logger.info(f"Loaded system prompt template from {template_path}")
            _template_cache[cache_key] = template
            return template
        else:
            logger.warning(f"System prompt template not found at {template_path}, using fallback")
            fallback = """You are {agent_name}, a helpful VOICE assistant that can control {username}'s computer and answer questions through voice conversation.

You are speaking with {username} (the logged-in user on this system).

IMPORTANT: You are a VOICE assistant. You speak to {username}, and {username} speaks to you. This is a voice-based conversation system, NOT a text-based chat interface.

{tools_description}"""
            _template_cache[cache_key] = fallback
            return fallback
    except Exception as e:
        logger.error(f"Error loading system prompt template: {e}", exc_info=True)
        return """You are {agent_name}, a helpful VOICE assistant that can control {username}'s computer and answer questions through voice conversation.

You are speaking with {username} (the logged-in user on this system).

IMPORTANT: You are a VOICE assistant. You speak to {username}, and {username} speaks to you. This is a voice-based conversation system, NOT a text-based chat interface.

{tools_description}"""


def build_tools_description(tools: List) -> str:
    """Build a structured description of available tools organized by category.
    Describes every tool passed in (from loader.py) - loader is single source of truth, no hardcoded allowlist.
    """
    if not tools:
        return ""

    categories = {
        'SCREENSHOT & VISION': ['screenshot_analyzer', 'vision_analyzer', 'image_generator'],
        'CODE & FILES': ['execute_code', 'file_operations', 'open_file', 'document_extractor', 'pdf_page_extractor', 'index_folder', 'convert_document'],
        'WEB': ['web_search', 'web_fetch'],
        'UI ELEMENT TARGETING': ['get_window_tree', 'find_element', 'move_to_element', 'click_element_by_id'],
        'MOUSE CONTROL': ['mouse_movement', 'mouse_actions'],
        'KEYBOARD': ['text_editing', 'caret_movement', 'special_key', 'function_key', 'type_text'],
        'WINDOWS & NAVIGATION': ['open_window', 'keyboard_shortcut', 'open_file_menu', 'oracle_control', 'oracle_globe', 'smart_open', 'mode_control', 'shortcut'],
        'MEDIA': ['media_control', 'save_audio', 'audio_transcriber', 'video_transcriber', 'transcription_doctor', 'file_converter'],
        'CLIPBOARD': ['clipboard_action', 'rework_clipboard', 'summarize_clipboard'],
        'ACTIONS & SNIPPETS': ['create_action', 'create_step_runner', 'play_action', 'stop_action', 'list_actions', 'create_snippet', 'use_snippet', 'start_recording', 'stop_recording'],
        'CHAT': ['new_chat', 'clear_chat', 'open_page'],
        'APPLICATION': ['exit_app', 'wake_up', 'system_info'],
        'INTEGRATIONS': ['send_file_to_telegram', 'send_voice_note_to_telegram', 'git_operations', 'create_cursor_ticket', 'google_workspace', 'markdown_to_google_doc', 'upload_doc_to_google', 'playwright_browser'],
        'PROJECTS': ['list_projects', 'get_project_details', 'switch_project', 'query_current_project', 'deactivate_project', 'create_project_from_folder', 'add_files_to_project', 'create_project_ticket', 'open_project', 'start_project', 'open_and_start_project'],
    }
    all_categorized = {n for names in categories.values() for n in names}
    tool_by_name = {t.name: t for t in tools}

    description_parts = []
    for category, tool_names in categories.items():
        category_tools = [tool_by_name[n] for n in tool_names if n in tool_by_name]
        if category_tools:
            description_parts.append(f"\n{category}:")
            for tool in category_tools:
                desc = getattr(tool, 'description', None) or ''
                short_desc = desc.split('.')[0] if '.' in desc else desc[:60]
                description_parts.append(f"  • {tool.name}: {short_desc}")

    other = [t for t in tools if t.name not in all_categorized]
    if other:
        description_parts.append("\nOTHER TOOLS:")
        for tool in other:
            desc = getattr(tool, 'description', None) or ''
            short_desc = desc.split('.')[0] if '.' in desc else desc[:60]
            description_parts.append(f"  • {tool.name}: {short_desc}")

    return "\n".join(description_parts) if description_parts else ""
