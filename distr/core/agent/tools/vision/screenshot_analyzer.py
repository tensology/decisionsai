"""
Screenshot Analyzer Tool

A tool that captures screenshots and analyzes them using vision-enabled LLMs.
Supports commands like "look at my screen", "analyze this window", "what do you see".

Refactored: utility functions live in screen_capture.py and vision_api.py.
This module contains the ScreenshotAnalyzerTool class and re-exports the
public helpers so existing imports continue to work.
"""

import json
import logging
import os
import platform
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

# Re-export utilities so callers using the old import paths still work
from distr.core.agent.tools.vision.screen_capture import (  # noqa: F401
    capture_all_screens,
    capture_screenshot,
    get_current_mouse_screen,
    get_screen_by_number,
    get_screens_sorted_by_position,
    image_to_base64,
    pyautogui,
)
from distr.core.agent.tools.vision.vision_api import (  # noqa: F401
    build_action_prompt,
    build_summary_prompt,
    call_openai_vision,
    resolve_vision_llm_config,
)
from distr.core.agent.services.vision.action_mode import resolve_execute_action

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class ScreenshotAnalyzerInput(BaseModel):
    """Input schema for screenshot_analyzer tool."""
    prompt: str = Field(description="The question or instruction about what to analyze in the screenshot")
    region: Optional[str] = Field(default=None, description="Optional: 'full', 'window', 'selection', or 'all'.")
    capture_only: Optional[bool] = Field(default=None, description="If True, capture screenshot and return file path only — skip vision LLM analysis. Use when the screenshot file is needed as input to other tools (save to folder, attach to ticket, send to pi, etc.).")
    execute_action: Optional[bool] = Field(default=None, description="If False, locate and return coordinates only without moving/clicking.")


# ---------------------------------------------------------------------------
# Telegram helpers (shared across _run sub-methods)
# ---------------------------------------------------------------------------

TELEGRAM_SEND_PATTERNS = [
    'tell the gram', 'tell telegram', 'send by telegram', 'send to telegram',
    'send via telegram', 'send through telegram', 'send it to telegram',
    'send that to telegram', 'send to my telegram', 'telegram', 'send telegram',
]

SEND_PATTERNS = [
    'send that to me', 'send it to me', 'send that', 'send it',
    'send the screenshot', 'send the picture', 'send that picture',
    'send me that', 'send me it', 'send to me', 'send that image',
    'give me a screenshot', 'give me the screenshot', 'give me a picture', 'give me the picture',
    'give me screenshot', 'give me picture', 'give me that', 'give me it',
    'give me screen', 'give me the screen', 'give me screen 1', 'give me screen 2',
    'give me', 'get me', 'show me',
]

# Patterns that indicate the user wants the screenshot FILE as an artifact
# to route to other tools (NOT just capture-and-analyze or send-to-telegram)
# Keep this broad — if the screenshot is a STEP IN A CHAIN, not the end goal,
# we should return the file path for the LLM to route wherever it wants.
CAPTURE_ONLY_PATTERNS = [
    'and save', 'and put', 'and add', 'and attach', 'and send',
    'and copy', 'and move', 'and upload',
    'and paste', 'and insert', 'and include',
    'screenshot and', 'capture and', 'picture and',
    'save it to', 'save the screenshot', 'save that to',
    'put it in', 'put that in', 'put the screenshot in',
    'add it to', 'add the screenshot to',
    'attach it to', 'attach the screenshot to',
    'send it to pi', 'push to the cli', 'push it to the cli', 'send to the cli',
    'send to pi', 'push to pi',
    'screenshot to', 'screenshot in', 'screenshot for',
]


def _check_telegram_request() -> bool:
    """Return True if the current request originated from Telegram."""
    if hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request:
        return True
    for thread in threading.enumerate():
        if hasattr(thread, 'telegram_request') and thread.telegram_request:
            threading.current_thread().telegram_request = True
            logger.info(f"📸 Found telegram_request=True on thread '{thread.name}' — preserving")
            return True
    return False


def _persist_screenshot(screenshot_path: str, prefix: str = "screenshot") -> Optional[str]:
    """Copy a screenshot to a persistent temp location and return the new path."""
    try:
        persistent_dir = Path(tempfile.gettempdir()) / "decisions_ai_screenshots"
        persistent_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = persistent_dir / f"{prefix}_{ts}.png"
        shutil.copy2(screenshot_path, dest)
        logger.debug(f"Persisted screenshot: {dest}")
        return str(dest)
    except Exception as e:
        logger.error(f"Failed to persist screenshot: {e}")
        return None


def _store_for_telegram(persistent_path: str, raw: bool = False) -> None:
    """Store a screenshot path on the current thread for Telegram delivery."""
    threading.current_thread().telegram_analyzed_image = persistent_path
    ScreenshotAnalyzerTool._last_telegram_screenshot = persistent_path
    if raw:
        threading.current_thread().telegram_send_raw_screenshot = persistent_path
    logger.info(f"📸 Stored for Telegram: {persistent_path} (raw={raw})")


# ---------------------------------------------------------------------------
# Prompt / text helpers
# ---------------------------------------------------------------------------

_TRIGGER_PHRASES = [
    'look at my screen', 'see my screen', 'analyze my screen', 'check my screen',
    'look at this window', 'see this window', 'analyze this window',
    'what do you see', "what's on the screen", 'describe the screen',
    'read this screen', 'what does this say', 'can you see', 'can you see what',
    'take a screenshot', 'take a picture', 'capture screen', 'take screenshot',
    'take screenshot of', 'take picture of', 'capture my screen', 'screenshot screen',
]

_ACTION_KEYWORDS = [
    'move mouse', 'move the mouse', 'click', 'click on', 'go to', 'control',
    'move to', 'move cursor', 'position', 'coordinates', 'x and y', 'x, y',
    'where is', 'find the', 'locate', 'point to', 'target', 'move my mouse',
    'move my cursor', 'move my mask', 'that i am looking at', "that i'm looking at",
    'what i see', "what i'm looking at", 'on my screen', 'that button', 'that link',
    'the compose button', 'the save button', 'the submit button', 'the search box',
]

_MY_SCREEN_PATTERNS = [
    'my screen', 'this screen', 'the screen', 'see my screen', 'look at my screen',
    'on my screen', 'on this screen', 'on the screen',
    'that i am looking at', "that i'm looking at", 'what i see', "what i'm looking at",
    "that i'm viewing", 'that i am viewing', "the screen i'm on", 'the screen i am on',
    'picture of my screen', 'screenshot of my screen', 'capture my screen',
    'take a picture of my screen',
]

_WORD_TO_NUMBER = {
    'one': 1, 'won': 1, 'two': 2, 'too': 2, 'to': 2,
    'three': 3, 'tree': 3, 'four': 4, 'for': 4, 'fore': 4,
    'five': 5, 'fife': 5, 'six': 6, 'sicks': 6,
    'seven': 7, 'eight': 8, 'ate': 8, 'nine': 9, 'ten': 10,
}


def _extract_prompt(prompt: str, original_text: str) -> str:
    """Clean up the raw prompt, stripping trigger phrases."""
    if prompt and prompt != "__ORIGINAL_TEXT__":
        return prompt
    if not original_text:
        return "What do you see in these screenshots? Provide a summary breakdown of what's on each screen."
    text_lower = original_text.lower()
    cleaned = original_text
    for trigger in _TRIGGER_PHRASES:
        if trigger in text_lower:
            cleaned = original_text.replace(trigger, '').strip()
            break
    if not cleaned or len(cleaned) < 5:
        return "What do you see in these screenshots? Provide a summary breakdown of what's on each screen."
    return cleaned


def _extract_screen_number(text: str) -> Optional[int]:
    """Parse a screen number from user text, handling STT mistakes."""
    text_norm = re.sub(r'screen\s+too\b', 'screen two', text)
    text_norm = re.sub(r'to\s+screen\s+too\b', 'to screen two', text_norm)

    for pattern in [r'screen\s+(\d+)', r'to\s+screen\s+(\d+)']:
        m = re.search(pattern, text_norm)
        if m:
            return int(m.group(1))

    for word, num in _WORD_TO_NUMBER.items():
        if re.search(rf'screen\s+{word}\b', text_norm) or re.search(rf'to\s+screen\s+{word}\b', text_norm):
            return num
    return None


def _resolve_capture_region(
    region: Optional[str],
    screen_number: Optional[int],
    text: str,
) -> str:
    """Decide which capture region to use based on user intent."""
    text_lower = text.lower()
    is_my_screen = any(p in text_lower for p in _MY_SCREEN_PATTERNS)
    is_all = any(w in text_lower for w in ['all screens', 'all monitors', 'every screen', 'all of my screens'])

    if screen_number:
        return f"screen_{screen_number}"
    if is_my_screen and not is_all:
        return "current_mouse_screen"
    if is_all:
        return "all"
    # Default: current mouse screen (ignore region param from tool call)
    return "current_mouse_screen"



def _intent_to_action(intent) -> str:
    """Map a VisionIntent to the action string for JSON responses."""
    from distr.core.agent.services.vision.intent_classifier import VisionIntent
    _map = {
        VisionIntent.CLICK_ELEMENT: "click",
        VisionIntent.HOVER_ELEMENT: "hover",
        VisionIntent.DOUBLE_CLICK: "double_click",
        VisionIntent.RIGHT_CLICK: "right_click",
        VisionIntent.SCROLL_TO: "scroll_down",
        VisionIntent.DRAG_DROP: "drag",
        VisionIntent.NAVIGATE_MENU: "click",
        VisionIntent.INTERACT_FORM: "click",
        VisionIntent.LOCATE: "click",
        VisionIntent.FIND_TEXT: "highlight",
        VisionIntent.LOCATE_ICON: "click",
    }
    return _map.get(intent, "click")


