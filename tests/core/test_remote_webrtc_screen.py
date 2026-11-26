"""Live screen over WebRTC (remote app "Chrome Remote (WebRTC)" transport)."""

import asyncio
import importlib.util
import json
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

_TG_DIR = Path(__file__).resolve().parents[2] / "distr" / "core" / "integrations" / "telegram"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


webrtc_screen = _load("webrtc_screen_test", _TG_DIR / "webrtc_screen.py")

_fake_tg_pkg = types.ModuleType("distr.core.integrations.telegram")
_fake_tg_pkg.__path__ = []
_fake_tg_utils = types.ModuleType("distr.core.integrations.telegram.utils")
_fake_tg_utils.hash_channel_id = lambda value: f"hash_{value}"
_fake_tg_utils.relay_internal_token = lambda: "test-token"
_TG_KEYS = ("distr.core.integrations.telegram", "distr.core.integrations.telegram.utils")
_prior_tg = {k: sys.modules[k] for k in _TG_KEYS if k in sys.modules}
try:
    sys.modules["distr.core.integrations.telegram"] = _fake_tg_pkg
    sys.modules["distr.core.integrations.telegram.utils"] = _fake_tg_utils
    remote_control = _load("remote_control_webrtc_test", _TG_DIR / "remote_control.py")
finally:
    for _k in _TG_KEYS:
        if _k in _prior_tg:
            sys.modules[_k] = _prior_tg[_k]
        else:
            sys.modules.pop(_k, None)


class _SyntheticGrabber:
    """Stands in for the real display grab; never touches the screen."""

    def __init__(self, geometry):
        self.size = webrtc_screen.target_size(geometry["width"], geometry["height"])

    def grab(self):
        import av

        frame = av.VideoFrame(self.size[0], self.size[1], "yuv420p")
        for plane, fill in zip(frame.planes, (90, 128, 128)):
            plane.update(bytes([fill]) * plane.buffer_size)
        return frame


@pytest.mark.parametrize(
    "size,expected",
    [((1920, 1080), (1600, 900)), ((3024, 1964), (1538, 1000)), ((1280, 720), (1280, 720)), ((1511, 981), (1510, 980))],
)
def test_target_size_fits_limits_and_is_even(size, expected):
    assert webrtc_screen.target_size(*size) == expected


def test_target_size_rejects_empty_screen():
    with pytest.raises(ValueError):
        webrtc_screen.target_size(0, 900)


def test_parse_input_accepts_pointer_commands_only():
    move = webrtc_screen.parse_input(json.dumps({"command": "mouse_move", "x": 5.6, "y": "7", "screen_number": 2}))
    assert move == {"command": "mouse_move", "button": "left", "x": 5, "y": 7, "screen_number": 2}
    up = webrtc_screen.parse_input('{"command": "mouse_up", "button": "right"}')
    assert up == {"command": "mouse_up", "button": "right", "screen_number": 1}
    assert webrtc_screen.parse_input('{"command": "type_text", "text": "x"}') is None
    assert webrtc_screen.parse_input('{"command": "mouse_down"}') is None
    assert webrtc_screen.parse_input("not json") is None
    assert webrtc_screen.parse_input('{"command": "mouse_down", "x": 1, "y": 2, "button": "evil"}')["button"] == "left"


def test_input_pump_coalesces_moves_and_releases_held_button():
    seen = []
    gate = threading.Event()

    def handler(msg):
        gate.wait(2)
        seen.append((msg["command"], msg.get("x")))

    pump = webrtc_screen.InputPump(handler)
    pump.put({"command": "mouse_down", "x": 1, "y": 1, "button": "left"})
    for x in range(2, 30):
        pump.put({"command": "mouse_move", "x": x, "y": 1})
    pump.stop()
    gate.set()
    pump._thread.join(2)
    assert seen[0] == ("mouse_down", 1)
    assert seen[-2] == ("mouse_move", 29)
    assert len(seen) < 10  # stale moves were dropped
    assert seen[-1] == ("mouse_up", None)  # session ended mid-drag


def test_answer_refuses_without_screen_recording_permission(monkeypatch):
    monkeypatch.setattr(webrtc_screen, "screen_capture_allowed", lambda: False)
    server = webrtc_screen.WebRTCScreenServer(Mock(), grabber_factory=_SyntheticGrabber, stun_urls=[])
    with pytest.raises(RuntimeError, match="Screen Recording"):
        server.answer("v=0\r\nm=video 9 UDP\r\n", "offer", {"width": 100, "height": 100}, "s")


def test_answer_rejects_non_video_offer():
    server = webrtc_screen.WebRTCScreenServer(Mock(), grabber_factory=_SyntheticGrabber, stun_urls=[])
    with pytest.raises(RuntimeError, match="Invalid WebRTC offer"):
        server.answer("v=0\r\n", "offer", {"width": 100, "height": 100}, "s")


