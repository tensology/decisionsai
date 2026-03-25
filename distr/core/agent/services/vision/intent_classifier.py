"""
Vision Intent Classifier

Classifies user prompts for screenshot/vision tasks to route to the appropriate
backend: fast text-based locate (pytesseract), UI grounding, or full vision LLM.

20 Use Cases:
 1. DESCRIBE_SCREEN    — "What do you see?", "Describe my screen"
 2. READ_TEXT          — "Read the text on screen", "What does it say?"
 3. LOCATE             — "Where is X?", "Find the button"
 4. CLICK_ELEMENT      — "Click the Submit button"
 5. HOVER_ELEMENT      — "Hover over the menu"
 6. DOUBLE_CLICK       — "Double-click the file icon"
 7. RIGHT_CLICK        — "Right-click the desktop"
 8. SCROLL_TO          — "Scroll down to the footer", "Scroll to Settings"
 9. DRAG_DROP          — "Drag the file to the trash"
10. FIND_TEXT          — "Find the word 'error' on screen"
11. READ_ERROR         — "What error is showing?", "Read the error message"
12. IDENTIFY_APP       — "What app is open?", "Which window is active?"
13. LOCATE_ICON        — "Find the Wi-Fi icon", "Where is the battery icon?"
14. INTERACT_FORM      — "Fill in the search box with 'hello'"
15. NAVIGATE_MENU      — "Open the File menu", "Go to Settings > General"
16. MULTI_SCREEN_NAV   — "Move to screen 2", "What's on my other monitor?"
17. CHECK_STATE        — "Is the toggle on or off?", "Is it loading?"
18. READ_NOTIFICATION  — "What notification just appeared?", "Read the alert"
19. COMPARE_SCREEN     — "What changed?", "Compare before and after"
20. COUNT_ELEMENTS     — "How many tabs are open?", "Count the icons"

Plus:
 - BROWSER_TASK  — Multi-element browser interaction
 - MULTI_STEP    — Complex workflow → Step Runner
 - UNKNOWN       — Fallback to vision LLM
"""

import re
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class VisionIntent(str, Enum):
    """Intent for vision/screenshot requests."""
    # Informational
    DESCRIBE_SCREEN = "describe_screen"       # 1. General screen description
    READ_TEXT = "read_text"                    # 2. Read text content on screen
    READ_ERROR = "read_error"                 # 11. Read error/warning messages
    READ_NOTIFICATION = "read_notification"   # 18. Read notifications/alerts
    IDENTIFY_APP = "identify_app"             # 12. Identify open app/window
    CHECK_STATE = "check_state"               # 17. Check UI state (toggle, loading, etc.)
    COMPARE_SCREEN = "compare_screen"         # 19. Compare / what changed
    COUNT_ELEMENTS = "count_elements"         # 20. Count UI elements

    # Locate / Find
    LOCATE = "locate"                         # 3. Where is X?
    FIND_TEXT = "find_text"                   # 10. Find specific text on screen
    LOCATE_ICON = "locate_icon"              # 13. Find icon/system tray element

    # Mouse actions
    CLICK_ELEMENT = "click_element"           # 4. Click a specific element
    HOVER_ELEMENT = "hover_element"           # 5. Hover over element
    DOUBLE_CLICK = "double_click"             # 6. Double-click element
    RIGHT_CLICK = "right_click"              # 7. Right-click element
    SCROLL_TO = "scroll_to"                  # 8. Scroll to element/area
    DRAG_DROP = "drag_drop"                  # 9. Drag and drop

    # Complex interactions
    INTERACT_FORM = "interact_form"           # 14. Fill form fields
    NAVIGATE_MENU = "navigate_menu"           # 15. Navigate menus
    MULTI_SCREEN_NAV = "multi_screen_nav"    # 16. Multi-screen navigation

    # Compound / fallback
    BROWSER_TASK = "browser_task"
    MULTI_STEP = "multi_step"
    UNKNOWN = "unknown"

    # Legacy alias kept for backward compat
    OPINION = "describe_screen"
    MOUSE_ACTION = "click_element"


# ── Pattern groups ──

