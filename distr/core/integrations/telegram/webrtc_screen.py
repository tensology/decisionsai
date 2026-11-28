"""Live screen over WebRTC for the remote app's Chrome Remote (WebRTC) transport.

The browser sends an SDP offer through the relay (``webrtc_offer``); this module
answers it with one H.264/VP8 video track of the chosen display and accepts
pointer input on the ``input`` data channel, so mouse moves never touch the relay.
aiortc runs on one private asyncio loop thread; capture and input run off it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from typing import Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

FPS = 20
# Send up to the display's native (Retina) pixels so text stays sharp when zoomed on a phone.
MAX_WIDTH = 2560
MAX_HEIGHT = 1600
# aiortc's stock encoder limits (H.264 0.5-3 Mbps from 1 Mbps, VP8 0.25-1.5 Mbps) blur desktop
# text; receiver estimates (REMB) still steer the rate inside these wider bounds.
ENCODER_BITRATES = {
    "h264": {"MIN_BITRATE": 2_000_000, "DEFAULT_BITRATE": 6_000_000, "MAX_BITRATE": 12_000_000},
    "vpx": {"MIN_BITRATE": 1_500_000, "DEFAULT_BITRATE": 5_000_000, "MAX_BITRATE": 10_000_000},
}
STUN_URLS = ["stun:stun.l.google.com:19302"]
ALLOWED_ICE_HOSTS = frozenset({"decisions.tensology.com", "stun.l.google.com"})
ALLOWED_ICE_SCHEMES = frozenset({"stun", "turn", "turns"})
OFFER_TIMEOUT_SECONDS = 15.0
VIDEO_CLOCK_RATE = 90000
INPUT_COMMANDS = {"mouse_move", "mouse_down", "mouse_up", "scroll"}
MAX_SCROLL_PIXELS = 2000
PERMISSION_ERROR = (
    "Screen Recording is off for Decisions. Turn it on in System Settings > "
    "Privacy & Security > Screen & System Audio Recording."
)


def is_benign_ice_close(exc: BaseException | None) -> bool:
    """True when aiortc raises after the ICE transport was already shut down."""
    if exc is None:
        return False
    return (
        type(exc).__name__ == "InvalidStateError"
        and "RTCIceTransport is closed" in str(exc)
    )


def webrtc_loop_exception_handler(loop, context):
    """Ignore the late __connect() race after a session or ICE transport is closed."""
    if is_benign_ice_close(context.get("exception")):
        logger.info("WebRTC ICE transport already closed; ignoring stale connect task")
        return
    loop.default_exception_handler(context)



def _ice_host(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw.split("?", 1)[0] else raw.replace(":", "://", 1))
    return (parsed.hostname or "").lower().rstrip(".")


def sanitize_ice_servers(servers) -> list:
    """Keep stun/turn URLs on known hosts only, so a client cannot redirect media."""
    if not isinstance(servers, list):
        return [{"urls": url} for url in STUN_URLS]
    out = []
    for item in servers:
        if not isinstance(item, dict):
            continue
        urls = item.get("urls")
        url_list = urls if isinstance(urls, list) else [urls]
        kept = []
        for url in url_list:
            if not isinstance(url, str) or len(url) > 256:
                continue
            scheme = url.split(":", 1)[0].lower()
            if scheme in ALLOWED_ICE_SCHEMES and _ice_host(url) in ALLOWED_ICE_HOSTS:
                kept.append(url)
        if not kept:
            continue
        entry = {"urls": kept[0] if len(kept) == 1 else kept}
        username, credential = item.get("username"), item.get("credential")
        if isinstance(username, str) and isinstance(credential, str):
            if 1 <= len(username) <= 128 and 1 <= len(credential) <= 256:
                entry["username"] = username
                entry["credential"] = credential
        out.append(entry)
    return out or [{"urls": url} for url in STUN_URLS]


def _rtc_ice_servers(servers):
    from aiortc import RTCIceServer
    out = []
    for item in sanitize_ice_servers(servers):
        kwargs = {"urls": item["urls"]}
        if item.get("username") and item.get("credential"):
            kwargs["username"] = item["username"]
            kwargs["credential"] = item["credential"]
        out.append(RTCIceServer(**kwargs))
    return out


def target_size(width: int, height: int, max_width: int = MAX_WIDTH, max_height: int = MAX_HEIGHT,
                pixel_scale: float = 1.0):
    """Scale (width, height) points times ``pixel_scale`` (native pixels per point) to fit the
    stream limits, keeping aspect; even sizes for yuv420p."""
    if width <= 0 or height <= 0:
        raise ValueError("screen has no size")
    pixel_scale = pixel_scale if pixel_scale and pixel_scale > 0 else 1.0
    width, height = width * pixel_scale, height * pixel_scale
    scale = min(1.0, max_width / width, max_height / height)
    out_w = max(2, int(width * scale) // 2 * 2)
    out_h = max(2, int(height * scale) // 2 * 2)
    return out_w, out_h


def _as_geometry(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    geo = item.get("geometry") if "geometry" in item and "width" not in item else item
    if not isinstance(geo, dict):
        return None
    try:
        out = {k: int(geo.get(k) or 0) for k in ("x", "y", "width", "height")}
    except (TypeError, ValueError):
        return None
    if out["width"] <= 0 or out["height"] <= 0:
        return None
    return out


def union_geometry(screens) -> dict:
    """Bounding box of the given displays in desktop points, using their real arrangement."""
    geos = [g for g in (_as_geometry(item) for item in (screens or [])) if g]
    if not geos:
        raise ValueError("screen geometry is missing")
    min_x = min(g["x"] for g in geos)
    min_y = min(g["y"] for g in geos)
    max_x = max(g["x"] + g["width"] for g in geos)
    max_y = max(g["y"] + g["height"] for g in geos)
    return {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y}


def composite_display_frames(union: dict, parts, scale: float = 1.0):
    """Paste captured display frames onto a black canvas in their real arrangement.

    ``parts`` is a list of ``(geometry, ndarray, pixel_format)`` where pixel_format is
    ``bgra`` or ``rgb24``. Returns ``(canvas, "bgra")``.
    """
    import numpy as np

    try:
        import cv2
    except Exception:
        cv2 = None
    from PIL import Image

    if not scale or scale <= 0:
        scale = 1.0
    out_w = max(1, int(round(union["width"] * scale)))
    out_h = max(1, int(round(union["height"] * scale)))
    canvas = np.zeros((out_h, out_w, 4), dtype=np.uint8)
    canvas[:, :, 3] = 255
    for geo, frame, pixel_format in parts or []:
        if frame is None or not isinstance(geo, dict):
            continue
        tw = max(1, int(round(geo["width"] * scale)))
        th = max(1, int(round(geo["height"] * scale)))
        arr = frame
        if cv2 is not None:
            # OpenCV area-resize is an order of magnitude faster than PIL here,
            # which is what kept a 3-display overview around 4 fps.
            interp = cv2.INTER_AREA if (tw <= arr.shape[1] and th <= arr.shape[0]) else cv2.INTER_LINEAR
            if pixel_format == "rgb24":
                resized = arr if (arr.shape[1] == tw and arr.shape[0] == th) else cv2.resize(arr, (tw, th), interpolation=interp)
                bgra = np.dstack((resized[:, :, 2], resized[:, :, 1], resized[:, :, 0], np.full(resized.shape[:2], 255, np.uint8)))
            else:
                src = arr[:, :, :4] if arr.ndim == 3 and arr.shape[2] >= 4 else np.dstack((arr[:, :, :3], np.full(arr.shape[:2], 255, np.uint8)))
                bgra = src if (src.shape[1] == tw and src.shape[0] == th) else cv2.resize(src, (tw, th), interpolation=interp)
        elif pixel_format == "rgb24":
            img = Image.fromarray(arr)
            if img.size != (tw, th):
                img = img.resize((tw, th), Image.Resampling.BILINEAR)
            rgb = np.asarray(img)
            bgra = np.dstack((rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0], np.full(rgb.shape[:2], 255, np.uint8)))
        else:
            if arr.ndim == 3 and arr.shape[2] >= 4:
                rgba = arr[:, :, [2, 1, 0, 3]]
                img = Image.fromarray(rgba)
            else:
                img = Image.fromarray(arr[:, :, :3][:, :, ::-1])
            if img.size != (tw, th):
                img = img.resize((tw, th), Image.Resampling.BILINEAR)
            converted = np.asarray(img)
            if converted.ndim == 3 and converted.shape[2] == 4:
                bgra = converted[:, :, [2, 1, 0, 3]]
            else:
                bgra = np.dstack((converted[:, :, 2], converted[:, :, 1], converted[:, :, 0], np.full(converted.shape[:2], 255, np.uint8)))
        dx = int(round((geo["x"] - union["x"]) * scale))
        dy = int(round((geo["y"] - union["y"]) * scale))
        src_x0 = max(0, -dx)
        src_y0 = max(0, -dy)
        dst_x0 = max(0, dx)
        dst_y0 = max(0, dy)
        src_x1 = min(bgra.shape[1], out_w - dst_x0 + src_x0)
        src_y1 = min(bgra.shape[0], out_h - dst_y0 + src_y0)
        if src_x1 <= src_x0 or src_y1 <= src_y0:
            continue
        dst_x1 = dst_x0 + (src_x1 - src_x0)
        dst_y1 = dst_y0 + (src_y1 - src_y0)
        canvas[dst_y0:dst_y1, dst_x0:dst_x1] = bgra[src_y0:src_y1, src_x0:src_x1]
    return canvas, "bgra"


def tune_encoders(bitrates: dict = ENCODER_BITRATES) -> None:
    """Widen aiortc's encoder bitrate bounds (module constants read on every rate update)."""
    from aiortc.codecs import h264, vpx

    for module, values in ((h264, bitrates["h264"]), (vpx, bitrates["vpx"])):
        for name, value in values.items():
            setattr(module, name, value)


