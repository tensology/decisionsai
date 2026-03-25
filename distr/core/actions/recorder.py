"""
Action Recorder Service

Records keyboard and mouse events for action playback.
"""

import json
import re
import logging
from datetime import datetime
from pathlib import Path
from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse
from distr.core.paths import RECORDINGS_DIR

logger = logging.getLogger(__name__)


def slugify(text):
    """Convert text to a URL-friendly slug"""
    # Convert to lowercase
    text = text.lower()
    # Replace spaces and special characters with hyphens
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    # Remove leading/trailing hyphens
    text = text.strip('-')
    return text


class ActionRecorder:
    """Records keyboard and mouse events for action playback"""
    
    def __init__(self, action_id=None, action_title=None):
        """
        Initialize the action recorder.
        
        Args:
            action_id: ID of the action being recorded (optional)
            action_title: Title of the action (used for filename)
        """
        self.action_id = action_id
        self.action_title = action_title
        self.log = {}
        self.event_counter = 0
        self.start_dt = None
        self.keyboard_listener = None
        self.mouse_listener = None
        self.is_recording = False
        self.shift_pressed = False
        self.ctrl_pressed = False
        self.cmd_pressed = False
        self.last_event_time = None
        self.pressed_keys = set()
        self.mouse_button_held = None
        self.recording_filename = None
        
        # Ensure recordings directory exists
        Path(RECORDINGS_DIR).mkdir(parents=True, exist_ok=True)
    
    def add_event(self, event_type, details):
        """Add an event to the log"""
        self.event_counter += 1
        current_time = datetime.now()
        
        if self.last_event_time:
            time_diff = (current_time - self.last_event_time).total_seconds()
        else:
            time_diff = 0
        
        self.log[f"{self.event_counter:02d}"] = {
            "type": event_type,
            "details": details,
            "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "time_since_last_event": f"{time_diff:.3f}"
        }
        self.last_event_time = current_time
    
    def get_key_name(self, key):
        """Get a readable name for a key"""
        try:
            return key.char
        except AttributeError:
            key_str = str(key).replace("Key.", "")
            # Handle special keys
            if key_str == "cmd":
                return "command"
            elif key_str == "cmd_l":
                return "command"
            elif key_str == "cmd_r":
                return "command"
            return key_str.lower()
    
    def on_keyboard_press(self, key):
        """Handle keyboard key press"""
        if not self.is_recording:
            return
        
        if key in self.pressed_keys:
            return  # Key is already pressed, ignore repeat events
        
        self.pressed_keys.add(key)
        
        # Track modifier keys
        if key == pynput_keyboard.Key.shift or key == pynput_keyboard.Key.shift_l or key == pynput_keyboard.Key.shift_r:
            self.shift_pressed = True
        elif key == pynput_keyboard.Key.ctrl or key == pynput_keyboard.Key.ctrl_l or key == pynput_keyboard.Key.ctrl_r:
            self.ctrl_pressed = True
        elif key == pynput_keyboard.Key.cmd or key == pynput_keyboard.Key.cmd_l or key == pynput_keyboard.Key.cmd_r:
            self.cmd_pressed = True
        
        key_name = self.get_key_name(key)
        self.add_event("keyboard", f"Press {key_name}")
    
    def on_keyboard_release(self, key):
        """Handle keyboard key release"""
        if not self.is_recording:
            return
        
        self.pressed_keys.discard(key)
        
        # Track modifier keys
        if key == pynput_keyboard.Key.shift or key == pynput_keyboard.Key.shift_l or key == pynput_keyboard.Key.shift_r:
            self.shift_pressed = False
        elif key == pynput_keyboard.Key.ctrl or key == pynput_keyboard.Key.ctrl_l or key == pynput_keyboard.Key.ctrl_r:
            self.ctrl_pressed = False
        elif key == pynput_keyboard.Key.cmd or key == pynput_keyboard.Key.cmd_l or key == pynput_keyboard.Key.cmd_r:
            self.cmd_pressed = False
        
        key_name = self.get_key_name(key)
        self.add_event("keyboard", f"Release {key_name}")
    
    def on_mouse_click(self, x, y, button, pressed):
        """Handle mouse click events"""
        if not self.is_recording:
            return
        
        button_name = str(button).split(".")[-1].lower()
        if pressed:
            self.mouse_button_held = button
            # Check if this is a double click (two clicks in quick succession)
            last_event = self.log.get(f"{self.event_counter:02d}", {})
            if last_event.get("type") == "mouse" and "click" in last_event.get("details", ""):
                # Check time difference
                if self.last_event_time:
                    time_diff = (datetime.now() - self.last_event_time).total_seconds()
                    if time_diff < 0.3:  # Double click threshold
                        self.add_event("mouse", f"double_click, {x},{y}, {button_name}")
                        return
            self.add_event("mouse", f"click, {x},{y}, {button_name}")
        else:
            self.mouse_button_held = None
            self.add_event("mouse", f"release, {x},{y}, {button_name}")
    
    def on_mouse_move(self, x, y):
        """Handle mouse movement events"""
        if not self.is_recording:
            return
        
        if self.mouse_button_held:
            button_name = str(self.mouse_button_held).split(".")[-1].lower()
            self.add_event("mouse", f"drag, {x},{y}, {button_name}")
        else:
            self.add_event("mouse", f"move, {x},{y}")
    
    def start_recording(self, action_id=None, action_title=None):
        """
        Start recording keyboard and mouse events.
        
        Args:
            action_id: ID of the action being recorded (optional, uses existing if not provided)
            action_title: Title of the action (optional, uses existing if not provided)
        """
        if self.is_recording:
            logger.warning("Recording already in progress")
            return
        
        # Only update if provided (allows calling without parameters if already set)
        if action_id is not None:
            self.action_id = action_id
        if action_title is not None:
            self.action_title = action_title
        
        # Validate we have required info
        if not self.action_title:
            raise ValueError("action_title is required to start recording")
        
        # Generate filename from title - include action_id to ensure uniqueness
        # This prevents overwriting files when multiple actions have the same title
        if self.action_title and self.action_id:
            slug = slugify(self.action_title)
            self.recording_filename = f"{slug}-{self.action_id}.json"
        elif self.action_title:
            slug = slugify(self.action_title)
            self.recording_filename = f"{slug}.json"
        else:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            if self.action_id:
                self.recording_filename = f"action-{self.action_id}-{timestamp}.json"
            else:
                self.recording_filename = f"action-{timestamp}.json"
        
        # Reset log
        self.log = {}
        self.event_counter = 0
        self.is_recording = True
        self.start_dt = datetime.now()
        self.last_event_time = self.start_dt
        
        # Start listeners - simplified to match working playground version
        try:
            # Record initial mouse position first (test access)
            try:
                initial_x, initial_y = pynput_mouse.Controller().position
                self.add_event("mouse", f"initial_position, {initial_x},{initial_y}")
            except Exception as e:
                logger.error(f"Error getting initial mouse position: {e}")
                raise Exception(f"Cannot access mouse input. Please check system permissions.")
            
            # Create listeners (simplified, matching playground)
            self.keyboard_listener = pynput_keyboard.Listener(
                on_press=self.on_keyboard_press,
                on_release=self.on_keyboard_release
            )
            self.mouse_listener = pynput_mouse.Listener(
                on_move=self.on_mouse_move,
                on_click=self.on_mouse_click
            )
            
            # Start listeners
            self.keyboard_listener.start()
            self.mouse_listener.start()
            
            logger.info(f"Started recording action: {self.action_title} (ID: {self.action_id})")
        except Exception as e:
            logger.error(f"Error starting recording listeners: {e}", exc_info=True)
            self.is_recording = False
            # Clean up any partially started listeners
            if self.keyboard_listener:
                try:
                    self.keyboard_listener.stop()
                except Exception:
                    pass
                self.keyboard_listener = None
            if self.mouse_listener:
                try:
                    self.mouse_listener.stop()
                except Exception:
                    pass
                self.mouse_listener = None
            raise
    
    def stop_recording(self):
        """Stop recording and save to file"""
        if not self.is_recording:
            logger.warning("No recording in progress")
            return None
        
        self.is_recording = False
        
        # Stop listeners
        try:
            if self.keyboard_listener:
                self.keyboard_listener.stop()
            if self.mouse_listener:
                self.mouse_listener.stop()
        except Exception as e:
            logger.error(f"Error stopping listeners: {e}")
        
        # Save to file
        if self.recording_filename:
            filepath = Path(RECORDINGS_DIR) / self.recording_filename
            try:
                with open(filepath, 'w') as f:
                    json.dump(self.log, f, indent=2)
                logger.info(f"Recording saved to {filepath}")
                return self.recording_filename
            except Exception as e:
                logger.error(f"Error saving recording: {e}")
                return None
        
        return None
    
    def is_active(self):
        """Check if recording is currently active"""
        return self.is_recording


