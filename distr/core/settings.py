"""
Settings Utility Module

This module provides utility functions for loading and saving settings.
It handles the persistence of application settings and provides a consistent interface
for accessing and modifying settings across the application.

Key Features:
- Settings loading and saving
- Default settings management
- Settings validation
- Error handling and logging
"""

import logging
import os
import platform
import re
from typing import Dict, Any

# Import database utilities
from distr.core.db import get_session, Settings
from distr.core.hotkeys import DEFAULTS as HOTKEY_DEFAULTS

# Default settings
DEFAULT_SETTINGS = {
    'load_splash_sound': True,
    'show_about': False,
    'welcome_greet_me': True,
    'load_on_startup': True,
    'always_confirm_file_operations': True,
    'restore_position': True,
    'selected_oracle': 'oracle',
    'sphere_size': 180,
    'playback_speed': 1.0,
    'startup_listening_state': 'remember',
    'oracle_position': 'Custom',
    'assemblyai_enabled': False,
    'assemblyai_key': '',
    'speechmatics_enabled': False,
    'speechmatics_key': '',
    'openai_enabled': False,
    'openai_key': '',
    'anthropic_enabled': False,
    'anthropic_key': '',
    'openrouter_enabled': False,
    'openrouter_key': '',
    'groq_enabled': False,
    'groq_key': '',
    'kilo_enabled': False,
    'kilo_key': '',
    'gemini_enabled': False,
    'gemini_key': '',
    'ollama_enabled': True,
    'ollama_url': 'http://localhost:11434/',
    'accepted_eula': False,
    'locked_input': 'System Default',
    'locked_output': 'System Default',
    # Note: Jira and Trello accounts are now stored in connected_accounts JSON field
    # Ticket Board agent global settings
    'kanban_agent_enabled': False,
    'kanban_agent_frequency': 'daily',
    'kanban_agent_time': '09:00',
    'kanban_agent_hours': '[]',
    'kanban_agent_days': '[]',
    'kanban_agent_monthly_day': 1,
    'kanban_agent_source_lane': 'Current',
    'kanban_agent_done_lane': 'QA/Assess',
    'kanban_agent_orchestrator_provider': '',
    'kanban_agent_orchestrator_model': '',
    'kanban_agent_coder_provider': '',
    'kanban_agent_coder_model': '',
    'kanban_agent_sub_provider': '',
    'kanban_agent_sub_model': '',
    'kanban_cli_tool': '',
    'kanban_cli_auth': '',
    # Telegram response format settings
    'telegram_text_only_override': False,
    'telegram_auto_match_mode': True,
    'global_ptt_hotkey_enabled': True,
    'global_ptt_hotkey_combo': HOTKEY_DEFAULTS['global_ptt_hotkey_combo'],
    'oracle_size_hotkey_decrease_modifier': HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_modifier'],
    'oracle_size_hotkey_decrease_key': HOTKEY_DEFAULTS['oracle_size_hotkey_decrease_key'],
    'oracle_size_hotkey_increase_modifier': HOTKEY_DEFAULTS['oracle_size_hotkey_increase_modifier'],
    'oracle_size_hotkey_increase_key': HOTKEY_DEFAULTS['oracle_size_hotkey_increase_key'],
    'recording_hotkey_enabled': True,
    'recording_hotkey_modifier': HOTKEY_DEFAULTS['recording_hotkey_modifier'],
    'recording_hotkey_key': HOTKEY_DEFAULTS['recording_hotkey_key'],
    'dictation_hotkey_enabled': HOTKEY_DEFAULTS['dictation_hotkey_enabled'],
    'dictation_hotkey_modifier': HOTKEY_DEFAULTS['dictation_hotkey_modifier'],
    'dictation_hotkey_key': HOTKEY_DEFAULTS['dictation_hotkey_key'],
    'skin_nav_hotkey_previous_modifier': HOTKEY_DEFAULTS['skin_nav_hotkey_previous_modifier'],
    'skin_nav_hotkey_previous_key': HOTKEY_DEFAULTS['skin_nav_hotkey_previous_key'],
    'skin_nav_hotkey_next_modifier': HOTKEY_DEFAULTS['skin_nav_hotkey_next_modifier'],
    'skin_nav_hotkey_next_key': HOTKEY_DEFAULTS['skin_nav_hotkey_next_key'],
    'skin_select_hotkey_modifier': HOTKEY_DEFAULTS['skin_select_hotkey_modifier'],
    'web_hotkey_chat_modifier': HOTKEY_DEFAULTS['web_hotkey_chat_modifier'],
    'web_hotkey_chat_key': HOTKEY_DEFAULTS['web_hotkey_chat_key'],
    'web_hotkey_projects_modifier': HOTKEY_DEFAULTS['web_hotkey_projects_modifier'],
    'web_hotkey_projects_key': HOTKEY_DEFAULTS['web_hotkey_projects_key'],
    'web_hotkey_actions_modifier': HOTKEY_DEFAULTS['web_hotkey_actions_modifier'],
    'web_hotkey_actions_key': HOTKEY_DEFAULTS['web_hotkey_actions_key'],
    'web_hotkey_snippets_modifier': HOTKEY_DEFAULTS['web_hotkey_snippets_modifier'],
    'web_hotkey_snippets_key': HOTKEY_DEFAULTS['web_hotkey_snippets_key'],
    'web_hotkey_workflows_modifier': HOTKEY_DEFAULTS['web_hotkey_workflows_modifier'],
    'web_hotkey_workflows_key': HOTKEY_DEFAULTS['web_hotkey_workflows_key'],
    'web_hotkey_preferences_modifier': HOTKEY_DEFAULTS['web_hotkey_preferences_modifier'],
    'web_hotkey_preferences_key': HOTKEY_DEFAULTS['web_hotkey_preferences_key'],
}

