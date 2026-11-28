"""
Clipboard-based Actions Tools for LangChain.

These tools handle clipboard-based actions like explain, elaborate, and read.
They copy to clipboard first, then process the clipboard content.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import re
import time
import subprocess
from distr.core.agent.tools.base import get_platform_modifier_key

try:
    import pyautogui
    # Disable pyautogui FAILSAFE to prevent mouse operations from being blocked
    pyautogui.FAILSAFE = False
except Exception:
    pyautogui = None

logger = logging.getLogger(__name__)


def get_clipboard_content():
    """Get content from clipboard using platform-specific methods."""
    try:
        import platform
        system = platform.system()
        
        if system == "Darwin":  # macOS
            import subprocess
            result = subprocess.run(
                ['pbpaste'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout if result.returncode == 0 else None
        elif system == "Windows":
            import subprocess
            result = subprocess.run(
                ['powershell', '-command', 'Get-Clipboard'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout.strip() if result.returncode == 0 else None
        else:  # Linux
            try:
                import subprocess
                # Try xclip first
                result = subprocess.run(
                    ['xclip', '-selection', 'clipboard', '-o'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
            try:
                # Fallback to xsel
                result = subprocess.run(
                    ['xsel', '--clipboard', '--output'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                return result.stdout if result.returncode == 0 else None
            except Exception:
                pass
            return None
    except Exception as e:
        logger.error(f"Error getting clipboard content: {e}", exc_info=True)
        return None


def set_clipboard_content(text: str) -> bool:
    """Write text to clipboard using platform-specific methods."""
    try:
        import platform
        import subprocess

        system = platform.system()
        payload = text if text is not None else ""

        if system == "Darwin":
            result = subprocess.run(["pbcopy"], input=payload, text=True, timeout=1)
            return result.returncode == 0
        if system == "Windows":
            result = subprocess.run(
                ["powershell", "-command", "Set-Clipboard"],
                input=payload,
                text=True,
                timeout=1,
            )
            return result.returncode == 0

        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                result = subprocess.run(cmd, input=payload, text=True, timeout=1)
                if result.returncode == 0:
                    return True
            except Exception:
                continue
        return False
    except Exception as e:
        logger.error("Error setting clipboard content: %s", e, exc_info=True)
        return False



_CLIPBOARD_DISCUSS = re.compile(
    r"\b(talk|discuss|review|go\s+through|walk\s+through|explain|tell\s+me|"
    r"what\s+you\s+think|about\s+it|with\s+me)\b",
    re.IGNORECASE,
)
_EXISTING_CLIPBOARD = re.compile(
    r"(?is)^(?:(?:can|could|would|will)\s+you\s+|please\s+)?"
    r"(?:"
    r"(?:read(?:\s+(?:aloud|out\s*loud|to\s*me))?\s+)(?:from\s+|what(?:\'?s|\s+is)\s+(?:in|on)\s+)?(?:the\s+|my\s+)?clipboard"
    r"|"
    r"from\s+(?:the\s+|my\s+)?clipboard"
    r")"
    r"(?:\s+(?:aloud|out\s*loud|to\s*me|please))*(?:[.!?,]+)?$"
)
_WHAT_I_COPIED = re.compile(r"(?i)\bwhat\s+I\s+copied\b")
_READ_AND_CLIPBOARD = re.compile(r"(?i)\bread\b.*\bclipboard\b")
_SELECTION_READ = re.compile(
    r"(?is)^(?:(?:can|could|would|will)\s+you\s+|please\s+)?"
    r"read(?:\s+(?:aloud|out\s*loud|to\s*me))?\s+"
    r"(?:this|that|it|selected|(?:the\s+|this\s+|that\s+)?(?:selection|selected(?:\s+text)?))"
    r"(?:\s+(?:aloud|out\s*loud|to\s*me|please))*(?:[.!?,]+)?$"
)


def is_existing_clipboard_request(text: str) -> bool:
    """True when the user wants the current clipboard spoken, not a new copy."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if _CLIPBOARD_DISCUSS.search(raw):
        return False
    if _WHAT_I_COPIED.search(raw) or _READ_AND_CLIPBOARD.search(raw):
        return True
    return bool(_EXISTING_CLIPBOARD.match(raw))


