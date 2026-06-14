"""
Action Player Service

Plays back recorded keyboard and mouse events from JSON files.
"""

import json
import time
import logging
from datetime import datetime
from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode

logger = logging.getLogger(__name__)


def get_key(key_string):
    """Convert key string to pynput Key or KeyCode"""
    key_mapping = {
        'Shift': Key.shift,
        'Shift_l': Key.shift_l,
        'Shift_r': Key.shift_r,
        'Ctrl': Key.ctrl,
        'Ctrl_l': Key.ctrl_l,
        'Ctrl_r': Key.ctrl_r,
        'Alt': Key.alt,
        'Alt_l': Key.alt_l,
        'Alt_r': Key.alt_r,
        'Cmd': Key.cmd,
        'Command': Key.cmd,
        'Enter': Key.enter,
        'Space': Key.space,
        'Backspace': Key.backspace,
        'Tab': Key.tab,
        'Esc': Key.esc,
        'Escape': Key.esc,
        'Up': Key.up,
        'Down': Key.down,
        'Left': Key.left,
        'Right': Key.right,
        'Delete': Key.delete,
        'Home': Key.home,
        'End': Key.end,
        'Page_up': Key.page_up,
        'Page_down': Key.page_down,
        'Caps_lock': Key.caps_lock,
        'F1': Key.f1,
        'F2': Key.f2,
        'F3': Key.f3,
        'F4': Key.f4,
        'F5': Key.f5,
        'F6': Key.f6,
        'F7': Key.f7,
        'F8': Key.f8,
        'F9': Key.f9,
        'F10': Key.f10,
        'F11': Key.f11,
        'F12': Key.f12,
        'Option': Key.alt,
        'Media_play_pause': Key.media_play_pause,
        'Media_volume_mute': Key.media_volume_mute,
        'Media_volume_down': Key.media_volume_down,
        'Media_volume_up': Key.media_volume_up,
        'Media_previous': Key.media_previous,
        'Media_next': Key.media_next,
    }
    
    # Handle platform-specific keys
    try:
        key_mapping['Insert'] = Key.insert
    except AttributeError:
        pass
    
    try:
        key_mapping['Num_lock'] = Key.num_lock
    except AttributeError:
        pass
    
    try:
        key_mapping['Scroll_lock'] = Key.scroll_lock
    except AttributeError:
        pass
    
    try:
        key_mapping['Print_screen'] = Key.print_screen
    except AttributeError:
        pass
    
    try:
        key_mapping['Pause'] = Key.pause
    except AttributeError:
        pass
    
    # If it's a single character, return it as is
    if len(key_string) == 1:
        return key_string
    
    # Normalize key string (handle lowercase/uppercase variations)
    key_string_lower = key_string.lower()
    for k, v in key_mapping.items():
        if k.lower() == key_string_lower:
            return v
    
    # If the key is not in our mapping, try to create a KeyCode from it
    try:
        return KeyCode.from_char(key_string)
    except (ValueError, TypeError):
        logger.warning(f"Could not map key: {key_string}")
        return None


