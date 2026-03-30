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
from distr.core.utils import load_settings_from_db as core_load_settings
from distr.core.utils import save_settings_to_db as core_save_settings

# Default settings
DEFAULT_SETTINGS = {
    'load_splash_sound': True,
    'show_about': False,
    'welcome_greet_me': True,
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
    'rube_enabled': False,
    'rube_token': '',
    'ollama_enabled': True,
    'ollama_url': 'http://localhost:11434/',
    'accepted_eula': False,
    'locked_input': 'System Default',
    'locked_output': 'System Default',
    # Note: Jira and Trello accounts are now stored in connected_accounts JSON field
    # Kanban agent global settings
    'kanban_agent_enabled': False,
    'kanban_agent_frequency': 'daily',
    'kanban_agent_time': '09:00',
    'kanban_agent_hours': '[]',
    'kanban_agent_days': '[]',
    'kanban_agent_monthly_day': 1,
    'kanban_agent_source_lane': '',
    'kanban_agent_done_lane': '',
    'kanban_agent_orchestrator_provider': '',
    'kanban_agent_orchestrator_model': '',
    'kanban_agent_coder_provider': '',
    'kanban_agent_coder_model': '',
    'kanban_agent_sub_provider': '',
    'kanban_agent_sub_model': '',
    'kanban_cli_tool': '',
    'kanban_cli_auth': '',
}

def load_settings_from_db() -> Dict[str, Any]:
    """
    Load settings from the database.
    
    Returns:
        Dict[str, Any]: Loaded settings dictionary
    """
    try:
        # Use the core utility to load settings
        settings = core_load_settings()
        
        # If no settings found, return defaults
        if not settings:
            logging.debug("No settings found in database, using defaults")
            return DEFAULT_SETTINGS.copy()
        
        # Merge with defaults for any missing values
        # IMPORTANT: settings from DB take precedence over defaults
        merged_settings = {**DEFAULT_SETTINGS, **settings}

        # Migrate legacy GIF filename values for selected_oracle
        selected = merged_settings.get('selected_oracle')
        if isinstance(selected, str) and re.match(r'^\d+\.gif$', selected):
            from distr.core.skin_migration import migrate_selected_oracle
            merged_settings['selected_oracle'] = migrate_selected_oracle(selected)
            try:
                core_save_settings(merged_settings)
                logging.info(f"Migrated selected_oracle from '{selected}' to '{merged_settings['selected_oracle']}'")
            except Exception as exc:
                logging.error(f"Failed to persist migrated selected_oracle: {exc}")

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