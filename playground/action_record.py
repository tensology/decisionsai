import json
import hashlib
from datetime import datetime
from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse

class ActionLogger:
    def __init__(self):
        self.log = {}
        self.event_counter = 0
        self.start_dt = None
        self.keyboard_listener = None
        self.mouse_listener = None
        self.is_recording = False
        self.shift_pressed = False
        self.ctrl_pressed = False
        self.last_event_time = None
        self.pressed_keys = set()
        self.mouse_button_held = None

    def add_event(self, event_type, details):
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

    def keyboard_callback(self, key):
        try:
            char = key.char
            self.add_event("keyboard", char)
        except AttributeError:
            key_name = str(key).replace("Key.", "")
            action = "Press" if key in [pynput_keyboard.Key.shift, pynput_keyboard.Key.ctrl] else "Hold Down"
            self.add_event("keyboard", f"{action} {key_name.capitalize()}")

    def mouse_callback(self, x, y, button, pressed):
        button_name = str(button).split(".")[-1]
        if pressed:
            self.mouse_button_held = button
            action = "click"
            if self.log.get(f"{self.event_counter:02d}", {}).get("details", "").startswith("click"):
                action = "double click"
            self.add_event("mouse", f"{action}, {x},{y}, {button_name}")
        else:
            self.mouse_button_held = None
            self.add_event("mouse", f"release, {x},{y}, {button_name}")

    def mouse_move_callback(self, x, y):
        if self.mouse_button_held:
            button_name = str(self.mouse_button_held).split(".")[-1]
            self.add_event("mouse", f"drag, {x},{y}, {button_name}")
        else:
            self.add_event("mouse", f"move, {x},{y}")

    def on_press(self, key):
        if key in self.pressed_keys:
            return  # Key is already pressed, ignore repeat events

        self.pressed_keys.add(key)
        if key == pynput_keyboard.Key.shift:
            self.shift_pressed = True
        elif key == pynput_keyboard.Key.ctrl:
            self.ctrl_pressed = True
        elif key == pynput_keyboard.Key.space and self.shift_pressed and self.ctrl_pressed:
            self.stop_recording()
            return False

        key_name = self.get_key_name(key)
        self.add_event("keyboard", f"Press {key_name}")

    def on_release(self, key):
        self.pressed_keys.discard(key)
        if key == pynput_keyboard.Key.shift:
            self.shift_pressed = False
        elif key == pynput_keyboard.Key.ctrl:
            self.ctrl_pressed = False

        key_name = self.get_key_name(key)
        self.add_event("keyboard", f"Release {key_name}")

    def get_key_name(self, key):
        try:
            return key.char
        except AttributeError:
            return str(key).replace("Key.", "").capitalize()

    def start_recording(self):
        self.is_recording = True
        self.start_dt = datetime.now()
        self.last_event_time = self.start_dt

        # Record initial mouse position
        initial_x, initial_y = pynput_mouse.Controller().position
        self.add_event("mouse", f"initial_position, {initial_x},{initial_y}")

        self.keyboard_listener = pynput_keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release
        )
        self.mouse_listener = pynput_mouse.Listener(
            on_move=self.mouse_move_callback,
            on_click=self.mouse_callback
        )
        self.keyboard_listener.start()
        self.mouse_listener.start()
        print(f"{datetime.now()} - Started recording")
        print("Press Shift+Control+Spacebar to stop recording")

    def stop_recording(self):
        self.is_recording = False
        self.keyboard_listener.stop()
        self.mouse_listener.stop()
        print("Stopping action logger...")
        self.save_to_json()

    def save_to_json(self):
        timestamp = self.start_dt.strftime("%Y%m%d%H%M%S")
        md5_hash = hashlib.md5(timestamp.encode()).hexdigest()[:8]
        filename = f"actions/action-{md5_hash}.json"
        
        with open(filename, 'w') as f:
            json.dump(self.log, f, indent=2)
        
        print(f"Actions saved to {filename}")

    def start(self):
        input("Are you ready to begin recording? Press Enter to start: ")
        self.start_recording()
        
        try:
            self.keyboard_listener.join()
            self.mouse_listener.join()
        except KeyboardInterrupt:
            self.stop_recording()

if __name__ == "__main__":
    action_logger = ActionLogger()
    action_logger.start()