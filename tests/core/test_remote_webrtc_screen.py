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
    [((1920, 1080), (1920, 1080)), ((3024, 1964), (2462, 1600)), ((1280, 720), (1280, 720)), ((1511, 981), (1510, 980))],
)
def test_target_size_fits_limits_and_is_even(size, expected):
    assert webrtc_screen.target_size(*size) == expected


def test_target_size_sends_retina_pixels_within_limits():
    # A 1512x982-point Retina panel is 3024x1964 pixels: sent at 2462x1600, not 1512x982.
    assert webrtc_screen.target_size(1512, 982, pixel_scale=2.0) == (2462, 1600)
    assert webrtc_screen.target_size(1920, 1080, pixel_scale=1.0) == (1920, 1080)
    assert webrtc_screen.target_size(1920, 1080, pixel_scale=0) == (1920, 1080)


def test_tune_encoders_widens_aiortc_bitrate_bounds(monkeypatch):
    from aiortc.codecs import h264, vpx

    for module in (h264, vpx):
        for name in ("MIN_BITRATE", "DEFAULT_BITRATE", "MAX_BITRATE"):
            monkeypatch.setattr(module, name, getattr(module, name))
    webrtc_screen.tune_encoders()
    assert (h264.MIN_BITRATE, h264.DEFAULT_BITRATE, h264.MAX_BITRATE) == (2_000_000, 6_000_000, 12_000_000)
    assert (vpx.MIN_BITRATE, vpx.DEFAULT_BITRATE, vpx.MAX_BITRATE) == (1_500_000, 5_000_000, 10_000_000)
    encoder = h264.H264Encoder()
    encoder.target_bitrate = 100_000_000  # a high receiver estimate is capped at the new max
    assert encoder.target_bitrate == 12_000_000


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


def test_parse_input_accepts_bounded_scroll():
    assert webrtc_screen.parse_input('{"command": "scroll", "dx": 3.7, "dy": -12}') == {"command": "scroll", "dx": 3, "dy": -12}
    assert webrtc_screen.parse_input('{"command": "scroll", "dx": 0, "dy": 99999}') == {"command": "scroll", "dx": 0, "dy": 2000}
    assert webrtc_screen.parse_input('{"command": "scroll", "dx": 0, "dy": 0}') is None
    assert webrtc_screen.parse_input('{"command": "scroll", "dy": "up"}') is None


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
        assert (answer["width"], answer["height"]) == (1920, 1080)
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
    assert (frames[-1].width, frames[-1].height) == (1920, 1080)
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
    server.answer.assert_called_once_with("offer-sdp", "offer", {"x": 1920, "y": 0, "width": 1512, "height": 982}, "abc", ice_servers=None)
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
    monkeypatch.setattr(remote_control.platform, "system", lambda: "Linux")
    host = _Host()
    host._move_mouse_to = Mock()
    host._apply_webrtc_input({"command": "mouse_down", "x": 3, "y": 4, "screen_number": 2, "button": "left"})
    host._move_mouse_to.assert_called_once_with(3, 4, 2, instant=True)
    fake.mouseDown.assert_called_once_with(button="left")
    host._apply_webrtc_input({"command": "mouse_up", "button": "left", "screen_number": 2})
    fake.mouseUp.assert_called_once_with(button="left")
    assert host._move_mouse_to.call_count == 1


def test_sanitize_ice_servers_keeps_known_hosts_and_falls_back():
    cleaned = webrtc_screen.sanitize_ice_servers([
        {"urls": "turn:evil.example:3478", "username": "x", "credential": "yyyyyyyy"},
        {"urls": "turn:decisions.tensology.com:3478?transport=udp", "username": "1700:rc", "credential": "abcd1234"},
        {"urls": "stun:stun.l.google.com:19302"},
    ])
    assert cleaned[0]["urls"] == "turn:decisions.tensology.com:3478?transport=udp"
    assert cleaned[0]["username"] == "1700:rc"
    assert cleaned[1] == {"urls": "stun:stun.l.google.com:19302"}
    assert webrtc_screen.sanitize_ice_servers([{"urls": "turn:evil.example:3478"}]) == [
        {"urls": "stun:stun.l.google.com:19302"}
    ]


