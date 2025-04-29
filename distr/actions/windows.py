from distr.core.utils import load_settings_from_db, save_settings_to_db, load_actions_config
from AppKit import NSWorkspace, NSApplicationActivateIgnoringOtherApps
from distr.core.signals import signal_manager
from distr.core.constants import ORACLE_DIR
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QCursor
from fuzzywuzzy import fuzz
import subprocess
import pyautogui
import logging
import Quartz
import json
import time
import os

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Define the screen edge percentage
SCREEN_EDGE_PERCENTAGE = 15

def open_window(chat_manager, action, data):
    print(f"open_window function called with action: {action} and data: {data}")
    speech = data.get('text', '').lower()
    print(f"Received request to open/focus on: {speech}")
    
    # Load the actions.config.json file
    config = load_actions_config()
    shortcut_names = config.get('shortcut_names', {})
    print(f"Loaded shortcut names: {shortcut_names}")
    
    # Handle "open spotlight" separately
    if "spotlight" in speech:
        print("Executing spotlight command")
        pyautogui.hotkey('command', 'space')
        return

    # Extract app name from speech
    app_name = extract_app_name(speech, shortcut_names)
    
    if not app_name:
        print("No application name found in shortcut names, trying running apps...")
        app_name = find_app_in_running_apps(speech)

    if not app_name:
        print("No application name found in running apps, trying all installed apps...")
        app_name = find_app_in_installed_apps(speech)
    
    if not app_name:
        print("No application name could be extracted")
        return
    
    print(f"Attempting to open or focus on: {app_name}")
    
    workspace = NSWorkspace.sharedWorkspace()
    running_apps = workspace.runningApplications()
    print(f"Number of running applications: {len(running_apps)}")

    # Function to check if an app is running (case-insensitive)
    def is_app_running(name):
        return any(app for app in running_apps if app.localizedName().lower() == name.lower())

    # Try to find the running app with various name formats
    app_variations = [app_name, app_name.title(), app_name.lower(), app_name.upper()]
    target_app = None
    for variation in app_variations:
        print(f"Checking for running app with name: {variation}")
        if is_app_running(variation):
            target_app = next(app for app in running_apps if app.localizedName().lower() == variation.lower())
            print(f"Found running app: {target_app.localizedName()}")
            break
    
    if target_app:
        # Application is already running, bring it to front
        print(f"Activating existing app: {target_app.localizedName()}")
        target_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        print(f"Focused on {target_app.localizedName()}")
        center_mouse_on_app(target_app)
    else:
        # Application is not running, try to open it
        print(f"App not found running. Attempting to launch: {app_name}")
        try:
            workspace.launchApplication_(app_name)
            print(f"Launched {app_name}")
            time.sleep(1)  # Wait for the app to launch
            center_mouse_on_app(app_name)
        except Exception as e:
            print(f"Failed to open {app_name}. Error: {str(e)}")
            # Try alternative methods to open the application
            try:
                print(f"Attempting to open {app_name} using subprocess")
                subprocess.run(["open", "-a", app_name], check=True)
                print(f"Opened {app_name} using alternative method")
                time.sleep(1)  # Wait for the app to launch
                center_mouse_on_app(app_name)
            except subprocess.CalledProcessError as e:
                print(f"Failed to open {app_name} using alternative method. Error: {str(e)}")
    
    print(f"Completed attempt to open or focus on {app_name}")

def extract_app_name(speech, shortcut_names):
    words = speech.split()
    for i in range(len(words)):
        for j in range(i+1, len(words)+1):
            potential_app = " ".join(words[i:j])
            if potential_app in shortcut_names:
                return shortcut_names[potential_app]
            for shortcut, full_name in shortcut_names.items():
                if potential_app in shortcut or shortcut in potential_app:
                    return full_name
    return None

