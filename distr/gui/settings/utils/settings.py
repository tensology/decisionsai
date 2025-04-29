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
from typing import Dict, Any

# Import database utilities
from distr.core.db import get_session, Settings
from distr.core.utils import load_settings_from_db as core_load_settings
from distr.core.utils import save_settings_to_db as core_save_settings

# Default settings
DEFAULT_SETTINGS = {
    'load_splash_sound': False,
    'show_about': False,
    'restore_position': True,
    'consent_given': False,
    'selected_oracle': None,
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
    'ollama_enabled': True,
    'ollama_url': 'http://localhost:11434/'
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
        merged_settings = {**DEFAULT_SETTINGS, **settings}
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
            
        return True
    except Exception as e:
        logging.error(f"Error validating settings: {str(e)}")
        return False 