def test_loopback_streams_video_and_delivers_input(monkeypatch):
    from aiortc import RTCPeerConnection, RTCSessionDescription

    monkeypatch.setattr(webrtc_screen, "screen_capture_allowed", lambda: True)
    inputs = []
    server = webrtc_screen.WebRTCScreenServer(inputs.append, grabber_factory=_SyntheticGrabber, stun_urls=[])
    geometry = {"x": 0, "y": 0, "width": 1920, "height": 1080}

    async def run():
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        channel = pc.createDataChannel("input")
        frames = []
        got_track = asyncio.Event()

        @pc.on("track")
        def on_track(track):
            async def pull():
                try:
                    while True:
                        frames.append(await track.recv())
                except Exception:
                    pass

            asyncio.ensure_future(pull())
            got_track.set()

        await pc.setLocalDescription(await pc.createOffer())
        loop = asyncio.get_running_loop()
        answer = await loop.run_in_executor(None, server.answer, pc.localDescription.sdp, "offer", geometry, "sess-1")
        assert (answer["width"], answer["height"]) == (1600, 900)
        await pc.setRemoteDescription(RTCSessionDescription(answer["sdp"], "answer"))
        deadline = time.monotonic() + 15
        while (len(frames) < 10 or channel.readyState != "open") and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        channel.send(json.dumps({"command": "mouse_move", "x": 10, "y": 20, "screen_number": 1}))
        channel.send(json.dumps({"command": "type_text", "text": "ignored"}))
        await asyncio.sleep(0.5)
        # A close for another session id must not end this one.
        assert await loop.run_in_executor(None, server.close, "other") is False
        assert server.active_track is not None
        await pc.close()
        await asyncio.sleep(0.5)
        return frames

    frames = asyncio.run(run())
    assert len(frames) >= 10
    assert (frames[-1].width, frames[-1].height) == (1600, 900)
    assert inputs == [{"command": "mouse_move", "button": "left", "x": 10, "y": 20, "screen_number": 1}]
    deadline = time.monotonic() + 5
    while server.active_track is not None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert server.active_track is None  # browser hang-up ends the session


class _SignalCollector:
    def emit(self, *_args):
        return None


class _Host(remote_control.TelegramRemoteControlMixin):
    def __init__(self):
        self.remote_control_command_received = _SignalCollector()
        self._remote_control_lock = threading.Lock()
        self.responses = []

    def _send_websocket_message(self, message):
        self.responses.append(message)


def _host_with_server(server):
    host = _Host()
    host._webrtc_server = server
    host._get_screens_list = Mock(return_value=[{"screen_number": 2, "geometry": {"x": 1920, "y": 0, "width": 1512, "height": 982}}])
    return host


def test_offer_is_answered_on_fast_path_without_input_lock():
    server = Mock()
    server.answer.return_value = {"sdp": "answer-sdp", "type": "answer", "width": 1512, "height": 982}
    host = _host_with_server(server)
    with host._remote_control_lock:  # a long mouse/keyboard command must not block signalling
        host._handle_remote_control_command({
            "command": "webrtc_offer", "request_id": "r1",
            "data": {"sdp": "offer-sdp", "type": "offer", "screen_number": 2, "session_id": "abc"},
        })
    server.answer.assert_called_once_with("offer-sdp", "offer", {"x": 1920, "y": 0, "width": 1512, "height": 982}, "abc")
    assert host.responses == [{
        "type": "remote_control_response", "command": "webrtc_offer", "request_id": "r1",
        "data": {"sdp": "answer-sdp", "type": "answer", "width": 1512, "height": 982},
    }]


def test_offer_errors_are_plain_responses():
    server = Mock()
    server.answer.side_effect = RuntimeError(webrtc_screen.PERMISSION_ERROR)
    host = _host_with_server(server)
    host._handle_remote_control_command({"command": "webrtc_offer", "request_id": "r2", "data": {"screen_number": 2}})
    assert host.responses[-1]["error"] == webrtc_screen.PERMISSION_ERROR
    host._handle_remote_control_command({"command": "webrtc_offer", "request_id": "r3", "data": {"screen_number": 9}})
    assert host.responses[-1]["error"] == "Screen 9 not found"


def test_close_targets_the_session():
    server = Mock()
    server.close.return_value = True
    host = _host_with_server(server)
    host._handle_remote_control_command({"command": "webrtc_close", "request_id": "r4", "data": {"session_id": "abc"}})
    server.close.assert_called_once_with("abc")
    assert host.responses[-1]["data"] == {"success": True, "closed": True}


def test_data_channel_input_moves_presses_and_releases(monkeypatch):
    fake = Mock()
    monkeypatch.setattr(remote_control, "pyautogui", fake)
    host = _Host()
    host._move_mouse_to = Mock()
    host._apply_webrtc_input({"command": "mouse_down", "x": 3, "y": 4, "screen_number": 2, "button": "left"})
    host._move_mouse_to.assert_called_once_with(3, 4, 2, instant=True)
    fake.mouseDown.assert_called_once_with(button="left")
    host._apply_webrtc_input({"command": "mouse_up", "button": "left", "screen_number": 2})
    fake.mouseUp.assert_called_once_with(button="left")
    assert host._move_mouse_to.call_count == 1
