"""
Vision LLM API Utilities

Functions for resolving vision LLM configuration and calling vision APIs.
Extracted from screenshot_analyzer.py for better organisation.

Includes specialised prompt builders for 20 vision use cases.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_vision_llm_config(settings: dict) -> tuple[str, str]:
    """Resolve vision provider/model from global settings only."""
    provider = (
        (settings.get('vision_llm_provider') or '').strip()
        or (settings.get('conversational_llm_provider') or '').strip()
        or 'Ollama'
    )
    model = (
        (settings.get('vision_llm_model') or '').strip()
        or (settings.get('conversational_llm_model') or '').strip()
        or ''
    )
    return provider, model


# Models known to support vision
def is_vision_model_supported(vision_provider: str, vision_model: str) -> bool:
    """Check whether a vision model is configured.
    
    The actual model validation happens in the settings UI.
    At runtime we just check that *something* is configured.
    """
    return bool(vision_model and vision_model.strip())


def call_openai_vision(
    base64_images: list[str],
    enhanced_prompt: str,
    vision_model: str,
    is_action_request: bool,
) -> str:
    """
    Call the OpenAI vision API with one or more base64-encoded images.

    Args:
        base64_images: List of base64-encoded WebP image strings.
        enhanced_prompt: The prompt to send alongside the images.
        vision_model: The OpenAI model name to use.
        is_action_request: Whether to request JSON response format.

    Returns:
        The raw text content from the vision model response.

    Raises:
        Exception on API errors (caller should handle).
    """
    from openai import OpenAI
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    openai_key = settings.get('openai_key', '')
    if not openai_key:
        raise ValueError("OpenAI API key not configured. Please set it in settings.")

    client = OpenAI(api_key=openai_key)

    content_items: list[dict] = [{"type": "text", "text": enhanced_prompt}]
    for b64 in base64_images:
        content_items.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/webp;base64,{b64}"},
        })

    vision_messages = [{"role": "user", "content": content_items}]

    if not vision_model:
        vision_model = "gpt-4o"

    create_kwargs: dict = {
        "model": vision_model,
        "messages": vision_messages,
        "max_tokens": 2000,
        "timeout": 60.0,
    }

    if is_action_request and any(v in vision_model.lower() for v in ['gpt-4o', 'gpt-4-turbo', 'o1', 'o3']):
        create_kwargs["response_format"] = {"type": "json_object"}

    logger.info(f"ScreenshotAnalyzer: Calling vision API with {len(base64_images)} image(s), model: {vision_model}")

    import time
    max_retries = 2
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(**create_kwargs)
            break
        except Exception as retry_err:
            last_err = retry_err
            err_str = str(retry_err).lower()
            if attempt < max_retries and any(k in err_str for k in [
                'connection', 'ssl', 'timeout', 'network', 'reset by peer',
                'bad_record_mac', 'eof occurred',
            ]):
                wait = 1.0 * (attempt + 1)
                logger.warning("Vision API attempt %d failed (%s), retrying in %.1fs...",
                               attempt + 1, type(retry_err).__name__, wait)
                time.sleep(wait)
            else:
                raise
    else:
        raise last_err  # type: ignore[misc]

    if not response or not response.choices:
        raise RuntimeError("Vision API returned empty response.")

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Vision API returned empty content.")

    preview = content[:200] if len(content) > 200 else content
    logger.info(f"ScreenshotAnalyzer: Vision analysis complete ({len(content)} chars). Preview: {preview}")
    return content


# ---------------------------------------------------------------------------
# JSON response instructions (shared across action prompts)
# ---------------------------------------------------------------------------

_ACTION_JSON_INSTRUCTIONS = """
IMPORTANT: You must respond with a JSON object containing:
- "type": "action"
- "x": integer (X coordinate of the target location - relative to the screenshot)
- "y": integer (Y coordinate of the target location - relative to the screenshot)
- "screen": integer (screen number where the target is located)
- "action": string (the action to perform: "click", "hover", "double_click", "right_click", "scroll_up", "scroll_down", "drag")
- "description": string (brief description of what you're targeting)
- "summary": string (optional summary of what you see)

If you can see the element AT ALL — even partially, at the edge, small, or inside an image/picture — you MUST provide coordinates. Give your best estimate. Approximate coordinates are acceptable and expected.

For text in input fields, search bars, or text areas:
- "end of the text" = right edge of the last visible character
- "beginning of the text" = left edge of the first character
- "the word X" = center of that word
You CAN estimate these positions from the screenshot. Do NOT refuse.

ONLY respond with target_not_found if the element is genuinely NOT visible anywhere in the screenshot:
{
  "type": "target_not_found",
  "summary": "Describe what you actually see",
  "description": "Brief note that the requested element is not visible"
}

You MUST always respond with valid JSON. Never respond with plain text or a summary when coordinates are requested."""

_SUMMARY_JSON_INSTRUCTIONS = """
Respond with a JSON object containing:
- "type": "summary"
- "summary": string (detailed description)
- "description": string (brief description)"""

_DRAG_JSON_INSTRUCTIONS = """
IMPORTANT: You must respond with a JSON object containing:
- "type": "drag"
- "start_x": integer (X coordinate of the drag source)
- "start_y": integer (Y coordinate of the drag source)
- "end_x": integer (X coordinate of the drop target)
- "end_y": integer (Y coordinate of the drop target)
- "screen": integer (screen number)
- "description": string (what you're dragging and where)
- "summary": string (optional summary)

If either element is NOT visible, respond with:
{
  "type": "target_not_found",
  "summary": "Describe what you actually see",
  "description": "Brief note about what's missing"
}"""

_FORM_JSON_INSTRUCTIONS = """
IMPORTANT: You must respond with a JSON object containing:
- "type": "form_action"
- "actions": array of action objects, each with:
  - "action": "click" | "type" | "select" | "check" | "uncheck" | "clear"
  - "x": integer (X coordinate)
  - "y": integer (Y coordinate)
  - "text": string (text to type, if applicable)
  - "description": string (what field/element)
- "screen": integer (screen number)
- "summary": string (what you're doing)

If the form/fields are NOT visible, respond with:
{
  "type": "target_not_found",
  "summary": "Describe what you actually see",
  "description": "Brief note about what's missing"
}"""


# ---------------------------------------------------------------------------
# Prompt builders per intent
# ---------------------------------------------------------------------------

def build_action_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Build the enhanced prompt for action (coordinate) requests."""
    return f"""{prompt}
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Example JSON format:
{{
  "type": "action",
  "x": 500,
  "y": 300,
  "screen": 1,
  "action": "click",
  "description": "Green button in the center",
  "summary": "I can see a green button labeled 'Submit' in the center of the screen"
}}

CRITICAL: The screen number you provide MUST match the screen number of the screenshot where you see the target."""


def build_summary_prompt(prompt: str) -> str:
    """Build the enhanced prompt for informational/summary requests."""
    return f"""{prompt}

Please provide a detailed summary breakdown of what you see in the screenshot(s).
{_SUMMARY_JSON_INSTRUCTIONS}

Example JSON format:
{{
  "type": "summary",
  "summary": "Detailed description here...",
  "description": "Brief description"
}}"""


def build_click_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for click element use case."""
    return f"""Find the UI element described and provide its coordinates for clicking.

User request: {prompt}
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "click" in your response."""


def build_hover_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for hover element use case."""
    return f"""Find the element described and provide its coordinates for hovering.

User request: {prompt}

POSITIONING RULES:
- For "end of the text/word" in an input field or search bar: target the RIGHT EDGE of the last character in the text.
- For "beginning of the text/word": target the LEFT EDGE of the first character.
- For "the word X": target the CENTER of that specific word.
- For "center of" an image, picture, icon, or visual element: target the CENTER of that visual element's bounding area.
- For any visible element (UI controls, images, pictures, logos, avatars, illustrations): provide your best coordinate estimate. Approximate is fine.
- You are NOT limited to UI elements. If the user asks to move to something visible in a photo, image, illustration, or any visual content on screen, estimate the coordinates of that thing within the image.
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "hover" in your response."""


def build_double_click_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for double-click use case."""
    return f"""Find the UI element described and provide its coordinates for double-clicking.

User request: {prompt}
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "double_click" in your response."""


def build_right_click_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for right-click use case."""
    return f"""Find the UI element described and provide its coordinates for right-clicking (context menu).

User request: {prompt}
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "right_click" in your response."""


def build_scroll_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for scroll-to use case."""
    return f"""The user wants to scroll to a specific element or area. Identify the target location.

User request: {prompt}

If the target is already visible, provide its coordinates. If not visible, indicate the scroll direction needed.
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "scroll_down" or "scroll_up" in your response. If the target IS visible, set "action" to "click" and provide its coordinates."""


def build_drag_drop_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for drag-and-drop use case."""
    return f"""The user wants to drag one element to another location. Identify both the source and destination.

User request: {prompt}
{_DRAG_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}"""


def build_find_text_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for finding specific text on screen."""
    return f"""Search the screenshot for specific text content and provide its location.

User request: {prompt}
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "highlight" in your response. Provide the exact coordinates of where the text appears."""


def build_read_error_prompt(prompt: str) -> str:
    """Prompt for reading error messages."""
    return f"""{prompt}

Focus specifically on any error messages, warnings, alerts, or failure indicators visible on screen.
Read and transcribe the EXACT error text. Include error codes if visible.

Respond with a JSON object:
- "type": "error_report"
- "error_text": string (the exact error message text)
- "error_type": string ("error" | "warning" | "info" | "none")
- "summary": string (explanation of what the error means and possible fixes)
- "description": string (brief description of where the error appears)

If no error is visible:
{{
  "type": "error_report",
  "error_text": "",
  "error_type": "none",
  "summary": "No errors visible on screen. [describe what IS visible]",
  "description": "No error messages found"
}}"""


def build_read_notification_prompt(prompt: str) -> str:
    """Prompt for reading notifications/alerts."""
    return f"""{prompt}

Focus on any notifications, alerts, popups, toasts, banners, or dialog boxes visible on screen.
Read and transcribe the EXACT notification text.

Respond with a JSON object:
- "type": "notification_report"
- "notification_text": string (the exact notification text)
- "notification_type": string ("notification" | "alert" | "popup" | "toast" | "banner" | "dialog" | "none")
- "summary": string (what the notification is about)
- "description": string (where it appears on screen)

If no notification is visible, set notification_type to "none"."""


def build_identify_app_prompt(prompt: str) -> str:
    """Prompt for identifying the active app/window."""
    return f"""{prompt}

Identify the application(s) and window(s) visible on screen. For each visible window, note:
- Application name
- Window title
- Whether it appears to be the active/focused window

Respond with a JSON object:
- "type": "app_report"
- "active_app": string (the app that appears focused/in front)
- "active_window_title": string (title of the active window)
- "visible_apps": array of strings (all visible application names)
- "summary": string (detailed description of what's open)
- "description": string (brief summary)"""


def build_check_state_prompt(prompt: str) -> str:
    """Prompt for checking UI element state."""
    return f"""{prompt}

Examine the UI element(s) in question and determine their current state.
Look for visual indicators: checked/unchecked, on/off, enabled/disabled, active/inactive,
loading spinners, progress bars, greyed-out elements, highlighted/selected states.

Respond with a JSON object:
- "type": "state_report"
- "element": string (which element you're checking)
- "state": string (the current state: "on", "off", "enabled", "disabled", "loading", "active", "inactive", "checked", "unchecked", etc.)
- "confidence": string ("high" | "medium" | "low")
- "summary": string (detailed explanation of the state)
- "description": string (brief description)"""


def build_compare_prompt(prompt: str) -> str:
    """Prompt for comparing screens / detecting changes."""
    return f"""{prompt}

Compare the screenshot(s) and identify any differences or changes.
Look for: new elements, removed elements, changed text, moved elements,
color changes, state changes, new notifications, etc.

Respond with a JSON object:
- "type": "comparison_report"
- "changes": array of strings (each change detected)
- "summary": string (overall description of differences)
- "description": string (brief summary)

If only one screenshot is provided, describe what you see and note that comparison requires a reference."""


def build_count_prompt(prompt: str) -> str:
    """Prompt for counting UI elements."""
    return f"""{prompt}

Count the specific UI elements requested. Be precise — count each distinct instance.

Respond with a JSON object:
- "type": "count_report"
- "element_type": string (what you're counting)
- "count": integer (the number found)
- "details": array of strings (brief description of each counted item, if reasonable)
- "summary": string (e.g. "I count 7 open tabs in Chrome")
- "description": string (brief summary)"""


def build_read_text_prompt(prompt: str) -> str:
    """Prompt for reading text content on screen."""
    return f"""{prompt}

Read and transcribe the text content visible on screen. Be thorough and accurate.
Include all readable text, preserving the layout/structure where possible.

{_SUMMARY_JSON_INSTRUCTIONS}

In the "summary" field, include the transcribed text content."""


def build_locate_icon_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for locating icons/system tray elements."""
    return f"""Find the specific icon or indicator described by the user.

User request: {prompt}

Look in system trays, menu bars, status bars, taskbars, docks, and toolbars.
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "click" in your response."""


def build_navigate_menu_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for menu navigation."""
    return f"""The user wants to navigate through menus. Find the first menu item to click.

User request: {prompt}

If the menu path has multiple levels (e.g. File > Save As), provide coordinates for the FIRST item to click.
The system will handle subsequent clicks.
{_ACTION_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}

Set "action" to "click" in your response."""


def build_interact_form_prompt(
    prompt: str,
    screen_info_text: str,
    elements_context: str = "",
    ocr_context: str = "",
) -> str:
    """Prompt for form interaction."""
    return f"""The user wants to interact with form elements on screen.

User request: {prompt}
{_FORM_JSON_INSTRUCTIONS}{screen_info_text}{elements_context}{ocr_context}"""


def build_multi_screen_prompt(prompt: str) -> str:
    """Prompt for multi-screen navigation."""
    return f"""{prompt}

Describe what's visible on each screen/monitor. If multiple screenshots are provided,
describe each one separately with its screen number.

Respond with a JSON object:
- "type": "multi_screen_report"
- "screens": array of objects, each with:
  - "screen": integer (screen number)
  - "description": string (what's on this screen)
  - "active_app": string (main app visible)
- "summary": string (overview of all screens)
- "description": string (brief summary)"""


# ---------------------------------------------------------------------------
# Intent → prompt builder mapping
# ---------------------------------------------------------------------------

def build_prompt_for_intent(
    intent,
    prompt: str,
    screen_info_text: str = "",
    elements_context: str = "",
    ocr_context: str = "",
) -> tuple[str, bool]:
    """
    Build the appropriate prompt for a given VisionIntent.

    Returns (enhanced_prompt, is_action_request) tuple.
    is_action_request=True means we expect JSON with coordinates.
    """
    from distr.core.agent.services.vision.intent_classifier import VisionIntent, ACTION_INTENTS, LOCATE_INTENTS, INFO_INTENTS

    # Action intents — need coordinates
    if intent == VisionIntent.CLICK_ELEMENT:
        return build_click_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.HOVER_ELEMENT:
        return build_hover_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.DOUBLE_CLICK:
        return build_double_click_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.RIGHT_CLICK:
        return build_right_click_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.SCROLL_TO:
        return build_scroll_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.DRAG_DROP:
        return build_drag_drop_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.INTERACT_FORM:
        return build_interact_form_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.NAVIGATE_MENU:
        return build_navigate_menu_prompt(prompt, screen_info_text, elements_context, ocr_context), True

    # Locate intents — need coordinates
    if intent == VisionIntent.LOCATE:
        return build_action_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.FIND_TEXT:
        return build_find_text_prompt(prompt, screen_info_text, elements_context, ocr_context), True
    if intent == VisionIntent.LOCATE_ICON:
        return build_locate_icon_prompt(prompt, screen_info_text, elements_context, ocr_context), True

    # Informational intents — no coordinates
    if intent == VisionIntent.READ_ERROR:
        return build_read_error_prompt(prompt), False
    if intent == VisionIntent.READ_NOTIFICATION:
        return build_read_notification_prompt(prompt), False
    if intent == VisionIntent.IDENTIFY_APP:
        return build_identify_app_prompt(prompt), False
    if intent == VisionIntent.CHECK_STATE:
        return build_check_state_prompt(prompt), False
    if intent == VisionIntent.COMPARE_SCREEN:
        return build_compare_prompt(prompt), False
    if intent == VisionIntent.COUNT_ELEMENTS:
        return build_count_prompt(prompt), False
    if intent == VisionIntent.READ_TEXT:
        return build_read_text_prompt(prompt), False
    if intent == VisionIntent.MULTI_SCREEN_NAV:
        return build_multi_screen_prompt(prompt), False
    if intent == VisionIntent.DESCRIBE_SCREEN:
        return build_summary_prompt(prompt), False

    # Fallback: check if it looks like an action request
    if intent in ACTION_INTENTS or intent in LOCATE_INTENTS:
        return build_action_prompt(prompt, screen_info_text, elements_context, ocr_context), True

    return build_summary_prompt(prompt), False
