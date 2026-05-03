"""
Sidecar Tools — Python execution, drag, scroll, wait.

Talks to the local sidecar process via HTTP on 127.0.0.1:SIDECAR_PORT
for accessibility tree operations and physical interaction primitives.

Note: screen_analyze has been removed. Use screenshot_analyzer instead,
which handles the full screenshot → vision LLM → coordinate pipeline.
"""

import logging
from typing import Optional

from langchain.tools import BaseTool

logger = logging.getLogger(__name__)


def _call_sidecar(tool: str, params: dict, timeout: int = 120) -> dict:
    from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool

    return call_sidecar_tool(tool, params, timeout=timeout)


# ── run_python ────────────────────────────────────────────────────────────────

class RunPythonTool(BaseTool):
    name: str = "run_python"
    description: str = (
        "Execute arbitrary Python code on the user's machine. "
        "Use this for complex tasks that don't have dedicated tools: "
        "batch file operations, image processing, data transformation, "
        "web scraping, GUI automation via pyautogui, video editing, etc.\n"
        "The code runs as a standalone script with full system access.\n"
        "Optional: specify packages to pip install before execution.\n"
        "Example: run_python(code='import os; print(os.listdir(\"~/Desktop\"))', packages=['pillow'])"
    )

    def _run(self, code: str = "", packages: Optional[list] = None, timeout: float = 60000, **kwargs) -> str:
        if not code:
            return "Error: code parameter is required"
        try:
            params: dict = {"code": code, "timeout": timeout}
            if packages:
                params["packages"] = packages
            result = _call_sidecar("run_python", params, timeout=int(timeout / 1000) + 10)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            exit_code = result.get("exit_code", -1)
            parts = []
            if stdout:
                parts.append(f"Output:\n{stdout}")
            if stderr:
                parts.append(f"Errors:\n{stderr}")
            parts.append(f"Exit code: {exit_code}")
            return "\n".join(parts)
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("run_python failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── drag_to ───────────────────────────────────────────────────────────────────

class DragToTool(BaseTool):
    name: str = "drag_to"
    description: str = (
        "Drag from one position to another. Use element IDs from get_window_tree "
        "or raw screen coordinates.\n"
        "Parameters: from_element_id OR (from_x, from_y), "
        "to_element_id OR (to_x, to_y), duration_ms (default 500).\n"
        "Example: drag_to(from_element_id=5, to_x=800, to_y=400)"
    )

    def _run(self, from_element_id: int = 0, from_x: int = 0, from_y: int = 0,
             to_element_id: int = 0, to_x: int = 0, to_y: int = 0,
             duration_ms: int = 500, **kwargs) -> str:
        try:
            params: dict = {"duration_ms": duration_ms}
            if from_element_id:
                params["from_element_id"] = from_element_id
            else:
                params["from_x"] = from_x
                params["from_y"] = from_y
            if to_element_id:
                params["to_element_id"] = to_element_id
            else:
                params["to_x"] = to_x
                params["to_y"] = to_y
            result = _call_sidecar("drag_to", params)
            if result.get("success"):
                return f"Dragged from ({result.get('from_x')},{result.get('from_y')}) to ({result.get('to_x')},{result.get('to_y')})"
            return f"Drag failed: {result}"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("drag_to failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── scroll ────────────────────────────────────────────────────────────────────

class ScrollTool(BaseTool):
    name: str = "scroll"
    description: str = (
        "Scroll at the current mouse position or at specified coordinates.\n"
        "direction: 'up', 'down', 'left', 'right' (default: 'down')\n"
        "amount: scroll units (default: 3)\n"
        "x, y: optional coordinates to scroll at.\n"
        "Example: scroll(direction='down', amount=5)"
    )

    def _run(self, direction: str = "down", amount: int = 3,
             x: int = 0, y: int = 0, **kwargs) -> str:
        try:
            params: dict = {"direction": direction, "amount": amount}
            if x or y:
                params["x"] = x
                params["y"] = y
            result = _call_sidecar("scroll", params)
            if result.get("success"):
                return f"Scrolled {direction} by {amount}"
            return f"Scroll failed: {result}"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("scroll failed: %s", e, exc_info=True)
            return f"Error: {e}"


# ── wait_for_element ──────────────────────────────────────────────────────────

class WaitForElementTool(BaseTool):
    name: str = "wait_for_element"
    description: str = (
        "Wait until a UI element appears in the accessibility tree. "
        "Polls repeatedly until found or timeout.\n"
        "Parameters: name, control_type, timeout (ms, default 10000), "
        "interval (ms, default 500), app_name (optional).\n"
        "Example: wait_for_element(name='Save', timeout=15000)"
    )

    def _run(self, name: str = "", control_type: str = "", timeout: int = 10000,
             interval: int = 500, app_name: str = "", **kwargs) -> str:
        try:
            params: dict = {"timeout": timeout, "interval": interval}
            if name:
                params["name"] = name
            if control_type:
                params["control_type"] = control_type
            if app_name:
                params["app_name"] = app_name
            result = _call_sidecar("wait_for_element", params, timeout=int(timeout / 1000) + 5)
            if result.get("found"):
                elements = result.get("elements", [])
                lines = [f"Found {len(elements)} element(s):"]
                for el in elements[:10]:
                    rect = el.get("rect", {})
                    lines.append(
                        f"  [{el.get('id')}] {el.get('control_type','?')} "
                        f"name={el.get('name','')!r} "
                        f"rect=({rect.get('x',0)},{rect.get('y',0)} {rect.get('w',0)}x{rect.get('h',0)})"
                    )
                return "\n".join(lines)
            return f"Element not found within {timeout}ms (name={name!r}, control_type={control_type!r})"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("wait_for_element failed: %s", e, exc_info=True)
            return f"Error: {e}"