def find_app_in_running_apps(speech):
    workspace = NSWorkspace.sharedWorkspace()
    running_apps = workspace.runningApplications()
    best_match = None
    highest_ratio = 0
    
    for app in running_apps:
        app_name = app.localizedName()
        ratio = fuzz.partial_ratio(speech.lower(), app_name.lower())
        if ratio > highest_ratio:
            highest_ratio = ratio
            best_match = app_name
    
    if highest_ratio > 80:  # You can adjust this threshold
        print(f"Found running app match: {best_match} with confidence {highest_ratio}%")
        return best_match
    return None

def find_app_in_installed_apps(speech):
    applications_path = "/Applications"
    installed_apps = [f.replace('.app', '') for f in os.listdir(applications_path) if f.endswith('.app')]
    
    best_match = None
    highest_ratio = 0
    
    for app in installed_apps:
        ratio = fuzz.partial_ratio(speech.lower(), app.lower())
        if ratio > highest_ratio:
            highest_ratio = ratio
            best_match = app
    
    if highest_ratio > 80:  # You can adjust this threshold
        print(f"Found installed app match: {best_match} with confidence {highest_ratio}%")
        return best_match
    return None

def center_mouse_on_app(app):
    if isinstance(app, str):
        # If app is a string, find the running app with that name
        workspace = NSWorkspace.sharedWorkspace()
        running_apps = workspace.runningApplications()
        target_app = next((a for a in running_apps if a.localizedName().lower() == app.lower()), None)
    else:
        target_app = app

    if target_app:
        # Get the app's windows
        app_windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
        app_window = next((w for w in app_windows if w.get(Quartz.kCGWindowOwnerName, "").lower() == target_app.localizedName().lower()), None)

        if app_window:
            # Get the window bounds
            bounds = app_window.get(Quartz.kCGWindowBounds)
            if bounds:
                # Calculate the center of the window
                center_x = bounds['X'] + bounds['Width'] // 2
                center_y = bounds['Y'] + bounds['Height'] // 2
                
                # Move the mouse to the center of the window
                pyautogui.moveTo(center_x, center_y)
                print(f"Moved mouse to center of {target_app.localizedName()} window")
            else:
                print(f"Could not get window bounds for {target_app.localizedName()}")
        else:
            print(f"Could not find window for {target_app.localizedName()}")
    else:
        print(f"Could not find running app to center mouse on")

def open_file_menu(chat_manager, action, data):
    # Code to open the file menu
    pyautogui.hotkey('command', 'shift', 'f')

def mouse_move(chat_manager, action, data):
    # Get the relative movement values
    x, y = action.get('params', [0, 0])
    
    # Define the number of steps for the animation
    num_steps = 10
    
    # Calculate the step size for each axis
    step_x = x / num_steps
    step_y = y / num_steps
    
    # Animate the mouse movement
    for _ in range(num_steps):
        pyautogui.moveRel(step_x, step_y)
        # time.sleep(0.01)  # Short delay between steps for smooth animation

def mouse_click(chat_manager, action, data):
    # Code to perform a mouse click
    params = action.get('params', ['left'])
    button = params[0]
    if 'double' in params:
        pyautogui.doubleClick(button=button)
    else:
        pyautogui.click(button=button)

def mouse_center(chat_manager, action, data):
    screen = get_current_screen()
    screen_geometry = screen.geometry()
    
    # Calculate the center position of the current screen
    center_x = screen_geometry.left() + screen_geometry.width() // 2
    center_y = screen_geometry.top() + screen_geometry.height() // 2
    
    # Move the mouse to the center of the current screen
    pyautogui.moveTo(center_x, center_y)

def mouse_vertical_middle(chat_manager, action, data):
    screen = get_current_screen()
    screen_geometry = screen.geometry()
    current_x, current_y = pyautogui.position()
    
    # Calculate the vertical middle position of the current screen
    middle_y = screen_geometry.top() + screen_geometry.height() // 2
    
    # Move the mouse to the vertical middle, maintaining current x-position
    pyautogui.moveTo(current_x, middle_y)