_DESCRIBE_SCREEN_PATTERNS = [
    r"\b(what\s+do\s+you\s+see|what\'?s?\s+on\s+(the\s+|my\s+)?screen)\b",
    r"\b(describe|summarize)\s+(what\'?s?\s+on\s+)?(the\s+|my\s+)?screen\b",
    r"\b(can\s+you\s+see\s+my\s+screen|look\s+at\s+my\s+screen)\b",
    r"\b(what\s+do\s+you\s+think|what\'?s?\s+your\s+opinion|your\s+thoughts?)\b",
    r"\b(is\s+this\s+good|look\s+good|look\s+ok|does\s+this\s+look)\b",
    r"\b(analyze|evaluate|assess|review)\s+(this|the\s+screen)\b",
    r"\b(how\s+does\s+it\s+look|how\s+do\s+you\s+like)\b",
    r"\b(tell\s+me\s+what\s+you\s+see|what\s+is\s+on\s+the\s+screen)\b",
    r"\b(what\s+am\s+i\s+looking\s+at)\b",
]

_READ_TEXT_PATTERNS = [
    r"\b(read|read\s+out)\s+(the\s+)?(text|words?|content)\s+(on|from)\s+(the\s+|my\s+)?screen\b",
    r"\b(what\s+does\s+(this|that|it)\s+say)\b",
    r"\bread\s+(this|that|the)\s+screen\b",
    r"\bread\s+what\'?s?\s+on\s+(the\s+)?screen\b",
    r"\b(read\s+the\s+text|read\s+me\s+the\s+text)\b",
    r"\b(what\s+text\s+is\s+(on|visible))\b",
]

_READ_ERROR_PATTERNS = [
    r"\b(what\s+error|what\'?s?\s+the\s+error|read\s+the\s+error)\b",
    r"\b(what\s+went\s+wrong|what\'?s?\s+wrong)\b",
    r"\b(why\s+is\s+(this|that|it)\s+not\s+working)\b",
    r"\b(why\s+isn\'?t\s+(this|that|it)\s+(working|loading))\b",
    r"\b(what\s+is\s+the\s+(error|warning|issue|problem))\b",
    r"\b(is\s+there\s+an?\s+error)\b",
    r"\b(read\s+the\s+(warning|alert|error)\s+message)\b",
    r"\b(what\s+does\s+the\s+error\s+say)\b",
]

_READ_NOTIFICATION_PATTERNS = [
    r"\b(what\s+notification|read\s+the\s+notification)\b",
    r"\b(what\s+alert|read\s+the\s+alert)\b",
    r"\b(what\s+(just\s+)?popped\s+up|what\s+(just\s+)?appeared)\b",
    r"\b(what\s+is\s+that\s+(notification|alert|popup|pop-up|banner|toast))\b",
    r"\b(read\s+the\s+(popup|pop-up|banner|toast|dialog))\b",
    r"\b(what\s+does\s+the\s+(notification|alert|popup)\s+say)\b",
]

_IDENTIFY_APP_PATTERNS = [
    r"\b(what\s+app|which\s+app|what\s+application|which\s+application)\b",
    r"\b(what\s+is\s+open|what\'?s?\s+open|which\s+window)\b",
    r"\b(what\s+program|which\s+program)\b",
    r"\b(what\s+am\s+i\s+running|what\s+is\s+running)\b",
    r"\b(identify\s+the\s+(app|application|window|program))\b",
    r"\b(which\s+(app|window|program)\s+is\s+(active|focused|in\s+front))\b",
]

_CHECK_STATE_PATTERNS = [
    r"\b(is\s+(the\s+)?(toggle|switch|checkbox)\s+(on|off|enabled|disabled|checked|unchecked))\b",
    r"\b(is\s+it\s+(loading|spinning|processing|buffering))\b",
    r"\b(is\s+(the\s+)?(button|element)\s+(active|inactive|disabled|enabled|greyed|grayed))\b",
    r"\b(what\s+state\s+is|what\s+status)\b",
    r"\b(is\s+it\s+(on|off|active|inactive|selected|deselected))\b",
    r"\b(check\s+if|check\s+whether)\b.*\b(enabled|disabled|active|on|off|loading|visible)\b",
    r"\b(is\s+the\s+page\s+(loaded|ready|done))\b",
    r"\b(has\s+it\s+(finished|loaded|completed))\b",
]

_COMPARE_SCREEN_PATTERNS = [
    r"\b(what\s+changed|what\'?s?\s+different|what\s+is\s+different)\b",
    r"\b(compare|diff|difference)\b.*\b(screen|before|after)\b",
    r"\b(before\s+and\s+after|side\s+by\s+side)\b",
    r"\b(did\s+(anything|something)\s+change)\b",
    r"\b(has\s+(anything|something|it)\s+changed)\b",
    r"\b(spot\s+the\s+difference)\b",
]