def _is_ambiguous_click_target(description: str, confidence: Optional[Any]) -> tuple[bool, str]:
    """Detect vague/unsafe click targets from vision output."""
    desc = (description or "").strip().lower()
    vague_markers = (
        "part of",
        "near",
        "around",
        "approx",
        "approximately",
        "looks like",
        "likely",
        "possible",
        "maybe",
        "close to",
    )
    if any(m in desc for m in vague_markers):
        return True, "description_is_vague"
    try:
        conf = float(confidence) if confidence is not None else None
    except Exception:
        conf = None
    if conf is not None and conf < 0.65:
        return True, f"low_confidence_{conf:.2f}"
    return False, ""


def _split_pointer_targets(text: str) -> list[str]:
    """
    Split multi-target pointer commands into sequential single-target commands.

    Example:
      "move my mouse to X then move my mouse to Y"
      -> ["move my mouse to X", "move my mouse to Y"]
    """
    if not text:
        return []
    lowered = text.lower()
    if " then " not in lowered:
        return []
    if not re.search(r"\b(move|hover|click|tap|press)\b", lowered):
        return []
    # Split on natural sequence words while preserving segment text.
    parts = [p.strip(" ,.") for p in re.split(r"\b(?:and then|then)\b", text, flags=re.IGNORECASE) if p.strip(" ,.")]
    if len(parts) < 2:
        return []
    # Only keep parts that still look like pointer target commands.
    pointer_parts = []
    for p in parts:
        if re.search(r"\b(move|hover|click|tap|press)\b", p, flags=re.IGNORECASE) and re.search(r"\bto\b", p, flags=re.IGNORECASE):
            pointer_parts.append(p)
    return pointer_parts if len(pointer_parts) >= 2 else []


# ---------------------------------------------------------------------------
# The Tool
# ---------------------------------------------------------------------------