_oracle_migration_run = False


def _migrate_legacy_oracle_setting(settings: dict) -> dict:
    global _oracle_migration_run
    if _oracle_migration_run:
        return settings
    selected = settings.get('selected_oracle')
    if isinstance(selected, str) and re.match(r'^\d+\.gif$', selected):
        from distr.core.skin_migration import migrate_selected_oracle
        from distr.core.utils import save_settings_to_db as core_save_settings
        settings['selected_oracle'] = migrate_selected_oracle(selected)
        try:
            core_save_settings(settings)
            logging.info("Migrated selected_oracle from '%s' to '%s'", selected, settings['selected_oracle'])
        except Exception as exc:
            logging.error("Failed to persist migrated selected_oracle: %s", exc)
    _oracle_migration_run = True
    return settings


def load_settings_from_db() -> Dict[str, Any]:
    """
    Load settings from the database.
    
    Returns:
        Dict[str, Any]: Loaded settings dictionary
    """
    try:
        from distr.core.utils import load_settings_from_db as core_load_settings
        # Use the core utility to load settings
        settings = core_load_settings()
        
        # If no settings found, return defaults
        if not settings:
            logging.debug("No settings found in database, using defaults")
            return DEFAULT_SETTINGS.copy()
        
        # Merge with defaults for any missing values.
        # IMPORTANT: settings from DB take precedence over defaults, but SQL NULL
        # must not clobber defaults (boolean columns often deserialize as None).
        db_overlay = {k: v for k, v in settings.items() if v is not None}
        merged_settings = {**DEFAULT_SETTINGS, **db_overlay}

        # Migrate legacy GIF filename values for selected_oracle (runs at most once per process)
        merged_settings = _migrate_legacy_oracle_setting(merged_settings)

        # Log EULA status for debugging
        if 'accepted_eula' in merged_settings:
            logging.debug(f"Loaded settings: accepted_eula={merged_settings.get('accepted_eula')}")
        return merged_settings
    except Exception as e:
        logging.error(f"Error loading settings from database: {str(e)}")
        return DEFAULT_SETTINGS.copy()

def save_settings_to_db(settings: Dict[str, Any]) -> None:
    """
    Save settings to the database.
    
    Args:
        settings (Dict[str, Any]): Settings to save
    """
    try:
        from distr.core.utils import save_settings_to_db as core_save_settings
        # Use the core utility to save settings
        core_save_settings(settings)
        logging.debug("Settings saved successfully to database")
    except Exception as e:
        logging.error(f"Error saving settings to database: {str(e)}")