def is_selection_read_request(text: str) -> bool:
    """True when the user wants the on-screen selection spoken."""
    raw = str(text or "").strip()
    if not raw or is_existing_clipboard_request(raw):
        return False
    return bool(_SELECTION_READ.match(raw))


def _ax_copy_attribute(element, attribute: str):
    """Return an AX attribute value, tolerating pyobjc calling conventions."""
    from ApplicationServices import AXUIElementCopyAttributeValue

    try:
        result = AXUIElementCopyAttributeValue(element, attribute, None)
    except TypeError:
        result = AXUIElementCopyAttributeValue(element, attribute)
    if isinstance(result, tuple):
        if len(result) >= 2:
            err, value = result[0], result[1]
            if value is not None:
                return value
            if err not in (0, None) and not isinstance(err, int):
                return err
            return None
        return result[0]
    return result


def accessibility_is_trusted() -> bool:
    """Whether the current process is allowed to use macOS Accessibility APIs."""
    for mod in ("ApplicationServices", "Quartz", "HIServices"):
        try:
            fn = getattr(__import__(mod), "AXIsProcessTrusted")
            return bool(fn())
        except Exception:
            continue
    return False


def _ax_attribute_is_supported(element, attribute: str) -> bool:
    try:
        from ApplicationServices import AXUIElementCopyAttributeNames

        try:
            result = AXUIElementCopyAttributeNames(element, None)
        except TypeError:
            result = AXUIElementCopyAttributeNames(element)
        names = result[1] if isinstance(result, tuple) and len(result) > 1 else result
        if names:
            return attribute in set(str(n) for n in names)
    except Exception:
        pass
    try:
        return _ax_copy_attribute(element, attribute) is not None
    except Exception:
        return False


def _walk_ax_for_selected_text_support(element, depth: int, budget: list[int]) -> bool:
    if budget[0] <= 0 or depth < 0 or element is None:
        return False
    budget[0] -= 1
    if _ax_attribute_is_supported(element, "AXSelectedText"):
        return True
    children = _ax_copy_attribute(element, "AXChildren") or []
    try:
        child_list = list(children)
    except TypeError:
        child_list = []
    for child in child_list[:24]:
        if _walk_ax_for_selected_text_support(child, depth - 1, budget):
            return True
    return False


def ax_selected_text_support_by_app(max_apps: int = 40) -> list[dict]:
    """Read-only survey of running apps that expose AXSelectedText. Does not change focus or the clipboard."""
    results: list[dict] = []
    try:
        from ApplicationServices import AXUIElementCreateApplication
        from AppKit import NSWorkspace
    except Exception as exc:
        return [{"app": "(unavailable)", "supports_ax_selected_text": False, "error": str(exc)}]

    trusted = accessibility_is_trusted()
    apps = list(NSWorkspace.sharedWorkspace().runningApplications() or [])
    counted = 0
    for app in apps:
        if counted >= max_apps:
            break
        try:
            if app.isHidden() and not app.isActive():
                # Still include regular app bundles; skip background-only agents without a bundle name.
                pass
            name = str(app.localizedName() or app.bundleIdentifier() or "")
            bundle = str(app.bundleIdentifier() or "")
            pid = int(app.processIdentifier())
            activation = int(app.activationPolicy())
            if not name or activation != 0:  # NSApplicationActivationPolicyRegular == 0
                continue
            counted += 1
            entry = {
                "app": name,
                "bundle": bundle,
                "pid": pid,
                "supports_ax_selected_text": False,
                "accessible": False,
                "trusted_caller": trusted,
            }
            try:
                ax_app = AXUIElementCreateApplication(pid)
                windows = _ax_copy_attribute(ax_app, "AXWindows")
                entry["accessible"] = windows is not None or _ax_copy_attribute(ax_app, "AXRole") is not None
                budget = [80]
                if _walk_ax_for_selected_text_support(ax_app, 5, budget):
                    entry["supports_ax_selected_text"] = True
                    entry["accessible"] = True
            except Exception as exc:
                entry["error"] = str(exc)
            results.append(entry)
        except Exception:
            continue
    results.sort(key=lambda row: (not row.get("supports_ax_selected_text"), row.get("app") or ""))
    return results