class ActionPlayer:
    """Plays back recorded keyboard and mouse events"""
    
    def __init__(self):
        # Don't create controllers here - create them in the thread/process where they're used
        # This avoids crashes when controllers are created in main thread but used in background thread
        self.mouse_controller = None
        self.keyboard_controller = None
        self.is_playing = False
    
    def _ensure_controllers(self):
        """Ensure controllers are created (create them in the current thread context)"""
        if self.mouse_controller is None:
            self.mouse_controller = mouse.Controller()
        if self.keyboard_controller is None:
            self.keyboard_controller = keyboard.Controller()
    
    def load_action_data(self, file_path):
        """Load action data from JSON file"""
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading action file {file_path}: {e}")
            raise
    
    def execute_action(self, action_data, play_sticky=False):
        """
        Execute action events with proper timing.
        
        Args:
            action_data: Dictionary of events from JSON file
            play_sticky: If True, ignore timing and mouse movement (only clicks and keyboard)
        """
        if self.is_playing:
            logger.warning("Action playback already in progress")
            return
        
        # Create controllers in the current thread context (important for avoiding crashes)
        self._ensure_controllers()
        
        self.is_playing = True
        start_time = datetime.now()
        last_event_time = start_time
        pressed_keys = set()
        
        try:
            # Sort events by key (they're numbered "01", "02", etc.)
            sorted_events = sorted(action_data.items(), key=lambda x: int(x[0]))
            
            for event_key, event in sorted_events:
                if not self.is_playing:
                    break
                
                current_time = datetime.now()
                
                # Handle timing (unless play_sticky is True)
                if not play_sticky:
                    time_diff = float(event.get('time_since_last_event', '0'))
                    # Wait for the appropriate time before executing the next action
                    elapsed = (current_time - last_event_time).total_seconds()
                    sleep_time = max(0, time_diff - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                
                event_type = event.get('type')
                details = event.get('details', '')
                
                if event_type == 'mouse':
                    self._handle_mouse_event(details, play_sticky)
                elif event_type == 'keyboard':
                    self._handle_keyboard_event(details, pressed_keys)
                
                last_event_time = datetime.now()
            
            # Release any keys that are still pressed
            for key in pressed_keys:
                try:
                    self.keyboard_controller.release(key)
                except Exception:
                    pass
                pressed_keys.discard(key)
            
            logger.info("Action execution completed")
        except Exception as e:
            logger.error(f"Error executing action: {e}", exc_info=True)
            raise
        finally:
            self.is_playing = False
    
    def _handle_mouse_event(self, details, play_sticky):
        """Handle mouse event"""
        try:
            # Ensure controllers are created
            self._ensure_controllers()
            
            if not self.mouse_controller:
                logger.error("Mouse controller not available")
                return
            
            parts = details.split(', ')
            if len(parts) < 2:
                return
            
            action = parts[0].strip()
            
            # Skip mouse movement if play_sticky is True
            if play_sticky and action in ['move', 'initial_position']:
                return
            
            # Parse coordinates
            coords = parts[1].split(',')
            if len(coords) < 2:
                return
            
            x, y = float(coords[0]), float(coords[1])
            
            if action == 'move' or action == 'initial_position':
                self.mouse_controller.position = (int(x), int(y))
            elif action == 'click':
                self.mouse_controller.position = (int(x), int(y))
                if len(parts) >= 3:
                    button_name = parts[2].strip()
                    try:
                        button = getattr(mouse.Button, button_name)
                        self.mouse_controller.press(button)
                    except AttributeError:
                        logger.warning(f"Unknown mouse button: {button_name}")
            elif action == 'release':
                self.mouse_controller.position = (int(x), int(y))
                if len(parts) >= 3:
                    button_name = parts[2].strip()
                    try:
                        button = getattr(mouse.Button, button_name)
                        self.mouse_controller.release(button)
                    except AttributeError:
                        logger.warning(f"Unknown mouse button: {button_name}")
            elif action == 'double_click' or action == 'double click':
                self.mouse_controller.position = (int(x), int(y))
                if len(parts) >= 3:
                    button_name = parts[2].strip()
                    try:
                        button = getattr(mouse.Button, button_name)
                        self.mouse_controller.click(button, 2)
                    except AttributeError:
                        logger.warning(f"Unknown mouse button: {button_name}")
            elif action == 'drag':
                self.mouse_controller.position = (int(x), int(y))
        except Exception as e:
            logger.error(f"Error handling mouse event '{details}': {e}", exc_info=True)
    
    def _handle_keyboard_event(self, details, pressed_keys):
        """Handle keyboard event"""
        try:
            # Ensure controllers are created
            self._ensure_controllers()
            
            if not self.keyboard_controller:
                logger.error("Keyboard controller not available")
                return
            
            if details.startswith('Press '):
                key_name = details.replace('Press ', '').strip()
                key = get_key(key_name)
                if key:
                    self.keyboard_controller.press(key)
                    pressed_keys.add(key)
            elif details.startswith('Release '):
                key_name = details.replace('Release ', '').strip()
                key = get_key(key_name)
                if key:
                    self.keyboard_controller.release(key)
                    pressed_keys.discard(key)
            else:
                # Type each character individually (for raw character input)
                for char in details:
                    if char.isupper() or char in '!@#$%^&*()_+{}|:"<>?':
                        with self.keyboard_controller.pressed(Key.shift):
                            self.keyboard_controller.press(char.lower())
                            self.keyboard_controller.release(char.lower())
                    else:
                        self.keyboard_controller.press(char)
                        self.keyboard_controller.release(char)
                    time.sleep(0.01)  # Small delay between keypresses
        except Exception as e:
            logger.error(f"Error handling keyboard event '{details}': {e}", exc_info=True)
    
    def stop(self):
        """Stop playback"""
        self.is_playing = False
    
    def is_active(self):
        """Check if playback is currently active"""
        return self.is_playing


def play_action_file(file_path, play_sticky=False):
    """
    Convenience function to play an action file.
    
    Args:
        file_path: Path to the JSON action file
        play_sticky: If True, ignore timing and mouse movement
    """
    player = ActionPlayer()
    action_data = player.load_action_data(file_path)
    player.execute_action(action_data, play_sticky=play_sticky)