class ScreenshotAnalyzerTool(BaseTool):
    """Tool to capture screenshots and analyze them using vision-enabled LLMs.

    When called, this tool:
    1. Captures a screenshot (full screen, active window, or user-selected region)
    2. Converts it to base64
    3. Calls the LLM service with the image and user's prompt
    4. Returns the model's analysis
    """

    name: str = "screenshot_analyzer"
    description: str = (
        "🎯 PRIMARY TOOL for ALL screen interaction — both seeing AND clicking. "
        "Always use this tool when the user wants to click, open, or interact with ANY element on screen. "
        "This tool captures a screenshot, uses vision AI to find the target, and AUTOMATICALLY moves the mouse and clicks it. "
        ""
        "CRITICAL: NEVER use execute_code with pyautogui to blindly click coordinates — you CANNOT see the screen! "
        "ALWAYS use screenshot_analyzer first so the vision model can find the exact pixel position, then the tool clicks for you. "
        ""
        "USE THIS TOOL when user says: "
        "- 'click [element]', 'open [element]', 'go into [element]', 'press [button]' → tool finds and CLICKS it "
        "- 'move mouse to [element]', 'go to [element]' → tool finds and MOVES there "
        "- 'double-click [element]', 'right-click [element]' → tool finds and performs the action "
        "- 'take a screenshot', 'capture screen', 'screenshot screen X' "
        "- 'look at my screen', 'what's on screen', 'read this screen' "
        "- 'what do you see', 'analyze this window', 'describe what's on screen' "
        "- 'take a screenshot and save/send/attach it' "
        "- ANY request where a screenshot is the FIRST step in a multi-tool chain "
        ""
        "IMPORTANT BEHAVIOR — THREE MODES: "
        ""
        "1. CAPTURE-ONLY (returns a routable file path artifact): "
        "   Triggered when the user wants the screenshot FILE to use in other tools: "
        "   - 'take a screenshot and save it to [folder]' "
        "   - 'take a screenshot and add/attach it to [ticket/card]' "
        "   - 'take a screenshot and send it to pi' / 'push to the CLI' "
        "   - 'take a screenshot and [any action that needs the file]' "
        "   - 'screenshot screen 2 and put it in my downloads' "
        "   In this mode the tool captures the screenshot, persists it, and returns the file path "
        "   with [ACTION REQUIRED] directives. The LLM MUST then chain to the next tool(s) "
        "   (file_operations, ticket_board, pi_agent, send_file_to_telegram, execute_code, etc.) "
        ""
        "2. DIRECT SEND (capture + send to Telegram, no analysis): "
        "   Triggered for simple 'give me a screenshot' / 'send it to me' in Telegram: "
        "   - 'give me a screenshot', 'give me screenshot', 'send it to me' (from Telegram) "
        "   - 'take a screenshot and send it to telegram' "
        "   Tool captures screenshot, returns 'Done', screenshot auto-sent to Telegram. "
        ""
        "3. ANALYSIS + ACTION (capture + vision + execute action): "
        "   Triggered when user wants to interact with something on screen: "
        "   - 'click the third video', 'open that folder', 'press the submit button' "
        "   - 'what do you see', 'describe the screen', 'read this screen' "
        "   Tool captures screenshot AND analyzes it with vision LLM AND executes the action (click, move, etc.). "
        ""
        "SCREENSHOT CHAINING — When the tool returns a file path with [ACTION REQUIRED]: "
        "- The screenshot is captured and saved to a persistent path on disk "
        "- You MUST chain to the next tool(s) the user requested "
        "- Common chains: "
        "  * screenshot + save to folder → call execute_code to copy/move the file "
        "  * screenshot + attach to ticket → call ticket_board with the file path "
        "  * screenshot + send to pi CLI → call pi_agent with the file path referenced "
        "  * screenshot + send to Telegram → call send_file_to_telegram with file_path "
        "  * screenshot + multiple actions → chain ALL tools in sequence "
        "- DO NOT say 'Done' until ALL tools in the chain have completed "
        "- If a tool result contains [ACTION REQUIRED], silently call the next tool "
        ""
        "The tool can capture: "
        "- Specific screen numbers: 'screen 1', 'screen 2', etc. "
        "- Current mouse screen: 'my screen', 'this screen' "
        "- All screens: 'all screens', 'every screen' "
    )
    _last_telegram_screenshot: Optional[str] = None
    args_schema: type[BaseModel] = ScreenshotAnalyzerInput

    llm_service: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self.llm_service = llm_service
        self._shown_warnings: set = set()

    # ------------------------------------------------------------------
    # Direct-send fast path (no vision analysis)
    # ------------------------------------------------------------------

    def _handle_direct_send(self, prompt: str, **kwargs) -> str:
        """Handle 'give me a screenshot' style requests — capture and return immediately."""
        logger.info("📸 DIRECT SEND mode — bypassing analysis")
        is_telegram = _check_telegram_request()

        text_to_check = (prompt or "").lower()
        wants_telegram = any(p in text_to_check for p in TELEGRAM_SEND_PATTERNS)

        # Extract screen number
        screen_number = None
        for num, word in [(1, 'one'), (2, 'two'), (3, 'three'), (4, 'four'), (5, 'five'), (6, 'six')]:
            if f"screen {num}" in text_to_check or f"screen {word}" in text_to_check:
                screen_number = num
                break

        try:
            tmp_dir = tempfile.mkdtemp(prefix="decisions_ai_direct_")
            screenshot_path = self._capture_single_screen(tmp_dir, screen_number)
            if not screenshot_path:
                return "Error: Failed to capture screenshot."

            persistent = _persist_screenshot(screenshot_path, "direct_screenshot")
            if not persistent:
                return "Error: Failed to persist screenshot."

            if wants_telegram and not is_telegram:
                return (
                    f"Result: {persistent}\n"
                    f'[ACTION REQUIRED: Call send_file_to_telegram with file_path="{persistent}" '
                    f"to send the screenshot to Telegram]"
                )

            if is_telegram:
                _store_for_telegram(persistent, raw=True)

            return "Done"
        except Exception as e:
            logger.error(f"Direct screenshot capture failed: {e}")
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Capture-only: return file path artifact for tool chaining
    # ------------------------------------------------------------------

    def _handle_capture_only(self, prompt: str, original_text: str, **kwargs) -> str:
        """Capture a screenshot and return the file path as a routable artifact.

        Used when the user wants the screenshot FILE to pass to other tools
        (save to folder, attach to ticket, send to pi, etc.).
        Returns the file path with [ACTION REQUIRED] so the LLM chains to the next tool.
        """
        logger.info("📸 CAPTURE-ONLY mode — returning file path artifact for chaining")

        text_lower = (original_text or prompt or "").lower()

        # Extract screen number
        screen_number = _extract_screen_number(text_lower)

        # Resolve capture region (respect user's screen/region intent)
        capture_region = _resolve_capture_region(None, screen_number, text_lower)

        try:
            is_telegram = _check_telegram_request()

            if is_telegram:
                # Telegram path — capture, persist, and store for Telegram delivery
                with tempfile.TemporaryDirectory() as tmp_dir:
                    screenshot_paths, screenshot_to_screen_map, captured_screen_number = \
                        self._capture_screenshots(capture_region, tmp_dir)
                    if not screenshot_paths:
                        return "Error: Failed to capture screenshot."
                    pp = _persist_screenshot(screenshot_paths[0], "capture_only")
                    if not pp:
                        return "Error: Failed to persist screenshot."
                    _store_for_telegram(pp, raw=True)
                    # Also return path for chaining
                    return (
                        f"Result: Screenshot captured at {pp}\n"
                        f"[ACTION REQUIRED: The screenshot file is at \"{pp}\". "
                        f"Chain to the next tool(s) the user requested.]"
                    )

            # Desktop path — capture, persist, return file path with chaining directives
            with tempfile.TemporaryDirectory() as tmp_dir:
                screenshot_paths, screenshot_to_screen_map, captured_screen_number = \
                    self._capture_screenshots(capture_region, tmp_dir)
                if not screenshot_paths:
                    return "Error: Failed to capture screenshot."

                # Persist ALL screenshots before temp dir is deleted
                persistent_paths = []
                for idx, sp in enumerate(screenshot_paths):
                    pp = _persist_screenshot(sp, f"capture_only_{idx}")
                    if pp:
                        persistent_paths.append(pp)

                if not persistent_paths:
                    return "Error: Failed to persist screenshot."

                primary = persistent_paths[0]

                if len(persistent_paths) == 1:
                    result = (
                        f"Result: Screenshot captured at: {primary}\n"
                        f'[ACTION REQUIRED: The screenshot file is at "{primary}". '
                        f"Continue with the user's request using this file path.]"
                    )
                else:
                    paths_str = ", ".join(f'"{p}"' for p in persistent_paths)
                    result = (
                        f"Result: {len(persistent_paths)} screenshot(s) captured: {paths_str}\n"
                        f'[ACTION REQUIRED: The primary screenshot is at "{primary}". '
                        f"Continue with the user's request using this file path.]"
                    )

                logger.info(f"📸 Capture-only returning artifact: {primary}")
                return result

        except Exception as e:
            logger.error(f"Capture-only screenshot failed: {e}", exc_info=True)
            return f"Error: Failed to capture screenshot: {e}"

    # ------------------------------------------------------------------
    # Capture a single screen (used by direct-send and main path)
    # ------------------------------------------------------------------

    @staticmethod
    def _capture_single_screen(tmp_dir: str, screen_number: Optional[int] = None) -> Optional[str]:
        """Capture a single screen and return the file path, or None on failure."""
        if screen_number:
            target = get_screen_by_number(screen_number)
            if not target:
                return None
            path = os.path.join(tmp_dir, f"screen_{screen_number}.png")
        else:
            target = get_current_mouse_screen()
            path = os.path.join(tmp_dir, "screenshot.png")

        if target:
            from distr.core.screen_utils import CachedScreenWrapper
            if isinstance(target, CachedScreenWrapper):
                if platform.system() == "Darwin":
                    import subprocess, time
                    geo = target.geometry()
                    result = subprocess.run(
                        ['screencapture', '-R',
                         f"{geo.left()},{geo.top()},{geo.width()},{geo.height()}", path],
                        capture_output=True, timeout=10,
                    )
                    time.sleep(0.1)
                    if result.returncode == 0 and os.path.exists(path):
                        return path
                    # Fallback
                    if capture_screenshot(path, "full"):
                        return path
                    return None
                else:
                    return path if capture_screenshot(path, "full") else None
            else:
                from PyQt6.QtGui import QPixmap
                pixmap = target.grabWindow(0)
                return path if pixmap.save(path, 'PNG') else None
        else:
            return path if capture_screenshot(path, "full") else None

    # ------------------------------------------------------------------
    # Vision model support check
    # ------------------------------------------------------------------

    def _check_vision_support(self, vision_provider: str, vision_model: str, **kwargs) -> Optional[str]:
        """Return an error string if no vision model is configured, else None.
        
        Model capability validation happens in the settings UI.
        At runtime we just need *something* configured.
        """
        if vision_model and vision_model.strip():
            return None
        return "Error: No vision model configured. Please select one in the LLMs settings tab."

    # ------------------------------------------------------------------
    # Screenshot capture orchestration
    # ------------------------------------------------------------------

    def _capture_screenshots(
        self,
        capture_region: str,
        tmp_dir: str,
    ) -> tuple[list[str], dict[str, int], Optional[int]]:
        """
        Capture screenshots based on the resolved capture region.

        Returns:
            (screenshot_paths, screenshot_to_screen_map, captured_screen_number)
        """
        screenshot_paths: list[str] = []
        screen_map: dict[str, int] = {}
        captured_num: Optional[int] = None

        if capture_region == "all":
            screenshot_paths = capture_all_screens(tmp_dir)
            for p in screenshot_paths:
                fn = os.path.basename(p)
                if fn.startswith("screen_") and fn.endswith(".png"):
                    try:
                        screen_map[p] = int(fn.replace("screen_", "").replace(".png", ""))
                    except ValueError:
                        pass

        elif capture_region == "current_mouse_screen":
            captured_num = 1
            current_screen = get_current_mouse_screen()
            if current_screen:
                path = os.path.join(tmp_dir, "screenshot.png")
                try:
                    from distr.core.screen_utils import CachedScreenWrapper, get_current_mouse_screen_simple

                    # Determine screen number
                    try:
                        info = get_current_mouse_screen_simple()
                        if info and 'screen_number' in info:
                            captured_num = info['screen_number']
                        elif isinstance(current_screen, CachedScreenWrapper):
                            captured_num = (
                                current_screen.screen_number()
                                if hasattr(current_screen, 'screen_number')
                                else current_screen._screen_number
                            )
                    except Exception:
                        pass

                    if isinstance(current_screen, CachedScreenWrapper):
                        geo = current_screen.geometry()
                        if platform.system() == "Darwin":
                            import subprocess, time
                            coord = f"{geo.left()},{geo.top()},{geo.width()},{geo.height()}"
                            r = subprocess.run(
                                ['screencapture', '-R', coord, path],
                                capture_output=True, timeout=10,
                            )
                            time.sleep(0.2)
                            if r.returncode == 0 and os.path.exists(path):
                                screenshot_paths = [path]
                            else:
                                if capture_screenshot(path, "full"):
                                    screenshot_paths = [path]
                        else:
                            if capture_screenshot(path, "full"):
                                screenshot_paths = [path]
                    else:
                        from PyQt6.QtGui import QPixmap
                        if current_screen.grabWindow(0).save(path, 'PNG'):
                            screenshot_paths = [path]
                except Exception as e:
                    logger.error(f"Error capturing mouse screen: {e}", exc_info=True)
            else:
                path = os.path.join(tmp_dir, "screenshot.png")
                if capture_screenshot(path, "full"):
                    screenshot_paths = [path]
                    captured_num = 1

        elif capture_region.startswith("screen_"):
            try:
                sn = int(capture_region.split("_")[1])
                captured_num = sn
                target = get_screen_by_number(sn)
                if target:
                    path = os.path.join(tmp_dir, f"screen_{sn}.png")
                    screen_map[path] = sn
                    from distr.core.screen_utils import CachedScreenWrapper
                    if isinstance(target, CachedScreenWrapper):
                        geo = target.geometry()
                        if platform.system() == "Darwin":
                            import subprocess
                            r = subprocess.run(
                                ['screencapture', '-R',
                                 f"{geo.left()},{geo.top()},{geo.width()},{geo.height()}", path],
                                capture_output=True, timeout=10,
                            )
                            if r.returncode == 0 and os.path.exists(path):
                                screenshot_paths = [path]
                        else:
                            if capture_screenshot(path, "full"):
                                screenshot_paths = [path]
                    else:
                        from PyQt6.QtGui import QPixmap
                        if target.grabWindow(0).save(path, 'PNG'):
                            screenshot_paths = [path]
            except (ValueError, IndexError):
                path = os.path.join(tmp_dir, "screenshot.png")
                if capture_screenshot(path, "full"):
                    screenshot_paths = [path]
        else:
            path = os.path.join(tmp_dir, "screenshot.png")
            if capture_screenshot(path, capture_region):
                screenshot_paths = [path]

        return screenshot_paths, screen_map, captured_num


    # ------------------------------------------------------------------
    # Fast-path element / OCR locate
    # ------------------------------------------------------------------

    @staticmethod
    def _try_fast_locate(
        vision_intent,
        screenshot_paths: list[str],
        original_text: str,
        prompt: str,
        captured_screen_number: Optional[int],
        screenshot_to_screen_map: dict[str, int],
        execute_action: bool = True,
    ) -> Optional[str]:
        """
        Try element detection + OCR before falling back to the vision LLM.
        Returns a JSON result string if a match is found, else None.
        """
        from distr.core.agent.services.vision.intent_classifier import VisionIntent, LOCATE_INTENTS, ACTION_INTENTS
        if vision_intent not in LOCATE_INTENTS and vision_intent not in ACTION_INTENTS:
            return None
        if not screenshot_paths:
            return None

        from distr.core.agent.services.vision.locate import extract_search_target_from_prompt
        search_target = extract_search_target_from_prompt(original_text or prompt)
        screen_num = captured_screen_number or screenshot_to_screen_map.get(screenshot_paths[0]) or 1

        def _apply_offset(raw_x: int, raw_y: int, scr: int):
            from distr.core.agent.tools.input.mouse_utils import smooth_move_to
            ox, oy = 0, 0
            logical_w, logical_h = 0, 0
            image_w, image_h = 0, 0
            try:
                from distr.core import screen_utils
                cache = getattr(screen_utils, "_screen_info_cache", None) or {}
                sl = cache.get("screens", [])
                if sl and 1 <= scr <= len(sl):
                    info = sl[scr - 1]
                    geo = info.get("geometry", {})
                    ox, oy = geo.get("x", 0), geo.get("y", 0)
                    logical_w = int(geo.get("width", 0) or 0)
                    logical_h = int(geo.get("height", 0) or 0)
            except Exception:
                pass
            try:
                from PIL import Image
                with Image.open(screenshot_paths[0]) as im:
                    image_w, image_h = im.size
            except Exception:
                pass

            # Map image-space coordinates to screen logical coordinates.
            # This is more reliable than dividing by scale_factor only, because
            # screenshots may be resized before detection.
            if logical_w > 0 and logical_h > 0 and image_w > 0 and image_h > 0:
                sx = float(logical_w) / float(image_w)
                sy = float(logical_h) / float(image_h)
                lx, ly = int(raw_x * sx), int(raw_y * sy)
            else:
                lx, ly = int(raw_x), int(raw_y)
            ax, ay = lx + ox, ly + oy
            logger.info(
                "_apply_offset raw=(%d,%d) image=(%d,%d) logical=(%d,%d) mapped=(%d,%d) offset=(%d,%d) final=(%d,%d)",
                raw_x, raw_y, image_w, image_h, logical_w, logical_h, lx, ly, ox, oy, ax, ay
            )
            smooth_move_to(ax, ay)
            return ax, ay

        # Gather OCR word boxes
        ocr_word_boxes: list = []
        ocr_locate_result = None
        if search_target:
            try:
                from distr.core.agent.services.vision.locate import locate_text, _check_pytesseract
                if _check_pytesseract():
                    import pytesseract as _pyt
                    from PIL import Image as _PILImg
                    _img = _PILImg.open(screenshot_paths[0])
                    _raw = _pyt.image_to_data(_img, output_type=_pyt.Output.DICT)
                    for _i in range(len(_raw.get('text', []))):
                        _w = (_raw['text'][_i] or '').strip()
                        if not _w:
                            continue
                        _c = float(_raw['conf'][_i] or 0)
                        if _c < 0:
                            continue
                        ocr_word_boxes.append({
                            'text': _w, 'left': _raw['left'][_i], 'top': _raw['top'][_i],
                            'width': _raw['width'][_i], 'height': _raw['height'][_i],
                            'conf': _c,
                            'line_key': (_raw['block_num'][_i], _raw['par_num'][_i], _raw['line_num'][_i]),
                        })
                    ocr_locate_result = locate_text(screenshot_paths[0], search_target)
            except Exception as e:
                logger.debug("OCR word box gathering skipped: %s", e)

        # 1. OpenCV element detection
        element_match = None
        detected_elements: list = []
        try:
            from distr.core.agent.services.vision.element_detector import detect_elements, find_element_by_description
            detected_elements = detect_elements(screenshot_paths[0])
            if detected_elements:
                element_match = find_element_by_description(
                    detected_elements, search_target or original_text or prompt,
                    ocr_data=ocr_word_boxes or None,
                )
        except Exception as e:
            logger.debug("Element detection skipped: %s", e)

        if element_match:
            result_data = {
                "type": "action", "x": element_match["x"], "y": element_match["y"],
                "screen": screen_num,
                "action": _intent_to_action(vision_intent),
                "description": f"Found {element_match.get('kind', 'element')} ({element_match.get('region', '')}) via element detection",
                "summary": f"Located at ({element_match['x']}, {element_match['y']})",
            }
            if execute_action and vision_intent in ACTION_INTENTS and pyautogui:
                try:
                    _apply_offset(element_match["x"], element_match["y"], screen_num)
                except Exception:
                    from distr.core.agent.tools.input.mouse_utils import smooth_move_to
                    sf = 1.0
                    try:
                        from distr.core import screen_utils as _su
                        _c = getattr(_su, "_screen_info_cache", None) or {}
                        _sl = _c.get("screens", [])
                        if _sl and 1 <= screen_num <= len(_sl):
                            sf = _sl[screen_num - 1].get("scale_factor", 1.0) or 1.0
                    except Exception:
                        pass
                    smooth_move_to(int(element_match["x"] / sf), int(element_match["y"] / sf))
            d = result_data.get("description") or ""
            s = result_data.get("summary") or ""
            return " ".join(p for p in (d.strip(), s.strip()) if p).strip() or "Located an element on screen."

        # 2. pytesseract OCR direct match
        if ocr_locate_result:
            loc = ocr_locate_result
            result_data = {
                "type": "action", "x": loc["x"], "y": loc["y"], "screen": screen_num,
                "action": _intent_to_action(vision_intent),
                "description": f"Found '{loc['matched_text']}' via OCR",
                "summary": f"Located '{loc['matched_text']}' at ({loc['x']}, {loc['y']})",
            }
            if execute_action and vision_intent in ACTION_INTENTS and pyautogui:
                try:
                    _apply_offset(loc["x"], loc["y"], screen_num)
                except Exception:
                    from distr.core.agent.tools.input.mouse_utils import smooth_move_to
                    sf = 1.0
                    try:
                        from distr.core import screen_utils as _su
                        _c = getattr(_su, "_screen_info_cache", None) or {}
                        _sl = _c.get("screens", [])
                        if _sl and 1 <= screen_num <= len(_sl):
                            sf = _sl[screen_num - 1].get("scale_factor", 1.0) or 1.0
                    except Exception:
                        pass
                    smooth_move_to(int(loc["x"] / sf), int(loc["y"] / sf))
            d = result_data.get("description") or ""
            s = result_data.get("summary") or ""
            return " ".join(p for p in (d.strip(), s.strip()) if p).strip() or "Located text on screen via OCR."

        return None  # Fall through to vision LLM

    # ------------------------------------------------------------------
    # Vision LLM analysis
    # ------------------------------------------------------------------

    def _call_vision_llm(
        self,
        screenshot_paths: list[str],
        prompt: str,
        original_text: str,
        is_action_request: bool,
        capture_region: str,
        captured_screen_number: Optional[int],
        screenshot_to_screen_map: dict[str, int],
        image_screen_info: list[int],
        should_send_raw: bool,
        vision_provider: str,
        vision_model: str,
        vision_intent=None,
        execute_action: bool = True,
    ) -> str:
        """Convert screenshots to base64, call the vision API, and process the result."""
        vision_provider_key = (vision_provider or "").strip().lower()

        # Convert to base64
        base64_images: list[str] = []
        screen_info_list: list[int] = []
        for sp in screenshot_paths:
            if not os.path.exists(sp):
                continue
            b64 = image_to_base64(sp)
            if b64:
                base64_images.append(b64)
                sn = screenshot_to_screen_map.get(sp) or captured_screen_number or 1
                screen_info_list.append(sn)

        if not base64_images:
            return "Error: Failed to process screenshot images."

        # Build screen info text
        if capture_region == "all" and len(base64_images) > 1:
            sit = "\n\nIMPORTANT: Multiple screenshots provided.\n"
            for i, sn in enumerate(screen_info_list):
                sit += f"- Image {i+1}: Screen {sn}\n"
            sit += "\nSpecify which screen number coordinates are relative to."
        elif captured_screen_number is not None:
            sit = f"\n\nNOTE: Screenshot from screen {captured_screen_number}. Use screen {captured_screen_number}."
        else:
            sit = ""

        # Build enhanced prompt using intent-aware builder
        from distr.core.agent.tools.vision.vision_api import build_prompt_for_intent
        elements_ctx = ""
        ocr_ctx = ""
        if is_action_request:
            try:
                from distr.core.agent.services.vision.element_detector import detect_elements, build_elements_description
                elems = detect_elements(screenshot_paths[0]) if screenshot_paths else []
                if elems:
                    elements_ctx = "\n\n" + build_elements_description(elems) + "\n\nUse element IDs and coordinates above."
            except Exception:
                pass

            try:
                from distr.core.agent.services.vision.locate import build_ocr_context
                if screenshot_paths:
                    _t = build_ocr_context(screenshot_paths[0])
                    if _t:
                        ocr_ctx = "\n\n" + _t + "\n\nUse OCR text above for precise location."
            except Exception:
                pass

        if vision_intent is not None:
            enhanced, is_action_request = build_prompt_for_intent(
                vision_intent, prompt, sit, elements_ctx, ocr_ctx,
            )
        elif is_action_request:
            enhanced = build_action_prompt(prompt, sit, elements_ctx, ocr_ctx)
        else:
            enhanced = build_summary_prompt(prompt)

        # Call the API
        try:
            vision_result = self._call_vision_api(
                vision_provider_key, vision_model, base64_images, enhanced, is_action_request,
            )
            if vision_result.startswith("Error:"):
                return vision_result
            return self._process_vision_result(
                vision_result, is_action_request, capture_region,
                captured_screen_number, should_send_raw, vision_intent,
                execute_action=execute_action,
            )
        except Exception as e:
            logger.error(f"Vision processing error: {e}", exc_info=True)
            return f"Error processing screenshots: {e}"

    @staticmethod
    def _is_retriable_vision_error(error_text: str) -> bool:
        """Return True when a vision-provider error should trigger fallback."""
        text = (error_text or "").lower()
        if not text:
            return False
        retry_markers = (
            "rate limit", "429", "quota", "insufficient", "credit", "billing",
            "timeout", "timed out", "connection", "network", "temporarily",
            "overloaded", "503", "502", "500", "unavailable", "capacity",
        )
        return any(marker in text for marker in retry_markers)


    # ------------------------------------------------------------------
    # Call vision LLM API (supports all providers)
    # ------------------------------------------------------------------

    def _call_vision_api(
        self,
        vision_provider_key: str,
        vision_model: str,
        base64_images: list[str],
        enhanced_prompt: str,
        is_action_request: bool,
    ) -> str:
        """Call the vision API for any supported provider.

        Returns the raw text from the vision model, or an ``Error: ...`` string.
        """
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()

        # Build OpenAI-compatible content items (used by openai, openrouter, kilocode, groq)
        content_items: list[dict] = [{"type": "text", "text": enhanced_prompt}]
        for b64 in base64_images:
            content_items.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/webp;base64,{b64}"},
            })
        vision_messages = [{"role": "user", "content": content_items}]

        # --- OpenAI ---
        if vision_provider_key == "openai":
            try:
                return call_openai_vision(base64_images, enhanced_prompt, vision_model, is_action_request)
            except ValueError as ve:
                return f"Error: {ve}"
            except RuntimeError as re_:
                return f"Error: {re_}"
            except Exception as api_err:
                err = str(api_err)
                if "Connection" in err or "timeout" in err.lower():
                    return "Error: Connection issue with OpenAI API. Check your internet."
                if "rate_limit" in err.lower() or "429" in err:
                    return "Error: OpenAI API rate limit exceeded. Wait and try again."
                if "401" in err or "unauthorized" in err.lower():
                    return "Error: Invalid OpenAI API key."
                return f"Error calling vision API: {err}"

        # --- Ollama ---
        if vision_provider_key == "ollama":
            try:
                import requests as _requests
                ollama_url = settings.get('ollama_url', 'http://localhost:11434/')
                if not ollama_url.endswith('/'):
                    ollama_url += '/'
                if not vision_model:
                    vision_model = "llava"
                logger.info("ScreenshotAnalyzer: Calling Ollama vision API with model: %s", vision_model)
                resp = _requests.post(
                    f"{ollama_url}api/chat",
                    json={
                        "model": vision_model,
                        "messages": [{
                            "role": "user",
                            "content": enhanced_prompt,
                            "images": base64_images,
                        }],
                        "stream": False,
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = (data.get('message') or {}).get('content', '')
                    if content:
                        logger.info("ScreenshotAnalyzer: Ollama vision complete (%d chars)", len(content))
                        return content
                    return "Error: Ollama vision API returned empty response."
                return f"Error: Ollama vision API failed (status {resp.status_code}): {resp.text[:200]}"
            except Exception as e:
                logger.error("Error calling Ollama vision API: %s", e, exc_info=True)
                return f"Error calling Ollama vision API: {e}"

        # --- OpenRouter / KiloCode / Groq (OpenAI-compatible) ---
        if vision_provider_key in ("openrouter", "kilocode", "groq", "gemini"):
            try:
                import requests as _requests
                if vision_provider_key == "openrouter":
                    api_key = settings.get('openrouter_key', '')
                    base_url = "https://openrouter.ai/api/v1/chat/completions"
                elif vision_provider_key == "kilocode":
                    api_key = settings.get('kilocode_key', '')
                    base_url = (settings.get('kilocode_url') or "https://api.kilo.ai/api/gateway").rstrip('/') + "/chat/completions"
                elif vision_provider_key == "gemini":
                    api_key = settings.get('gemini_key', '')
                    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                else:  # groq
                    api_key = settings.get('groq_key', '')
                    base_url = "https://api.groq.com/openai/v1/chat/completions"

                if not api_key:
                    return f"Error: {vision_provider_key.title()} API key not configured. Please set it in settings."

                logger.info("ScreenshotAnalyzer: Calling %s vision API with model: %s", vision_provider_key, vision_model)
                resp = _requests.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": vision_model or "openai/gpt-4o",
                        "messages": vision_messages,
                        "max_tokens": 2000,
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get('choices', [])
                    if choices:
                        content = choices[0].get('message', {}).get('content', '')
                        if content:
                            logger.info("ScreenshotAnalyzer: %s vision complete (%d chars)", vision_provider_key, len(content))
                            return content
                    return f"Error: {vision_provider_key.title()} vision API returned empty response."
                return f"Error: {vision_provider_key.title()} vision API failed (status {resp.status_code}): {resp.text[:200]}"
            except Exception as e:
                logger.error("Error calling %s vision API: %s", vision_provider_key, e, exc_info=True)
                return f"Error calling {vision_provider_key.title()} vision API: {e}"

        # --- Anthropic ---
        if vision_provider_key == "anthropic":
            try:
                from anthropic import Anthropic
                api_key = settings.get('anthropic_key', '')
                if not api_key:
                    return "Error: Anthropic API key not configured. Please set it in settings."
                if not vision_model:
                    vision_model = "claude-3-5-sonnet-20241022"
                client = Anthropic(api_key=api_key)
                # Anthropic uses a different image format
                content_parts: list[dict] = [{"type": "text", "text": enhanced_prompt}]
                for b64 in base64_images:
                    content_parts.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/webp", "data": b64},
                    })
                logger.info("ScreenshotAnalyzer: Calling Anthropic vision API with model: %s", vision_model)
                resp = client.messages.create(
                    model=vision_model,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": content_parts}],
                )
                if resp and resp.content:
                    content = resp.content[0].text
                    logger.info("ScreenshotAnalyzer: Anthropic vision complete (%d chars)", len(content))
                    return content
                return "Error: Anthropic vision API returned empty response."
            except ImportError:
                return "Error: Anthropic library not available. Install it: pip install anthropic"
            except Exception as e:
                logger.error("Error calling Anthropic vision API: %s", e, exc_info=True)
                return f"Error calling Anthropic vision API: {e}"

        return f"Error: Vision provider '{vision_provider_key}' not supported. Supported: OpenAI, Ollama, OpenRouter, KiloCode, Groq, Anthropic, Google Gemini."

    # ------------------------------------------------------------------
    # Process vision LLM JSON result
    # ------------------------------------------------------------------

    def _process_vision_result(
        self,
        vision_result: str,
        is_action_request: bool,
        capture_region: str,
        captured_screen_number: Optional[int],
        should_send_raw: bool,
        vision_intent=None,
        execute_action: bool = True,
    ) -> str:
        """Parse the vision LLM response and execute actions (mouse move, click, etc.) if needed."""
        from distr.core.agent.services.vision.intent_classifier import VisionIntent, ACTION_INTENTS
        try:
            raw = vision_result.strip()
            if raw.startswith("```"):
                raw = re.sub(r'^```(?:json)?\s*', '', raw)
                raw = re.sub(r'\s*```$', '', raw)
            result_data = json.loads(raw)

            result_type = result_data.get('type', '')

            # Hallucination guard
            if result_type == 'action' and 'x' in result_data and 'y' in result_data:
                try:
                    _vx, _vy = int(result_data['x']), int(result_data['y'])
                    _img_w, _img_h = 1920, 1080
                    _cx, _cy = _img_w // 2, _img_h // 2
                    _near_top_center = abs(_vx - _cx) < _img_w * 0.08 and _vy < _img_h * 0.12
                    _near_dead_center = abs(_vx - _cx) < _img_w * 0.06 and abs(_vy - _cy) < _img_h * 0.06
                    _desc = (result_data.get('description') or '').lower()
                    _vague = any(w in _desc for w in [
                        'center of the screen', 'middle of the screen', 'top of the screen',
                        'approximate location', 'estimated position', 'general area',
                        'could not find', 'not visible', 'unable to locate',
                    ])
                    if (_near_top_center or _near_dead_center) and _vague:
                        logger.warning("Vision LLM suspicious coords (%d,%d) with vague desc — treating as not found", _vx, _vy)
                        result_data['type'] = 'target_not_found'
                        result_data['summary'] = result_data.get('summary', '') or "Could not confidently locate the target."
                        result_type = 'target_not_found'
                except (ValueError, TypeError):
                    pass

            # Target not found
            if result_type == 'target_not_found':
                summary = result_data.get('summary', 'The requested element is not visible.')
                try:
                    from distr.core.agent.services.computer_use_context import record_candidate_target
                    record_candidate_target(
                        source="screenshot_analyzer",
                        status="not_found",
                        description=summary,
                    )
                except Exception:
                    pass
                result = (
                    f"TARGET NOT FOUND: {summary}\n\n"
                    "[ACTION REQUIRED] Do NOT call mouse_movement to move to screen center. "
                    "Ask the user to bring the target into view."
                )

            # Drag-and-drop response
            elif result_type == 'drag' and all(k in result_data for k in ('start_x', 'start_y', 'end_x', 'end_y')):
                result = self._execute_drag_drop(result_data, capture_region, captured_screen_number)

            # Form interaction response (multi-action)
            elif result_type == 'form_action' and 'actions' in result_data:
                result = self._execute_form_actions(result_data, capture_region, captured_screen_number)

            # Action with coordinates — move mouse / click / etc.
            elif result_type == 'action' and 'x' in result_data and 'y' in result_data:
                action = result_data.get('action', 'click')
                raw_x, raw_y = int(result_data['x']), int(result_data['y'])
                screen = result_data.get('screen', captured_screen_number or 1)
                desc = result_data.get('description', 'target location')
                confidence = result_data.get("confidence")
                try:
                    from distr.core.agent.services.computer_use_context import record_candidate_target
                    record_candidate_target(
                        source="screenshot_analyzer",
                        x=raw_x,
                        y=raw_y,
                        screen=int(screen) if screen else 1,
                        description=desc,
                        status="found",
                    )
                except Exception:
                    pass

                # Safety: do not click vague/low-confidence targets.
                ambiguous, ambiguity_reason = _is_ambiguous_click_target(desc, confidence)
                click_like_actions = {"click", "double_click", "right_click"}
                if execute_action and action in click_like_actions and ambiguous:
                    result = (
                        f"TARGET AMBIGUOUS: Found '{desc}' at ({raw_x}, {raw_y}) on screen {int(screen)} "
                        f"but did not click because {ambiguity_reason}. "
                        "Please refine the target text or ask me to locate-only first."
                    )
                    execute_action = False
                elif execute_action:
                    result = self._execute_mouse_move(result_data, capture_region, captured_screen_number)
                else:
                    result = (
                        f"Located {desc} at coordinates ({raw_x}, {raw_y}) on screen {int(screen)}. "
                        "No action executed because execute_action is false."
                    )
                # Execute the specific action after moving
                if execute_action and vision_intent in ACTION_INTENTS and pyautogui:
                    try:
                        if action == 'double_click':
                            pyautogui.doubleClick()
                            result = result.replace("Moved mouse to", "Double-clicked")
                        elif action == 'right_click':
                            pyautogui.rightClick()
                            result = result.replace("Moved mouse to", "Right-clicked")
                        elif action == 'click' and vision_intent == VisionIntent.CLICK_ELEMENT:
                            pyautogui.click()
                            result = result.replace("Moved mouse to", "Clicked")
                        elif action in ('scroll_down', 'scroll_up'):
                            clicks = -5 if action == 'scroll_down' else 5
                            pyautogui.scroll(clicks)
                            result += f"\nScrolled {'down' if clicks < 0 else 'up'}"
                    except Exception as e:
                        logger.warning("Post-move action '%s' failed: %s", action, e)

                # Always append a structured pointer payload for traceability.
                pointer_payload = {
                    "target": desc,
                    "action": action,
                    "raw_x": raw_x,
                    "raw_y": raw_y,
                    "screen": int(screen) if screen else 1,
                    "executed": bool(execute_action),
                    "confidence": confidence,
                    "ambiguous": bool(ambiguous),
                    "ambiguity_reason": ambiguity_reason or "",
                }
                logger.debug("screenshot_analyzer pointer_payload: %s", pointer_payload)

            # Informational reports (error, notification, state, count, app, comparison, multi-screen)
            elif result_type in ('error_report', 'notification_report', 'state_report',
                                 'count_report', 'app_report', 'comparison_report', 'multi_screen_report'):
                result = self._format_info_report(result_data)

            # Summary
            else:
                summary_text = result_data.get('summary', result_data.get('description', vision_result))
                # If this was supposed to be an action request but the vision model returned
                # a summary instead, flag it clearly so the agent doesn't just parrot the summary
                if is_action_request and vision_intent is not None:
                    from distr.core.agent.services.vision.intent_classifier import ACTION_INTENTS, LOCATE_INTENTS
                    if vision_intent in ACTION_INTENTS or vision_intent in LOCATE_INTENTS:
                        result = (
                            f"TARGET NOT FOUND: The vision model could not provide coordinates.\n"
                            f"Context: {summary_text}\n\n"
                            "[ACTION REQUIRED] Do NOT call mouse_movement to move to screen center. "
                            "Ask the user to scroll or bring the target into view, then try again."
                        )
                    else:
                        result = summary_text
                else:
                    result = summary_text

        except (json.JSONDecodeError, ValueError):
            logger.warning("Vision response is not valid JSON, treating as summary")
            # If this was an action request, the model refused to give coordinates
            if is_action_request and vision_intent is not None:
                from distr.core.agent.services.vision.intent_classifier import ACTION_INTENTS, LOCATE_INTENTS
                if vision_intent in ACTION_INTENTS or vision_intent in LOCATE_INTENTS:
                    result = (
                        f"TARGET NOT FOUND: Vision model did not return coordinates.\n"
                        f"Raw response: {vision_result[:300]}\n\n"
                        "[ACTION REQUIRED] Do NOT call mouse_movement to move to screen center. "
                        "Ask the user to scroll or bring the target into view, then try again."
                    )
                else:
                    result = vision_result
            else:
                result = vision_result

        if should_send_raw:
            result += (
                "\n\n[NOTE: Screenshot captured and will be sent directly to you via Telegram. "
                "No need to ask where to send it.]"
            )

        return result

    # ------------------------------------------------------------------
    # Drag-and-drop execution
    # ------------------------------------------------------------------

    @staticmethod
    def _execute_drag_drop(
        result_data: dict,
        capture_region: str,
        captured_screen_number: Optional[int],
    ) -> str:
        """Execute a drag-and-drop from vision LLM coordinates."""
        try:
            sx, sy = int(result_data['start_x']), int(result_data['start_y'])
            ex, ey = int(result_data['end_x']), int(result_data['end_y'])
            screen = int(result_data.get('screen', captured_screen_number or 1))
            if screen < 1:
                screen = 1

            scale_factor = 1.0
            offset_x, offset_y = 0, 0
            try:
                from distr.core import screen_utils as _su
                _cache = getattr(_su, "_screen_info_cache", None) or {}
                _sl = _cache.get("screens", [])
                if _sl and 1 <= screen <= len(_sl):
                    info = _sl[screen - 1]
                    scale_factor = info.get("scale_factor", 1.0) or 1.0
                    geo = info.get("geometry", {})
                    offset_x, offset_y = geo.get("x", 0), geo.get("y", 0)
            except Exception:
                pass

            if scale_factor > 1.0:
                sx, sy = int(sx / scale_factor), int(sy / scale_factor)
                ex, ey = int(ex / scale_factor), int(ey / scale_factor)
            sx += offset_x
            sy += offset_y
            ex += offset_x
            ey += offset_y

            if pyautogui:
                from distr.core.agent.tools.input.mouse_utils import smooth_move_to
                smooth_move_to(sx, sy)
                import time
                time.sleep(0.1)
                pyautogui.mouseDown()
                time.sleep(0.05)
                smooth_move_to(ex, ey)
                time.sleep(0.05)
                pyautogui.mouseUp()
                desc = result_data.get('description', 'element')
                return f"Dragged {desc} from ({sx},{sy}) to ({ex},{ey}) on screen {screen}"
            else:
                return f"Drag coordinates: from ({sx},{sy}) to ({ex},{ey}) on screen {screen} (pyautogui not available)"
        except Exception as e:
            return f"Error executing drag-and-drop: {e}"

    # ------------------------------------------------------------------
    # Form interaction execution
    # ------------------------------------------------------------------

    @staticmethod
    def _execute_form_actions(
        result_data: dict,
        capture_region: str,
        captured_screen_number: Optional[int],
    ) -> str:
        """Execute a sequence of form actions from vision LLM response."""
        actions = result_data.get('actions', [])
        if not actions:
            return result_data.get('summary', 'No form actions to execute.')

        screen = int(result_data.get('screen', captured_screen_number or 1))
        scale_factor = 1.0
        offset_x, offset_y = 0, 0
        try:
            from distr.core import screen_utils as _su
            _cache = getattr(_su, "_screen_info_cache", None) or {}
            _sl = _cache.get("screens", [])
            if _sl and 1 <= screen <= len(_sl):
                info = _sl[screen - 1]
                scale_factor = info.get("scale_factor", 1.0) or 1.0
                geo = info.get("geometry", {})
                offset_x, offset_y = geo.get("x", 0), geo.get("y", 0)
        except Exception:
            pass

        results = []
        for act in actions:
            try:
                action_type = act.get('action', 'click')
                x, y = int(act.get('x', 0)), int(act.get('y', 0))
                if scale_factor > 1.0:
                    x, y = int(x / scale_factor), int(y / scale_factor)
                x += offset_x
                y += offset_y
                desc = act.get('description', 'element')

                if pyautogui:
                    from distr.core.agent.tools.input.mouse_utils import smooth_move_to
                    smooth_move_to(x, y)
                    import time
                    time.sleep(0.1)

                    if action_type == 'click':
                        pyautogui.click()
                        results.append(f"Clicked {desc} at ({x},{y})")
                    elif action_type == 'type':
                        pyautogui.click()
                        time.sleep(0.05)
                        text_to_type = act.get('text', '')
                        if text_to_type:
                            pyautogui.typewrite(text_to_type, interval=0.02)
                        results.append(f"Typed '{text_to_type}' in {desc} at ({x},{y})")
                    elif action_type == 'clear':
                        pyautogui.click()
                        pyautogui.hotkey('command' if platform.system() == 'Darwin' else 'ctrl', 'a')
                        pyautogui.press('delete')
                        results.append(f"Cleared {desc} at ({x},{y})")
                    elif action_type in ('check', 'uncheck', 'select'):
                        pyautogui.click()
                        results.append(f"{action_type.capitalize()}ed {desc} at ({x},{y})")
                    else:
                        pyautogui.click()
                        results.append(f"{action_type} on {desc} at ({x},{y})")
                else:
                    results.append(f"Would {action_type} {desc} at ({x},{y}) (pyautogui not available)")
            except Exception as e:
                results.append(f"Error on {act.get('description', 'action')}: {e}")

        return "Form actions completed:\n" + "\n".join(f"  • {r}" for r in results)

    # ------------------------------------------------------------------
    # Format informational reports
    # ------------------------------------------------------------------

    @staticmethod
    def _format_info_report(result_data: dict) -> str:
        """Format informational vision reports into readable text."""
        report_type = result_data.get('type', '')
        summary = result_data.get('summary', '')
        description = result_data.get('description', '')

        if report_type == 'error_report':
            error_text = result_data.get('error_text', '')
            error_type = result_data.get('error_type', 'none')
            if error_type == 'none':
                return summary or "No errors visible on screen."
            parts = []
            if error_type:
                parts.append(f"[{error_type.upper()}]")
            if error_text:
                parts.append(f'"{error_text}"')
            if summary:
                parts.append(f"\n{summary}")
            return " ".join(parts)

        if report_type == 'notification_report':
            notif_text = result_data.get('notification_text', '')
            notif_type = result_data.get('notification_type', 'none')
            if notif_type == 'none':
                return summary or "No notifications visible."
            parts = [f"[{notif_type.upper()}]"]
            if notif_text:
                parts.append(f'"{notif_text}"')
            if summary:
                parts.append(f"\n{summary}")
            return " ".join(parts)

        if report_type == 'state_report':
            element = result_data.get('element', 'element')
            state = result_data.get('state', 'unknown')
            confidence = result_data.get('confidence', '')
            return f"{element}: {state}" + (f" (confidence: {confidence})" if confidence else "") + (f"\n{summary}" if summary else "")

        if report_type == 'count_report':
            element_type = result_data.get('element_type', 'elements')
            count = result_data.get('count', '?')
            details = result_data.get('details', [])
            result = f"Count: {count} {element_type}"
            if details:
                result += "\n" + "\n".join(f"  • {d}" for d in details[:20])
            if summary:
                result += f"\n{summary}"
            return result

        if report_type == 'app_report':
            active = result_data.get('active_app', '')
            title = result_data.get('active_window_title', '')
            visible = result_data.get('visible_apps', [])
            parts = []
            if active:
                parts.append(f"Active app: {active}")
            if title:
                parts.append(f"Window: {title}")
            if visible:
                parts.append(f"Visible: {', '.join(visible)}")
            if summary:
                parts.append(summary)
            return "\n".join(parts)

        if report_type == 'comparison_report':
            changes = result_data.get('changes', [])
            if changes:
                return "Changes detected:\n" + "\n".join(f"  • {c}" for c in changes) + (f"\n{summary}" if summary else "")
            return summary or "No changes detected."

        if report_type == 'multi_screen_report':
            screens = result_data.get('screens', [])
            if screens:
                parts = []
                for s in screens:
                    sn = s.get('screen', '?')
                    desc = s.get('description', '')
                    app = s.get('active_app', '')
                    parts.append(f"Screen {sn}: {app}" + (f" — {desc}" if desc else ""))
                return "\n".join(parts) + (f"\n\n{summary}" if summary else "")
            return summary or description or "No screen information available."

        # Generic fallback
        return summary or description or str(result_data)

    # ------------------------------------------------------------------
    # Mouse movement from vision coordinates
    # ------------------------------------------------------------------

    @staticmethod
    def _execute_mouse_move(
        result_data: dict,
        capture_region: str,
        captured_screen_number: Optional[int],
    ) -> str:
        """Move the mouse to coordinates returned by the vision LLM."""
        try:
            x, y = int(result_data['x']), int(result_data['y'])
            screen = result_data.get('screen')
            if screen is None:
                if captured_screen_number is not None:
                    screen = captured_screen_number
                elif capture_region.startswith("screen_"):
                    try:
                        screen = int(capture_region.split("_")[1])
                    except (ValueError, IndexError):
                        screen = 1
                else:
                    screen = 1
            else:
                screen = int(screen)
            if screen < 1:
                screen = 1

            # Apply screen offset from the shared cache (main process) and map
            # coordinates into logical screen space if screenshot-space dimensions
            # are provided by the model result.
            ox, oy = 0, 0
            logical_w, logical_h = 0, 0
            try:
                from distr.core import screen_utils as _su
                _cache = getattr(_su, "_screen_info_cache", None) or {}
                _sl = _cache.get("screens", [])
                if _sl and 1 <= screen <= len(_sl):
                    info = _sl[screen - 1]
                    geo = info.get("geometry", {})
                    ox, oy = geo.get("x", 0), geo.get("y", 0)
                    logical_w = int(geo.get("width", 0) or 0)
                    logical_h = int(geo.get("height", 0) or 0)
            except Exception:
                pass

            image_w = int(result_data.get("image_width", 0) or 0)
            image_h = int(result_data.get("image_height", 0) or 0)
            if logical_w > 0 and logical_h > 0 and image_w > 0 and image_h > 0:
                sx = float(logical_w) / float(image_w)
                sy = float(logical_h) / float(image_h)
                x, y = int(x * sx), int(y * sy)
            x += ox
            y += oy

            logger.info(
                "_execute_mouse_move: raw=(%d,%d) image=(%d,%d) logical=(%d,%d) offset=(%d,%d) final=(%d,%d) screen=%d",
                int(result_data['x']), int(result_data['y']), image_w, image_h, logical_w, logical_h, ox, oy, x, y, screen
            )

            from distr.core.agent.tools.input.mouse_utils import smooth_move_to
            smooth_move_to(x, y)
            desc = result_data.get('description', 'target location')
            try:
                from distr.core.agent.services.computer_use_context import record_action
                record_action(
                    "move_mouse",
                    "success",
                    {
                        "x": x,
                        "y": y,
                        "screen": screen,
                        "description": desc,
                        "source": "screenshot_analyzer",
                    },
                )
            except Exception:
                pass
            return f"Moved mouse to {desc} at coordinates ({x}, {y}) on screen {screen}"

        except ImportError:
            return f"Error: pyautogui not available. Coordinates: ({result_data.get('x')}, {result_data.get('y')})"
        except Exception as e:
            return f"Error moving mouse: {e}. Coordinates: ({result_data.get('x')}, {result_data.get('y')})"


    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def _run(self, prompt: str = "", region: Optional[str] = None, **kwargs) -> str:
        """
        Capture screenshot(s) and analyze them.

        Args:
            prompt: The question or instruction about what to analyze.
            region: Optional region to capture ('full', 'window', 'selection', 'all').

        Returns:
            JSON string with structured data: either action data (coordinates) or summary.
        """
        # ── Fast path: direct send (no analysis) ──
        if kwargs.get('direct_send', False):
            return self._handle_direct_send(prompt, **kwargs)

        # ── Detect capture-only mode ──
        # When the user wants the screenshot FILE to route to other tools,
        # we capture and return the file path with [ACTION REQUIRED] — skip vision LLM.
        capture_only = kwargs.get('capture_only', False)
        if not capture_only:
            # Resolve original_text early so we can check capture-only patterns
            _early_text = (
                kwargs.get('last_user_message', '') or
                kwargs.get('text', '') or
                kwargs.get('transcription', '') or
                kwargs.get('original_text', '') or
                ''
            )
            combined_text = (f"{_early_text} {prompt or ''}").lower()
            if any(p in combined_text for p in CAPTURE_ONLY_PATTERNS):
                # Guard: don't enter capture-only mode for action-oriented requests.
                # Example: "find X on my screen and move my mouse there" contains
                # "and move" but should still run full vision locate + action.
                has_action_intent = any(k in combined_text for k in _ACTION_KEYWORDS)
                if has_action_intent:
                    logger.info(
                        "📸 Capture-only pattern matched, but action intent detected; "
                        "continuing with full vision analysis"
                    )
                else:
                    capture_only = True

        # ── Capture-only mode: capture + return file path artifact ──
        if capture_only:
            # Resolve original_text for capture-only handler
            original_text = (
                kwargs.get('last_user_message', '') or
                kwargs.get('text', '') or
                kwargs.get('transcription', '') or
                kwargs.get('original_text', '') or
                ''
            )
            return self._handle_capture_only(prompt, original_text, **kwargs)

        # ── Resolve original text ──
        original_text = (
            kwargs.get('last_user_message', '') or
            kwargs.get('text', '') or
            kwargs.get('transcription', '') or
            kwargs.get('original_text', '')
        )
        # Multi-target pointer sequencing:
        # "move to A then move to B" should execute deterministically in order.
        if not kwargs.get("_is_pointer_substep", False):
            pointer_steps = _split_pointer_targets(original_text or prompt or "")
            if pointer_steps:
                logger.info("ScreenshotAnalyzer: split multi-target pointer request into %d steps", len(pointer_steps))
                step_results = []
                for idx, step in enumerate(pointer_steps, start=1):
                    sub_result = self._run(
                        prompt=step,
                        region=region,
                        last_user_message=step,
                        original_text=step,
                        execute_action=kwargs.get("execute_action", True),
                        _is_pointer_substep=True,
                    )
                    step_results.append(f"Step {idx}: {sub_result}")
                return "\n".join(step_results)
        original_text_for_pattern = original_text
        prompt = _extract_prompt(prompt, original_text)

        # ── Vision intent classification ──
        from distr.core.agent.services.vision.intent_classifier import classify_vision_intent, VisionIntent, ACTION_INTENTS, LOCATE_INTENTS, INFO_INTENTS
        vision_intent = classify_vision_intent(original_text or prompt)
        if vision_intent == VisionIntent.MULTI_STEP:
            return (
                "This looks like a complex multi-step task. Use the Workflows page "
                "to break it down into ordered steps."
            )

        # ── Detect action vs informational request ──
        text_lower = (original_text or prompt).lower()
        is_action_request = (
            vision_intent in ACTION_INTENTS
            or vision_intent in LOCATE_INTENTS
            or any(kw in text_lower for kw in _ACTION_KEYWORDS)
        )
        execute_action = resolve_execute_action(
            kwargs.get('execute_action'),
            vision_intent,
            LOCATE_INTENTS,
        )
        # If the user explicitly asked to physically move/click/hover, force action
        # execution even when intent classifier lands on a locate-style intent.
        explicit_pointer_action = bool(re.search(
            r"\b(move|hover|click|double[- ]?click|right[- ]?click|tap|press)\b",
            text_lower,
            re.IGNORECASE,
        ))
        if explicit_pointer_action and not execute_action:
            execute_action = True
            logger.info("ScreenshotAnalyzer: forcing execute_action=True from explicit pointer-action text")
        logger.info(
            "ScreenshotAnalyzer execute_action=%s (intent=%s)",
            execute_action,
            getattr(vision_intent, "value", str(vision_intent)),
        )

        # ── Resolve capture region ──
        screen_number = _extract_screen_number(text_lower)
        capture_region = _resolve_capture_region(region, screen_number, text_lower)

        # Safety: never use "full" unless explicitly requested
        if capture_region == "full":
            is_all = any(w in text_lower for w in ['all screens', 'all monitors', 'every screen'])
            if not is_all and screen_number is None:
                capture_region = "current_mouse_screen"

        logger.info(f"📸 Final capture_region={capture_region}")

        # ── Vision model check ──
        from distr.core.settings import load_settings_from_db
        from distr.core.llm_factory import resolve_computer_use_config
        settings = load_settings_from_db()
        default_vision_provider, default_vision_model = resolve_vision_llm_config(settings)
        vision_provider, vision_model = default_vision_provider, default_vision_model

        # For action/locate intents, prefer computer_use model if configured
        # (better at coordinate-finding), then fall back to vision LLM.
        from distr.core.agent.services.vision.intent_classifier import LOCATE_INTENTS
        using_computer_use_primary = False
        if is_action_request or vision_intent in LOCATE_INTENTS:
            cu_provider, cu_model = resolve_computer_use_config(settings)
            if cu_provider and cu_model:
                using_computer_use_primary = True
                logger.info(
                    "ScreenshotAnalyzer: Action/locate intent — using computer_use "
                    "model %s/%s instead of vision %s/%s",
                    cu_provider, cu_model, default_vision_provider, default_vision_model,
                )
                vision_provider, vision_model = cu_provider, cu_model

        err = self._check_vision_support(vision_provider, vision_model, **kwargs)
        if err:
            return err

        # ── Telegram flags ──
        is_telegram = _check_telegram_request()
        combined = f"{original_text_for_pattern or ''} {prompt or ''}".lower()
        original_lower = (original_text_for_pattern or "").lower()
        prompt_lower = (prompt or "").lower()
        wants_telegram = (
            any(p in combined for p in TELEGRAM_SEND_PATTERNS) or
            any(p in original_lower for p in TELEGRAM_SEND_PATTERNS) or
            any(p in prompt_lower for p in TELEGRAM_SEND_PATTERNS)
        )
        should_send_raw = (is_telegram or wants_telegram) and (
            any(p in combined for p in SEND_PATTERNS) or
            any(p in original_lower for p in SEND_PATTERNS) or
            any(p in prompt_lower for p in SEND_PATTERNS) or
            wants_telegram
        )

        # ── Check for uploaded Telegram image ──
        screenshot_paths: list[str] = []
        has_uploaded_image = False
        if is_telegram:
            uploaded = getattr(threading.current_thread(), 'telegram_uploaded_image', None)
            if uploaded and os.path.exists(uploaded):
                screenshot_paths = [uploaded]
                has_uploaded_image = True
                threading.current_thread().telegram_uploaded_image = None

        # ── Capture screenshots ──
        screenshot_to_screen_map: dict[str, int] = {}
        captured_screen_number: Optional[int] = None

        try:
            if has_uploaded_image:
                logger.info(f"📸 Using uploaded Telegram image: {screenshot_paths[0]}")
            else:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    screenshot_paths, screenshot_to_screen_map, captured_screen_number = \
                        self._capture_screenshots(capture_region, tmp_dir)

                    if not screenshot_paths:
                        return "Error: Failed to capture screenshots."

                    # Persist for Telegram
                    if is_telegram and screenshot_paths:
                        pp = _persist_screenshot(screenshot_paths[0], "analyzed_screenshot")
                        if pp:
                            _store_for_telegram(pp, raw=should_send_raw)

                    # Persist ALL screenshots before temp dir is deleted
                    persistent_paths = []
                    for idx, sp in enumerate(screenshot_paths):
                        pp = _persist_screenshot(sp, f"screenshot_{idx}")
                        if pp:
                            persistent_paths.append(pp)
                    if persistent_paths:
                        # Update screen map for persistent paths
                        new_map: dict[str, int] = {}
                        for old, new in zip(screenshot_paths, persistent_paths):
                            if old in screenshot_to_screen_map:
                                new_map[new] = screenshot_to_screen_map[old]
                        screenshot_paths = persistent_paths
                        screenshot_to_screen_map = new_map

            # ── Direct send shortcut (Telegram raw screenshot) ──
            if should_send_raw and screenshot_paths:
                pp = screenshot_paths[0]
                if wants_telegram and not is_telegram and os.path.exists(pp):
                    return (
                        f"Result: {pp}\n"
                        f'[ACTION REQUIRED: Call send_file_to_telegram with file_path="{pp}" '
                        f"to send the screenshot to Telegram]"
                    )
                if is_telegram and os.path.exists(pp):
                    _store_for_telegram(pp, raw=True)
                    return "Done"

            # ── Fast locate (element detection / OCR) ──
            fast_result = self._try_fast_locate(
                vision_intent, screenshot_paths, original_text, prompt,
                captured_screen_number, screenshot_to_screen_map,
                execute_action=bool(execute_action),
            )
            if fast_result is not None:
                return fast_result

            # ── Build image screen info ──
            image_screen_info = []
            for sp in screenshot_paths:
                sn = screenshot_to_screen_map.get(sp) or captured_screen_number or 1
                image_screen_info.append(sn)

            try:
                from distr.core.agent.services.computer_use_context import record_observation
                record_observation(
                    source="screenshot_analyzer",
                    details={
                        "capture_region": capture_region,
                        "screenshot_count": len(screenshot_paths),
                        "screen_numbers": image_screen_info,
                        "provider": vision_provider,
                        "model": vision_model,
                        "is_action_request": is_action_request,
                        "execute_action": bool(execute_action),
                        "prompt": (prompt or "")[:500],
                    },
                )
            except Exception:
                pass

            # ── Call vision LLM (primary path) ──
            primary_result = self._call_vision_llm(
                screenshot_paths=screenshot_paths,
                prompt=prompt,
                original_text=original_text,
                is_action_request=is_action_request,
                capture_region=capture_region,
                captured_screen_number=captured_screen_number,
                screenshot_to_screen_map=screenshot_to_screen_map,
                image_screen_info=image_screen_info,
                should_send_raw=should_send_raw,
                vision_provider=vision_provider,
                vision_model=vision_model,
                vision_intent=vision_intent,
                execute_action=bool(execute_action),
            )
            if not (isinstance(primary_result, str) and primary_result.startswith("Error:")):
                return primary_result

            # Fallback strategy: for actionable requests, if Computer Use fails due to
            # provider/model limits (quota, rate limit, timeout, outage), retry with
            # the standard vision model automatically.
            if (
                using_computer_use_primary
                and (default_vision_provider or "").strip()
                and (default_vision_model or "").strip()
                and self._is_retriable_vision_error(primary_result)
            ):
                logger.warning(
                    "ScreenshotAnalyzer: computer_use failed (%s). Falling back to vision %s/%s",
                    primary_result[:180],
                    default_vision_provider,
                    default_vision_model,
                )
                fallback_result = self._call_vision_llm(
                    screenshot_paths=screenshot_paths,
                    prompt=prompt,
                    original_text=original_text,
                    is_action_request=is_action_request,
                    capture_region=capture_region,
                    captured_screen_number=captured_screen_number,
                    screenshot_to_screen_map=screenshot_to_screen_map,
                    image_screen_info=image_screen_info,
                    should_send_raw=should_send_raw,
                    vision_provider=default_vision_provider,
                    vision_model=default_vision_model,
                    vision_intent=vision_intent,
                    execute_action=bool(execute_action),
                )
                if not (isinstance(fallback_result, str) and fallback_result.startswith("Error:")):
                    return fallback_result
                return (
                    f"{primary_result}\n\nFallback with vision model also failed:\n{fallback_result}"
                )

            return primary_result

        except Exception as e:
            logger.error(f"ScreenshotAnalyzer error: {e}", exc_info=True)
            return f"Error analyzing screenshot: {e}"

    async def _arun(self, prompt: str = "", region: Optional[str] = None, **kwargs) -> str:
        """Async version — runs sync _run in executor to avoid blocking the event loop."""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self._run(prompt=prompt, region=region, **kwargs)
        )