def snapshot_pasteboard() -> list[dict[str, bytes]]:
    """Snapshot every pasteboard item and type so Cmd+C can be undone."""
    try:
        from AppKit import NSPasteboard

        pb = NSPasteboard.generalPasteboard()
        items: list[dict[str, bytes]] = []
        for item in (pb.pasteboardItems() or []):
            data_by_type: dict[str, bytes] = {}
            for ptype in (item.types() or []):
                data = item.dataForType_(ptype)
                if data is not None:
                    data_by_type[str(ptype)] = bytes(data)
            if data_by_type:
                items.append(data_by_type)
        return items
    except Exception:
        logger.debug("NSPasteboard snapshot unavailable; falling back to text", exc_info=True)
        text = get_clipboard_content()
        if text is None:
            return []
        return [{"public.utf8-plain-text": text.encode("utf-8")}]


def restore_pasteboard(items: list[dict[str, bytes]] | None) -> bool:
    """Restore a snapshot taken by snapshot_pasteboard, including non-text types."""
    if items is None:
        return False
    try:
        from AppKit import NSData, NSPasteboard, NSPasteboardItem

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        if not items:
            return True
        new_items = []
        for data_by_type in items:
            item = NSPasteboardItem.alloc().init()
            for ptype, data in data_by_type.items():
                nsdata = NSData.dataWithBytes_length_(data, len(data))
                item.setData_forType_(nsdata, ptype)
            new_items.append(item)
        return bool(pb.writeObjects_(new_items))
    except Exception:
        logger.debug("NSPasteboard restore unavailable; falling back to text", exc_info=True)
        if not items:
            return set_clipboard_content("")
        for data_by_type in items:
            for ptype, data in data_by_type.items():
                key = ptype.lower()
                if "utf8-plain-text" in key or key.endswith("string") or "plain-text" in key:
                    try:
                        return set_clipboard_content(data.decode("utf-8"))
                    except Exception:
                        continue
        return False


