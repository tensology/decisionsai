"""
Accessibility Tree Tool — find and interact with UI elements via the OS
accessibility API (macOS: AppleScript/JXA, Windows: UIAutomation).

Talks to the local sidecar process via HTTP on 127.0.0.1:SIDECAR_PORT.
"""

import logging
import os
import threading
from typing import Optional

import requests
from langchain.tools import BaseTool
from distr.core.agent.services.computer_use_context import (
    record_action,
    record_candidate_target,
    record_observation,
)

logger = logging.getLogger(__name__)

_SIDECAR_PORT = int(os.environ.get("DECISIONSAI_SIDECAR_HTTP_PORT", "11435"))
_SIDECAR_BASE = f"http://127.0.0.1:{_SIDECAR_PORT}"
_SIDECAR_TIMEOUT = 20

_element_cache: list[dict] = []
_element_cache_lock = threading.Lock()


def _call_sidecar(tool: str, params: dict) -> dict:
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
            "Sidecar not running. Accessibility tree tools require the sidecar "
            "(starts automatically with the app)."
        )
    except Exception as e:
        raise RuntimeError(f"Sidecar call failed ({tool}): {e}")


def _is_sidecar_running() -> bool:
    try:
        requests.get(f"{_SIDECAR_BASE}/health", timeout=2)
        return True
    except Exception:
        return False


def _find_by_name(name: str, app_name: str = "") -> Optional[dict]:
    """Helper: find the best-matching element by name, updating the cache."""
    params: dict = {"name": name}
    if app_name:
        params["app_name"] = app_name
    result = _call_sidecar("find_element", params)
    elements = result.get("elements", [])
    if not elements:
        return None
    with _element_cache_lock:
        existing_ids = {e["id"] for e in _element_cache}
        for el in elements:
            if el["id"] not in existing_ids:
                _element_cache.append(el)
    return elements[0]


# ── Tool: get_window_tree ─────────────────────────────────────────────────────

class GetWindowTreeTool(BaseTool):
    name: str = "get_window_tree"
    description: str = (
        "Get all UI elements in the frontmost window as a structured list. "
        "Each element has an id, name, control_type, and bounding rect. "
        "Use the returned element IDs with move_to_element or click_element_by_id. "
        "Use this to explore what's on screen before targeting a specific element."
    )

    def _run(self, app_name: str = "", depth: int = 4, **kwargs) -> str:
        try:
            params: dict = {"depth": depth}
            if app_name:
                params["app_name"] = app_name
            result = _call_sidecar("get_window_tree", params)
            elements = result.get("elements", [])
            with _element_cache_lock:
                _element_cache.clear()
                _element_cache.extend(elements)
            record_observation(
                source="accessibility_tree",
                details={
                    "tool": "get_window_tree",
                    "app_name": app_name,
                    "window_title": result.get("window_title", "unknown"),
                    "pid": result.get("pid"),
                    "element_count": len(elements),
                },
            )
            lines = [f"Window: {result.get('window_title', 'unknown')} (pid={result.get('pid', '?')})"]
            lines.append(f"Found {len(elements)} elements:")
            for el in elements[:60]:
                rect = el.get("rect", {})
                lines.append(
                    f"  [{el['id']}] {el.get('control_type','?')} "
                    f"name={el.get('name','')!r} "
                    f"rect=({rect.get('x',0)},{rect.get('y',0)} {rect.get('w',0)}x{rect.get('h',0)})"
                )
            if len(elements) > 60:
                lines.append(f"  ... ({len(elements) - 60} more not shown)")
            return "\n".join(lines)
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("get_window_tree failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── Tool: find_element ────────────────────────────────────────────────────────

class FindElementTool(BaseTool):
    name: str = "find_element"
    description: str = (
        "Search for a UI element by name or control type in the frontmost window. "
        "Returns matching elements with their numeric IDs. "
        "Pass the id to move_to_element or click_element_by_id. "
        "Examples: find_element(name='address bar'), find_element(control_type='Button', name='Save')"
    )

    def _run(self, name: str = "", control_type: str = "", app_name: str = "", **kwargs) -> str:
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
            with _element_cache_lock:
                existing_ids = {e["id"] for e in _element_cache}
                for el in elements:
                    if el["id"] not in existing_ids:
                        _element_cache.append(el)
            first = elements[0]
            rect = first.get("rect", {})
            if rect:
                cx = int(rect.get("x", 0) + rect.get("w", 0) / 2)
                cy = int(rect.get("y", 0) + rect.get("h", 0) / 2)
                record_candidate_target(
                    source="accessibility_tree",
                    x=cx,
                    y=cy,
                    screen=1,
                    description=first.get("name", ""),
                    status="found",
                )
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
    name: str = "move_to_element"
    description: str = (
        "Move the mouse to a UI element. "
        "Provide EITHER element_id (integer from find_element/get_window_tree) "
        "OR element_name (string to search for automatically). "
        "element_id is preferred when you already have it. "
        "Example: move_to_element(element_id=42) or move_to_element(element_name='Save button')"
    )

    def _run(self, element_id: int = 0, element_name: str = "", app_name: str = "", **kwargs) -> str:
        try:
            # Auto-lookup by name if no id provided
            if not element_id and element_name:
                el = _find_by_name(element_name, app_name)
                if not el:
                    return f"No element found matching name={element_name!r}"
                element_id = el["id"]
                logger.info("move_to_element: resolved '%s' -> id=%s", element_name, element_id)

            if not element_id:
                return (
                    "Error: provide element_id (int) or element_name (str). "
                    "Call find_element first to get an element ID."
                )

            result = _call_sidecar("move_mouse", {"element_id": element_id})
            if result.get("success"):
                record_action(
                    "move_mouse",
                    "success",
                    {
                        "source": "accessibility_tree",
                        "element_id": element_id,
                        "x": result.get("x"),
                        "y": result.get("y"),
                    },
                )
                return f"Moved mouse to element [{element_id}] at ({result.get('x')}, {result.get('y')})"
            return f"Failed to move mouse to element [{element_id}]"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("move_to_element failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── Tool: click_element ───────────────────────────────────────────────────────

class ClickElementTool(BaseTool):
    name: str = "click_element_by_id"
    description: str = (
        "Click a UI element. "
        "Provide EITHER element_id (integer from find_element/get_window_tree) "
        "OR element_name (string to search for automatically). "
        "action can be: click (default), double_click, right_click. "
        "Example: click_element_by_id(element_id=42) or click_element_by_id(element_name='OK button')"
    )

    def _run(self, element_id: int = 0, element_name: str = "", action: str = "click", app_name: str = "", **kwargs) -> str:
        try:
            # Auto-lookup by name if no id provided
            if not element_id and element_name:
                el = _find_by_name(element_name, app_name)
                if not el:
                    return f"No element found matching name={element_name!r}"
                element_id = el["id"]
                logger.info("click_element_by_id: resolved '%s' -> id=%s", element_name, element_id)

            if not element_id:
                return (
                    "Error: provide element_id (int) or element_name (str). "
                    "Call find_element first to get an element ID."
                )

            result = _call_sidecar("click_element", {"element_id": element_id, "action": action})
            if result.get("success"):
                record_action(
                    action,
                    "success",
                    {
                        "source": "accessibility_tree",
                        "element_id": element_id,
                        "x": result.get("x"),
                        "y": result.get("y"),
                    },
                )
                return f"Clicked element [{element_id}] ({action}) at ({result.get('x')}, {result.get('y')})"
            return f"Failed to click element [{element_id}]"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("click_element_by_id failed: %s", e, exc_info=True)
            return f"Error: {e}"