def test_offer_forwards_relay_ice_servers():
    server = Mock()
    server.answer.return_value = {"sdp": "answer-sdp", "type": "answer", "width": 1512, "height": 982}
    host = _host_with_server(server)
    ice = [{"urls": "turn:decisions.tensology.com:3478?transport=udp", "username": "1700:rc", "credential": "abcd1234"}]
    host._handle_remote_control_command({
        "command": "webrtc_offer", "request_id": "r1",
        "data": {"sdp": "offer-sdp", "type": "offer", "screen_number": 2, "session_id": "abc", "ice_servers": ice},
    })
    server.answer.assert_called_once_with(
        "offer-sdp", "offer", {"x": 1920, "y": 0, "width": 1512, "height": 982}, "abc", ice_servers=ice,
    )


class _FakeQuartz(types.ModuleType):
    """Records Quartz mouse events instead of posting them (never moves the real mouse)."""

    def __init__(self):
        super().__init__("Quartz")
        self.posted = []
        names = ("kCGMouseButtonLeft", "kCGMouseButtonRight", "kCGMouseButtonCenter", "kCGEventLeftMouseDown",
                 "kCGEventLeftMouseUp", "kCGEventLeftMouseDragged", "kCGEventRightMouseDown", "kCGEventRightMouseUp",
                 "kCGEventRightMouseDragged", "kCGEventOtherMouseDown", "kCGEventOtherMouseUp",
                 "kCGEventOtherMouseDragged", "kCGEventMouseMoved", "kCGHIDEventTap", "kCGMouseEventClickState", "kCGScrollEventUnitPixel")
        for name in names:
            setattr(self, name, name)
        self.location = types.SimpleNamespace(x=100.0, y=200.0)

    def CGEventCreate(self, _source):
        return None

    def CGEventGetLocation(self, _event):
        return self.location

    def CGEventCreateMouseEvent(self, _source, kind, point, button):
        return {"kind": kind, "point": tuple(point), "button": button}

    def CGEventSetIntegerValueField(self, event, field, value):
        event[field] = value

    def CGEventCreateScrollWheelEvent(self, _source, unit, count, dy, dx):
        return {"kind": "scroll", "unit": unit, "dy": dy, "dx": dx}

    def CGEventPost(self, _tap, event):
        self.posted.append(event)


