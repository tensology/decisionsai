import base64
import importlib.util
import sys
import threading
import types
from pathlib import Path


_REMOTE_CONTROL_PATH = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "core"
    / "integrations"
    / "telegram"
    / "remote_control.py"
)

# Prevent package-level imports from pulling PyQt6-heavy __init__ modules while we
# exec remote_control.py — then **remove** stubs so later tests see the real package.
_fake_tg_pkg = types.ModuleType("distr.core.integrations.telegram")
_fake_tg_pkg.__path__ = []
_fake_tg_utils = types.ModuleType("distr.core.integrations.telegram.utils")
_fake_tg_utils.hash_channel_id = lambda value: f"hash_{value}"
_fake_tg_utils.relay_internal_token = lambda *args, **kwargs: ""

_TG_KEYS = ("distr.core.integrations.telegram", "distr.core.integrations.telegram.utils")
_prior_tg = {k: sys.modules[k] for k in _TG_KEYS if k in sys.modules}
try:
    sys.modules["distr.core.integrations.telegram"] = _fake_tg_pkg
    sys.modules["distr.core.integrations.telegram.utils"] = _fake_tg_utils

    _SPEC = importlib.util.spec_from_file_location("remote_control_under_test", _REMOTE_CONTROL_PATH)
    _MODULE = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(_MODULE)
    TelegramRemoteControlMixin = _MODULE.TelegramRemoteControlMixin
finally:
    for _k in _TG_KEYS:
        if _k in _prior_tg:
            sys.modules[_k] = _prior_tg[_k]
        else:
            sys.modules.pop(_k, None)


class _SignalCollector:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class _Host(TelegramRemoteControlMixin):
    def __init__(self):
        self.remote_control_command_received = _SignalCollector()
        self._remote_control_lock = threading.Lock()
        self.responses = []
        self.typed_text = []

    def _send_websocket_message(self, message):
        self.responses.append(message)

    def _type_text_quick(self, text):
        self.typed_text.append(text)


def _install_fake_qt(monkeypatch, app_obj):
    qt_mod = types.ModuleType("PyQt6")
    qt_widgets_mod = types.ModuleType("PyQt6.QtWidgets")

    class _QApplication:
        @staticmethod
        def instance():
            return app_obj

    qt_widgets_mod.QApplication = _QApplication
    monkeypatch.setitem(sys.modules, "PyQt6", qt_mod)
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", qt_widgets_mod)


def _install_fake_signals(monkeypatch, emit_fn):
    fake_signals_mod = types.ModuleType("distr.core.signals")
    fake_signal_manager = types.SimpleNamespace(
        send_text_input=types.SimpleNamespace(emit=emit_fn)
    )
    fake_signals_mod.signal_manager = fake_signal_manager
    monkeypatch.setitem(sys.modules, "distr.core.signals", fake_signals_mod)


def _patch_message_bus_deliver(monkeypatch, emits):
    """Remote control routes via ``IntegrationMessageBus.deliver_telegram_user_input``."""

    class _Bus:
        def deliver_telegram_user_input(
            self,
            *,
            text,
            image_path=None,
            telegram_chat_id=None,
            speak=None,
        ):
            emits.append((text, True, image_path, speak))

    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: _Bus(),
    )


def _voice_payload(mode="dictate", mime_type="audio/webm"):
    audio_b64 = base64.b64encode(b"not-real-audio").decode("ascii")
    return {
        "command": "voice_transcribe",
        "request_id": "ws-req-1",
        "data": {"audio_data": audio_b64, "mime_type": mime_type, "mode": mode},
    }


def test_instruction_command_routes_as_telegram_without_desktop_tts(monkeypatch):
    host = _Host()
    emits = []

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    _install_fake_signals(monkeypatch, lambda *args: emits.append(args))
    _patch_message_bus_deliver(monkeypatch, emits)

    host._handle_remote_control_command(
        {
            "command": "instruction",
            "request_id": "ins-1",
            "data": {"text": "open browser"},
        }
    )

    assert emits
    assert emits[-1] == ("open browser", True, None, False)


def test_voice_transcribe_reports_agent_not_available(monkeypatch):
    host = _Host()

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    _install_fake_qt(monkeypatch, None)

    host._handle_remote_control_command(_voice_payload())

    assert host.responses
    last = host.responses[-1]
    assert last["command"] == "voice_transcribe"
    assert "Agent not available" in (last.get("error") or "")


def test_voice_transcribe_reports_timeout(monkeypatch):
    host = _Host()

    class _QueueNoResult:
        def put(self, *_args, **_kwargs):
            return None

    class _App:
        agent_command_queue = _QueueNoResult()

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    _install_fake_qt(monkeypatch, _App())
    monkeypatch.setattr("threading.Event.wait", lambda self, timeout=None: False)

    host._handle_remote_control_command(_voice_payload())

    assert host.responses
    last = host.responses[-1]
    assert last["command"] == "voice_transcribe"
    assert "Transcription timed out" in (last.get("error") or "")


def test_voice_transcribe_command_mode_routes_to_agent_with_telegram_flag(monkeypatch):
    host = _Host()
    emits = []

    class _QueueSetsResult:
        def __init__(self, owner):
            self._owner = owner

        def put(self, _payload, **_kwargs):
            callbacks = getattr(self._owner, "_pending_voice_callbacks", {})
            assert callbacks
            req_id = next(iter(callbacks.keys()))
            event, holder = callbacks[req_id]
            holder["transcript"] = "hello remote command"
            event.set()

    class _App:
        def __init__(self, owner):
            self.agent_command_queue = _QueueSetsResult(owner)

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    _install_fake_qt(monkeypatch, _App(host))
    _install_fake_signals(monkeypatch, lambda *args: emits.append(args))
    _patch_message_bus_deliver(monkeypatch, emits)

    host._handle_remote_control_command(_voice_payload(mode="command"))

    rc_responses = [m for m in host.responses if m.get("command") == "voice_transcribe"]
    assert rc_responses
    assert rc_responses[-1].get("data", {}).get("text") == "hello remote command"
    assert emits
    assert emits[-1] == ("hello remote command", True, None, False)


def test_voice_transcribe_ogg_normalizes_to_wav(monkeypatch):
    host = _Host()

    class _QueueSetsResult:
        def __init__(self, owner):
            self._owner = owner
            self.paths = []

        def put(self, payload, **_kwargs):
            _command, params = payload
            self.paths.append(params["audio_file_path"])
            callbacks = getattr(self._owner, "_pending_voice_callbacks", {})
            req_id = next(iter(callbacks.keys()))
            event, holder = callbacks[req_id]
            holder["transcript"] = "compressed audio worked"
            event.set()

    class _App:
        def __init__(self, owner):
            self.agent_command_queue = _QueueSetsResult(owner)

    class _Result:
        returncode = 0

    ffmpeg_calls = []

    def _fake_ffmpeg(cmd, **_kwargs):
        ffmpeg_calls.append(cmd)
        out_path = cmd[-1]
        Path(out_path).write_bytes(b"wav-data")
        return _Result()

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    _install_fake_qt(monkeypatch, _App(host))
    monkeypatch.setattr("subprocess.run", _fake_ffmpeg)

    app = sys.modules["PyQt6.QtWidgets"].QApplication.instance()
    host._handle_remote_control_command(_voice_payload(mime_type="audio/ogg;codecs=opus"))

    assert ffmpeg_calls
    assert app.agent_command_queue.paths
    assert app.agent_command_queue.paths[0].endswith(".wav")