_COUNT_ELEMENTS_PATTERNS = [
    r"\b(how\s+many)\s+(tabs?|windows?|icons?|buttons?|items?|elements?|files?|folders?|notifications?|messages?|emails?|results?|rows?|columns?|images?|links?)\b",
    r"\b(count\s+the)\s+(tabs?|windows?|icons?|buttons?|items?|elements?|files?|folders?|notifications?|messages?|emails?|results?|rows?|columns?|images?|links?)\b",
    r"\b(how\s+many\s+(are|is)\s+(there|visible|open|showing))\b",
    r"\b(number\s+of)\s+(tabs?|windows?|icons?|buttons?|items?|elements?)\b",
]

_LOCATE_PATTERNS = [
    r"\bwhere\s+is\s+(the\s+)?(.+?)(?:\?|$)",
    r"\b(find|locate|show\s+me)\s+(the\s+)?(.+?)(?:\?|$)",
    r"\b(which\s+one\s+is|which\s+is)\s+(the\s+)?(.+?)(?:\?|$)",
    r"\b(position|location)\s+of\s+(the\s+)?(.+?)(?:\?|$)",
]

_FIND_TEXT_PATTERNS = [
    r"\b(find|search\s+for|look\s+for)\s+(the\s+)?(word|text|phrase|string)\s+['\"]?(.+?)['\"]?\b",
    r"\b(is\s+the\s+(word|text|phrase))\s+['\"]?(.+?)['\"]?\s+(on|visible|there)\b",
    r"\b(can\s+you\s+(find|see|spot))\s+(the\s+)?(word|text)\s+['\"]?(.+?)['\"]?\b",
    r"\b(search\s+the\s+screen\s+for)\b",
    r"\b(does\s+it\s+say)\s+['\"]?(.+?)['\"]?\b",
]

_LOCATE_ICON_PATTERNS = [
    r"\b(find|locate|where\s+is)\s+(the\s+)?(wi-?fi|wifi|bluetooth|battery|volume|sound|network|clock|time|date)\s*(icon|symbol|indicator)?\b",
    r"\b(where\s+is\s+the)\s+(.+?)\s+(icon|symbol|indicator)\b",
    r"\b(find\s+the)\s+(.+?)\s+(icon|symbol|indicator)\b",
    r"\b(locate\s+the)\s+(.+?)\s+(icon|symbol|indicator|tray)\b",
    r"\b(system\s+tray|menu\s+bar|status\s+bar|taskbar|dock)\b.*\b(icon|find|where|locate)\b",
]

_CLICK_ELEMENT_PATTERNS = [
    r"\b(click|press)\s+(on\s+)?(the\s+)?(.+?)(?:\s+button|\s+link|\s+icon)?(?:\?|\.|$)",
    r"\b(go\s+to|navigate\s+to)\s+(the\s+)?(.+?)(?:\s+button|\s+link)?(?:\?|\.|$)",
    r"\b(tap|select|choose|pick)\s+(the\s+)?(.+?)(?:\s+button|\s+link|\s+option)?(?:\?|\.|$)",
    r"\b(hit|activate)\s+(the\s+)?(.+?)(?:\s+button)?(?:\?|\.|$)",
]

_HOVER_ELEMENT_PATTERNS = [
    r"\b(hover|hover\s+over)\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(move\s+(?:(?:the|my)\s+)?(?:mouse|mask|cursor)\s+(?:to|over))\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(point\s+(?:to|at))\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(move\s+to)\s+(the\s+)?(.+?)(?:\?|\.|$)",
]

_DOUBLE_CLICK_PATTERNS = [
    r"\b(double[- ]?click)\s+(on\s+)?(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(open)\s+(the\s+)?(.+?)\s+(folder|icon)\s*(by\s+double[- ]?click)?\b",
]

_RIGHT_CLICK_PATTERNS = [
    r"\b(right[- ]?click)\s+(on\s+)?(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(context\s+menu)\s+(on|for)\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(show\s+options\s+for)\s+(the\s+)?(.+?)(?:\?|\.|$)",
]

_SCROLL_TO_PATTERNS = [
    r"\b(scroll\s+(down|up)\s+to)\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(scroll\s+to)\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(scroll\s+until\s+you\s+see)\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(scroll\s+(down|up)\s+(?:a\s+)?(?:bit|little|lot|more|page))\b",
    r"\b(scroll\s+to\s+the\s+(top|bottom|end|beginning))\b",
]

