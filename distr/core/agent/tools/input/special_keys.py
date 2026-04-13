"""
Special Keys Tools for LangChain.

These tools handle special key presses like Space, Enter, Tab, Escape, arrows,
function keys, modifiers, and any other keyboard key.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)

# Comprehensive map of key aliases to pyautogui key names.
# All lookups are case-insensitive (done via .lower()).
_KEY_MAP = {
    # --- Modifiers ---
    "alt": "alt",
    "altleft": "altleft",
    "altright": "altright",
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctrlleft": "ctrlleft",
    "ctrlright": "ctrlright",
    "shift": "shift",
    "shiftleft": "shiftleft",
    "shiftright": "shiftright",
    "command": "command",
    "cmd": "command",
    "win": "win",
    "winleft": "winleft",
    "winright": "winright",
    "option": "option",
    "optionleft": "optionleft",
    "optionright": "optionright",
    "fn": "fn",
    # --- Whitespace / common ---
    "space": "space",
    "spacebar": "space",
    "enter": "enter",
    "return": "return",
    "tab": "tab",
    "backspace": "backspace",
    "delete": "delete",
    "del": "del",
    # --- Navigation ---
    "escape": "escape",
    "esc": "escape",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pgup": "pageup",
    "pagedown": "pagedown",
    "pgdn": "pagedown",
    "insert": "insert",
    # --- Function keys ---
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
    "f13": "f13",
    "f14": "f14",
    "f15": "f15",
    "f16": "f16",
    "f17": "f17",
    "f18": "f18",
    "f19": "f19",
    "f20": "f20",
    "f21": "f21",
    "f22": "f22",
    "f23": "f23",
    "f24": "f24",
    # --- Lock keys ---
    "capslock": "capslock",
    "numlock": "numlock",
    "scrolllock": "scrolllock",
    # --- Numpad ---
    "num0": "num0",
    "num1": "num1",
    "num2": "num2",
    "num3": "num3",
    "num4": "num4",
    "num5": "num5",
    "num6": "num6",
    "num7": "num7",
    "num8": "num8",
    "num9": "num9",
    "add": "add",
    "subtract": "subtract",
    "multiply": "multiply",
    "divide": "divide",
    "decimal": "decimal",
    "separator": "separator",
    # --- Media keys ---
    "playpause": "playpause",
    "nexttrack": "nexttrack",
    "prevtrack": "prevtrack",
    "stop": "stop",
    "volumeup": "volumeup",
    "volumedown": "volumedown",
    "volumemute": "volumemute",
    # --- Browser keys ---
    "browserback": "browserback",
    "browserforward": "browserforward",
    "browserrefresh": "browserrefresh",
    "browserstop": "browserstop",
    "browsersearch": "browsersearch",
    "browserfavorites": "browserfavorites",
    "browserhome": "browserhome",
    # --- Misc ---
    "printscreen": "printscreen",
    "prntscrn": "printscreen",
    "prtsc": "printscreen",
    "prtscr": "printscreen",
    "pause": "pause",
    "clear": "clear",
    "select": "select",
    "execute": "execute",
    "help": "help",
    "apps": "apps",
    "sleep": "sleep",
    "accept": "accept",
    "convert": "convert",
    "nonconvert": "nonconvert",
    "modechange": "modechange",
    "final": "final",
    "hangul": "hangul",
    "hanguel": "hangul",
    "hanja": "hanja",
    "junja": "junja",
    "kana": "kana",
    "kanji": "kanji",
    "yen": "yen",
    "launchapp1": "launchapp1",
    "launchapp2": "launchapp2",
    "launchmail": "launchmail",
    "launchmediaselect": "launchmediaselect",
}

# Letters and digits are handled dynamically (single-char pass-through),
# but add explicit aliases so they show up in docs / validation.
for _c in "abcdefghijklmnopqrstuvwxyz":
    _KEY_MAP[_c] = _c
for _d in "0123456789":
    _KEY_MAP[_d] = _d


def resolve_key(name: str) -> Optional[str]:
    """Resolve a human-friendly key name to a pyautogui key string.

    Returns None if the key is unrecognised.
    """
    if not name:
        return None
    lowered = name.strip().lower()
    mapped = _KEY_MAP.get(lowered)
    if mapped:
        return mapped
    # Single printable character — pass through directly
    if len(name) == 1:
        return name
    return None


class SpecialKeyTool(BaseTool):
    """Tool for pressing special keys."""
    
    name: str = "special_key"
    description: str = """EXECUTE special key presses: Enter, Space, Tab, Escape, arrows, F-keys, letters, numbers, modifiers, and more.
    
    CRITICAL: When user asks to press any key - CALL THIS TOOL IMMEDIATELY.
    DO NOT explain. DO NOT describe. JUST CALL IT.
    
    Supports ALL keyboard keys including:
    - Modifiers: shift, ctrl/control, alt, command/cmd, option, fn
    - Navigation: up, down, left, right (or ArrowUp, ArrowDown, etc.), home, end, pageup, pagedown
    - Function keys: f1-f24
    - Editing: enter, space, tab, backspace, delete, insert, escape
    - Letters: a-z
    - Numbers: 0-9
    - Lock keys: capslock, numlock, scrolllock
    - Media: playpause, volumeup, volumedown, volumemute, nexttrack, prevtrack
    - And many more.
    
    CALL THE TOOL - never explain it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        
    def get_triggers(self) -> list[str]:
        """Get triggers for special keys."""
        return [
            "press enter", "hit enter", "enter",
            "press space", "spacebar", "space",
            "press tab",
            "press escape", "press esc", "escape", "esc", "cancel",
            "press alt", "alt",
            "press control", "press ctrl", "control", "ctrl",
            "press command", "press cmd", "command", "cmd",
            "press up", "press down", "press left", "press right",
            "arrow up", "arrow down", "arrow left", "arrow right",
            "press f1", "press f2", "press f3", "press f4", "press f5",
            "press f6", "press f7", "press f8", "press f9", "press f10",
            "press f11", "press f12",
            "press home", "press end", "press pageup", "press pagedown",
            "press delete", "press backspace", "press insert",
            "press shift",
        ]
    
    def _run(self, key: str = "", text: str = "", **kwargs) -> str:
        """Execute special key press."""
        try:
            from distr.core.actions.keyboard import press_keys
            
            # Try to resolve key directly first
            if key:
                resolved = resolve_key(key)
                if resolved:
                    logger.info(f"Executing key press: {resolved}")
                    press_keys([resolved])
                    return f"Pressed {resolved} key"
            
            # Extract key from text if key param was empty or unresolvable
            if not key and text:
                extracted = self._extract_key_from_text(text)
                if extracted:
                    resolved = resolve_key(extracted)
                    if resolved:
                        logger.info(f"Executing key press (from text): {resolved}")
                        press_keys([resolved])
                        return f"Pressed {resolved} key"
            
            # If we had a key param but it didn't resolve, try text extraction
            if key and not resolve_key(key) and text:
                extracted = self._extract_key_from_text(text)
                if extracted:
                    resolved = resolve_key(extracted)
                    if resolved:
                        logger.info(f"Executing key press (fallback from text): {resolved}")
                        press_keys([resolved])
                        return f"Pressed {resolved} key"
            
            # Nothing worked
            if key:
                return (
                    f"Error: Unknown key '{key}'. "
                    f"Use standard key names like: enter, space, tab, escape, up, down, left, right, "
                    f"f1-f24, a-z, 0-9, shift, ctrl, alt, command, home, end, pageup, pagedown, "
                    f"delete, backspace, insert, capslock, numlock, printscreen, etc."
                )
            return (
                "Error: No key specified. "
                "Provide a key name like: enter, space, tab, escape, up, down, left, right, "
                "f1-f24, a-z, 0-9, shift, ctrl, alt, command, etc."
            )
            
        except Exception as e:
            logger.error(f"Error in SpecialKeyTool: {e}", exc_info=True)
            return f"Error pressing special key: {str(e)}"
    
    @staticmethod
    def _extract_key_from_text(text: str) -> Optional[str]:
        """Try to pull a key name out of free-form text like 'press Enter'."""
        text_lower = text.lower().strip()
        
        # "press <key>" pattern — grab the word after "press"
        if "press " in text_lower:
            after_press = text_lower.split("press ", 1)[1].strip().split()[0]
            if resolve_key(after_press):
                return after_press
        
        # "hit <key>" pattern
        if "hit " in text_lower:
            after_hit = text_lower.split("hit ", 1)[1].strip().split()[0]
            if resolve_key(after_hit):
                return after_hit
        
        # Bare key name (single word)
        words = text_lower.split()
        if len(words) == 1 and resolve_key(words[0]):
            return words[0]
        
        return None
    
    async def _arun(self, key: str = "", text: str = "", **kwargs) -> str:
        return self._run(key=key, text=text)
