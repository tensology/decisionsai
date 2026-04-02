"""
Accessibility Tree Tool — find and interact with UI elements via the OS
accessibility API (macOS: AppleScript/JXA, Windows: UIAutomation).

This tool talks to the local sidecar process via HTTP on 127.0.0.1:SIDECAR_PORT.
The sidecar is a Go binary (DecisionsAI/sidecar/) that exposes the OS
accessibility tree as a simple JSON RPC over WebSocket.

Why this instead of screenshot_analyzer for clicking?
- screenshot_analyzer uses vision LLM + OCR to find elements by pixel coords
- This tool asks the OS directly: "give me every button/field/menu in this window"
  and gets back structured data with element IDs and bounding rects.
- Result: exact, reliable, no vision API cost, works even when elements are
  visually ambiguous or off-screen.

Typical use:
  "move the mouse to the address bar in Chrome"
  → get_window_tree(pid=<chrome>) → find AXTextField near top → move_mouse(id)
"""

import json
import logging
import os
import subprocess
import threading
import time
from typing import Any, Optional

import requests
from langchain.tools import BaseTool
from pydantic import Field

logger = logging.getLogger(__name__)

# Sidecar HTTP port — the Go binary exposes a tiny REST API on this port
# so Python can call it without a WebSocket.
_SIDECAR_PORT = int(os.environ.get("DECISIONSAI_SIDECAR_HTTP_PORT", "11435"))
_SIDECAR_BASE = f"http://127.0.0.1:{_SIDECAR_PORT}"
_SIDECAR_TIMEOUT = 20  # seconds

# Cache the last window tree so click_element doesn't need to re-walk
_element_cache: list[dict] = []
_element_cache_lock = threading.Lock()


def _call_sidecar(tool: str, params: dict) -> dict:
    """Call the sidecar's HTTP tool endpoint."""
    try:
        resp = requests.post(
            f"{_SIDECAR_BASE}/tool/{tool}",
            json=params,
            timeout=_SIDECAR_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        raise RuntimeError(
            "Sidecar not running. The accessibility tree tools require the "
            "DecisionsAI sidecar to be running. It starts automatically with the app."
        )
    except Exception as e:
        raise RuntimeError(f"Sidecar call failed ({tool}): {e}")


def _is_sidecar_running() -> bool:
    try:
        requests.get(f"{_SIDECAR_BASE}/health", timeout=2)
        return True
    except Exception:
        return False


# ── Tool: get_window_tree ─────────────────────────────────────────────────────

class GetWindowTreeTool(BaseTool):
    """Get the accessibility tree of the frontmost window (or a specific app)."""

    name: str = "get_window_tree"
    description: str = (
        "Get a structured list of all UI elements in the frontmost window "
        "(or a specific app by name). Each element has an id, name, "
        "control_type, and bounding rect. Use element IDs with move_to_element "
        "or click_element. "
        "Use this when you need to find a specific UI element like an address bar, "
        "button, text field, or menu item by name rather than by visual appearance."
    )

    def _run(self, app_name: str = "", depth: int = 4, **kwargs) -> str:
        global _element_cache
        try:
            params: dict = {"depth": depth}
            if app_name:
                params["app_name"] = app_name
            result = _call_sidecar("get_window_tree", params)
            elements = result.get("elements", [])
            with _element_cache_lock:
                _element_cache = elements
            # Return a compact summary for the LLM
            lines = [f"Window: {result.get('window_title', 'unknown')} (pid={result.get('pid', '?')})"]
            lines.append(f"Found {len(elements)} elements:")
            for el in elements[:60]:  # cap at 60 to avoid huge context
                rect = el.get("rect", {})
                lines.append(
                    f"  [{el['id']}] {el.get('control_type','?')} "
                    f"name={el.get('name','')!r} "
                    f"rect=({rect.get('x',0)},{rect.get('y',0)} {rect.get('w',0)}x{rect.get('h',0)})"
                )
            if len(elements) > 60:
                lines.append(f"  ... ({len(elements) - 60} more elements not shown)")
            return "\n".join(lines)
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("get_window_tree failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── Tool: find_element ────────────────────────────────────────────────────────

class FindElementTool(BaseTool):
    """Search for a UI element by name or type in the frontmost window."""

    name: str = "find_element"
    description: str = (
        "Search for a UI element by name or control type in the frontmost window. "
        "Returns matching elements with their IDs for use with move_to_element or click_element. "
        "Examples: find_element(name='address bar'), find_element(control_type='Button', name='Save')"
    )

    def _run(self, name: str = "", control_type: str = "", app_name: str = "", **kwargs) -> str:
        global _element_cache
        try:
            params: dict = {}
            if name:
                params["name"] = name
            if control_type:
                params["control_type"] = control_type
            if app_name:
                params["app_name"] = app_name
            result = _call_sidecar("find_element", params)
            elements = result.get("elements", [])
            if not elements:
                return f"No elements found matching name={name!r} control_type={control_type!r}"
            # Update cache with found elements
            with _element_cache_lock:
                existing_ids = {e["id"] for e in _element_cache}
                for el in elements:
                    if el["id"] not in existing_ids:
                        _element_cache.append(el)
            lines = [f"Found {len(elements)} matching element(s):"]
            for el in elements:
                rect = el.get("rect", {})
                lines.append(
                    f"  [{el['id']}] {el.get('control_type','?')} "
                    f"name={el.get('name','')!r} "
                    f"rect=({rect.get('x',0)},{rect.get('y',0)} {rect.get('w',0)}x{rect.get('h',0)})"
                )
            return "\n".join(lines)
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("find_element failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── Tool: move_to_element ─────────────────────────────────────────────────────

class MoveToElementTool(BaseTool):
    """Move the mouse to a UI element by its ID from get_window_tree."""

    name: str = "move_to_element"
    description: str = (
        "Move the mouse cursor to a UI element by its ID from get_window_tree or find_element. "
        "This is the preferred way to move the mouse to a specific UI element like "
        "'the address bar', 'the Save button', 'the search field', etc. "
        "Always call get_window_tree or find_element first to get the element ID."
    )

    def _run(self, element_id: int, **kwargs) -> str:
        try:
            result = _call_sidecar("move_mouse", {"element_id": element_id})
            if result.get("success"):
                return f"Moved mouse to element [{element_id}] at ({result.get('x')}, {result.get('y')})"
            return f"Failed to move mouse to element [{element_id}]"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("move_to_element failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── Tool: click_element ───────────────────────────────────────────────────────

class ClickElementTool(BaseTool):
    """Click a UI element by its ID from get_window_tree."""

    name: str = "click_element_by_id"
    description: str = (
        "Click a UI element by its ID from get_window_tree or find_element. "
        "action can be: click (default), double_click, right_click. "
        "Always call get_window_tree or find_element first to get the element ID."
    )

    def _run(self, element_id: int, action: str = "click", **kwargs) -> str:
        try:
            result = _call_sidecar("click_element", {"element_id": element_id, "action": action})
            if result.get("success"):
                return f"Clicked element [{element_id}] ({action}) at ({result.get('x')}, {result.get('y')})"
            return f"Failed to click element [{element_id}]"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("click_element_by_id failed: %s", e, exc_info=True)
            return f"Error: {e}"