_DRAG_DROP_PATTERNS = [
    r"\b(drag)\s+(the\s+)?(.+?)\s+(to|into|onto|over)\s+(the\s+)?(.+?)(?:\?|\.|$)",
    r"\b(drag\s+and\s+drop)\b",
    r"\b(drop\s+it\s+(on|into|onto))\b",
]

_INTERACT_FORM_PATTERNS = [
    r"\b(fill\s+(in\s+)?(the\s+)?form|fill\s+out\s+(the\s+)?form)\b",
    r"\b(enter|type|input)\s+['\"]?(.+?)['\"]?\s+(in|into)\s+(the\s+)?(.+?)\s*(field|box|input)?\b",
    r"\b(type|enter)\s+(in\s+)?(the\s+)?(.+?)\s+(field|box|input)\b",
    r"\b(set\s+the\s+value|change\s+the\s+value)\b",
    r"\b(submit\s+the\s+form)\b",
    r"\b(clear\s+the\s+(field|input|box|form))\b",
    r"\b(check|uncheck)\s+(the\s+)?(.+?)\s*(checkbox|box)?\b",
    r"\b(select)\s+['\"]?(.+?)['\"]?\s+(from|in)\s+(the\s+)?(dropdown|select|menu)\b",
]

_NAVIGATE_MENU_PATTERNS = [
    r"\b(open\s+the)\s+(.+?)\s+(menu|dropdown|submenu)\b",
    r"\b(go\s+to|navigate\s+to)\s+(.+?)\s*>\s*(.+)\b",
    r"\b(click\s+on\s+the)\s+(.+?)\s+(menu|tab|toolbar)\b",
    r"\b(open|expand)\s+(the\s+)?(menu|dropdown|sidebar|navigation)\b",
    r"\b(select)\s+(.+?)\s+(from\s+the\s+menu|from\s+the\s+dropdown)\b",
    r"\b(menu\s*>\s*|settings\s*>\s*|file\s*>\s*|edit\s*>\s*|view\s*>\s*)\b",
]

_MULTI_SCREEN_NAV_PATTERNS = [
    r"\b(what\'?s?\s+on)\s+(my\s+)?(other|second|third)\s+(screen|monitor|display)\b",
    r"\b(show\s+me)\s+(the\s+)?(other|second|third)\s+(screen|monitor|display)\b",
    r"\b(switch\s+to|go\s+to)\s+(screen|monitor|display)\s+(\d+)\b",
    r"\b(move\s+to)\s+(screen|monitor|display)\s+(\d+)\b",
    r"\b(what\'?s?\s+on\s+screen)\s+(\d+)\b",
    r"\b(all\s+screens|all\s+monitors|every\s+screen)\b",
]

_BROWSER_TASK_PATTERNS = [
    r"\b(navigate\s+to|go\s+to)\s+(https?://|\w+\.\w+)\b",
    r"\b(open\s+the\s+url|visit\s+the\s+page)\b",
]

_MULTI_STEP_PATTERNS = [
    r"\b(do\s+all\s+of\s+these|follow\s+these\s+steps|execute\s+these\s+steps)\b",
    r"\b(step\s+by\s+step|one\s+by\s+one|in\s+order)\b",
    r"\b(first\s+.*\s+then\s+.*\s+then)\b",
    r"\b(set\s+up|configure|install)\s+(.+\s+and\s+.+)\b",
    r"\b(workflow|pipeline|sequence)\b",
]


# ── Compile all patterns ──

def _compile(patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns]

