import json
import os
import time
from datetime import datetime
from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode

def load_action_files():
    action_files = {}
    for filename in os.listdir('./actions'):
        if filename.endswith('.json'):
            action_files[filename] = f'./actions/{filename}'
    return action_files

def choose_action(action_files):
    while True:
        print("Available actions:")
        for i, filename in enumerate(action_files.keys(), 1):
            print(f"{i}. {filename}")
        
        try:
            choice = input("Enter the number of the action you want to execute (or 'q' to quit): ").strip()
            if choice.lower() == 'q':
                return None
            choice = int(choice) - 1
            if 0 <= choice < len(action_files):
                return list(action_files.values())[choice]
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number or 'q' to quit.")

def load_action_data(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)

def get_key(key_string):
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
        pass  # Insert key not available on this platform
    
    try:
        key_mapping['Num_lock'] = Key.num_lock
    except AttributeError:
        pass  # Num lock key not available on this platform
    
    try:
        key_mapping['Scroll_lock'] = Key.scroll_lock
    except AttributeError:
        pass  # Scroll lock key not available on this platform
    
    try:
        key_mapping['Print_screen'] = Key.print_screen
    except AttributeError:
        pass  # Print screen key not available on this platform
    
    try:
        key_mapping['Pause'] = Key.pause
    except AttributeError:
        pass  # Pause key not available on this platform
    
    # If it's a single character, return it as is
    if len(key_string) == 1:
        return key_string
    
    # If the key is not in our mapping, try to create a KeyCode from it
    return key_mapping.get(key_string, KeyCode.from_char(key_string))

def execute_action(action_data):
    mouse_controller = mouse.Controller()
    keyboard_controller = keyboard.Controller()

    start_time = datetime.now()
    last_event_time = start_time
    pressed_keys = set()

    for event in action_data.values():
        current_time = datetime.now()
        time_diff = float(event['time_since_last_event'])
        
        # Wait for the appropriate time before executing the next action
        time.sleep(max(0, time_diff - (current_time - last_event_time).total_seconds()))
        
        if event['type'] == 'mouse':
            details = event['details'].split(', ')
            action = details[0]
            x, y = map(float, details[1].split(','))
            
            if action == 'move':
                mouse_controller.position = (x, y)
            elif action in ['click', 'press']:
                mouse_controller.position = (x, y)
                button = getattr(mouse.Button, details[2])
                mouse_controller.press(button)
            elif action in ['release', 'unclick']:
                mouse_controller.position = (x, y)
                button = getattr(mouse.Button, details[2])
                mouse_controller.release(button)
            elif action == 'double click':
                mouse_controller.position = (x, y)
                button = getattr(mouse.Button, details[2])
                mouse_controller.click(button, 2)
            elif action == 'drag':
                mouse_controller.position = (x, y)
        
        elif event['type'] == 'keyboard':
            if event['details'].startswith('Press'):
                key = event['details'].split()[-1]
                key = get_key(key)
                keyboard_controller.press(key)
                pressed_keys.add(key)
            elif event['details'].startswith('Release'):
                key = event['details'].split()[-1]
                key = get_key(key)
                keyboard_controller.release(key)
                pressed_keys.discard(key)
            else:
                # Type each character individually
                for char in event['details']:
                    if char.isupper() or char in '!@#$%^&*()_+{}|:"<>?':
                        with keyboard_controller.pressed(Key.shift):
                            keyboard_controller.press(char.lower())
                            keyboard_controller.release(char.lower())
                    else:
                        keyboard_controller.press(char)
                        keyboard_controller.release(char)
                    time.sleep(0.01)  # Small delay between keypresses
        
        last_event_time = datetime.now()

    # Release any keys that are still pressed
    for key in pressed_keys:
        keyboard_controller.release(key)

    print("Action execution completed.")

def main():
    action_files = load_action_files()
    if not action_files:
        print("No action files found in the ./actions/ folder.")
        return

    chosen_file = choose_action(action_files)
    if chosen_file is None:
        print("Exiting the program.")
        return

    action_data = load_action_data(chosen_file)
    
    input("Press Enter to start executing the chosen action...")
    execute_action(action_data)

if __name__ == "__main__":
    main()