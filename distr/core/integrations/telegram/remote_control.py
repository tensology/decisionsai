"""Remote control mixin — mouse, keyboard, screenshots via Telegram."""

import difflib
import json
import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Set

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0
except ImportError:
    pyautogui = None

try:
    import requests
except ImportError:
    requests = None

from distr.core.integrations.telegram.utils import hash_channel_id, relay_internal_token

logger = logging.getLogger(__name__)


class TelegramRemoteControlMixin:
    """Methods for remote-control commands received over the Telegram WebSocket."""

    def _telegram_thread_id_for_message_bus(self) -> Optional[int]:
        """Telegram DM / channel id for R15 thread ↔ chat persistence."""
        raw = None
        if hasattr(self, "_get_chat_id"):
            try:
                raw = self._get_chat_id()
            except Exception:
                raw = None
        if raw is None:
            raw = getattr(self, "telegram_user_id", None)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _handle_remote_control_command(self, data: dict):
        """
        Handle remote control commands from the server via WebSocket.
        Supported commands: list_screens, screenshot, set_mouse_position, left_click, right_click, double_click, type_text, paste_text,
        key_up, key_down, key_enter, key_page_up, key_page_down, key_break,
        key_select_all, key_copy, key_paste, instruction
        """
        # Emit signal for app visibility
        self.remote_control_command_received.emit(data)
        try:
            from distr.core.notification_routing import record_surface_activity

            record_surface_activity("remote")
        except Exception:
            pass

        # Serialize command execution to prevent race conditions
        with self._remote_control_lock:
            try:
                command = data.get("command")
                command_data = data.get("data", {})
                take_screenshot = bool(command_data.get("take_screenshot", True))
                request_id = data.get(
                    "request_id"
                )  # Optional request ID for response correlation

                logger.info(
                    f"Remote control command received: {command}, request_id={request_id}"
                )

                if command == "list_screens":
                    # Return list of all screens (force update to detect new screens)
                    screens_info = self._get_screens_list(force_update=True)
                    # Map screen_name to name to match reference format
                    # Include scale_factor for coordinate calculation (especially important for High-DPI/Retina displays)
                    formatted_screens = []
                    for screen in screens_info:
                        formatted_screens.append(
                            {
                                "screen_number": screen.get("screen_number"),
                                "name": screen.get(
                                    "screen_name",
                                    screen.get(
                                        "name",
                                        f"Screen {screen.get('screen_number', 1)}",
                                    ),
                                ),
                                "geometry": screen.get("geometry", {}),
                                "scale_factor": screen.get(
                                    "scale_factor", 1.0
                                ),  # Device pixel ratio for coordinate calculation
                            }
                        )
                    response = {
                        "type": "remote_control_response",
                        "command": "list_screens",
                        "request_id": request_id,
                        "data": {"screens": formatted_screens},
                    }
                    self._send_websocket_message(response)
                    logger.info(
                        f"Sent screen list: {len(formatted_screens)} screen(s) with geometry and scale_factor"
                    )

                elif command == "remote_audio_stop":
                    audio_request_id = str(command_data.get("request_id") or request_id or "")
                    if not hasattr(self, "_cancelled_remote_audio_requests"):
                        self._cancelled_remote_audio_requests = set()
                    if audio_request_id:
                        self._cancelled_remote_audio_requests.add(audio_request_id)
                        if len(self._cancelled_remote_audio_requests) > 100:
                            self._cancelled_remote_audio_requests = set(
                                list(self._cancelled_remote_audio_requests)[-50:]
                            )
                    pending = getattr(self, "_pending_remote_agent_response", None) or {}
                    if audio_request_id and pending.get("request_id") == audio_request_id:
                        self._pending_remote_agent_response = None
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "remote_audio_stop",
                            "request_id": request_id,
                            "data": {"success": True, "audio_request_id": audio_request_id},
                        }
                    )

                elif command == "screenshot":
                    # Capture screenshot of specific screen
                    screen_number = command_data.get("screen_number", 1)

                    # Get channel ID
                    channel = self._get_chat_id() or self.telegram_user_id
                    if not channel:
                        error_msg = "Channel ID not available (chat_id or telegram_user_id required)"
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "screenshot",
                                "request_id": request_id,
                                "error": error_msg,
                                "data": {"screen_number": screen_number},
                            }
                        )
                        return

                    # Capture and upload
                    screenshot_data = self._capture_screen_screenshot(screen_number)
                    if "error" in screenshot_data:
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "screenshot",
                                "request_id": request_id,
                                "error": screenshot_data.get("error"),
                                "data": {"screen_number": screen_number},
                            }
                        )
                    else:
                        # POST screenshot to server
                        post_result = self._post_screenshot_to_server(
                            channel=str(channel),
                            screen_number=screen_number,
                            image_data=screenshot_data.get("image_data"),
                            image_format=screenshot_data.get("format", "webp"),
                        )

                        # Clean up temp file
                        image_path = screenshot_data.get("image_path")
                        if image_path and os.path.exists(image_path):
                            try:
                                os.unlink(image_path)
                            except OSError:
                                pass

                        if "error" in post_result:
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "screenshot",
                                    "request_id": request_id,
                                    "error": post_result.get("error"),
                                    "data": {"screen_number": screen_number},
                                }
                            )
                        else:
                            # Get screen info for response
                            screens_info = self._get_screens_list()
                            screen_info = None
                            for screen in screens_info:
                                if screen.get("screen_number") == screen_number:
                                    screen_info = screen
                                    break

                            # Success - include all required fields per reference
                            response_data = {
                                "screen_number": screen_number,
                                "screen_name": screen_info.get(
                                    "screen_name", f"Screen {screen_number}"
                                )
                                if screen_info
                                else f"Screen {screen_number}",
                                "geometry": screen_info.get("geometry", {})
                                if screen_info
                                else {},
                                "format": screenshot_data.get("format", "jpeg"),
                                "filename": post_result.get("filename"),
                                "size": post_result.get("size"),
                            }
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "screenshot",
                                    "request_id": request_id,
                                    "data": response_data,
                                }
                            )

                elif command == "screenshot_stream":
                    # Fast path: capture screenshot and send raw bytes over WebSocket
                    # Binary frame format: [2 bytes screen_number big-endian] [raw WebP bytes]
                    screen_number = command_data.get("screen_number", 1)
                    import struct
                    screenshot_data = self._capture_screen_screenshot(screen_number)
                    if "error" in screenshot_data:
                        self._send_websocket_message({
                            "type": "remote_control_response",
                            "command": "screenshot_stream",
                            "request_id": request_id,
                            "error": screenshot_data.get("error") or "Screenshot capture failed",
                            "data": {"screen_number": screen_number},
                        })
                        return
                    image_data = screenshot_data.get("image_data")
                    if image_data:
                        header = struct.pack(">H", screen_number)
                        self._send_websocket_binary(header + image_data)
                    else:
                        self._send_websocket_message({
                            "type": "remote_control_response",
                            "command": "screenshot_stream",
                            "request_id": request_id,
                            "error": "Screenshot capture returned no image",
                            "data": {"screen_number": screen_number},
                        })
                    # No JSON response needed — the binary frame IS the response

                elif command == "set_mouse_position":
                    x = command_data.get("x")
                    y = command_data.get("y")
                    screen_number = command_data.get("screen_number")
                    button = command_data.get("button", "left")

                    logger.info(
                        f"DEBUG: set_mouse_position received: x={x}, y={y}, screen={screen_number}"
                    )

                    # Validate required fields per specification
                    if x is None or y is None:
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "set_mouse_position",
                                "request_id": request_id,
                                "error": "Missing x or y coordinates",
                                "data": {},
                            }
                        )
                        return

                    if screen_number is None:
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "set_mouse_position",
                                "request_id": request_id,
                                "error": "Missing screen_number (required)",
                                "data": {},
                            }
                        )
                        return

                    # Coordinates are absolute - use them directly
                    success = self._set_mouse_position(
                        x,
                        y,
                        screen_number=screen_number,
                        button=button,
                        take_screenshot=take_screenshot,
                    )
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "set_mouse_position",
                            "request_id": request_id,
                            "data": {"success": success, "x": x, "y": y},
                        }
                    )

                elif command == "mouse_down":
                    # Press and hold mouse button at optional x,y (or current position)
                    x = command_data.get("x")
                    y = command_data.get("y")
                    screen_number = command_data.get("screen_number")
                    button = command_data.get("button", "left")
                    try:
                        if x is not None and y is not None and screen_number is not None:
                            self._move_mouse_to(x, y, screen_number)
                        if pyautogui:
                            pyautogui.mouseDown(button=button)
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "mouse_down",
                            "request_id": request_id, "data": {"success": True},
                        })
                    except Exception as e:
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "mouse_down",
                            "request_id": request_id, "error": str(e), "data": {},
                        })

                elif command == "mouse_up":
                    # Release mouse button
                    button = command_data.get("button", "left")
                    try:
                        if pyautogui:
                            pyautogui.mouseUp(button=button)
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "mouse_up",
                            "request_id": request_id, "data": {"success": True},
                        })
                    except Exception as e:
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "mouse_up",
                            "request_id": request_id, "error": str(e), "data": {},
                        })

                elif command == "mouse_move":
                    # Move mouse to absolute x,y without clicking (for drag operations)
                    x = command_data.get("x")
                    y = command_data.get("y")
                    screen_number = command_data.get("screen_number")
                    if x is not None and y is not None and screen_number is not None:
                        try:
                            self._move_mouse_to(x, y, screen_number)
                            self._send_websocket_message({
                                "type": "remote_control_response", "command": "mouse_move",
                                "request_id": request_id, "data": {"success": True},
                            })
                        except Exception as e:
                            self._send_websocket_message({
                                "type": "remote_control_response", "command": "mouse_move",
                                "request_id": request_id, "error": str(e), "data": {},
                            })

                elif command == "right_click":
                    x = command_data.get("x")
                    y = command_data.get("y")
                    screen_number = command_data.get("screen_number")

                    if x is not None and y is not None:
                        success = self._set_mouse_position(
                            x,
                            y,
                            screen_number=screen_number,
                            button="right",
                            take_screenshot=take_screenshot,
                        )
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "right_click",
                                "request_id": request_id,
                                "data": {"success": success},
                            }
                        )
                    else:
                        # Right click current pos
                        try:
                            if pyautogui:
                                pyautogui.rightClick()
                            # Take screenshot
                            screens_info = self._get_screens_list()
                            # Logic simplified for brevity - assumes current screen capture logic if needed
                            # For now just strictly obey command
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "right_click",
                                    "request_id": request_id,
                                    "data": {"success": True},
                                }
                            )
                        except ImportError:
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "right_click",
                                    "request_id": request_id,
                                    "error": "pyautogui not available",
                                    "data": {},
                                }
                            )

                elif command == "left_click":
                    # Left click at current cursor position (no coordinates needed)
                    try:
                        if pyautogui:
                            pyautogui.click()
                        if take_screenshot:
                            # Take screenshot after click
                            try:
                                time.sleep(0.15)
                                from distr.core.screen_utils import (
                                    get_current_mouse_screen_simple,
                                )
                                screen_info = get_current_mouse_screen_simple()
                                if screen_info and "screen_number" in screen_info:
                                    target_screen_number = screen_info["screen_number"]
                                    screenshot_data = self._capture_screen_screenshot(
                                        target_screen_number, draw_cursor=True
                                    )
                                    if "error" not in screenshot_data:
                                        channel = self._get_chat_id() or self.telegram_user_id
                                        if channel:
                                            self._post_screenshot_to_server(
                                                channel=str(channel),
                                                screen_number=target_screen_number,
                                                image_data=screenshot_data.get("image_data"),
                                                image_format=screenshot_data.get("format", "webp"),
                                            )
                            except Exception as e:
                                logger.error(f"Error taking screenshot after left click: {e}")
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "left_click",
                                "request_id": request_id,
                                "data": {"success": True},
                            }
                        )
                    except ImportError:
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "left_click",
                                "request_id": request_id,
                                "error": "pyautogui not available",
                                "data": {},
                            }
                        )
                    except Exception as e:
                        logger.error(f"Error performing left click: {e}")
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "left_click",
                                "request_id": request_id,
                                "error": f"Failed to perform left click: {str(e)}",
                                "data": {},
                            }
                        )

                elif command == "double_click":
                    x = command_data.get("x")
                    y = command_data.get("y")
                    screen_number = command_data.get("screen_number")

                    if x is not None and y is not None:
                        success = self._double_click(
                            x, y, screen_number=screen_number, take_screenshot=take_screenshot
                        )
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "double_click",
                                "request_id": request_id,
                                "data": {"success": success},
                            }
                        )
                    else:
                        # Double click current pos
                        try:
                            if pyautogui:
                                pyautogui.doubleClick()
                            success = True
                            # Take screenshot after double click
                            if success and take_screenshot:
                                try:
                                    time.sleep(0.2)
                                    from distr.core.screen_utils import (
                                        get_current_mouse_screen_simple,
                                    )

                                    screen_info = get_current_mouse_screen_simple()
                                    if screen_info and "screen_number" in screen_info:
                                        target_screen_number = screen_info[
                                            "screen_number"
                                        ]
                                        screenshot_data = (
                                            self._capture_screen_screenshot(
                                                target_screen_number, draw_cursor=True
                                            )
                                        )
                                        if "error" not in screenshot_data:
                                            channel = (
                                                self._get_chat_id()
                                                or self.telegram_user_id
                                            )
                                            if channel:
                                                self._post_screenshot_to_server(
                                                    channel=str(channel),
                                                    screen_number=target_screen_number,
                                                    image_data=screenshot_data.get(
                                                        "image_data"
                                                    ),
                                                    image_format=screenshot_data.get(
                                                        "format", "webp"
                                                    ),
                                                )
                                except Exception as e:
                                    logger.error(
                                        f"Error taking screenshot after double click: {e}"
                                    )
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "double_click",
                                    "request_id": request_id,
                                    "data": {"success": success},
                                }
                            )
                        except ImportError:
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "double_click",
                                    "request_id": request_id,
                                    "error": "pyautogui not available",
                                    "data": {},
                                }
                            )
                        except Exception as e:
                            logger.error(f"Error performing double click: {e}")
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "double_click",
                                    "request_id": request_id,
                                    "error": f"Failed to perform double click: {str(e)}",
                                    "data": {},
                                }
                            )

                elif command == "type_text":
                    text_to_type = command_data.get("text", "")
                    if text_to_type:
                        block_reason = self._get_type_text_block_reason(
                            text=text_to_type, command_data=command_data
                        )
                        if block_reason:
                            logger.warning(
                                "Blocked type_text command request_id=%s: %s",
                                request_id,
                                block_reason,
                            )
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "type_text",
                                    "request_id": request_id,
                                    "error": block_reason,
                                    "data": {},
                                }
                            )
                            return
                        success = self._type_text_quick(
                            text_to_type,
                            take_screenshot=take_screenshot,
                            dictation=bool(command_data.get("dictation")),
                        )
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "type_text",
                                "request_id": request_id,
                                "data": {
                                    "success": success,
                                    "text_length": len(text_to_type),
                                },
                            }
                        )
                    else:
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "type_text",
                                "request_id": request_id,
                                "error": "Missing text parameter",
                                "data": {},
                            }
                        )

                elif command == "paste_text":
                    text_to_paste = command_data.get("text", "")
                    if text_to_paste:
                        success = self._paste_text_quick(
                            str(text_to_paste), take_screenshot=take_screenshot
                        )
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "paste_text",
                                "request_id": request_id,
                                "data": {
                                    "success": success,
                                    "text_length": len(str(text_to_paste)),
                                },
                            }
                        )
                    else:
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "paste_text",
                                "request_id": request_id,
                                "error": "Missing text parameter",
                                "data": {},
                            }
                        )

                elif command == "key_up":
                    success = self._press_key("up", take_screenshot=take_screenshot)
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_up",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_down":
                    success = self._press_key("down", take_screenshot=take_screenshot)
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_down",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_enter":
                    success = self._press_key("enter", take_screenshot=take_screenshot)
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_enter",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_backspace":
                    success = self._press_key("backspace", take_screenshot=take_screenshot)
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_backspace",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_escape":
                    success = self._press_key("escape", take_screenshot=take_screenshot)
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_escape",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_page_up":
                    success = self._press_key("pageup", take_screenshot=take_screenshot)
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_page_up",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_page_down":
                    success = self._press_key("pagedown", take_screenshot=take_screenshot)
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_page_down",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_break":
                    # Control + C (break/interrupt in terminal)
                    success = self._press_key_combination(
                        ["ctrl", "c"], take_screenshot=take_screenshot
                    )
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_break",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_swap_window":
                    # Command + ~ (macOS) or Alt + Tab (Windows/Linux) — switch windows
                    import platform
                    if platform.system() == "Darwin":
                        success = self._press_key_combination(
                            ["command", "`"], take_screenshot=take_screenshot
                        )
                    else:
                        success = self._press_key_combination(
                            ["alt", "tab"], take_screenshot=take_screenshot
                        )
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_swap_window",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_minimize":
                    import platform
                    if platform.system() == "Darwin":
                        success = self._press_key_combination(["command", "m"], take_screenshot=take_screenshot)
                    else:
                        success = self._press_key_combination(["win", "down"], take_screenshot=take_screenshot)
                    self._send_websocket_message({
                        "type": "remote_control_response", "command": "key_minimize",
                        "request_id": request_id, "data": {"success": success},
                    })

                elif command == "key_maximize":
                    import platform
                    if platform.system() == "Darwin":
                        success = self._press_key_combination(["ctrl", "command", "f"], take_screenshot=take_screenshot)
                    else:
                        success = self._press_key_combination(["win", "up"], take_screenshot=take_screenshot)
                    self._send_websocket_message({
                        "type": "remote_control_response", "command": "key_maximize",
                        "request_id": request_id, "data": {"success": success},
                    })

                elif command == "key_close_window":
                    import platform
                    if platform.system() == "Darwin":
                        success = self._press_key_combination(["command", "w"], take_screenshot=take_screenshot)
                    else:
                        success = self._press_key_combination(["ctrl", "w"], take_screenshot=take_screenshot)
                    self._send_websocket_message({
                        "type": "remote_control_response", "command": "key_close_window",
                        "request_id": request_id, "data": {"success": success},
                    })

                elif command == "key_select_all":
                    # Control + A (or Command + A on macOS) - Select all
                    import platform

                    if platform.system() == "Darwin":
                        # Use Command on macOS for standard shortcuts
                        success = self._press_key_combination(
                            ["command", "a"], take_screenshot=take_screenshot
                        )
                    else:
                        # Use Control on Windows/Linux
                        success = self._press_key_combination(
                            ["ctrl", "a"], take_screenshot=take_screenshot
                        )
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_select_all",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_copy":
                    # Control + C (or Command + C on macOS) - Copy
                    import platform

                    if platform.system() == "Darwin":
                        # Use Command on macOS for standard shortcuts
                        success = self._press_key_combination(
                            ["command", "c"], take_screenshot=take_screenshot
                        )
                    else:
                        # Use Control on Windows/Linux
                        success = self._press_key_combination(
                            ["ctrl", "c"], take_screenshot=take_screenshot
                        )
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_copy",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "key_paste":
                    # Control + V (or Command + V on macOS) - Paste
                    import platform

                    if platform.system() == "Darwin":
                        # Use Command on macOS for standard shortcuts
                        success = self._press_key_combination(
                            ["command", "v"], take_screenshot=take_screenshot
                        )
                    else:
                        # Use Control on Windows/Linux
                        success = self._press_key_combination(
                            ["ctrl", "v"], take_screenshot=take_screenshot
                        )
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": "key_paste",
                            "request_id": request_id,
                            "data": {"success": success},
                        }
                    )

                elif command == "instruction":
                    # Send instruction to agent as if it came from Telegram
                    instruction_text = command_data.get("text", "")
                    if not instruction_text:
                        self._send_websocket_message(
                            {
                                "type": "remote_control_response",
                                "command": "instruction",
                                "request_id": request_id,
                                "error": "Missing required field: text",
                                "data": {},
                            }
                        )
                    else:
                        try:
                            from distr.core.integrations.bus import (
                                get_integration_message_bus,
                            )

                            # Remote-app voice/text instructions should follow the
                            # remote return path (no local desktop player window).
                            # Treat as Telegram-originated and force text-format
                            # response so the remote app can handle TTS playback.
                            self._mark_remote_agent_response_context(
                                request_id=request_id,
                                source_command="instruction",
                                mode="command",
                            )
                            if hasattr(self, "_current_input_type"):
                                self._current_input_type = "text"
                            get_integration_message_bus().deliver_telegram_user_input(
                                text=str(instruction_text),
                                image_path=None,
                                telegram_chat_id=self._telegram_thread_id_for_message_bus(),
                                speak=False,
                            )
                            logger.info(
                                f"Forwarded instruction to agent via message bus: '{instruction_text[:50]}...'"
                            )
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "instruction",
                                    "request_id": request_id,
                                    "data": {
                                        "success": True,
                                        "message": "Instruction forwarded to agent",
                                    },
                                }
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to forward instruction to agent: {e}",
                                exc_info=True,
                            )
                            self._send_websocket_message(
                                {
                                    "type": "remote_control_response",
                                    "command": "instruction",
                                    "request_id": request_id,
                                    "error": f"Failed to forward instruction: {str(e)}",
                                    "data": {},
                                }
                            )

                elif command == "file_download":
                    # Stream a file from desktop to server as binary WS chunks
                    # Server will forward these to the phone as HTTP response
                    import os, struct
                    file_path = command_data.get("path", "")
                    home = os.path.realpath(os.path.expanduser("~"))
                    try:
                        real = os.path.realpath(file_path)
                        if real != home and not real.startswith(home + os.sep):
                            raise PermissionError("Path outside home directory")
                        if not os.path.isfile(real):
                            raise FileNotFoundError(f"Not a file: {real}")
                        file_size = os.path.getsize(real)
                        file_name = os.path.basename(real)
                        # Send metadata first as JSON
                        self._send_websocket_message({
                            "type": "remote_control_response",
                            "command": "file_download",
                            "request_id": request_id,
                            "data": {"name": file_name, "size": file_size, "status": "starting"},
                        })
                        # Stream file as binary chunks
                        # Binary frame format: [4 bytes "FILE"] [request_id as null-terminated string] [chunk data]
                        CHUNK_SIZE = 128 * 1024  # 128KB chunks to reduce socket monopolization
                        rid_bytes = request_id.encode("utf-8") + b"\x00"
                        with open(real, "rb") as f:
                            while True:
                                chunk = f.read(CHUNK_SIZE)
                                if not chunk:
                                    break
                                frame = b"FILE" + rid_bytes + chunk
                                self._send_websocket_binary(frame)
                                # Yield between chunks so control commands can be processed promptly.
                                time.sleep(0.005)
                        # Send completion marker
                        self._send_websocket_message({
                            "type": "remote_control_response",
                            "command": "file_download",
                            "request_id": request_id,
                            "data": {"status": "complete", "name": file_name, "size": file_size},
                        })
                    except Exception as e:
                        logger.error(f"File download error: {e}", exc_info=True)
                        self._send_websocket_message({
                            "type": "remote_control_response",
                            "command": "file_download",
                            "request_id": request_id,
                            "error": str(e), "data": {},
                        })

                elif command == "file_upload_init":
                    # Prepare to receive a file upload from server
                    # Server will send binary chunks, desktop writes them to disk
                    import os
                    directory = command_data.get("directory", "")
                    filename = command_data.get("filename", "")
                    file_size = command_data.get("size", 0)
                    home = os.path.realpath(os.path.expanduser("~"))
                    try:
                        real_dir = os.path.realpath(directory)
                        if real_dir != home and not real_dir.startswith(home + os.sep):
                            raise PermissionError("Directory outside home")
                        if not os.path.isdir(real_dir):
                            raise FileNotFoundError(f"Not a directory: {real_dir}")
                        safe_name = os.path.basename(filename)
                        dest = os.path.join(real_dir, safe_name)
                        # Store upload state
                        if not hasattr(self, '_pending_uploads'):
                            self._pending_uploads = {}
                        self._pending_uploads[request_id] = {
                            "path": dest, "size": file_size, "received": 0,
                            "file": open(dest, "wb"),
                        }
                        self._send_websocket_message({
                            "type": "remote_control_response",
                            "command": "file_upload_init",
                            "request_id": request_id,
                            "data": {"status": "ready", "path": dest},
                        })
                    except Exception as e:
                        logger.error(f"File upload init error: {e}", exc_info=True)
                        self._send_websocket_message({
                            "type": "remote_control_response",
                            "command": "file_upload_init",
                            "request_id": request_id,
                            "error": str(e), "data": {},
                        })

                elif command == "voice_text_input":
                    # Text input from remote UI modal (tap on voice button)
                    # No transcription needed — just route the text
                    text = command_data.get("text", "").strip()
                    mode = command_data.get("mode", "dictate")
                    if not text:
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "voice_text_input",
                            "request_id": request_id, "error": "No text", "data": {"mode": mode},
                        })
                    else:
                        if mode == "command":
                            from distr.core.integrations.bus import (
                                get_integration_message_bus,
                            )

                            self._mark_remote_agent_response_context(
                                request_id=request_id,
                                source_command="voice_text_input",
                                mode=mode,
                            )
                            if hasattr(self, "_current_input_type"):
                                self._current_input_type = "text"
                            get_integration_message_bus().deliver_telegram_user_input(
                                text=str(text),
                                image_path=None,
                                telegram_chat_id=self._telegram_thread_id_for_message_bus(),
                                speak=False,
                            )
                        elif mode == "dictate":
                            self._type_text_quick(text, dictation=True)
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "voice_text_input",
                            "request_id": request_id, "data": {"text": text, "mode": mode},
                        })

                elif command == "voice_transcribe":
                    # Voice transcription from remote UI — save audio, send to agent's loaded STT
                    import base64 as _b64
                    import tempfile
                    import uuid
                    import threading

                    audio_b64 = command_data.get("audio_data", "")
                    mime_type = command_data.get("mime_type", "audio/webm")
                    mode = command_data.get("mode", "dictate")

                    if not audio_b64:
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "voice_transcribe",
                            "request_id": request_id, "error": "No audio data", "data": {"mode": mode},
                        })
                    else:
                        def _do_voice_transcribe():
                            try:
                                audio_bytes = _b64.b64decode(audio_b64)

                                # Determine extension
                                ext = ".webm"
                                if "mp4" in mime_type: ext = ".mp4"
                                elif "ogg" in mime_type: ext = ".ogg"
                                elif "wav" in mime_type: ext = ".wav"

                                # Save to temp file
                                tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
                                tmp.write(audio_bytes)
                                tmp.close()
                                audio_path = tmp.name

                                # Convert to WAV 16kHz mono if ffmpeg available (same as Telegram voice)
                                wav_path = audio_path + ".wav"
                                try:
                                    import subprocess
                                    result = subprocess.run(
                                        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
                                        capture_output=True, timeout=10
                                    )
                                    if result.returncode == 0:
                                        import os
                                        os.unlink(audio_path)
                                        audio_path = wav_path
                                    else:
                                        logger.warning("ffmpeg conversion failed (rc=%d), using original", result.returncode)
                                except FileNotFoundError:
                                    logger.warning("ffmpeg not found, using original audio file")
                                except Exception as e:
                                    logger.warning("ffmpeg error: %s, using original", e)

                                # Send to agent's STT via command queue
                                from PyQt6.QtWidgets import QApplication
                                app = QApplication.instance()
                                if not app or not hasattr(app, "agent_command_queue"):
                                    self._send_websocket_message({
                                        "type": "remote_control_response", "command": "voice_transcribe",
                                        "request_id": request_id, "error": "Agent not available", "data": {"mode": mode},
                                    })
                                    return

                                voice_req_id = str(uuid.uuid4())

                                # Register callback for this transcription result
                                import threading as _thr
                                result_event = _thr.Event()
                                result_holder = {}

                                if not hasattr(self, '_pending_voice_callbacks'):
                                    self._pending_voice_callbacks = {}
                                self._pending_voice_callbacks[voice_req_id] = (result_event, result_holder)

                                app.agent_command_queue.put(
                                    ("transcribe_file", {
                                        "audio_file_path": audio_path,
                                        "request_id": voice_req_id,
                                        "input_type": "voice",
                                    }),
                                    block=False,
                                )

                                # Wait for result (up to 30s)
                                if result_event.wait(timeout=30.0):
                                    transcript = result_holder.get("transcript", "")
                                    error = result_holder.get("error")
                                    if transcript:
                                        self._send_websocket_message({
                                            "type": "remote_control_response", "command": "voice_transcribe",
                                            "request_id": request_id,
                                            "data": {"text": transcript, "mode": mode},
                                        })
                                        # For command mode, also send the text to the agent as input
                                        if mode == "command":
                                            from distr.core.integrations.bus import (
                                                get_integration_message_bus,
                                            )

                                            self._track_recent_remote_voice_command(
                                                request_id=request_id, transcript=str(transcript)
                                            )
                                            # Route as Telegram-originated so the desktop
                                            # player does not open for remote-app voice
                                            # requests. Force text response format so the
                                            # remote app can stream/play TTS itself.
                                            self._mark_remote_agent_response_context(
                                                request_id=request_id,
                                                source_command="voice_transcribe",
                                                mode=mode,
                                            )
                                            if hasattr(self, "_current_input_type"):
                                                self._current_input_type = "text"
                                            get_integration_message_bus().deliver_telegram_user_input(
                                                text=str(transcript),
                                                image_path=None,
                                                telegram_chat_id=self._telegram_thread_id_for_message_bus(),
                                                speak=False,
                                            )
                                    else:
                                        self._send_websocket_message({
                                            "type": "remote_control_response", "command": "voice_transcribe",
                                            "request_id": request_id,
                                            "error": error or "No speech detected", "data": {"mode": mode},
                                        })
                                else:
                                    self._pending_voice_callbacks.pop(voice_req_id, None)
                                    self._send_websocket_message({
                                        "type": "remote_control_response", "command": "voice_transcribe",
                                        "request_id": request_id,
                                        "error": "Transcription timed out", "data": {"mode": mode},
                                    })

                                # Cleanup temp file
                                try:
                                    import os
                                    os.unlink(audio_path)
                                except OSError:
                                    pass

                            except Exception as e:
                                logger.error("voice_transcribe error: %s", e, exc_info=True)
                                self._send_websocket_message({
                                    "type": "remote_control_response", "command": "voice_transcribe",
                                    "request_id": request_id, "error": str(e), "data": {"mode": mode},
                                })

                        threading.Thread(target=_do_voice_transcribe, daemon=True, name="VoiceTranscribe").start()

                elif command == "api_relay":
                    # Relay an API call to the local web UI server and return the response
                    api_method = (command_data.get("method") or "GET").upper()
                    api_path = command_data.get("path", "")
                    api_body = command_data.get("body")
                    response_type = (command_data.get("response_type") or "json").lower()
                    if not api_path:
                        self._send_websocket_message({
                            "type": "remote_control_response", "command": "api_relay",
                            "request_id": request_id, "error": "Missing path", "data": {},
                        })
                    else:
                        try:
                            import requests as _req
                            from distr.gui.web.server import get_unified_server
                            from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token
                            srv = get_unified_server()
                            base = srv.get_url() if srv and srv.is_running else "http://127.0.0.1:8765"
                            url = base + api_path
                            headers = {"Content-Type": "application/json", INTERNAL_AUTH_HEADER: get_internal_api_token()}
                            if api_method == "GET":
                                resp = _req.get(url, headers=headers, timeout=15)
                            elif api_method == "POST":
                                resp = _req.post(url, json=api_body, headers=headers, timeout=15)
                            elif api_method == "PATCH":
                                resp = _req.patch(url, json=api_body, headers=headers, timeout=15)
                            elif api_method == "PUT":
                                resp = _req.put(url, json=api_body, headers=headers, timeout=15)
                            elif api_method == "DELETE":
                                resp = _req.delete(url, headers=headers, timeout=15)
                            else:
                                resp = _req.request(api_method, url, json=api_body, headers=headers, timeout=15)
                            if response_type == "base64":
                                import base64 as _base64

                                resp_data = {
                                    "body_base64": _base64.b64encode(resp.content).decode("ascii"),
                                    "content_type": resp.headers.get("content-type", ""),
                                    "size_bytes": len(resp.content),
                                }
                            else:
                                try:
                                    resp_data = resp.json()
                                except Exception:
                                    resp_data = {"text": resp.text}
                            self._send_websocket_message({
                                "type": "remote_control_response", "command": "api_relay",
                                "request_id": request_id,
                                "data": {"status": resp.status_code, "response": resp_data},
                            })
                        except Exception as e:
                            logger.error(f"API relay error: {e}", exc_info=True)
                            self._send_websocket_message({
                                "type": "remote_control_response", "command": "api_relay",
                                "request_id": request_id, "error": str(e), "data": {},
                            })

                else:
                    self._send_websocket_message(
                        {
                            "type": "remote_control_response",
                            "command": command,
                            "request_id": request_id,
                            "error": f"Unknown command: {command}",
                            "data": {},
                        }
                    )

            except Exception as e:
                logger.error(
                    f"Error handling remote control command: {e}", exc_info=True
                )
                self._send_websocket_message(
                    {
                        "type": "remote_control_response",
                        "command": data.get("command", "unknown"),
                        "request_id": data.get("request_id"),
                        "error": str(e),
                        "data": {},
                    }
                )

    def _mark_remote_agent_response_context(
        self, request_id: Optional[str], source_command: str, mode: str = "command"
    ) -> None:
        """Store context for routing the next agent response back to remote-app."""
        try:
            self._pending_remote_agent_response = {
                "request_id": request_id,
                "source_command": source_command,
                "mode": mode,
                "created_at": time.time(),
            }
            logger.info(
                "Remote response context set: request_id=%s source=%s mode=%s",
                request_id,
                source_command,
                mode,
            )
        except Exception:
            logger.exception("Failed to set remote response context")

    def _track_recent_remote_voice_command(
        self, request_id: Optional[str], transcript: str
    ) -> None:
        """Store latest command-mode voice transcript to prevent accidental auto-typing."""
        self._recent_remote_voice_command = {
            "request_id": request_id,
            "transcript": (transcript or "").strip(),
            "created_at": time.time(),
        }
        logger.info(
            "Tracked recent remote voice command: request_id=%s chars=%d",
            request_id,
            len((transcript or "").strip()),
        )

    def _get_type_text_block_reason(self, text: str, command_data: dict) -> Optional[str]:
        """
        Prevent accidental text injection after command-mode voice.

        Remote voice in command mode should route through the agent/tool stack rather than
        immediately becoming raw keyboard text. If a near-immediate type_text follows a
        command-mode voice transcript with similar text, block it unless explicitly allowed.
        """
        try:
            if bool(command_data.get("allow_from_voice")):
                return None

            recent = getattr(self, "_recent_remote_voice_command", None) or {}
            if not recent:
                return None

            recent_ts = float(recent.get("created_at") or 0)
            if (time.time() - recent_ts) > 10.0:
                return None

            recent_transcript = (recent.get("transcript") or "").strip().lower()
            current_text = (text or "").strip().lower()
            if not recent_transcript or not current_text:
                return None

            similarity = difflib.SequenceMatcher(
                None, recent_transcript, current_text
            ).ratio()
            if similarity >= 0.7:
                return (
                    "Blocked type_text after command voice transcription "
                    "(likely unintended auto-typing). Use dictate mode or set allow_from_voice=true."
                )
            return None
        except Exception:
            logger.exception("type_text safety check failed; allowing command")
            return None

    def _get_screens_list(self, force_update: bool = False) -> list:
        """Get list of all screens with their information (thread-safe)."""
        screens_info = []

        # Method 1: Try reading from existing cache (unless forcing update)
        if not force_update:
            try:
                from distr.core.screen_utils import _screen_info_cache

                if _screen_info_cache and "screens" in _screen_info_cache:
                    return _screen_info_cache["screens"]
            except Exception as e:
                logger.debug(f"Cache check failed: {e}")

        # Method 2: If cache empty/missing OR forcing update, request update from Main Thread via Signal
        # This is necessary because we are running in a background thread and cannot access QScreen directly.
        try:
            import threading
            import time

            if threading.current_thread() is not threading.main_thread():
                # Emit signal to request main thread to update cache
                self._request_screen_update_signal.emit()
                # Wait up to 1 second for cache to be populated
                for _ in range(10):
                    time.sleep(0.1)
                    # If we forced update, we want to ensure we get the NEW data.
                    # Ideally we would wait for a confirmation, but polling the variable is a decent proxy
                    # assuming the main thread updates it quickly.
                    from distr.core.screen_utils import _screen_info_cache

                    if _screen_info_cache and "screens" in _screen_info_cache:
                        # If forcing update, we might want to wait a tiny bit more to ensure it's fresh?
                        # Actually, _screen_utils logic replaces the dict key, so if we see it, it is likely the 'current' state.
                        return _screen_info_cache["screens"]

                logger.warning(
                    "Timed out waiting for screen cache update from main thread"
                )
        except (ConnectionRefusedError, BrokenPipeError, OSError) as e:
            # During shutdown, connections may be closed - this is expected
            logger.debug(
                f"Screen update request failed during shutdown (expected): {e}"
            )
        except Exception as e:
            logger.error(f"Error requesting screen update: {e}")

        # Method 3: Fallback (should ideally not be reached if signal works)
        # Note: We removed the direct QApplication access as it causes crashes in bg threads.
        return screens_info

    def _draw_cursor_on_pil_image(self, img, screen_geo: dict):
        """Composite cursor.png onto a PIL Image in memory. Returns the modified image."""
        try:
            from PIL import Image, ImageDraw

            if not pyautogui:
                return img
            cursor_x, cursor_y = pyautogui.position()

            screen_left = screen_geo["x"]
            screen_top = screen_geo["y"]
            screen_right = screen_left + screen_geo["width"]
            screen_bottom = screen_top + screen_geo["height"]

            if not (
                screen_left <= cursor_x < screen_right
                and screen_top <= cursor_y < screen_bottom
            ):
                return img

            # Convert to RGBA for compositing
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            img_width, img_height = img.size
            rel_x = cursor_x - screen_left
            rel_y = cursor_y - screen_top

            # Handle DPI scaling
            scale_factor = 1.0
            if img_width != screen_geo["width"] or img_height != screen_geo["height"]:
                scale_x = img_width / screen_geo["width"]
                scale_y = img_height / screen_geo["height"]
                scale_factor = (scale_x + scale_y) / 2.0
                rel_x = int(rel_x * scale_x)
                rel_y = int(rel_y * scale_y)

            rel_x = max(0, min(rel_x, img_width - 1))
            rel_y = max(0, min(rel_y, img_height - 1))

            from distr.core.paths import IMAGES_DIR
            cursor_img_path = os.path.join(IMAGES_DIR, "cursor.png")
            if os.path.exists(cursor_img_path):
                cursor_img = Image.open(cursor_img_path)
                if cursor_img.mode != "RGBA":
                    cursor_img = cursor_img.convert("RGBA")

                if scale_factor != 1.0:
                    new_width = max(8, int(cursor_img.width * scale_factor))
                    new_height = max(8, int(cursor_img.height * scale_factor))
                    cursor_img = cursor_img.resize(
                        (new_width, new_height), Image.Resampling.LANCZOS
                    )

                img.paste(cursor_img, (rel_x, rel_y), cursor_img)
            else:
                # Fallback marker so cursor remains visible even if cursor.png is absent.
                radius = max(5, int(6 * scale_factor))
                draw = ImageDraw.Draw(img, "RGBA")
                draw.ellipse(
                    (rel_x - radius, rel_y - radius, rel_x + radius, rel_y + radius),
                    fill=(255, 64, 64, 220),
                    outline=(255, 255, 255, 220),
                    width=max(1, int(scale_factor)),
                )
            return img

        except Exception as e:
            logger.warning(f"Failed to draw cursor marker: {e}")
            return img

    def _capture_screen_pil_image(self, screen_number: int, draw_cursor: bool = True):
        """Capture a screen as PIL Image, optionally compositing cursor marker."""
        try:
            import platform
            import subprocess
            import tempfile
            from PIL import Image

            screens_info = self._get_screens_list()
            if not screens_info:
                return None
            if screen_number < 1 or screen_number > len(screens_info):
                return None

            target_screen_info = screens_info[screen_number - 1]
            screen_geo = target_screen_info["geometry"]
            system = platform.system()
            pil_img = None

            if system == "Darwin":
                x, y = screen_geo["x"], screen_geo["y"]
                width, height = screen_geo["width"], screen_geo["height"]
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                    png_tmp_path = tmp_file.name
                try:
                    result = subprocess.run(
                        ["screencapture", "-x", "-R", f"{x},{y},{width},{height}", png_tmp_path],
                        capture_output=True, timeout=10,
                    )
                    if result.returncode != 0 or not os.path.exists(png_tmp_path):
                        result = subprocess.run(
                            ["screencapture", "-R", f"{x},{y},{width},{height}", png_tmp_path],
                            capture_output=True, timeout=10,
                        )
                    if os.path.exists(png_tmp_path) and os.path.getsize(png_tmp_path) > 0:
                        pil_img = Image.open(png_tmp_path)
                        pil_img.load()
                finally:
                    try:
                        os.unlink(png_tmp_path)
                    except OSError:
                        pass
            elif system == "Windows":
                from PIL import ImageGrab
                bbox = (
                    screen_geo["x"],
                    screen_geo["y"],
                    screen_geo["x"] + screen_geo["width"],
                    screen_geo["y"] + screen_geo["height"],
                )
                pil_img = ImageGrab.grab(bbox=bbox)
            else:
                try:
                    from PIL import ImageGrab
                    bbox = (
                        screen_geo["x"],
                        screen_geo["y"],
                        screen_geo["x"] + screen_geo["width"],
                        screen_geo["y"] + screen_geo["height"],
                    )
                    pil_img = ImageGrab.grab(bbox=bbox)
                except Exception:
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                            png_tmp = tmp_file.name
                        subprocess.run(["gnome-screenshot", "-f", png_tmp], timeout=10)
                        if os.path.exists(png_tmp):
                            pil_img = Image.open(png_tmp)
                            pil_img.load()
                    finally:
                        try:
                            if 'png_tmp' in locals() and os.path.exists(png_tmp):
                                os.unlink(png_tmp)
                        except OSError:
                            pass

            if pil_img is None:
                return None

            if draw_cursor:
                pil_img = self._draw_cursor_on_pil_image(pil_img, screen_geo)

            if pil_img.mode in ("RGBA", "LA", "P"):
                rgb_img = Image.new("RGB", pil_img.size, (255, 255, 255))
                if pil_img.mode == "P":
                    pil_img = pil_img.convert("RGBA")
                rgb_img.paste(
                    pil_img, mask=pil_img.split()[-1] if pil_img.mode == "RGBA" else None
                )
                pil_img = rgb_img
            elif pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")

            return pil_img
        except Exception:
            return None

    def _capture_screen_screenshot(
        self, screen_number: int, draw_cursor: bool = True
    ) -> dict:
        """Capture screenshot of a specific screen.
        
        Pipeline: capture → PIL Image in memory → composite cursor → single WebP encode to BytesIO → done.
        """
        try:
            import io
            from PIL import Image

            screens_info = self._get_screens_list()

            if not screens_info:
                return {"error": "No screens available", "screen_number": screen_number}

            if screen_number < 1 or screen_number > len(screens_info):
                return {
                    "error": f"Invalid screen number: {screen_number}",
                    "screen_number": screen_number,
                }

            target_screen_info = screens_info[screen_number - 1]
            screen_geo = target_screen_info["geometry"]
            pil_img = self._capture_screen_pil_image(screen_number, draw_cursor=draw_cursor)

            if pil_img is None:
                return {"error": "Failed to capture screenshot", "screen_number": screen_number}

            # Single WebP encode straight to memory buffer (no temp file)
            buf = io.BytesIO()
            pil_img.save(buf, "WEBP", quality=65, method=2)
            image_data = buf.getvalue()

            return {
                "screen_number": screen_number,
                "screen_name": target_screen_info.get("name", f"Screen {screen_number}"),
                "geometry": screen_geo,
                "image_data": image_data,
                "format": "webp",
            }

        except Exception as e:
            logger.error(f"Error capturing screenshot: {e}", exc_info=True)
            return {"error": str(e), "screen_number": screen_number}

    def _move_mouse_to(self, x: int, y: int, screen_number: int):
        """Move mouse to absolute coordinates with smooth animation. Used for drag operations."""
        if not pyautogui:
            return
        # Minimal validation — clamp to screen bounds
        screens_info = self._get_screens_list()
        for s in screens_info:
            if s.get("screen_number") == screen_number:
                geo = s["geometry"]
                x = max(geo["x"], min(x, geo["x"] + geo["width"] - 1))
                y = max(geo["y"], min(y, geo["y"] + geo["height"] - 1))
                break
        # Calculate distance-based duration for natural drag feel
        cx, cy = pyautogui.position()
        dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
        # ~0.3s for short drags, up to ~0.8s for long cross-screen drags
        duration = min(0.8, max(0.2, dist / 2000))
        pyautogui.moveTo(x, y, duration=duration, _pause=False)

    def _set_mouse_position(
        self,
        x: int,
        y: int,
        screen_number: Optional[int] = None,
        button: str = "left",
        take_screenshot: bool = False,
    ) -> bool:
        """
        Set mouse position to absolute x, y coordinates.

        Per webserver-side specification:
        - x and y are absolute coordinates in global coordinate space (already calculated by sender)
        - screen_number is required and used to lock to the specified screen
        - Validates coordinates are within the specified screen bounds
        - Clamps coordinates to screen bounds if slightly out of range (with warning)
        - No coordinate transformation is performed - coordinates are used directly

        The sender calculates absolute coordinates using:
        1. Image pixel coordinates (from click on displayed image)
        2. Logical screen coordinates (dividing by scale_factor from list_screens)
        3. Absolute coordinates (adding screen geometry offset)
        """
        try:
            if not pyautogui:
                logger.error("pyautogui not available")
                return False
            import time

            # screen_number is required per specification
            if screen_number is None:
                logger.error("set_mouse_position: screen_number is required")
                return False

            # Get screen info to validate coordinates
            screens_info = self._get_screens_list(force_update=True)
            target_screen = None
            for screen_info in screens_info:
                if screen_info.get("screen_number") == screen_number:
                    target_screen = screen_info
                    break

            if not target_screen:
                logger.error(f"set_mouse_position: Screen {screen_number} not found")
                return False

            geo = target_screen["geometry"]
            screen_x = geo["x"]
            screen_y = geo["y"]
            screen_width = geo["width"]
            screen_height = geo["height"]

            # Validate that coordinates are within the specified screen bounds
            # Coordinates are absolute, so check if they fall within this screen's geometry
            if not (
                screen_x <= x < screen_x + screen_width
                and screen_y <= y < screen_y + screen_height
            ):
                logger.warning(
                    f"set_mouse_position: Coordinates ({x}, {y}) are outside screen {screen_number} bounds "
                    f"({screen_x}, {screen_y}, {screen_width}x{screen_height}). "
                    f"Clamping to screen bounds."
                )
                # Clamp coordinates to screen bounds
                x = max(screen_x, min(x, screen_x + screen_width - 1))
                y = max(screen_y, min(y, screen_y + screen_height - 1))

            logger.info(
                f"set_mouse_position: Moving to absolute coordinates ({x}, {y}) on screen {screen_number}"
            )

            # Move mouse to absolute coordinates (no transformation needed - they're already absolute)
            pyautogui.moveTo(x, y)
            time.sleep(0.3)

            # Click
            if button == "right":
                pyautogui.rightClick(x, y)
            elif button == "middle":
                pyautogui.middleClick(x, y)
            else:
                pyautogui.click(x, y)

            # Screenshot if requested
            if take_screenshot:
                try:
                    # Use the screen_number we already validated
                    screenshot_data = self._capture_screen_screenshot(
                        screen_number, draw_cursor=True
                    )
                    if "error" not in screenshot_data:
                        channel = self._get_chat_id() or self.telegram_user_id
                        if channel:
                            self._post_screenshot_to_server(
                                channel=str(channel),
                                screen_number=screen_number,
                                image_data=screenshot_data.get("image_data"),
                                image_format=screenshot_data.get("format", "webp"),
                            )
                except Exception as e:
                    logger.error(f"Error taking screenshot after mouse move: {e}")

            return True
        except Exception as e:
            logger.error(f"Error setting mouse position: {e}")
            return False

    def _double_click(
        self,
        x: int,
        y: int,
        screen_number: Optional[int] = None,
        take_screenshot: bool = False,
    ) -> bool:
        """
        Perform a double click at the specified coordinates.

        Args:
            x: Absolute X coordinate
            y: Absolute Y coordinate
            screen_number: Target screen number (required)
            take_screenshot: Whether to take a screenshot after the click

        Returns:
            True if successful, False otherwise
        """
        try:
            if not pyautogui:
                logger.error("pyautogui not available")
                return False
            import time

            # screen_number is required per specification
            if screen_number is None:
                logger.error("double_click: screen_number is required")
                return False

            # Get screen info to validate coordinates
            screens_info = self._get_screens_list(force_update=True)
            target_screen = None
            for screen_info in screens_info:
                if screen_info.get("screen_number") == screen_number:
                    target_screen = screen_info
                    break

            if not target_screen:
                logger.error(f"double_click: Screen {screen_number} not found")
                return False

            geo = target_screen["geometry"]
            screen_x = geo["x"]
            screen_y = geo["y"]
            screen_width = geo["width"]
            screen_height = geo["height"]

            # Validate that coordinates are within the specified screen bounds
            if not (
                screen_x <= x < screen_x + screen_width
                and screen_y <= y < screen_y + screen_height
            ):
                logger.warning(
                    f"double_click: Coordinates ({x}, {y}) are outside screen {screen_number} bounds "
                    f"({screen_x}, {screen_y}, {screen_width}x{screen_height}). "
                    f"Clamping to screen bounds."
                )
                # Clamp coordinates to screen bounds
                x = max(screen_x, min(x, screen_x + screen_width - 1))
                y = max(screen_y, min(y, screen_y + screen_height - 1))

            logger.info(
                f"double_click: Double clicking at absolute coordinates ({x}, {y}) on screen {screen_number}"
            )

            # Move mouse to absolute coordinates
            pyautogui.moveTo(x, y)
            time.sleep(0.3)

            # Double click
            pyautogui.doubleClick(x, y)

            # Screenshot if requested
            if take_screenshot:
                try:
                    # Use the screen_number we already validated
                    screenshot_data = self._capture_screen_screenshot(
                        screen_number, draw_cursor=True
                    )
                    if "error" not in screenshot_data:
                        channel = self._get_chat_id() or self.telegram_user_id
                        if channel:
                            self._post_screenshot_to_server(
                                channel=str(channel),
                                screen_number=screen_number,
                                image_data=screenshot_data.get("image_data"),
                                image_format=screenshot_data.get("format", "webp"),
                            )
                except Exception as e:
                    logger.error(f"Error taking screenshot after double click: {e}")

            return True
        except Exception as e:
            logger.error(f"Error performing double click: {e}")
            return False

    def _paste_text_quick(self, text: str, take_screenshot: bool = False) -> bool:
        """Paste text through the clipboard without appending Enter."""
        try:
            import platform
            import time

            if not pyautogui:
                logger.error("pyautogui not available")
                return False

            from distr.core.agent.tools.clipboard.clipboard_actions import (
                set_clipboard_content,
            )

            if not set_clipboard_content(text):
                logger.error("Failed to set clipboard for remote paste_text")
                return False

            modifier = "command" if platform.system() == "Darwin" else "ctrl"
            time.sleep(0.08)
            pyautogui.hotkey(modifier, "v")
            success = True

            if take_screenshot:
                time.sleep(0.2)
                from distr.core.screen_utils import get_current_mouse_screen_simple

                screen_info = get_current_mouse_screen_simple()
                if screen_info and "screen_number" in screen_info:
                    target_screen_number = screen_info["screen_number"]
                    screenshot_data = self._capture_screen_screenshot(
                        target_screen_number, draw_cursor=True
                    )
                    if "error" not in screenshot_data:
                        channel = self._get_chat_id() or self.telegram_user_id
                        if channel:
                            self._post_screenshot_to_server(
                                channel=str(channel),
                                screen_number=target_screen_number,
                                image_data=screenshot_data.get("image_data"),
                                image_format=screenshot_data.get("format", "webp"),
                            )
            return success
        except Exception as e:
            logger.error(f"Error pasting text: {e}", exc_info=True)
            return False

    def _type_text_quick(self, text: str, take_screenshot: bool = False, dictation: bool = False) -> bool:
        """Quickly type text as keyboard input."""
        try:
            import time
            from distr.core.audio.dictation import insert_text

            success = insert_text(text, instant=None if dictation else True, press_enter=True)

            if take_screenshot and success:
                try:
                    time.sleep(0.2)
                    from distr.core.screen_utils import get_current_mouse_screen_simple

                    screen_info = get_current_mouse_screen_simple()
                    if screen_info and "screen_number" in screen_info:
                        target_screen_number = screen_info["screen_number"]
                        screenshot_data = self._capture_screen_screenshot(
                            target_screen_number, draw_cursor=True
                        )
                        if "error" not in screenshot_data:
                            channel = self._get_chat_id() or self.telegram_user_id
                            if channel:
                                self._post_screenshot_to_server(
                                    channel=str(channel),
                                    screen_number=target_screen_number,
                                    image_data=screenshot_data.get("image_data"),
                                    image_format=screenshot_data.get("format", "webp"),
                                )
                except Exception as e:
                    logger.error(f"Error screenshot after typing: {e}")

            return success
        except Exception as e:
            logger.error(f"Error typing text: {e}")
            return False

    def _press_key(self, key: str, take_screenshot: bool = False) -> bool:
        """Press a single key."""
        try:
            import time
            import platform

            if not pyautogui:
                logger.error("pyautogui not available")
                return False

            success = False
            system = platform.system()

            # Map key names to pyautogui key names
            key_map = {
                "up": "up",
                "down": "down",
                "enter": "enter",
                "backspace": "backspace",
                "escape": "escape",
                "pageup": "pageup",
                "pagedown": "pagedown",
            }

            pyautogui_key = key_map.get(key.lower(), key.lower())

            if system == "Darwin":  # macOS
                import subprocess

                # Map to AppleScript key codes
                applescript_key_map = {
                    "up": "126",  # Up arrow
                    "down": "125",  # Down arrow
                    "enter": "36",  # Enter
                    "pageup": "116",  # Page Up
                    "pagedown": "121",  # Page Down
                }
                key_code = applescript_key_map.get(pyautogui_key)
                if key_code:
                    try:
                        subprocess.run(
                            [
                                "osascript",
                                "-e",
                                f'tell application "System Events" to key code {key_code}',
                            ],
                            capture_output=True,
                            timeout=5,
                        )
                        success = True
                    except Exception:
                        # Fallback to pyautogui
                        pyautogui.press(pyautogui_key)
                        success = True
                else:
                    pyautogui.press(pyautogui_key)
                    success = True
            else:
                # Windows/Linux
                pyautogui.press(pyautogui_key)
                success = True

            if take_screenshot and success:
                try:
                    time.sleep(0.2)
                    from distr.core.screen_utils import get_current_mouse_screen_simple

                    screen_info = get_current_mouse_screen_simple()
                    if screen_info and "screen_number" in screen_info:
                        target_screen_number = screen_info["screen_number"]
                        screenshot_data = self._capture_screen_screenshot(
                            target_screen_number, draw_cursor=True
                        )
                        if "error" not in screenshot_data:
                            channel = self._get_chat_id() or self.telegram_user_id
                            if channel:
                                self._post_screenshot_to_server(
                                    channel=str(channel),
                                    screen_number=target_screen_number,
                                    image_data=screenshot_data.get("image_data"),
                                    image_format=screenshot_data.get("format", "webp"),
                                )
                                # Clean up temp file
                                image_path = screenshot_data.get("image_path")
                                if image_path and os.path.exists(image_path):
                                    try:
                                        os.unlink(image_path)
                                    except OSError:
                                        pass
                except Exception as e:
                    logger.error(f"Error taking screenshot after key press: {e}")

            return success
        except Exception as e:
            logger.error(f"Error pressing key: {e}", exc_info=True)
            return False

    def _press_key_combination(self, keys: list, take_screenshot: bool = False) -> bool:
        """Press a key combination (e.g., ['ctrl', 'c'])."""
        try:
            import time
            import platform

            if not pyautogui:
                logger.error("pyautogui not available")
                return False

            success = False
            system = platform.system()

            if system == "Darwin":  # macOS
                import subprocess

                if len(keys) == 2:
                    modifier = keys[0].lower()
                    key_char = keys[1].lower()

                    # Map modifier to AppleScript modifier name
                    if modifier in ["ctrl", "control"]:
                        applescript_modifier = "control"
                    elif modifier in ["cmd", "command"]:
                        applescript_modifier = "command"
                    elif modifier in ["alt", "option"]:
                        applescript_modifier = "option"
                    elif modifier == "shift":
                        applescript_modifier = "shift"
                    else:
                        applescript_modifier = modifier

                    # Map common keys to AppleScript
                    key_map = {"a": "a", "c": "c", "v": "v", "x": "x"}

                    if key_char in key_map:
                        try:
                            subprocess.run(
                                [
                                    "osascript",
                                    "-e",
                                    f'tell application "System Events" to keystroke "{key_map[key_char]}" using {applescript_modifier} down',
                                ],
                                capture_output=True,
                                timeout=5,
                            )
                            success = True
                        except Exception:
                            # Fallback to pyautogui
                            # On macOS, pyautogui.hotkey uses Command by default for standard shortcuts
                            # But we want to respect the modifier specified
                            if modifier in ["cmd", "command"]:
                                pyautogui.hotkey("command", key_char)
                            else:
                                pyautogui.hotkey(*keys)
                            success = True
                    else:
                        # Use pyautogui for other combinations
                        if modifier in ["cmd", "command"]:
                            pyautogui.hotkey("command", key_char)
                        else:
                            pyautogui.hotkey(*keys)
                        success = True
                else:
                    # Use pyautogui for other combinations
                    pyautogui.hotkey(*keys)
                    success = True
            else:
                # Windows/Linux
                pyautogui.hotkey(*keys)
                success = True

            if take_screenshot and success:
                try:
                    time.sleep(0.2)
                    from distr.core.screen_utils import get_current_mouse_screen_simple

                    screen_info = get_current_mouse_screen_simple()
                    if screen_info and "screen_number" in screen_info:
                        target_screen_number = screen_info["screen_number"]
                        screenshot_data = self._capture_screen_screenshot(
                            target_screen_number, draw_cursor=True
                        )
                        if "error" not in screenshot_data:
                            channel = self._get_chat_id() or self.telegram_user_id
                            if channel:
                                self._post_screenshot_to_server(
                                    channel=str(channel),
                                    screen_number=target_screen_number,
                                    image_data=screenshot_data.get("image_data"),
                                    image_format=screenshot_data.get("format", "webp"),
                                )
                                # Clean up temp file
                                image_path = screenshot_data.get("image_path")
                                if image_path and os.path.exists(image_path):
                                    try:
                                        os.unlink(image_path)
                                    except OSError:
                                        pass
                except Exception as e:
                    logger.error(f"Error taking screenshot after key combination: {e}")

            return success
        except Exception as e:
            logger.error(f"Error pressing key combination: {e}", exc_info=True)
            return False

    def _post_screenshot_to_server(
        self, channel: str, screen_number: int, image_data: bytes, image_format: str
    ) -> dict:
        """POST screenshot to server as multipart form data (no base64 overhead).

        The channel value is hashed with today's date so the real
        Telegram user-ID is never sent over the wire.
        """
        if requests is None:
            return {"error": "requests package not available"}

        try:
            hashed_channel = hash_channel_id(channel)

            base = self.server_url.split("/ws/")[0]
            base = base.replace("wss://", "https://").replace("ws://", "http://")
            api_url = f"{base}/api/remote/screenshot/"

            # Multipart upload — raw bytes, no base64 encoding overhead
            files = {
                "file": (f"screen_{screen_number}.{image_format}", image_data, f"image/{image_format}")
            }
            form_data = {
                "channel": hashed_channel,
                "screen_number": str(screen_number),
                "format": image_format,
            }
            relay_token = relay_internal_token()
            headers = {"X-Relay-Internal-Token": relay_token} if relay_token else {}

            logger.info(f"POSTing screenshot to {api_url} (size={len(image_data)})")
            response = requests.post(api_url, data=form_data, files=files, headers=headers, timeout=30)

            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}: {response.text}"}

        except Exception as e:
            logger.error(f"Screenshot POST error: {e}")
            return {"error": str(e)}