_COMPILED = {
    VisionIntent.MULTI_STEP:        _compile(_MULTI_STEP_PATTERNS),
    VisionIntent.DRAG_DROP:         _compile(_DRAG_DROP_PATTERNS),
    VisionIntent.INTERACT_FORM:     _compile(_INTERACT_FORM_PATTERNS),
    VisionIntent.NAVIGATE_MENU:     _compile(_NAVIGATE_MENU_PATTERNS),
    VisionIntent.DOUBLE_CLICK:      _compile(_DOUBLE_CLICK_PATTERNS),
    VisionIntent.RIGHT_CLICK:       _compile(_RIGHT_CLICK_PATTERNS),
    VisionIntent.SCROLL_TO:         _compile(_SCROLL_TO_PATTERNS),
    VisionIntent.READ_ERROR:        _compile(_READ_ERROR_PATTERNS),
    VisionIntent.READ_NOTIFICATION: _compile(_READ_NOTIFICATION_PATTERNS),
    VisionIntent.CHECK_STATE:       _compile(_CHECK_STATE_PATTERNS),
    VisionIntent.COMPARE_SCREEN:    _compile(_COMPARE_SCREEN_PATTERNS),
    VisionIntent.COUNT_ELEMENTS:    _compile(_COUNT_ELEMENTS_PATTERNS),
    VisionIntent.IDENTIFY_APP:      _compile(_IDENTIFY_APP_PATTERNS),
    VisionIntent.FIND_TEXT:         _compile(_FIND_TEXT_PATTERNS),
    VisionIntent.LOCATE_ICON:       _compile(_LOCATE_ICON_PATTERNS),
    VisionIntent.MULTI_SCREEN_NAV:  _compile(_MULTI_SCREEN_NAV_PATTERNS),
    VisionIntent.LOCATE:            _compile(_LOCATE_PATTERNS),
    VisionIntent.HOVER_ELEMENT:     _compile(_HOVER_ELEMENT_PATTERNS),
    VisionIntent.CLICK_ELEMENT:     _compile(_CLICK_ELEMENT_PATTERNS),
    VisionIntent.BROWSER_TASK:      _compile(_BROWSER_TASK_PATTERNS),
    VisionIntent.READ_TEXT:         _compile(_READ_TEXT_PATTERNS),
    VisionIntent.DESCRIBE_SCREEN:   _compile(_DESCRIBE_SCREEN_PATTERNS),
}

# Classification order — most specific first
_CLASSIFICATION_ORDER = [
    VisionIntent.MULTI_STEP,
    VisionIntent.DRAG_DROP,
    VisionIntent.DOUBLE_CLICK,
    VisionIntent.RIGHT_CLICK,
    VisionIntent.SCROLL_TO,
    VisionIntent.READ_ERROR,
    VisionIntent.READ_NOTIFICATION,
    VisionIntent.CHECK_STATE,
    VisionIntent.COMPARE_SCREEN,
    VisionIntent.COUNT_ELEMENTS,
    VisionIntent.IDENTIFY_APP,
    VisionIntent.FIND_TEXT,
    VisionIntent.LOCATE_ICON,
    VisionIntent.MULTI_SCREEN_NAV,
    VisionIntent.NAVIGATE_MENU,
    VisionIntent.LOCATE,
    VisionIntent.INTERACT_FORM,
    VisionIntent.HOVER_ELEMENT,
    VisionIntent.CLICK_ELEMENT,
    VisionIntent.BROWSER_TASK,
    VisionIntent.READ_TEXT,
    VisionIntent.DESCRIBE_SCREEN,
]


# ── Helpers for backward compat ──

# Intents that behave like the old LOCATE (need coordinates)
LOCATE_INTENTS = frozenset({
    VisionIntent.LOCATE,
    VisionIntent.FIND_TEXT,
    VisionIntent.LOCATE_ICON,
})

# Intents that behave like the old MOUSE_ACTION (locate + execute)
ACTION_INTENTS = frozenset({
    VisionIntent.CLICK_ELEMENT,
    VisionIntent.HOVER_ELEMENT,
    VisionIntent.DOUBLE_CLICK,
    VisionIntent.RIGHT_CLICK,
    VisionIntent.SCROLL_TO,
    VisionIntent.DRAG_DROP,
    VisionIntent.INTERACT_FORM,
    VisionIntent.NAVIGATE_MENU,
})

# Intents that are purely informational (no mouse action needed)
INFO_INTENTS = frozenset({
    VisionIntent.DESCRIBE_SCREEN,
    VisionIntent.READ_TEXT,
    VisionIntent.READ_ERROR,
    VisionIntent.READ_NOTIFICATION,
    VisionIntent.IDENTIFY_APP,
    VisionIntent.CHECK_STATE,
    VisionIntent.COMPARE_SCREEN,
    VisionIntent.COUNT_ELEMENTS,
    VisionIntent.MULTI_SCREEN_NAV,
})


def classify_vision_intent(prompt: str) -> VisionIntent:
    """
    Classify the user's vision/screenshot prompt into an intent.

    Order matters: more specific intents are checked first.
    Returns the first matching intent, or UNKNOWN to fall back to vision LLM.
    """
    if not prompt or not isinstance(prompt, str):
        return VisionIntent.UNKNOWN

    text = prompt.strip()
    if not text:
        return VisionIntent.UNKNOWN

    for intent in _CLASSIFICATION_ORDER:
        for pat in _COMPILED[intent]:
            if pat.search(text):
                logger.debug("Vision intent: %s (pattern: %s)", intent.value, pat.pattern[:50])
                return intent

    return VisionIntent.UNKNOWN