def get_selected_text_via_accessibility() -> str | None:
    """Read the focused element's selected text with no clipboard involvement."""
    try:
        from ApplicationServices import AXUIElementCreateSystemWide

        system = AXUIElementCreateSystemWide()
        focused = _ax_copy_attribute(system, "AXFocusedUIElement")
        if not focused:
            raise RuntimeError("no focused element")
        value = _ax_copy_attribute(focused, "AXSelectedText")
        text = str(value or "")
        if text.strip():
            return text
    except Exception:
        logger.debug("AXSelectedText unavailable", exc_info=True)
    try:
        script = (
            'tell application "System Events"\n'
            '  tell (first application process whose frontmost is true)\n'
            '    try\n'
            '      set focusedEl to value of attribute "AXFocusedUIElement"\n'
            '      return value of attribute "AXSelectedText" of focusedEl\n'
            '    end try\n'
            '  end tell\n'
            'end tell\n'
        )
        result = subprocess.run(
            ["osascript"],
            input=script,
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0 and result.stdout and result.stdout.strip():
            return result.stdout[:-1] if result.stdout.endswith("\n") else result.stdout
    except Exception:
        logger.debug("AppleScript AXSelectedText unavailable", exc_info=True)
    return None


def _copy_selection_via_hotkey() -> str | None:
    if pyautogui is None:
        return None
    from distr.core.agent.tools.base import get_platform_modifier_key

    cmd_key = get_platform_modifier_key()
    logger.info("Pressing %s+C to copy selection", cmd_key)
    pyautogui.keyDown(cmd_key)
    pyautogui.press("c")
    pyautogui.keyUp(cmd_key)
    time.sleep(0.3)
    return get_clipboard_content()


def get_selected_text_for_reading() -> str | None:
    """Prefer AXSelectedText. If that is empty, Cmd+C with a full pasteboard restore."""
    ax_text = get_selected_text_via_accessibility()
    if ax_text and str(ax_text).strip():
        logger.info("Got selected text via AXSelectedText (%s chars, no clipboard)", len(ax_text))
        return ax_text
    logger.info("AXSelectedText empty; falling back to Cmd+C with clipboard restore")
    snapshot = snapshot_pasteboard()
    try:
        return _copy_selection_via_hotkey()
    finally:
        restore_pasteboard(snapshot)


class ClipboardActionTool(BaseTool):
    """Tool for clipboard-based actions: explain, elaborate, inspect, read aloud."""
    
    name: str = "clipboard_action"
    description: str = """EXECUTE clipboard actions: explain, elaborate, get clipboard content, read aloud, or write text into the clipboard.
    
    CRITICAL: This tool is ONLY for "explain this", "elaborate this", and "get clipboard" - NOT for copy/cut/paste.
    For "copy this", "cut this", "paste" → Use text_editing tool instead.
    
    DO NOT call this tool for copy/cut/paste operations - use text_editing tool.
    
    Clipboard intent distinction:
    - "read it out loud", "read this", "read from clipboard" means speak the clipboard/selection aloud.
    - "read/check/look at/load the clipboard so we can talk about it" means consume the clipboard into chat context with action="get"; do NOT read the whole clipboard aloud.

    When user says "explain this", "elaborate this", "what's in the clipboard", "get the clipboard", "show clipboard", "set clipboard to ...", or "write ... to clipboard" - YOU MUST CALL THIS TOOL IMMEDIATELY.
    DO NOT explain what the tool does. DO NOT describe the tool. DO NOT ask questions. JUST CALL IT.
    
    NOTE: "read this" is handled automatically and should NOT be called via this tool.
    
    The tool automatically:
    - For "explain this" / "elaborate this": Copies current selection (Cmd+C), gets clipboard content, processes it (explain/elaborate -> LLM)
    - For "get clipboard" / "what's in the clipboard" / "read clipboard and talk about it": Returns clipboard content directly to conversation context
    - For "read this" / "read from clipboard": Returns text for direct TTS playback
    - For "set clipboard to X" / "write X to clipboard": Writes X directly into the clipboard
    
    REQUIRED CALLS:
    - "explain this" -> CALL with action="explain" (do not explain, just call)
    - "elaborate this" -> CALL with action="elaborate" (do not explain, just call)
    - "what's in the clipboard" / "get the clipboard" / "show clipboard" / "read clipboard and let's talk about it" -> CALL with action="get" (returns clipboard content)
    - "read this aloud" / "read from clipboard" -> CALL with action="read" for selected text or action="get" only when the fast-action route is direct TTS
    - "set clipboard to hello" / "write hello to clipboard" -> CALL with action="set", content="hello"
    
    Available actions: explain, elaborate, get, set, write.
    For "read from clipboard" / "from clipboard" / "what I copied": action=read_clipboard (pbpaste only, then speak). For "read this" / "read the selection": action=read (selected text, then speak). Do not ask what format they want — just read it. DO NOT use this tool for copy/cut/paste - use text_editing tool instead.
    CALL THE TOOL - never describe it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        self._llm_service = llm_service
        self._read_task = None
        self._last_read_text = None
        
    def get_triggers(self) -> list[str]:
        """Get triggers for clipboard actions."""
        return [
            "explain this", "elaborate this", "read this", 
            "what's in the clipboard", "what is in the clipboard",
            "get the clipboard", "get clipboard", "show clipboard",
            "read my clipboard", "read the clipboard", 
            "see my clipboard"
        ]
    
    def _run(self, action: str = "", text: str = "", **kwargs) -> str:
        """Execute clipboard-based action."""
        try:
            # Extract action from text if not provided
            # Also check if action is empty but we have a user request - try to infer from the last user message
            if not action:
                text_lower = ""
                if text:
                    text_lower = text.lower().strip()
                    logger.info(f"Extracting action from text: '{text_lower}'")
                else:
                    # If both action and text are empty, this is likely a tool call without proper arguments
                    # Try to get the last user message from the LLM service if available
                    if self._llm_service and hasattr(self._llm_service, '_messages') and self._llm_service._messages:
                        # Get the last user message
                        for msg in reversed(self._llm_service._messages):
                            if msg.get('role') == 'user':
                                text = msg.get('content', '')
                                text_lower = text.lower().strip()
                                logger.info(f"No action/text provided, extracting from last user message: '{text_lower}'")
                                break
                
                if "explain" in text_lower and "this" in text_lower:
                    action = "explain"
                    logger.info("Detected: explain")
                elif "elaborate" in text_lower and ("on this" in text_lower or "this" in text_lower):
                    action = "elaborate"
                    logger.info("Detected: elaborate")
                elif is_existing_clipboard_request(text or text_lower):
                    action = "read_clipboard"
                    logger.info("Detected: read_clipboard")
                elif is_selection_read_request(text or text_lower):
                    action = "read"
                    logger.info("Detected: read selection")
                elif "read" in text_lower and "this" in text_lower:
                    action = "read"
                    logger.info("Detected: read")
                elif any(phrase in text_lower for phrase in ["set clipboard", "write clipboard", "put clipboard", "copy clipboard"]) or (
                    "clipboard" in text_lower and any(word in text_lower for word in [" to clipboard", " into clipboard", " onto clipboard"])
                ):
                    action = "set"
                    logger.info("Detected: set clipboard")
                elif any(phrase in text_lower for phrase in ["read my clipboard", "read the clipboard", "read from clipboard", "from clipboard"]):
                    action = "read_clipboard"
                    logger.info("Detected: read_clipboard (phrase)")
                elif any(phrase in text_lower for phrase in ["what's in the clipboard", "what is in the clipboard", "get the clipboard", "get clipboard", "get what's in", "get what is in", "go get", "go get what", "go get what's", "go get what is", "show clipboard", "show me clipboard", "what's on the clipboard", "see my clipboard"]):
                    action = "get"
                    logger.info("Detected: get clipboard")
                elif "get" in text_lower and "clipboard" in text_lower:
                    # Fallback: if both "get" and "clipboard" appear, it's likely a get request
                    action = "get"
                    logger.info("Detected: get clipboard (fallback - contains 'get' and 'clipboard')")
                elif "explain" in text_lower:
                    action = "explain"
                    logger.info("Detected: explain (fallback)")
                elif "elaborate" in text_lower:
                    action = "elaborate"
                    logger.info("Detected: elaborate (fallback)")
                elif "read" in text_lower:
                    action = "read"
                    logger.info("Detected: read (fallback)")
            
            if not action:
                logger.warning(f"Could not extract action from: action='{action}', text='{text}'")
                return "Error: No action specified. Available: explain, elaborate, read, read_clipboard, get, set"
            
            logger.info(f"Executing clipboard action: {action}")
            request_text = text or kwargs.get("last_user_message") or ""
            if action == "read" and is_existing_clipboard_request(request_text):
                action = "read_clipboard"
                logger.info("Coerced read -> read_clipboard for existing-clipboard request")

            if action in ("set", "write"):
                content = kwargs.get("content") or kwargs.get("clipboard_text") or kwargs.get("value") or ""
                if not content and text:
                    content = self._extract_set_content(text)
                if content is None or content == "":
                    return "Error: No clipboard text provided. Say 'set clipboard to ...' or pass content."
                if not set_clipboard_content(str(content)):
                    return "Error: Could not write to clipboard."
                return f"Clipboard updated ({len(str(content))} characters)."
            
            # Existing clipboard: pbpaste only, never Cmd+C.
            if action == "read_clipboard":
                clipboard_text = get_clipboard_content()
                if not clipboard_text or not clipboard_text.strip():
                    return "The clipboard is empty."
                logger.info("Got clipboard content for reading (%s chars, no copy)", len(clipboard_text))
                return f"READ_ACTION:{clipboard_text}"

            # For "get" action, use existing clipboard content directly (no copy needed).
            # This is a context-ingestion path for the LLM, not a direct TTS readout.
            if action == "get":
                clipboard_text = get_clipboard_content()
                if not clipboard_text or not clipboard_text.strip():
                    return "The clipboard is empty."
                logger.info(f"Got clipboard content ({len(clipboard_text)} chars) for get action")
                return f"CLIPBOARD CONTENT:\n\n{clipboard_text}\n\nThis is the current clipboard content. When the user refers to 'that list', 'what you see', 'the clipboard', or similar phrases, they are referring to this content above."

            # Selection read: AXSelectedText first, no clipboard. Cmd+C only as a restored fallback.
            if action == "read":
                exact_text = get_selected_text_for_reading()
                if not exact_text or not str(exact_text).strip():
                    return "Error: Could not get the selected text. Make sure you have text selected."
                logger.info("Got selected text for reading (%s chars, EXACT)", len(exact_text))
                return f"READ_ACTION:{exact_text}"

            # Step 1: Press Cmd+C to copy (for explain/elaborate only)
            # Use keyDown/keyUp separately for more reliable execution on macOS
            if pyautogui is None:
                return "Error: pyautogui is not available, so I cannot copy the current selection. I can still get or set clipboard content directly."
            cmd_key = get_platform_modifier_key()
            logger.info(f"Pressing {cmd_key}+C to copy selection")
            
            pyautogui.keyDown(cmd_key)
            pyautogui.press('c')
            pyautogui.keyUp(cmd_key)
            time.sleep(0.3)  # Increased delay to 0.3s to ensure clipboard updates
            
            # Step 2: Get clipboard content
            clipboard_text = get_clipboard_content()
            if not clipboard_text:
                logger.warning("Clipboard action: Failed to get clipboard content (empty or None)")
                return "Error: Could not get clipboard content. Make sure you have text selected."

            logger.info(f"Clipboard action: Got {len(clipboard_text)} chars")
            
            if action == "explain":
                # For explain/elaborate, strip whitespace is okay
                clipboard_text = clipboard_text.strip()
                if not clipboard_text:
                    return "Error: Clipboard is empty. Make sure you have text selected before using this command."
                logger.info(f"Got clipboard content ({len(clipboard_text)} chars): '{clipboard_text[:100]}...'")
                return self._handle_explain(clipboard_text)
            elif action == "elaborate":
                # For explain/elaborate, strip whitespace is okay
                clipboard_text = clipboard_text.strip()
                if not clipboard_text:
                    return "Error: Clipboard is empty. Make sure you have text selected before using this command."
                logger.info(f"Got clipboard content ({len(clipboard_text)} chars): '{clipboard_text[:100]}...'")
                return self._handle_elaborate(clipboard_text)
            else:
                return f"Error: Unknown action '{action}'. Available: explain, elaborate, read, read_clipboard, get, set"
            
        except Exception as e:
            logger.error(f"Error in ClipboardActionTool: {e}", exc_info=True)
            return f"Error executing clipboard action: {str(e)}"

    @staticmethod
    def _extract_set_content(text: str) -> str:
        """Extract clipboard payload from natural set/write phrasing."""
        if not text:
            return ""
        patterns = [
            r"\b(?:set|write|put|copy)\s+(?:the\s+)?clipboard\s+(?:to|as)\s+(.+)$",
            r"\b(?:set|write|put|copy)\s+(.+?)\s+(?:to|into|onto)\s+(?:my\s+|the\s+)?clipboard\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                content = match.group(1).strip()
                if (content.startswith('"') and content.endswith('"')) or (
                    content.startswith("'") and content.endswith("'")
                ):
                    content = content[1:-1]
                return content
        return ""
    
    def _handle_explain(self, text: str) -> str:
        """Send text to LLM for explanation."""
        if not self._llm_service:
            return "Error: LLM service not available"
        
        try:
            # Add user message to LLM conversation
            # The message will be processed by the normal LLM flow after tool execution
            prompt = f"Please explain the following:\n\n{text}"
            self._llm_service._messages.append({"role": "user", "content": prompt})
            
            logger.info(f"Added explain request to LLM conversation for {len(text)} characters")
            
            # Trigger LLM generation immediately instead of waiting for tool execution to complete
            import asyncio
            async def trigger_llm():
                if self._llm_service._generation_task and not self._llm_service._generation_task.done():
                    # Cancel existing generation
                    self._llm_service._cancelled = True
                    self._llm_service._generation_task.cancel()
                    try:
                        await self._llm_service._generation_task
                    except asyncio.CancelledError:
                        pass
                    self._llm_service._cancelled = False
                
                # Start new generation with the clipboard content
                self._llm_service._generation_task = asyncio.create_task(self._llm_service._generate_response())
            
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(trigger_llm())
            except RuntimeError:
                asyncio.run(trigger_llm())
            
            # Return a simple success message
            return f"Copied {len(text)} characters from clipboard. Processing explanation..."
        except Exception as e:
            logger.error(f"Error handling explain: {e}", exc_info=True)
            return f"Error sending to LLM: {str(e)}"
    
    def _handle_elaborate(self, text: str) -> str:
        """Send text to LLM for elaboration."""
        if not self._llm_service:
            return "Error: LLM service not available"
        
        try:
            # Add user message to LLM conversation
            # The message will be processed by the normal LLM flow after tool execution
            prompt = f"Please elaborate on the following:\n\n{text}"
            self._llm_service._messages.append({"role": "user", "content": prompt})
            
            logger.info(f"Added elaborate request to LLM conversation for {len(text)} characters")
            
            # Trigger LLM generation immediately instead of waiting for tool execution to complete
            import asyncio
            async def trigger_llm():
                if self._llm_service._generation_task and not self._llm_service._generation_task.done():
                    # Cancel existing generation
                    self._llm_service._cancelled = True
                    self._llm_service._generation_task.cancel()
                    try:
                        await self._llm_service._generation_task
                    except asyncio.CancelledError:
                        pass
                    self._llm_service._cancelled = False
                
                # Start new generation with the clipboard content
                self._llm_service._generation_task = asyncio.create_task(self._llm_service._generate_response())
            
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(trigger_llm())
            except RuntimeError:
                asyncio.run(trigger_llm())
            
            # Return a simple success message
            return f"Copied {len(text)} characters from clipboard. Processing elaboration..."
        except Exception as e:
            logger.error(f"Error handling elaborate: {e}", exc_info=True)
            return f"Error sending to LLM: {str(e)}"
    
    async def _handle_read_async(self, text: str) -> str:
        """Use the same lossless read-aloud path as clipboard fast actions."""
        if not self._llm_service:
            return "Error: LLM service not available (needed to push TextFrame)"
        try:
            await self._llm_service._fa_deliver_spoken_response(
                text, speakable_symbols=True, read_all=True
            )
            return f"Reading {len(text)} characters exactly as copied"
        except Exception as e:
            logger.error("Error handling read: %s", e, exc_info=True)
            return f"Error sending to TTS: {e}"

    async def _arun(self, action: str = "", text: str = "", **kwargs) -> str:
        # Execute _run to determine action and get data
        logger.info(f"ClipboardActionTool._arun called with action='{action}', text='{text[:50] if text else ''}'")
        
        result = self._run(action=action, text=text)
        logger.info(f"ClipboardActionTool._run returned: {str(result)[:100]}")
        
        # If _run returned a READ_ACTION marker, execute the async read handler
        if isinstance(result, str) and result.startswith("READ_ACTION:"):
            content_to_read = result[len("READ_ACTION:"):]
            
            # Fire-and-forget the read task so we don't block the response
            import asyncio
            logger.info(f"Spawning async read task for {len(content_to_read)} chars")
            logger.info(f"ClipboardActionTool: Creating _read_task for {len(content_to_read)} chars")
            self._last_read_text = content_to_read
            self._read_task = asyncio.create_task(self._handle_read_async(content_to_read))
            logger.info(f"ClipboardActionTool: _read_task created: {self._read_task}")
            
            return f"Reading {len(content_to_read)} characters..."
            
        return result