def test_mac_input_posts_drags_click_counts_and_scrolls(monkeypatch):
    quartz = _FakeQuartz()
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setattr(remote_control, "pyautogui", Mock())
    monkeypatch.setattr(remote_control.platform, "system", lambda: "Darwin")
    host = _Host()
    host._move_mouse_to = Mock()
    host._get_screens_list = lambda: [{"screen_number": 1, "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}}]
    host._apply_webrtc_input({"command": "mouse_down", "x": 100, "y": 200, "screen_number": 1, "button": "left"})
    host._move_mouse_to.assert_called_once_with(100, 200, 1, instant=True)
    # Held moves are "dragged" events (clamped to the screen), not plain moves.
    host._apply_webrtc_input({"command": "mouse_move", "x": 5000, "y": 300, "screen_number": 1, "button": "left"})
    assert host._move_mouse_to.call_count == 1
    host._apply_webrtc_input({"command": "mouse_up", "x": 1919, "y": 300, "screen_number": 1, "button": "left"})
    kinds = [e["kind"] for e in quartz.posted]
    assert kinds == ["kCGEventLeftMouseDown", "kCGEventLeftMouseDragged", "kCGEventLeftMouseDragged", "kCGEventLeftMouseUp"]
    assert quartz.posted[1]["point"] == (1919, 300)
    # A quick second tap at the same spot is a double-click.
    quartz.posted.clear()
    time.sleep(0.6)  # long after the drag: the next press starts a new click
    for _ in range(2):
        host._apply_webrtc_input({"command": "mouse_down", "x": 100, "y": 200, "screen_number": 1, "button": "left"})
        host._apply_webrtc_input({"command": "mouse_up", "x": 100, "y": 200, "screen_number": 1, "button": "left"})
    presses = [e for e in quartz.posted if e["kind"] in ("kCGEventLeftMouseDown", "kCGEventLeftMouseUp")]
    assert [e["kCGMouseEventClickState"] for e in presses] == [1, 1, 2, 2]
    quartz.posted.clear()
    host._apply_webrtc_input({"command": "scroll", "dx": 0, "dy": -40})
    assert quartz.posted == [{"kind": "scroll", "unit": "kCGScrollEventUnitPixel", "dy": -40, "dx": 0}]


def test_answer_reports_pointer_for_touch_cursor(monkeypatch):
    from aiortc import RTCPeerConnection

    monkeypatch.setattr(webrtc_screen, "screen_capture_allowed", lambda: True)

    class PointerGrabber(_SyntheticGrabber):
        def pointer_position(self):
            return {"x": 640, "y": 360}

    server = webrtc_screen.WebRTCScreenServer(lambda m: None, grabber_factory=PointerGrabber, stun_urls=[])

    async def run():
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        pc.createDataChannel("input")
        await pc.setLocalDescription(await pc.createOffer())
        loop = asyncio.get_running_loop()
        answer = await loop.run_in_executor(None, server.answer, pc.localDescription.sdp, "offer",
                                            {"x": 0, "y": 0, "width": 1280, "height": 720}, "sess-p")
        await pc.close()
        await loop.run_in_executor(None, server.close, None)
        return answer

    assert asyncio.run(run())["pointer"] == {"x": 640, "y": 360}


def test_union_geometry_uses_real_arrangement():
    screens = [
        {"x": 0, "y": 0, "width": 1512, "height": 982},
        {"screen_number": 2, "geometry": {"x": -1920, "y": -50, "width": 1920, "height": 1080}},
        {"x": 1512, "y": -80, "width": 1920, "height": 1080},
    ]
    assert webrtc_screen.union_geometry(screens) == {"x": -1920, "y": -80, "width": 5352, "height": 1110}


def test_composite_display_frames_places_screens_on_union_canvas():
    import numpy as np

    left = {"x": 0, "y": 0, "width": 10, "height": 8}
    right = {"x": 12, "y": 2, "width": 6, "height": 4}
    union = webrtc_screen.union_geometry([left, right])
    assert union == {"x": 0, "y": 0, "width": 18, "height": 8}
    left_frame = np.zeros((8, 10, 3), np.uint8)
    left_frame[:] = (255, 0, 0)
    right_frame = np.zeros((4, 6, 3), np.uint8)
    right_frame[:] = (0, 0, 255)
    canvas, fmt = webrtc_screen.composite_display_frames(
        union,
        [(left, left_frame, "rgb24"), (right, right_frame, "rgb24")],
        scale=2.0,
    )
    assert fmt == "bgra"
    assert canvas.shape == (16, 36, 4)
    assert list(canvas[0, 0]) == [0, 0, 255, 255]
    assert list(canvas[4, 24]) == [255, 0, 0, 255]
    assert list(canvas[0, 20, :3]) == [0, 0, 0]


def test_grabber_combined_uses_union_size(monkeypatch):
    displays = [
        {"x": 0, "y": 0, "width": 100, "height": 80},
        {"x": 120, "y": 0, "width": 40, "height": 80},
    ]
    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_pixel_scale", lambda self: 1.0)
    grabber = webrtc_screen.ScreenGrabber({"x": 0, "y": 0, "width": 1, "height": 1}, displays=displays)
    assert grabber.geometry == {"x": 0, "y": 0, "width": 160, "height": 80}
    assert grabber.size == webrtc_screen.target_size(160, 80, pixel_scale=1.0)




def test_parse_viewport_accepts_desktop_rect_only():
    assert webrtc_screen.parse_viewport('{"command": "viewport", "x": -10, "y": 2, "width": 100.4, "height": 80}') == {
        "x": -10, "y": 2, "width": 100, "height": 80,
    }
    assert webrtc_screen.parse_viewport('{"command": "mouse_move", "x": 1, "y": 2}') is None
    assert webrtc_screen.parse_viewport('{"command": "viewport", "x": 0, "y": 0, "width": 0, "height": 10}') is None
    assert webrtc_screen.parse_viewport("not json") is None


def test_intersect_rect_clips_to_union():
    union = {"x": -1920, "y": -80, "width": 5352, "height": 1110}
    view = webrtc_screen.intersect_rect(union, {"x": 0, "y": 0, "width": 1512, "height": 982})
    assert view == {"x": 0, "y": 0, "width": 1512, "height": 982}
    assert webrtc_screen.intersect_rect(union, {"x": 9000, "y": 0, "width": 10, "height": 10}) is None


def test_grabber_viewport_matches_single_screen_size(monkeypatch):
    """A crop the size of screen 1 is encoded at the same size as that display alone."""
    displays = [
        {"x": 0, "y": 0, "width": 1512, "height": 982},
        {"x": -1920, "y": -50, "width": 1920, "height": 1080},
        {"x": 1512, "y": -80, "width": 1920, "height": 1080},
    ]
    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_pixel_scale", lambda self: 2.0)
    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_pixel_scale_for", lambda self, geo: 2.0)
    stitched = webrtc_screen.ScreenGrabber({"x": 0, "y": 0, "width": 1, "height": 1}, displays=displays)
    solo = webrtc_screen.ScreenGrabber(displays[0])
    # Overview is still capped (the whole union does not get a 8K-wide stream).
    assert stitched.size == webrtc_screen.target_size(5352, 1110, pixel_scale=2.0)
    assert stitched.size[0] == 2560
    stitched.set_view({"x": 0, "y": 0, "width": 1512, "height": 982})
    assert stitched.size == solo.size == webrtc_screen.target_size(1512, 982, pixel_scale=2.0)
    assert stitched.size == (2462, 1600)
    # Source pixels per desktop point match the single-screen stream.
    assert abs(stitched.size[0] / 1512 - solo.size[0] / 1512) < 1e-9
    stitched.set_view(None)
    assert stitched.size[0] == 2560


def test_offer_all_screens_uses_union_geometry_and_displays():
    server = Mock()
    server.answer.return_value = {"sdp": "answer-sdp", "type": "answer", "width": 2560, "height": 600}
    host = _Host()
    host._webrtc_server = server
    screens = [
        {"screen_number": 1, "geometry": {"x": 0, "y": 0, "width": 1512, "height": 982}},
        {"screen_number": 2, "geometry": {"x": -1920, "y": -50, "width": 1920, "height": 1080}},
        {"screen_number": 3, "geometry": {"x": 1512, "y": -80, "width": 1920, "height": 1080}},
    ]
    host._get_screens_list = Mock(return_value=screens)
    host._handle_remote_control_command({
        "command": "webrtc_offer", "request_id": "r-all",
        "data": {"sdp": "offer-sdp", "type": "offer", "all_screens": True, "screen_number": 0, "session_id": "abc"},
    })
    args, kwargs = server.answer.call_args
    assert args[0] == "offer-sdp"
    assert args[1] == "offer"
    assert args[2] == {"x": -1920, "y": -80, "width": 5352, "height": 1110}
    assert args[3] == "abc"
    assert kwargs["displays"] == [s["geometry"] for s in screens]
    assert kwargs["ice_servers"] is None


def test_screenshot_stream_all_screens_sends_combined_frame():
    host = _Host()
    host.binaries = []
    host._send_websocket_binary = lambda data: host.binaries.append(data)
    host._capture_all_screens_screenshot = Mock(return_value={"image_data": b"WEBPFAKE", "screen_number": 0})
    host._handle_remote_control_command({
        "command": "screenshot_stream", "request_id": "s1",
        "data": {"all_screens": True},
    })
    assert host.binaries and host.binaries[0][:2] == b"\x00\x00"
    assert host.binaries[0][2:] == b"WEBPFAKE"


def test_capture_all_screens_composites_mocked_images():
    from PIL import Image

    host = _Host()
    screens = [
        {"screen_number": 1, "geometry": {"x": 0, "y": 0, "width": 10, "height": 8}},
        {"screen_number": 2, "geometry": {"x": 12, "y": 2, "width": 6, "height": 4}},
    ]
    host._get_screens_list = Mock(return_value=screens)

    def fake_capture(number, draw_cursor=True):
        if number == 1:
            return Image.new("RGB", (10, 8), (255, 0, 0))
        return Image.new("RGB", (6, 4), (0, 0, 255))

    host._capture_screen_pil_image = fake_capture
    result = host._capture_all_screens_screenshot()
    assert result["screen_number"] == 0
    assert result["geometry"] == {"x": 0, "y": 0, "width": 18, "height": 8}
    assert result["format"] == "webp"
    assert result["image_data"][:4] == b"RIFF"



def test_benign_ice_close_is_ignored_by_loop_handler():
    class InvalidStateError(Exception):
        pass

    benign = InvalidStateError("RTCIceTransport is closed")
    assert webrtc_screen.is_benign_ice_close(benign) is True
    assert webrtc_screen.is_benign_ice_close(RuntimeError("RTCIceTransport is closed")) is False
    assert webrtc_screen.is_benign_ice_close(None) is False

    seen = []

    class _Loop:
        def default_exception_handler(self, context):
            seen.append(context)

    loop = _Loop()
    webrtc_screen.webrtc_loop_exception_handler(loop, {"exception": benign})
    assert seen == []
    other = {"exception": RuntimeError("TURN TransactionFail")}
    webrtc_screen.webrtc_loop_exception_handler(loop, other)
    assert seen == [other]


def test_parse_cursor_overlay_reads_enabled_flag():
    assert webrtc_screen.parse_cursor_overlay('{"command": "cursor_overlay", "enabled": true}') is True
    assert webrtc_screen.parse_cursor_overlay('{"command": "cursor_overlay", "enabled": false}') is False
    assert webrtc_screen.parse_cursor_overlay('{"command": "viewport", "x": 0, "y": 0, "width": 10, "height": 10}') is None


def test_set_view_noop_when_unchanged(monkeypatch):
    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_pixel_scale", lambda self: 2.0)
    grabber = webrtc_screen.ScreenGrabber({"x": 0, "y": 0, "width": 1512, "height": 982})
    first = grabber.size
    grabber.set_view(None)
    assert grabber.size == first
    grabber.set_view({"x": 0, "y": 0, "width": 800, "height": 600})
    cropped = grabber.size
    assert cropped != first
    grabber.size = (2, 2)  # would stick if set_view wrongly no-ops on the same rect after a size change
    grabber.set_view({"x": 0, "y": 0, "width": 800, "height": 600})
    assert grabber.size == cropped


def test_overview_composite_uses_output_size(monkeypatch):
    """The unzoomed stitch is composited at the encoded size, not a full-Retina union."""
    import numpy as np

    displays = [
        {"x": 0, "y": 0, "width": 1512, "height": 982},
        {"x": -1920, "y": -50, "width": 1920, "height": 1080},
        {"x": 1512, "y": -80, "width": 1920, "height": 1080},
    ]
    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_pixel_scale", lambda self: 2.0)
    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_pixel_scale_for", lambda self, geo: 2.0)

    def grab_one(self, geometry):
        # Pretend each display is captured at Retina, like CGDisplayCreateImage.
        w, h = geometry["width"] * 2, geometry["height"] * 2
        return np.zeros((h, w, 4), np.uint8), "bgra"

    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_grab_one", grab_one)
    grabber = webrtc_screen.ScreenGrabber({"x": 0, "y": 0, "width": 1, "height": 1}, displays=displays)
    raw, fmt = grabber._grab_raw()
    assert fmt == "bgra"
    assert raw.shape[1] == 2560
    assert abs(raw.shape[0] - grabber.size[1]) <= 2
    # A full-Retina union would be 10704 x 2220; that path is what made the overview hitch.
    assert raw.shape[1] * raw.shape[0] < (10704 * 2220) // 4


def test_draw_pointer_skipped_when_local_cursor(monkeypatch):
    import numpy as np

    monkeypatch.setattr(webrtc_screen.ScreenGrabber, "_pixel_scale", lambda self: 1.0)
    grabber = webrtc_screen.ScreenGrabber({"x": 0, "y": 0, "width": 100, "height": 80})
    monkeypatch.setattr(grabber, "_pointer", lambda: (10, 10))
    frame = np.zeros((80, 100, 4), np.uint8)
    grabber.draw_pointer(frame, "bgra")
    assert frame[10, 10, :3].sum() > 0  # burned-in fallback
    frame[:] = 0
    grabber.set_local_cursor(True)
    grabber.draw_pointer(frame, "bgra")
    assert frame.sum() == 0


def test_instant_move_posts_quartz_mouse_moved(monkeypatch):
    quartz = _FakeQuartz()
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setattr(remote_control, "pyautogui", Mock())
    monkeypatch.setattr(remote_control.platform, "system", lambda: "Darwin")
    host = _Host()
    host._get_screens_list = lambda: [
        {"screen_number": 1, "geometry": {"x": 0, "y": 0, "width": 1512, "height": 982}},
        {"screen_number": 2, "geometry": {"x": -1920, "y": -50, "width": 1920, "height": 1080}},
    ]
    host._move_mouse_to(100, 200, 1, instant=True)
    host._move_mouse_to(-100, 10, 2, instant=True)
    assert [e["kind"] for e in quartz.posted] == ["kCGEventMouseMoved", "kCGEventMouseMoved"]
    assert quartz.posted[0]["point"] == (100, 200)
    assert quartz.posted[1]["point"] == (-100, 10)
    # Instant Quartz path must not also call pyautogui (the hitch at display seams).
    remote_control.pyautogui.moveTo.assert_not_called()
