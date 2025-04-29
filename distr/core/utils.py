from distr.core.constants import CORE_DIR, MODELS_DIR
from distr.core.db import Session, Settings, ScreenPosition
from distr.core.db import get_session
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint
from typing import Dict, Any
import hashlib
import logging
import json
import os

logger = logging.getLogger(__name__)

SETTINGS_DIR = os.path.join(MODELS_DIR, "settings")

def save_settings_to_db(settings_dict: Dict[str, Any]) -> None:
    """Save settings to database"""
    with Session() as session:
        settings = session.query(Settings).first()
        if not settings:
            settings = Settings()
            session.add(settings)
        
        # Convert lists to JSON strings before saving
        if 'indexed_folders' in settings_dict:
            settings_dict['indexed_folders'] = json.dumps(settings_dict['indexed_folders'])
        if 'connected_accounts' in settings_dict:
            settings_dict['connected_accounts'] = json.dumps(settings_dict['connected_accounts'])
        
        # Update all settings from the dictionary
        for key, value in settings_dict.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        
        try:
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Error saving settings: {str(e)}")
            raise

def load_settings_from_db() -> Dict[str, Any]:
    """Load settings from database and return as dictionary"""
    with Session() as session:
        settings = session.query(Settings).first()
        if not settings:
            return {}
        
        # Convert SQLAlchemy model to dictionary
        settings_dict = {}
        for column in Settings.__table__.columns:
            if column.name != 'id':
                value = getattr(settings, column.name)
                # Parse JSON strings back to lists
                if column.name in ['indexed_folders', 'connected_accounts'] and value:
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        value = []
                settings_dict[column.name] = value
                
        return settings_dict


def load_actions_config():
    path = os.path.join(CORE_DIR, "distr", "core", "actions.config.json")
    try:
        with open(path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"Error: Config file not found at {path}")
        config = {"actions": []}
    except json.JSONDecodeError:
        logger.error(f"Error: Invalid JSON in config file at {path}")
        config = {"actions": []}
    return config


def load_preferences_config():    
    path = os.path.join(SETTINGS_DIR, "preferences.json")
    try:
        with open(path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"Error: Preferences file not found at {path}")
        config = {}
    except json.JSONDecodeError:
        logger.error(f"Error: Invalid JSON in config file at {path}")
        config = {}
    return config


def get_screens_hash():
    screens = QApplication.screens()
    screen_info = sorted([
        f"{screen.name()}:{screen.geometry().width()}x{screen.geometry().height()}+{screen.geometry().x()}+{screen.geometry().y()}"
        for screen in screens
    ])
    screens_string = "|".join(screen_info)
    return hashlib.md5(screens_string.encode()).hexdigest()

def get_screen_names():
    return [screen.name() for screen in QApplication.screens()]

def save_oracle_position(x, y, screen=None):
    settings = load_settings_from_db()
    if not settings.get('restore_position'):
        logging.debug("Position not saved - restore_position setting is disabled")
        return
    
    if not screen:
        screen = QApplication.screenAt(QPoint(int(x), int(y)))
        if not screen:
            logging.warning(f"Could not find screen for position {x}, {y}")
            return
    
    # Get screen geometry for validation
    screen_geo = screen.geometry()
    
    # Ensure coordinates are within screen bounds and non-negative
    x = max(0, min(x, screen_geo.width()))
    y = max(0, min(y, screen_geo.height()))
    
    screens_id = get_screens_hash()
    logging.debug(f"\n=== Saving Oracle Position ===")
    logging.debug(f"Screen Configuration Hash: {screens_id}")
    logging.debug(f"Current Screen: {screen.name()}")
    logging.debug(f"Screen Geometry: {screen_geo}")
    logging.debug(f"Relative Position: ({x}, {y})")
    
    with get_session() as session:
        try:
            # Try to get existing record
            position = session.query(ScreenPosition).filter_by(screens_id=screens_id).first()
            
            if position:
                # Update existing record
                position.screen_name = screen.name()
                position.pos_x = x
                position.pos_y = y
            else:
                # Create new record
                position = ScreenPosition(
                    screens_id=screens_id,
                    screen_name=screen.name(),
                    pos_x=x,
                    pos_y=y
                )
                session.add(position)
            
            session.commit()
            logging.debug("Position saved successfully")
            
        except Exception as e:
            session.rollback()
            logging.error(f"Failed to save position: {e}")
            raise
