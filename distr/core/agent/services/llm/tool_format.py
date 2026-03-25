"""Tool parameter definitions and conversion utilities for OpenAI/Ollama function calling format."""

import logging

logger = logging.getLogger(__name__)


def get_tool_parameters(tool_name: str) -> dict:
    """Get Ollama-compatible parameters schema for a specific tool.
    
    Args:
        tool_name: Name of the tool
        
    Returns:
        Dictionary with 'type', 'properties', and 'required' keys for Ollama function calling
    """
    tool_params = {
        "open_window": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The name of the application to open or focus on (e.g., 'Chrome', 'Safari', 'Cursor', 'my code editor')"
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract app name if app_name not provided)"
                }
            },
            "required": []
        },
        "keyboard_shortcut": {
            "type": "object",
            "properties": {
                "shortcut": {
                    "type": "string",
                    "description": "The shortcut name: 'new_tab', 'previous_tab', 'next_tab', 'close', 'quit', 'open_spotlight', 'open_gpt'",
                    "enum": ["new_tab", "previous_tab", "next_tab", "close", "quit", "open_spotlight", "open_gpt"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract shortcut if shortcut not provided)"
                }
            },
            "required": []
        },
        "oracle_control": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The action to perform: 'hide', 'show', or 'change'",
                    "enum": ["hide", "show", "change"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract action if action not provided)"
                }
            },
            "required": []
        },
        "oracle_globe": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full user request text (e.g., 'change globe', 'next globe', 'previous globe', 'change previous globe')"
                },
                "transcription": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full transcription buffer if available"
                }
            },
            "required": []
        },
        "open_file_menu": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "open_file": {
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "The name of the file to open (e.g., 'dogbreeds.txt', 'report.pdf', 'image.jpg'). Can include partial path like 'Downloads/image.jpg'"
                },
                "search_folders": {
                    "type": "string",
                    "description": "Optional comma-separated list of folders to search (e.g., 'Downloads,Documents,Desktop'). Defaults to common folders if not specified."
                }
            },
            "required": []
        },
        "text_editing": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The text editing operation: 'copy', 'paste', 'cut', 'select_all', 'undo', 'redo', 'backspace', 'delete', 'clear_line', 'delete_line', 'force_delete'",
                    "enum": ["copy", "paste", "cut", "select_all", "undo", "redo", "backspace", "delete", "clear_line", "delete_line", "force_delete"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract operation if operation not provided)"
                }
            },
            "required": []
        },
        "caret_movement": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "description": "The direction to move the cursor: 'up', 'down', 'left', 'right', 'page_up', 'page_down', 'home', 'end', 'delete_forward'",
                    "enum": ["up", "down", "left", "right", "page_up", "page_down", "home", "end", "delete_forward"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract direction if direction not provided)"
                }
            },
            "required": []
        },
        "mouse_movement": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "REQUIRED: The mouse movement action. Absolute positions: 'move_center', 'move_top', 'move_bottom', 'move_left', 'move_right', 'move_middle', 'move_vertical_middle'. Relative movement: 'move' (requires direction parameter).",
                    "enum": ["move_center", "move_top", "move_bottom", "move_left", "move_right", "move_middle", "move_vertical_middle", "move"]
                },
                "direction": {
                    "type": "string",
                    "description": "Direction for 'move' action only: 'up', 'down', 'left', 'right', 'slow_up', 'slow_down', 'slow_left', 'slow_right'"
                },
                "screen_number": {
                    "type": "integer",
                    "description": "Optional: Screen number (1, 2, 3, etc.) to move to. If provided, moves to center of specified screen. If not provided, uses current screen."
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract action, direction, and screen number if not provided)"
                }
            },
            "required": ["action"]
        },
        "mouse_actions": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "REQUIRED: The mouse action: 'click', 'double_click', 'right_click', 'scroll_up', 'scroll_down'",
                    "enum": ["click", "double_click", "right_click", "scroll_up", "scroll_down"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract action if not provided)"
                }
            },
            "required": ["action"]
        },
        "media_control": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The media control action: 'play', 'pause', 'stop', 'next_track', 'previous_track', 'volume_up', 'volume_down', 'mute', 'refresh', 'reload'",
                    "enum": ["play", "pause", "stop", "next_track", "previous_track", "volume_up", "volume_down", "mute", "refresh", "reload"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract action if action not provided)"
                }
            },
            "required": []
        },
        "function_key": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The function key to press: 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'",
                    "enum": ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract key if key not provided)"
                }
            },
            "required": []
        },
        "special_key": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The special key to press: 'space', 'enter', 'tab', 'escape', 'alt', 'control', 'command'",
                    "enum": ["space", "enter", "tab", "escape", "alt", "control", "command"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract key if key not provided)"
                }
            },
            "required": []
        },
        "clipboard_action": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "REQUIRED: The clipboard action: 'explain this', 'elaborate this', or 'get' (for 'what's in the clipboard'). Extract from user request immediately. NOTE: 'read this' is handled separately. Do NOT use for 'create snippet' requests.",
                    "enum": ["explain", "elaborate", "get"]
                },
                "text": {
                    "type": "string",
                    "description": "The full user request text (used to extract action if action not provided)"
                }
            },
            "required": ["action"]
        },
        "save_audio": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full user request text (optional, tool will copy clipboard automatically)"
                }
            },
            "required": []
        },
        "exit_app": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full user request text (optional)"
                }
            },
            "required": []
        },
        "rework_clipboard": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full user request text (optional, tool will copy clipboard automatically)"
                }
            },
            "required": []
        },
        "summarize_clipboard": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full user request text (e.g. 'summarize and read', 'summarize this'). Tool copies clipboard automatically."
                }
            },
            "required": []
        },
        "create_snippet": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full user request text. Use this tool when the user wants to create a new 'snippet' or 'code snippet' from their clipboard. This tool reads the clipboard AUTOMATICALLY. Do NOT call clipboard_action first."
                }
            },
            "required": []
        },
        "create_step_runner": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "The task or workflow to break down into steps. Extract from user message (e.g. 'check my calendar every morning' from 'create steps to check my calendar every morning'). Use when user says 'create steps for the step runner', 'break down [task] into steps', 'add a workflow to the step runner'."
                }
            },
            "required": []
        },
        "use_snippet": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The full user request text (e.g., 'copy snippet <x> to clipboard', 'paste snippet <x>')"
                }
            },
            "required": []
        },
        "clear_chat": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "Confirmation to clear the chat (default: true). Always set to true when user requests to clear chat."
                }
            },
            "required": []
        },
        "new_chat": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
    
    # Return tool-specific parameters or default
    if tool_name in tool_params:
        return tool_params[tool_name]
    else:
        # Tool not found in hardcoded list - return empty dict to trigger args_schema extraction
        return {}


