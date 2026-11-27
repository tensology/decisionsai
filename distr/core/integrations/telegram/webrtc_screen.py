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
    """Grab one display as a scaled video frame, with the pointer drawn in."""

    def __init__(self, geometry: dict, cursor_path: Optional[str] = None):
        self.geometry = {k: int(geometry.get(k) or 0) for k in ("x", "y", "width", "height")}
        if self.geometry["width"] <= 0 or self.geometry["height"] <= 0:
            raise ValueError("screen geometry is missing")
        self._display_id = None
        self.size = target_size(self.geometry["width"], self.geometry["height"], pixel_scale=self._pixel_scale())
        self._cursor = None
        self._cursor_path = cursor_path

    def _quartz_display(self):
        import Quartz

        if self._display_id is not None:
            return self._display_id
        err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
        best = Quartz.CGMainDisplayID()
        for display_id in (ids or [])[: count or 0]:
            bounds = Quartz.CGDisplayBounds(display_id)
            if int(bounds.origin.x) == self.geometry["x"] and int(bounds.origin.y) == self.geometry["y"]:
                best = display_id
                break
        self._display_id = best
        return best

    def _pixel_scale(self) -> float:
        """Native pixels per point for this display (2.0 on Retina), 1.0 when unknown."""
        if platform.system() != "Darwin":
            return 1.0
        try:
            import Quartz

            mode = Quartz.CGDisplayCopyDisplayMode(self._quartz_display())
            pixels = Quartz.CGDisplayModeGetPixelWidth(mode)
            return max(1.0, min(3.0, pixels / self.geometry["width"]))
        except Exception:
            return 1.0

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

    def _grab_raw(self):
        """Full-resolution writable frame and its pixel format ("bgra" or "rgb24")."""
        import numpy as np

        if platform.system() == "Darwin":
            import Quartz

            image = Quartz.CGDisplayCreateImage(self._quartz_display())
            if image is None:
                return None, None
            width = Quartz.CGImageGetWidth(image)
            height = Quartz.CGImageGetHeight(image)
            row = Quartz.CGImageGetBytesPerRow(image)
            data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
            arr = np.frombuffer(data, dtype=np.uint8, count=height * row).reshape(height, row // 4, 4)
            return np.ascontiguousarray(arr[:, :width, :]), "bgra"
        from PIL import ImageGrab

        g = self.geometry
        img = ImageGrab.grab(bbox=(g["x"], g["y"], g["x"] + g["width"], g["y"] + g["height"]))
        return np.array(img.convert("RGB")), "rgb24"

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

    def draw_pointer(self, frame, pixel_format: str):
        """Composite the pointer onto a full-resolution frame in place."""
        import numpy as np

        pos = self._pointer()
        if pos is None:
            return frame
        g = self.geometry
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
        self.draw_pointer(raw, pixel_format)
        frame = av.VideoFrame.from_ndarray(raw, format=pixel_format)
        out_w, out_h = self.size
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
        self._grabber_factory = grabber_factory or (lambda geo: ScreenGrabber(geo, cursor_path))
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
                threading.Thread(target=loop.run_forever, name="webrtc-loop", daemon=True).start()
                self._loop = loop
            return self._loop

    def _run(self, coro, timeout: float = OFFER_TIMEOUT_SECONDS):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()).result(timeout)

    @property
    def active_track(self):
        return self._session[3] if self._session else None

    def answer(self, offer_sdp: str, offer_type: str, geometry: dict, session_id: str,
               ice_servers=None) -> dict:
        """Answer a browser offer; returns {"sdp", "type"}. Raises RuntimeError with a plain reason."""
        if offer_type != "offer" or not isinstance(offer_sdp, str) or "m=video" not in offer_sdp:
            raise RuntimeError("Invalid WebRTC offer")
        if not screen_capture_allowed():
            raise RuntimeError(PERMISSION_ERROR)
        grabber = self._grabber_factory(geometry)
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
        await pc.close()
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
