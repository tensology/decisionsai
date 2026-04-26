"""
WebRTC screen streaming for remote control.

Uses aiortc to send a live desktop video track to the remote web app.
"""

import asyncio
import logging
from fractions import Fraction
from typing import Callable, Optional

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

logger = logging.getLogger(__name__)

DEFAULT_FPS = 3.0
MIN_FPS = 0.3
MAX_FPS = 10.0


class ScreenVideoTrack(VideoStreamTrack):
    """aiortc video track that captures desktop frames."""

    def __init__(self, screen_number: int, capture_fn: Callable, fps: float = DEFAULT_FPS):
        super().__init__()
        self.screen_number = screen_number
        self._capture_fn = capture_fn
        self._fps = max(MIN_FPS, min(MAX_FPS, float(fps)))

    @property
    def fps(self) -> float:
        return self._fps

    @fps.setter
    def fps(self, value: float):
        self._fps = max(MIN_FPS, min(MAX_FPS, float(value)))

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        await asyncio.sleep(max(0.0, (1.0 / self._fps)))

        img = None
        try:
            img = self._capture_fn(self.screen_number)
        except Exception as err:
            logger.error("WebRTC capture failed: %s", err)

        if img is None:
            arr = np.zeros((720, 1280, 3), dtype=np.uint8)
        else:
            if img.mode != "RGB":
                img = img.convert("RGB")
            arr = np.asarray(img, dtype=np.uint8)

        frame = VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base if time_base else Fraction(1, 90000)
        return frame


class WebRTCSession:
    """Manages one remote WebRTC peer connection for desktop video."""

    def __init__(self, screen_number: int, capture_fn: Callable, fps: float = DEFAULT_FPS):
        self.screen_number = screen_number
        self._capture_fn = capture_fn
        self._fps = max(MIN_FPS, min(MAX_FPS, float(fps)))
        self._pc: Optional[RTCPeerConnection] = None
        self._track: Optional[ScreenVideoTrack] = None

    async def _wait_for_ice_gathering(self, timeout_sec: float = 3.0):
        pc = self._pc
        if not pc:
            return
        if pc.iceGatheringState == "complete":
            return
        waited = 0.0
        while waited < timeout_sec and pc.iceGatheringState != "complete":
            await asyncio.sleep(0.1)
            waited += 0.1

    async def create_answer(self, offer_sdp: str, offer_type: str = "offer") -> dict:
        await self.close()

        pc = RTCPeerConnection()
        self._pc = pc
        self._track = ScreenVideoTrack(
            screen_number=self.screen_number,
            capture_fn=self._capture_fn,
            fps=self._fps,
        )
        pc.addTrack(self._track)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("WebRTC connection state: %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await self.close()

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=offer_sdp, type=offer_type)
        )
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._wait_for_ice_gathering()

        local_desc = pc.localDescription
        if not local_desc:
            raise RuntimeError("WebRTC local description was not created")
        return {"type": local_desc.type, "sdp": local_desc.sdp}

    async def set_fps(self, fps: float):
        self._fps = max(MIN_FPS, min(MAX_FPS, float(fps)))
        if self._track:
            self._track.fps = self._fps

    async def close(self):
        track = self._track
        self._track = None
        if track:
            try:
                track.stop()
            except Exception:
                pass
        pc = self._pc
        self._pc = None
        if pc:
            await pc.close()