def convert_tools_to_openai_format(tools_list):
    """Convert LangChain tools to OpenAI/Ollama function calling format.
    
    Args:
        tools_list: List of tool objects to convert
        
    Returns:
        List of dictionaries in OpenAI function calling format
    """
    result_tools = []
    for tool in tools_list:
        # Try to get tool-specific parameters from hardcoded list first
        parameters = get_tool_parameters(tool.name)
        
        # If not found in hardcoded list, try to extract from tool's args_schema (for LangChain tools)
        if not parameters or parameters == {}:
            if hasattr(tool, 'args_schema') and tool.args_schema:
                try:
                    # Get JSON schema from Pydantic model (try v2 first, fallback to v1)
                    try:
                        # Pydantic v2
                        schema = tool.args_schema.model_json_schema()
                    except AttributeError:
                        # Pydantic v1
                        schema = tool.args_schema.schema()
                    
                    # Convert Pydantic schema to OpenAI/Ollama format
                    properties = {}
                    required = []
                    
                    for field_name, field_info in schema.get('properties', {}).items():
                        # Map Pydantic types to JSON schema types
                        field_type = field_info.get('type', 'string')
                        # Handle union types (e.g., "string | None" -> "string")
                        if isinstance(field_type, list):
                            # For Optional types, get the non-None type
                            field_type = next((t for t in field_type if t != 'null'), field_type[0] if field_type else 'string')
                        
                        # Normalize type
                        if field_type == 'string':
                            field_type = 'string'
                        elif field_type == 'integer' or field_type == 'int':
                            field_type = 'integer'
                        elif field_type == 'number' or field_type == 'float':
                            field_type = 'number'
                        elif field_type == 'boolean' or field_type == 'bool':
                            field_type = 'boolean'
                        elif field_type == 'array':
                            field_type = 'array'
                        elif field_type == 'object':
                            field_type = 'object'
                        else:
                            # Default to string if unknown type
                            field_type = 'string'
                        
                        properties[field_name] = {
                            "type": field_type,
                            "description": field_info.get('description', '')
                        }
                        
                        # Handle array items if present
                        if field_type == 'array' and 'items' in field_info:
                            items = field_info['items']
                            # Handle both dict and list formats
                            if isinstance(items, dict):
                                properties[field_name]['items'] = items
                            elif isinstance(items, list) and items:
                                properties[field_name]['items'] = items[0]
                    
                    # Get required fields
                    required = schema.get('required', [])
                    
                    parameters = {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                    
                    logger.info(f"✅ Extracted parameters from args_schema for tool '{tool.name}': {len(properties)} properties, {len(required)} required")
                    logger.debug(f"Tool '{tool.name}' parameters: {parameters}")
                except Exception as e:
                    logger.error(f"❌ Failed to extract parameters from args_schema for tool '{tool.name}': {e}", exc_info=True)
                    # Fallback to empty parameters
                    parameters = {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
            else:
                # No args_schema and not in hardcoded list - use empty parameters
                # Check if tool has args_schema attribute but it's None or not set
                if hasattr(tool, 'args_schema'):
                    if tool.args_schema is None:
                        logger.debug(f"Tool '{tool.name}' has args_schema attribute but it's None - using empty parameters")
                    else:
                        logger.debug(f"Tool '{tool.name}' has args_schema but extraction may have failed - using empty parameters")
                else:
                    logger.debug(f"Tool '{tool.name}' not found in hardcoded parameters and has no args_schema attribute")
                parameters = {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
        
        # Create Ollama function format
        ollama_tool = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters
            }
        }
        result_tools.append(ollama_tool)
    
    return result_tools

