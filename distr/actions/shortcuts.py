import pyautogui

def keypress(chat_manager, action, params):
    keys = action.get('params', [])
    print(keys)
    if isinstance(keys, list):
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(keys)

    