def mouse_horizontal_middle(chat_manager, action, data):
    screen = get_current_screen()
    screen_geometry = screen.geometry()
    current_x, current_y = pyautogui.position()
    
    # Calculate the horizontal middle position of the current screen
    middle_x = screen_geometry.left() + screen_geometry.width() // 2
    
    # Move the mouse to the horizontal middle, maintaining current y-position
    pyautogui.moveTo(middle_x, current_y)

def mouse_top(chat_manager, action, data):
    screen = get_current_screen()
    screen_geometry = screen.geometry()
    current_x, current_y = pyautogui.position()
    
    # Calculate the top position based on SCREEN_EDGE_PERCENTAGE
    top_y = screen_geometry.top() + int(screen_geometry.height() * (SCREEN_EDGE_PERCENTAGE / 100))
    
    # Move the mouse to the top of the current screen, maintaining current x-position
    pyautogui.moveTo(current_x, top_y)

def mouse_bottom(chat_manager, action, data):
    screen = get_current_screen()
    screen_geometry = screen.geometry()
    current_x, current_y = pyautogui.position()
    
    # Calculate the bottom position based on SCREEN_EDGE_PERCENTAGE
    bottom_y = screen_geometry.bottom() - int(screen_geometry.height() * (SCREEN_EDGE_PERCENTAGE / 100))
    
    # Move the mouse to the bottom of the current screen, maintaining current x-position
    pyautogui.moveTo(current_x, bottom_y)

def mouse_left(chat_manager, action, data):
    screen = get_current_screen()
    screen_geometry = screen.geometry()
    current_x, current_y = pyautogui.position()
    
    # Calculate the left position based on SCREEN_EDGE_PERCENTAGE
    left_x = screen_geometry.left() + int(screen_geometry.width() * (SCREEN_EDGE_PERCENTAGE / 100))
    
    # Move the mouse to the left of the current screen, maintaining current y-position
    pyautogui.moveTo(left_x, current_y)

def mouse_right(chat_manager, action, data):
    screen = get_current_screen()
    screen_geometry = screen.geometry()
    current_x, current_y = pyautogui.position()
    
    # Calculate the right position based on SCREEN_EDGE_PERCENTAGE
    right_x = screen_geometry.right() - int(screen_geometry.width() * (SCREEN_EDGE_PERCENTAGE / 100))
    
    # Move the mouse to the right of the current screen, maintaining current y-position
    pyautogui.moveTo(right_x, current_y)

def mouse_scroll(chat_manager, action, data):
    # Code to perform a mouse scroll
    amount = action.get('params', [0])[0]
    pyautogui.scroll(amount)

def hide_oracle(chat_manager, action, data):
    signal_manager.hide_oracle.emit()

def show_oracle(chat_manager, action, data):
    signal_manager.show_oracle.emit()

def change_oracle(chat_manager, action, data):
    # Get all available oracle files
    oracle_files = [f for f in os.listdir(ORACLE_DIR) if f.endswith('.gif')]
    oracle_files.sort(key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else float('inf'))
    
    settings = load_settings_from_db()
    current_file = settings.get('selected_oracle', '0.gif')
    
    # Find next oracle
    try:
        current_idx = oracle_files.index(current_file)
        next_idx = (current_idx + 1) % len(oracle_files)
        next_file = oracle_files[next_idx]
        
        # Save to settings
        settings['selected_oracle'] = next_file
        save_settings_to_db(settings)
        
        # Emit signals to update both oracle window and settings window
        signal_manager.change_oracle.emit()
        signal_manager.sync_oracle_selection.emit(next_file)
        logging.debug(f"Changed oracle to: {next_file}")
    except ValueError:
        logging.error(f"Current oracle file {current_file} not found in directory")


def exit_app(chat_manager, action, data):
    print("Exiting application...")
    signal_manager.exit_app.emit()


def get_current_screen():
    app = QApplication.instance()
    screen = app.screenAt(QCursor.pos())
    return screen


def copy_transcription(chat_manager, action, data):
    # this is an action that actually copies text from a proverbial clipboard that resides within the oracle.
    # that text is everything the agent last said
    pass