def get_system_folder_paths() -> Dict[str, str]:
    """
    Get mapping of common folder names to their actual system paths.
    
    Returns:
        Dict mapping folder names (like "Desktop", "Documents") to full paths
    """
    home = os.path.expanduser("~")
    system = platform.system()
    
    folder_map = {}
    
    if system == "Darwin":  # macOS
        folder_map = {
            "Desktop": os.path.join(home, "Desktop"),
            "Documents": os.path.join(home, "Documents"),
            "Downloads": os.path.join(home, "Downloads"),
            "Pictures": os.path.join(home, "Pictures"),
            "Music": os.path.join(home, "Music"),
            "Movies": os.path.join(home, "Movies"),
            "Applications": "/Applications",
            "my Desktop": os.path.join(home, "Desktop"),
            "my Documents": os.path.join(home, "Documents"),
            "my Downloads": os.path.join(home, "Downloads"),
        }
    elif system == "Windows":
        folder_map = {
            "Desktop": os.path.join(home, "Desktop"),
            "Documents": os.path.join(home, "Documents"),
            "Downloads": os.path.join(home, "Downloads"),
            "Pictures": os.path.join(home, "Pictures"),
            "Music": os.path.join(home, "Music"),
            "Videos": os.path.join(home, "Videos"),
            "my Desktop": os.path.join(home, "Desktop"),
            "my Documents": os.path.join(home, "Documents"),
            "my Downloads": os.path.join(home, "Downloads"),
        }
    else:  # Linux
        folder_map = {
            "Desktop": os.path.join(home, "Desktop"),
            "Documents": os.path.join(home, "Documents"),
            "Downloads": os.path.join(home, "Downloads"),
            "Pictures": os.path.join(home, "Pictures"),
            "Music": os.path.join(home, "Music"),
            "Videos": os.path.join(home, "Videos"),
            "my Desktop": os.path.join(home, "Desktop"),
            "my Documents": os.path.join(home, "Documents"),
            "my Downloads": os.path.join(home, "Downloads"),
        }
    
    return folder_map


def resolve_folder_path(folder_name: str) -> str:
    """
    Resolve a folder name (like "my Desktop") to its actual system path.
    
    Args:
        folder_name: The folder name to resolve (e.g., "my Desktop", "Desktop")
        
    Returns:
        The full path to the folder, or the original name if not found
    """
    folder_map = get_system_folder_paths()
    
    # Try exact match first
    if folder_name in folder_map:
        return folder_map[folder_name]
    
    # Try case-insensitive match
    folder_name_lower = folder_name.lower()
    for key, path in folder_map.items():
        if key.lower() == folder_name_lower:
            return path
    
    # If not found in map, check if it's already a valid path
    if os.path.exists(folder_name):
        return folder_name
    
    # Return original if nothing found
    logging.warning(f"Could not resolve folder path for: {folder_name}")
    return folder_name


def validate_settings(settings: Dict[str, Any]) -> bool:
    """
    Validate settings dictionary.
    
    Args:
        settings (Dict[str, Any]): Settings to validate
        
    Returns:
        bool: True if settings are valid
    """
    try:
        # Check required keys
        required_keys = set(DEFAULT_SETTINGS.keys())
        if not all(key in settings for key in required_keys):
            logging.error("Missing required settings keys")
            return False
            
        # Validate specific settings
        if not isinstance(settings['sphere_size'], (int, float)) or settings['sphere_size'] < 60:
            logging.error("Invalid sphere size")
            return False
            
        if not isinstance(settings['playback_speed'], (int, float)) or settings['playback_speed'] < 0.5:
            logging.error("Invalid playback speed")
            return False
        
        # Validate folder paths if indexed_folders exists
        if 'indexed_folders' in settings:
            indexed_folders = settings['indexed_folders']
            if isinstance(indexed_folders, str):
                try:
                    import json
                    indexed_folders = json.loads(indexed_folders)
                except (json.JSONDecodeError, ValueError):
                    indexed_folders = []
            
            if isinstance(indexed_folders, list):
                for folder in indexed_folders:
                    resolved = resolve_folder_path(folder)
                    if not os.path.exists(resolved):
                        logging.warning(f"Indexed folder path does not exist: {resolved}")
            
        return True
    except Exception as e:
        logging.error(f"Error validating settings: {str(e)}")
        return False 