def parse_input(raw) -> Optional[dict]:
    """Validate one data-channel pointer message; None when it is not a pointer command."""
    try:
        msg = json.loads(raw) if isinstance(raw, (str, bytes)) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("command") not in INPUT_COMMANDS:
        return None
    if msg["command"] == "scroll":
        try:
            dx, dy = int(msg.get("dx") or 0), int(msg.get("dy") or 0)
        except (TypeError, ValueError):
            return None
        if not dx and not dy:
            return None
        clamp = lambda v: max(-MAX_SCROLL_PIXELS, min(MAX_SCROLL_PIXELS, v))
        return {"command": "scroll", "dx": clamp(dx), "dy": clamp(dy)}
    out = {"command": msg["command"], "button": msg.get("button") if msg.get("button") in ("left", "right", "middle") else "left"}
    if msg["command"] != "mouse_up" or ("x" in msg and "y" in msg):
        try:
            out["x"] = int(msg["x"])
            out["y"] = int(msg["y"])
        except (KeyError, TypeError, ValueError):
            if msg["command"] != "mouse_up":
                return None
    try:
        out["screen_number"] = int(msg.get("screen_number") or 1)
    except (TypeError, ValueError):
        out["screen_number"] = 1
    return out


def parse_viewport(raw) -> Optional[dict]:
    """A desktop-point crop for the live stream. None when the message is not a viewport."""
    try:
        msg = json.loads(raw) if isinstance(raw, (str, bytes)) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("command") != "viewport":
        return None
    try:
        x, y = int(msg["x"]), int(msg["y"])
        width, height = int(msg["width"]), int(msg["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def parse_cursor_overlay(raw) -> Optional[bool]:
    """True/False when the client reports a local cursor overlay; None otherwise."""
    try:
        msg = json.loads(raw) if isinstance(raw, (str, bytes)) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("command") != "cursor_overlay":
        return None
    return bool(msg.get("enabled"))


def intersect_rect(geo: dict, view: dict) -> dict | None:
    """Intersection of two desktop-point rectangles, or None when they do not overlap."""
    x0 = max(geo["x"], view["x"])
    y0 = max(geo["y"], view["y"])
    x1 = min(geo["x"] + geo["width"], view["x"] + view["width"])
    y1 = min(geo["y"] + geo["height"], view["y"] + view["height"])
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def screen_capture_allowed() -> bool:
    """macOS Screen Recording permission for this process (True off macOS)."""
    if platform.system() != "Darwin":
        return True
    try:
        import Quartz

        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


class ScreenGrabber:
    """Grab one display, or every display composited in its real arrangement, as a scaled frame."""

    def __init__(self, geometry: dict, cursor_path: Optional[str] = None, displays=None):
        display_geos = [g for g in (_as_geometry(item) for item in (displays or [])) if g]
        self.displays = display_geos
        if display_geos:
            self.geometry = union_geometry(display_geos)
        else:
            self.geometry = {k: int((geometry or {}).get(k) or 0) for k in ("x", "y", "width", "height")}
            if self.geometry["width"] <= 0 or self.geometry["height"] <= 0:
                raise ValueError("screen geometry is missing")
        self._display_ids = {}
        self._view_lock = threading.Lock()
        self._view = None  # None = the full geometry; else a desktop-point crop.
        self.size = self._size_for(self.geometry)
        self._cursor = None
        self._cursor_path = cursor_path
        self._local_cursor = False

    def _size_for(self, geo: dict):
        """Same cap as a single display: native (Retina) pixels, at most MAX_WIDTH x MAX_HEIGHT."""
        return target_size(geo["width"], geo["height"], pixel_scale=self._pixel_scale())

    def set_view(self, view: Optional[dict]) -> None:
        """Crop the stream to ``view`` (desktop points) so a zoomed region stays sharp.

        The output size is the same ``target_size`` a single display of that region would
        use, so each display in a stitched zoom matches single-screen quality. The full
        union stays at the 2560-wide cap, which is enough for the unzoomed overview and
        stays inside common H.264 / phone decoder limits. A full-res composite of three
        Retina displays would be ~8714x1806, above Level 5.2.
        """
        if not view:
            rect = None
        else:
            try:
                rect = intersect_rect(self.geometry, {
                    "x": int(view["x"]), "y": int(view["y"]),
                    "width": int(view["width"]), "height": int(view["height"]),
                })
            except (KeyError, TypeError, ValueError):
                return
        size = self._size_for(rect or self.geometry)
        with self._view_lock:
            if rect == self._view and size == self.size:
                return
            self._view = rect
            self.size = size

    def set_local_cursor(self, enabled: bool) -> None:
        """Skip burning the pointer into the video when the client draws its own."""
        self._local_cursor = bool(enabled)

    def _quartz_display(self, geometry: Optional[dict] = None):

        import Quartz

        geo = geometry or self.geometry
        key = (geo["x"], geo["y"], geo["width"], geo["height"])
        if key in self._display_ids:
            return self._display_ids[key]
        err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
        best = Quartz.CGMainDisplayID()
        for display_id in (ids or [])[: count or 0]:
            bounds = Quartz.CGDisplayBounds(display_id)
            if int(bounds.origin.x) == geo["x"] and int(bounds.origin.y) == geo["y"]:
                best = display_id
                break
        self._display_ids[key] = best
        return best

    def _pixel_scale_for(self, geometry: dict) -> float:
        """Native pixels per point for one display (2.0 on Retina), 1.0 when unknown."""
        if platform.system() != "Darwin":
            return 1.0
        try:
            import Quartz

            mode = Quartz.CGDisplayCopyDisplayMode(self._quartz_display(geometry))
            pixels = Quartz.CGDisplayModeGetPixelWidth(mode)
            return max(1.0, min(3.0, pixels / geometry["width"]))
        except Exception:
            return 1.0

    def _pixel_scale(self) -> float:
        """Native pixels per point; the highest scale when compositing several displays."""
        if self.displays:
            return max(self._pixel_scale_for(geo) for geo in self.displays)
        return self._pixel_scale_for(self.geometry)

    def pointer_position(self) -> Optional[dict]:
        """Current pointer in desktop points when it is on this display, else None."""
        pos = self._pointer()
        if pos is None:
            return None
        g = self.geometry
        x, y = int(pos[0]), int(pos[1])
        if g["x"] <= x < g["x"] + g["width"] and g["y"] <= y < g["y"] + g["height"]:
            return {"x": x, "y": y}
        return None

    def _grab_one(self, geometry: dict):
        """Full-resolution writable frame of one display and its pixel format ("bgra" or "rgb24")."""
        import numpy as np

        if platform.system() == "Darwin":
            import Quartz

            image = Quartz.CGDisplayCreateImage(self._quartz_display(geometry))
            if image is None:
                return None, None
            width = Quartz.CGImageGetWidth(image)
            height = Quartz.CGImageGetHeight(image)
            row = Quartz.CGImageGetBytesPerRow(image)
            data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
            arr = np.frombuffer(data, dtype=np.uint8, count=height * row).reshape(height, row // 4, 4)
            return np.ascontiguousarray(arr[:, :width, :]), "bgra"
        from PIL import ImageGrab

        g = geometry
        img = ImageGrab.grab(bbox=(g["x"], g["y"], g["x"] + g["width"], g["y"] + g["height"]))
        return np.array(img.convert("RGB")), "rgb24"

    def _grab_raw(self):
        """Writable frame of the current view (or the full geometry) and its pixel format.

        Multi-display captures are composited at the *output* scale, not at full
        Retina union size, so the overview stays near the 20 fps budget instead
        of building a ~8K-wide canvas and downscaling it every frame.
        """
        with self._view_lock:
            view = self._view
            out_w, out_h = self.size
        target = view or self.geometry
        if len(self.displays) > 1:
            parts = []
            for geo in self.displays:
                if view and not intersect_rect(geo, view):
                    continue
                frame, fmt = self._grab_one(geo)
                parts.append((geo, frame, fmt))
            scale = out_w / target["width"] if target["width"] else 1.0
            return composite_display_frames(target, parts, scale)
        raw = self._grab_one(self.displays[0] if self.displays else self.geometry)
        if view is None or raw[0] is None:
            return raw
        frame, fmt = raw
        scale = frame.shape[1] / self.geometry["width"]
        x0 = max(0, min(frame.shape[1] - 1, int(round((view["x"] - self.geometry["x"]) * scale))))
        y0 = max(0, min(frame.shape[0] - 1, int(round((view["y"] - self.geometry["y"]) * scale))))
        x1 = max(x0 + 1, min(frame.shape[1], int(round((view["x"] + view["width"] - self.geometry["x"]) * scale))))
        y1 = max(y0 + 1, min(frame.shape[0], int(round((view["y"] + view["height"] - self.geometry["y"]) * scale))))
        return frame[y0:y1, x0:x1], fmt

    def _pointer(self):
        try:
            if platform.system() == "Darwin":
                import Quartz

                loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
                return loc.x, loc.y
            import pyautogui

            return pyautogui.position()
        except Exception:
            return None

    def _cursor_rgba(self, scale: float):
        import numpy as np

        if self._cursor is None or self._cursor[0] != scale:
            cursor = None
            path = self._cursor_path
            if path and os.path.exists(path):
                from PIL import Image

                img = Image.open(path).convert("RGBA")
                if abs(scale - 1.0) > 0.05:
                    img = img.resize((max(8, round(img.width * scale)), max(8, round(img.height * scale))), Image.Resampling.LANCZOS)
                cursor = np.asarray(img)
            self._cursor = (scale, cursor)
        return self._cursor[1]

    def draw_pointer(self, frame, pixel_format: str, geo: Optional[dict] = None):
        """Composite the pointer onto a frame in place. ``geo`` is the desktop rect of the frame."""
        import numpy as np

        if self._local_cursor:
            return frame
        pos = self._pointer()
        if pos is None:
            return frame
        g = geo or self.geometry
        out_h, out_w = frame.shape[:2]
        scale = out_w / g["width"]
        px, py = int((pos[0] - g["x"]) * scale), int((pos[1] - g["y"]) * out_h / g["height"])
        if not (0 <= px < out_w and 0 <= py < out_h):
            return frame
        cursor = self._cursor_rgba(round(scale, 2))
        colour = (255, 64, 64) if pixel_format == "bgra" else (64, 64, 255)
        if cursor is None:
            frame[max(0, py - 4):py + 5, max(0, px - 4):px + 5, :3] = colour[::-1] if pixel_format == "rgb24" else colour
            return frame
        ch, cw = cursor.shape[:2]
        h, w = min(ch, out_h - py), min(cw, out_w - px)
        alpha = cursor[:h, :w, 3:4].astype(np.float32) / 255.0
        src = cursor[:h, :w, 2::-1] if pixel_format == "bgra" else cursor[:h, :w, :3]
        region = frame[py:py + h, px:px + w, :3].astype(np.float32)
        frame[py:py + h, px:px + w, :3] = (src.astype(np.float32) * alpha + region * (1 - alpha)).astype(np.uint8)
        return frame

    def grab(self):
        """Return one scaled yuv420p av.VideoFrame of the display, or None."""
        import av

        raw, pixel_format = self._grab_raw()
        if raw is None:
            return None
        with self._view_lock:
            view = self._view
            out_w, out_h = self.size
        self.draw_pointer(raw, pixel_format, view or self.geometry)
        frame = av.VideoFrame.from_ndarray(raw, format=pixel_format)
        # Area averaging keeps small text legible when Retina pixels are scaled down.
        return frame.reformat(width=out_w, height=out_h, format="yuv420p", interpolation="AREA")


def _safe_grab(grabber: ScreenGrabber):
    try:
        return grabber.grab()
    except Exception as exc:
        logger.warning("WebRTC screen grab failed: %s", exc)
        return None


def _make_track(grabber: ScreenGrabber, executor: ThreadPoolExecutor, fps: int = FPS):
    import av
    from aiortc import MediaStreamTrack

    class ScreenVideoTrack(MediaStreamTrack):
        kind = "video"

        def __init__(self):
            super().__init__()
            self._start = None
            self._next = 0.0
            self._last = None
            self.frames = 0

        async def recv(self):
            loop = asyncio.get_running_loop()
            now = time.monotonic()
            if self._start is None:
                self._start = now
                self._next = now
            elif self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = max(self._next + 1.0 / fps, time.monotonic() - 0.5 / fps)
            frame = await loop.run_in_executor(executor, _safe_grab, grabber)
            if frame is None:
                frame = self._last
            if frame is None:
                frame = av.VideoFrame(grabber.size[0], grabber.size[1], "yuv420p")
                for plane, fill in zip(frame.planes, (16, 128, 128)):
                    plane.update(bytes([fill]) * plane.buffer_size)
            self._last = frame
            frame.pts = int((time.monotonic() - self._start) * VIDEO_CLOCK_RATE)
            frame.time_base = Fraction(1, VIDEO_CLOCK_RATE)
            self.frames += 1
            return frame

    return ScreenVideoTrack()


class InputPump:
    """Apply data-channel pointer input on one worker thread, newest move wins."""

    def __init__(self, handler: Callable[[dict], None]):
        self._handler = handler
        self._queue: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._button_down = None
        self._thread = threading.Thread(target=self._run, name="webrtc-input", daemon=True)
        self._thread.start()

    def put(self, msg: dict) -> None:
        self._queue.put(msg)

    def stop(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            msg = self._queue.get()
            if msg is None:
                break
            if msg["command"] == "mouse_move":
                # Drop stale moves already superseded in the queue.
                while True:
                    try:
                        nxt = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None or nxt["command"] != "mouse_move":
                        self._apply(msg)
                        msg = nxt
                        break
                    msg = nxt
                if msg is None:
                    break
            self._apply(msg)
        if self._button_down:
            # Never leave a button held when the session ends mid-drag.
            self._apply({"command": "mouse_up", "button": self._button_down, "screen_number": 1})

    def _apply(self, msg: dict) -> None:
        if msg["command"] == "mouse_down":
            self._button_down = msg.get("button") or "left"
        elif msg["command"] == "mouse_up":
            self._button_down = None
        try:
            self._handler(msg)
        except Exception as exc:
            logger.warning("WebRTC input %s failed: %s", msg.get("command"), exc)


class WebRTCScreenServer:
    """One live screen session at a time, driven from relay command threads."""

    def __init__(self, input_handler: Callable[[dict], None], cursor_path: Optional[str] = None,
                 grabber_factory: Callable[[dict], ScreenGrabber] = None, stun_urls=None,
                 ice_servers=None, fps: int = FPS):
        self._input_handler = input_handler
        self._cursor_path = cursor_path
        self._grabber_factory = grabber_factory or (
            lambda geo, displays=None: ScreenGrabber(geo, cursor_path, displays=displays)
        )
        if ice_servers is not None:
            self._ice_servers = sanitize_ice_servers(ice_servers)
        else:
            self._ice_servers = [{"urls": url} for url in (STUN_URLS if stun_urls is None else stun_urls)]
        self._stun_urls = [s["urls"] if isinstance(s["urls"], str) else s["urls"][0] for s in self._ice_servers]
        self._fps = fps
        self._loop = None
        self._loop_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="webrtc-capture")
        self._session = None  # (session_id, pc, pump, track)

    def _ensure_loop(self):
        with self._loop_lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                loop.set_exception_handler(webrtc_loop_exception_handler)
                threading.Thread(target=loop.run_forever, name="webrtc-loop", daemon=True).start()
                self._loop = loop
            return self._loop

    def _run(self, coro, timeout: float = OFFER_TIMEOUT_SECONDS):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()).result(timeout)

    @property
    def active_track(self):
        return self._session[3] if self._session else None

    def _make_grabber(self, geometry: dict, displays=None):
        factory = self._grabber_factory
        if displays:
            try:
                return factory(geometry, displays)
            except TypeError:
                pass
        return factory(geometry)

    def answer(self, offer_sdp: str, offer_type: str, geometry: dict, session_id: str,
               ice_servers=None, displays=None) -> dict:
        """Answer a browser offer; returns {"sdp", "type"}. Raises RuntimeError with a plain reason."""
        if offer_type != "offer" or not isinstance(offer_sdp, str) or "m=video" not in offer_sdp:
            raise RuntimeError("Invalid WebRTC offer")
        if not screen_capture_allowed():
            raise RuntimeError(PERMISSION_ERROR)
        grabber = self._make_grabber(geometry, displays)
        servers = sanitize_ice_servers(ice_servers) if ice_servers is not None else self._ice_servers
        return self._run(self._answer(offer_sdp, grabber, str(session_id or ""), servers))

    def close(self, session_id: Optional[str] = None) -> bool:
        """Close the live session (only if it matches ``session_id`` when one is given)."""
        if self._loop is None:
            return False
        return self._run(self._close(session_id), timeout=5)

    async def _close(self, session_id: Optional[str] = None) -> bool:
        session = self._session
        if not session or (session_id is not None and session[0] != session_id):
            return False
        self._session = None
        _, pc, pump, _ = session
        pump.stop()
        try:
            await pc.close()
        except Exception as exc:
            if not is_benign_ice_close(exc):
                raise
        logger.info("WebRTC screen session %s closed", session[0][:12])
        return True

    async def _answer(self, offer_sdp: str, grabber: ScreenGrabber, session_id: str, ice_servers=None) -> dict:
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        from aiortc.rtcrtpsender import RTCRtpSender

        await self._close()
        tune_encoders()
        config = RTCConfiguration(iceServers=_rtc_ice_servers(ice_servers if ice_servers is not None else self._ice_servers))
        pc = RTCPeerConnection(configuration=config)
        pump = InputPump(self._input_handler)
        track = _make_track(grabber, self._executor, self._fps)
        self._session = (session_id, pc, pump, track)

        @pc.on("datachannel")
        def on_datachannel(channel):
            @channel.on("message")
            def on_message(raw):
                view = parse_viewport(raw)
                if view is not None:
                    setter = getattr(grabber, "set_view", None)
                    if setter:
                        setter(view)
                    return
                overlay = parse_cursor_overlay(raw)
                if overlay is not None:
                    setter = getattr(grabber, "set_local_cursor", None)
                    if setter:
                        setter(overlay)
                    return
                msg = parse_input(raw)
                if msg:
                    pump.put(msg)

            @channel.on("close")
            def on_close():
                asyncio.ensure_future(self._close(session_id))

        @pc.on("connectionstatechange")
        async def on_state():
            logger.info("WebRTC screen session %s: %s", session_id[:12], pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await self._close(session_id)

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
            video = next((t for t in pc.getTransceivers() if t.kind == "video"), None)
            if video is None:
                raise RuntimeError("Offer has no video")
            video.direction = "sendonly"
            pc.addTrack(track)
            caps = RTCRtpSender.getCapabilities("video").codecs
            prefs = [c for c in caps if c.mimeType == "video/H264"] + [c for c in caps if c.mimeType == "video/VP8"]
            if prefs:
                video.setCodecPreferences(prefs)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception:
            await self._close(session_id)
            raise
        logger.info("WebRTC screen session %s answered at %sx%s, %s fps", session_id[:12], *grabber.size, self._fps)
        answer = {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "width": grabber.size[0], "height": grabber.size[1]}
        pointer = getattr(grabber, "pointer_position", lambda: None)()
        if pointer:
            # Lets a phone's trackpad-style cursor start where the real pointer is.
            answer["pointer"] = pointer
        return answer
