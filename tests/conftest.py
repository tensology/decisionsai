"""Root pytest hooks for the whole ``tests/`` tree.

Headless CI often lacks PyQt6. Stub minimal Qt *before* ``distr.core.signals`` can
import, and stub ``distr.core.settings`` before code paths that pull GUI deps.

Cost: a few ``sys.modules`` assignments once per pytest process (negligible).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Settings — many tests import code that touches DB-backed settings first.
# ---------------------------------------------------------------------------
if "distr.core.settings" not in sys.modules:
    _settings_stub = MagicMock()
    _settings_stub.load_settings_from_db = MagicMock(return_value={})
    sys.modules["distr.core.settings"] = _settings_stub


_QTCORE_IMPORT_NAMES = (
    "QObject",
    "pyqtSignal",
    "pyqtSlot",
    "QTimer",
    "QThread",
    "QUrl",
    "QCoreApplication",
    "QMetaObject",
    "Qt",
    "Q_ARG",
    "QByteArray",
)


def _qtcore_complete(mod: object | None) -> bool:
    """Real installs must expose everything Telegram + GUI stubs import at module level."""
    if mod is None:
        return False
    return all(hasattr(mod, name) for name in _QTCORE_IMPORT_NAMES)


def _qtwebsockets_complete() -> bool:
    try:
        import PyQt6.QtWebSockets as ws  # noqa: PLC0415
    except ImportError:
        return False
    return hasattr(ws, "QWebSocket")


def _install_pyqt6_stub() -> None:
    """Headless-friendly Qt: enough for ``TelegramWebSocketManager`` import chain."""
    qtcore = types.ModuleType("PyQt6.QtCore")
    qtcore._decisions_stub = True

    class QObject:
        """Placeholder QObject base."""

        def __init__(self, parent=None) -> None:
            self._parent = parent

    class _StubSignal:
        """No ``__slots__`` — tests may ``monkeypatch`` ``.emit`` on bound signals."""

        def __init__(self, *type_args) -> None:
            self._slots: list = []

        def connect(self, slot, *args, **kwargs):
            self._slots.append(slot)

        def disconnect(self, slot=None):
            if slot is None:
                self._slots.clear()
            else:
                self._slots = [s for s in self._slots if s is not slot]

        def emit(self, *args):
            for cb in list(self._slots):
                if callable(cb):
                    cb(*args)

    def pyqtSignal(*types):
        return _StubSignal(*types)

    def pyqtSlot(*types, **kwargs):
        def wrap(fn):
            return fn

        if types and callable(types[0]) and len(types) == 1 and not kwargs:
            return types[0]
        return wrap

    def pyqtProperty(*args, **kwargs):  # noqa: N802  — Qt naming
        """Minimal decorator so oracle/widgets using ``@pyqtProperty`` import under stubs."""

        def deco(fn):
            return fn

        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]
        return deco

    class QParallelAnimationGroup:
        def __init__(self, *a, **k) -> None:
            pass

    class QPropertyAnimation:
        def __init__(self, *a, **k) -> None:
            pass

    class QEasingCurve:
        def __init__(self, *a, **k) -> None:
            pass

    class QPoint:
        """Minimal QPoint for ``QRect.center()`` used by chat bubble positioning."""

        __slots__ = ("_x", "_y")

        def __init__(self, x: int = 0, y: int = 0) -> None:
            self._x, self._y = int(x), int(y)

        def x(self) -> int:
            return self._x

        def y(self) -> int:
            return self._y

    class QRect:
        """Enough geometry for ``ChatBubbleWidget`` clamp tests."""

        __slots__ = ("_x", "_y", "_w", "_h")

        def __init__(self, x: int = 0, y: int = 0, w: int = 0, h: int = 0, *a, **k) -> None:
            self._x, self._y, self._w, self._h = int(x), int(y), int(w), int(h)

        def left(self) -> int:
            return self._x

        def top(self) -> int:
            return self._y

        def right(self) -> int:
            return self._x + self._w - 1 if self._w > 0 else self._x

        def bottom(self) -> int:
            return self._y + self._h - 1 if self._h > 0 else self._y

        def center(self):
            return QPoint(self._x + self._w // 2, self._y + self._h // 2)

        def width(self) -> int:
            return self._w

        def height(self) -> int:
            return self._h

    class QRectF:
        """Minimal rect for ``ChatBubbleWidget.paintEvent``."""

        __slots__ = ("_x", "_y", "_w", "_h")

        def __init__(self, x=0.0, y=0.0, w=0.0, h=0.0, *a, **k) -> None:
            self._x, self._y = float(x), float(y)
            self._w, self._h = float(w), float(h)

        def adjusted(self, dx1, dy1, dx2, dy2):
            return QRectF(
                self._x + float(dx1),
                self._y + float(dy1),
                self._w - float(dx1) + float(dx2),
                self._h - float(dy1) + float(dy2),
            )

        @property
        def bottom(self):
            return self._y + self._h

    class QSize:
        def __init__(self, *a, **k) -> None:
            pass

    class QPointF:
        def __init__(self, x=0.0, y=0.0, *a, **k) -> None:
            self._x, self._y = float(x), float(y)

    class QTimer:
        def __init__(self, parent=None) -> None:
            self.timeout = MagicMock()

        def setSingleShot(self, _single: bool) -> None:
            pass

        def start(self, *_args, **_kwargs) -> None:
            pass

        def stop(self) -> None:
            pass

        @staticmethod
        def singleShot(_ms: int, _callback) -> None:
            """Headless no-op; scripts/tests patch where timing matters."""

    class QThread:
        def __init__(self, parent=None) -> None:
            pass

        def start(self) -> None:
            pass

        def wait(self, timeout=None) -> bool:
            return True

    class QUrl:
        """Enough for chat/browser tests that inspect schemes without full Qt."""

        def __init__(self, url: str = "") -> None:
            self._url = url or ""

        def scheme(self) -> str:
            u = self._url
            if "://" in u:
                return u.split(":", 1)[0].lower()
            if ":" in u:
                return u.split(":", 1)[0].lower()
            return ""

        def toString(self) -> str:
            return self._url

        def isEmpty(self) -> bool:
            return len(self._url) == 0

    class QCoreApplication:
        _instance = None

        def __init__(self, argv=None) -> None:
            QCoreApplication._instance = self
            self._argv = argv or []

        def quit(self) -> None:
            pass

        def exec(self) -> int:
            return 0

        @staticmethod
        def instance():
            return QCoreApplication._instance

    class Qt:
        """Subset of enums referenced by ``distr.gui.oracle`` and chat tests."""

        class ConnectionType:
            QueuedConnection = 1

        class WidgetAttribute:
            WA_TranslucentBackground = 120
            WA_TransparentForMouseEvents = 121
            WA_ShowWithoutActivating = 98

        class WindowType:
            FramelessWindowHint = 0x00000001
            WindowStaysOnTopHint = 0x00000020
            Tool = 0x00000004

        class PenStyle:
            NoPen = 0
            SolidLine = 1

        class AlignmentFlag:
            AlignCenter = 0x00000084
            AlignLeft = 0x00000001
            AlignTop = 0x00000020

        class TextFlag:
            TextWordWrap = 0x0200

        class AspectRatioMode:
            KeepAspectRatio = 1
            KeepAspectRatioByExpanding = 2

        class TransformationMode:
            SmoothTransformation = 2

        class PenCapStyle:
            RoundCap = 0x00000020

        class PenJoinStyle:
            RoundJoin = 0x00000080

        class MouseButton:
            LeftButton = 1
            RightButton = 2

    class QMetaObject:
        @staticmethod
        def invokeMethod(target, slot_name, connection_type, *qargs) -> bool:
            return False

    def Q_ARG(typ, value):  # noqa: N802
        return None

    class QByteArray(bytes):
        """Enough for isinstance checks; behaves like bytes."""

    qtcore.QObject = QObject
    qtcore.pyqtSignal = pyqtSignal
    qtcore.pyqtSlot = pyqtSlot
    qtcore.pyqtProperty = pyqtProperty
    qtcore.QParallelAnimationGroup = QParallelAnimationGroup
    qtcore.QPropertyAnimation = QPropertyAnimation
    qtcore.QEasingCurve = QEasingCurve
    qtcore.QRect = QRect
    qtcore.QRectF = QRectF
    qtcore.QSize = QSize
    qtcore.QPointF = QPointF
    qtcore.QTimer = QTimer
    qtcore.QThread = QThread
    qtcore.QUrl = QUrl
    qtcore.QCoreApplication = QCoreApplication
    qtcore.QMetaObject = QMetaObject
    qtcore.Qt = Qt
    qtcore.Q_ARG = Q_ARG
    qtcore.QByteArray = QByteArray
    qtcore.QPoint = QPoint

    pyqt_pkg = types.ModuleType("PyQt6")
    pyqt_pkg.__path__ = []
    pyqt_pkg._decisions_stub_pkg = True
    sys.modules["PyQt6"] = pyqt_pkg
    sys.modules["PyQt6.QtCore"] = qtcore

    qt_widgets = types.ModuleType("PyQt6.QtWidgets")

    class QWidget:
        """Headless QWidget with geometry + attributes for ``ChatBubbleWidget`` tests."""

        def __init__(self, parent=None, *args, **kwargs) -> None:
            self._parent = parent
            flags = 0
            if args:
                flags = int(args[0])
            self._window_flags = flags
            self._attrs: dict[int, bool] = {}
            self._visible = False
            self._x = 0
            self._y = 0
            self._w = 100
            self._h = 100

        def setAttribute(self, attr, on=True) -> None:
            self._attrs[int(attr)] = bool(on)

        def testAttribute(self, attr) -> bool:
            return self._attrs.get(int(attr), False)

        def windowFlags(self):
            return self._window_flags

        def setFixedSize(self, w, h) -> None:
            self._w, self._h = int(w), int(h)

        def width(self) -> int:
            return self._w

        def height(self) -> int:
            return self._h

        def x(self) -> int:
            return self._x

        def y(self) -> int:
            return self._y

        def move(self, x, y) -> None:
            self._x, self._y = int(x), int(y)

        def show(self) -> None:
            self._visible = True

        def hide(self) -> None:
            self._visible = False

        def close(self) -> None:
            self._visible = False

        def update(self) -> None:
            pass

        def isVisible(self) -> bool:
            return self._visible

    class QMainWindow(QWidget):
        """Stub base for ``OracleWindow`` MI — MagicMock breaks metaclass resolution."""

    class QScreen:
        def availableGeometry(self):
            return QRect(0, 0, 1920, 1080)

    class QApplication:
        _instance = None

        def __init__(self, argv=None) -> None:
            QApplication._instance = self
            self._argv = list(argv) if argv is not None else []

        @staticmethod
        def instance():
            return QApplication._instance

        def primaryScreen(self):
            return QScreen()

    qt_widgets.QWidget = QWidget
    qt_widgets.QMainWindow = QMainWindow
    qt_widgets.QApplication = QApplication

    _widget_exports = (
        "QGraphicsDropShadowEffect",
        "QHBoxLayout",
        "QInputDialog",
        "QLabel",
        "QLineEdit",
        "QListWidget",
        "QListWidgetItem",
        "QMenu",
        "QMessageBox",
        "QPushButton",
        "QScrollArea",
        "QStackedWidget",
        "QTextBrowser",
        "QVBoxLayout",
    )
    for _name in _widget_exports:
        setattr(qt_widgets, _name, MagicMock(name=_name))
    sys.modules["PyQt6.QtWidgets"] = qt_widgets

    qt_gui = types.ModuleType("PyQt6.QtGui")

    class QFont:
        def __init__(self, family="", pointSize=-1, *args, **kwargs) -> None:
            self.family = family
            self.pointSize = pointSize

    class _BoundingRect:
        """QFontMetrics.boundingRect returns a rect with ``width()`` / ``height()``."""

        def __init__(self, width: int, height: int) -> None:
            self._w = width
            self._h = height

        def width(self) -> int:
            return self._w

        def height(self) -> int:
            return self._h

    class QFontMetrics:
        def __init__(self, font) -> None:
            self._font = font

        def horizontalAdvance(self, text: str) -> int:
            if not text:
                return 0
            return len(text) * 7

        def boundingRect(self, x, y, w, h, flags, text):  # noqa: ARG002
            text = text or ""
            usable = max(int(w), 40) if w else 260
            adv = min(max(len(text) * 7, 40), usable)
            cpl = max(usable // 7, 1)
            lines = max(1, (len(text) + cpl - 1) // cpl) if text else 1
            return _BoundingRect(adv, 14 * lines)

    class QPainterPath:
        def addRoundedRect(self, *a, **k) -> None:
            return None

        def moveTo(self, *a) -> None:
            return None

        def lineTo(self, *a) -> None:
            return None

        def closeSubpath(self) -> None:
            return None

        def united(self, other):
            return self

    class QPen:
        def __init__(self, *a, **k) -> None:
            pass

    class QPainter:
        class RenderHint:
            Antialiasing = 1

        def __init__(self, device=None) -> None:
            pass

        def setRenderHint(self, *a, **k) -> None:
            pass

        def setPen(self, *a) -> None:
            pass

        def setBrush(self, *a) -> None:
            pass

        def setFont(self, *a) -> None:
            pass

        def drawPath(self, *a) -> None:
            pass

        def drawText(self, *a) -> None:
            pass

        def end(self) -> None:
            pass

    qt_gui.QFont = QFont
    qt_gui.QFontMetrics = QFontMetrics
    qt_gui.QPainterPath = QPainterPath
    qt_gui.QPen = QPen
    qt_gui.QPainter = QPainter

    class QTextDocument:
        class ResourceType:
            UnknownResource = 0

    qt_gui.QTextDocument = QTextDocument

    _gui_exports = (
        "QAction",
        "QBrush",
        "QColor",
        "QContextMenuEvent",
        "QCursor",
        "QDesktopServices",
        "QGuiApplication",
        "QIcon",
        "QImage",
        "QImageReader",
        "QMouseEvent",
        "QMovie",
        "QPixmap",
        "QRegion",
        "QShortcut",
    )
    for _name in _gui_exports:
        _clean = _name.strip()
        setattr(qt_gui, _clean, MagicMock(name=_clean))
    sys.modules["PyQt6.QtGui"] = qt_gui

    class _WsSignal:
        def connect(self, *a, **k) -> None:
            pass

        def emit(self, *a, **k) -> None:
            pass

    class QWebSocket:
        def __init__(self) -> None:
            self.connected = _WsSignal()
            self.disconnected = _WsSignal()
            self.textMessageReceived = _WsSignal()
            self.binaryMessageReceived = _WsSignal()
            self.error = _WsSignal()
            self.sslErrors = _WsSignal()

        def sendTextMessage(self, *a, **k) -> None:
            pass

        def sendBinaryMessage(self, *a, **k) -> None:
            pass

        def setProxy(self, *a, **k) -> None:
            pass

    qt_ws = types.ModuleType("PyQt6.QtWebSockets")
    qt_ws.QWebSocket = QWebSocket
    sys.modules["PyQt6.QtWebSockets"] = qt_ws

    qt_net = types.ModuleType("PyQt6.QtNetwork")

    class QNetworkProxy:
        class ProxyType:
            NoProxy = 0

        def __init__(self, *a, **k) -> None:
            pass

    class QNetworkProxyFactory:
        @staticmethod
        def setUseSystemConfiguration(_enable: bool) -> None:
            pass

    qt_net.QNetworkProxy = QNetworkProxy
    qt_net.QNetworkProxyFactory = QNetworkProxyFactory
    qt_net.QSslConfiguration = MagicMock()
    qt_net.QSslSocket = MagicMock()
    qt_net.QSsl = MagicMock()
    sys.modules["PyQt6.QtNetwork"] = qt_net


def _ensure_pyqt_minimal_stubs() -> None:
    """Prefer real PyQt6 when complete; else stub enough for imports + Telegram package."""
    qc = sys.modules.get("PyQt6.QtCore")
    if qc is not None and getattr(qc, "_decisions_stub", False):
        return

    real_core = None
    try:
        import PyQt6.QtCore as real_core  # noqa: PLC0415
    except ImportError:
        pass

    if _qtcore_complete(real_core) and _qtwebsockets_complete():
        return

    for key in list(sys.modules.keys()):
        if key == "PyQt6" or key.startswith("PyQt6."):
            del sys.modules[key]

    _install_pyqt6_stub()


_ensure_pyqt_minimal_stubs()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: warm_tool_cache(), real fuzzy matching — slower; optional deps only",
    )
    config.addinivalue_line(
        "markers",
        "llm_verification: Ollama-backed assertions in tests/verification/test_tool_routing.py (slow; may fail if model mis-picks tools)",
    )
    config.addinivalue_line(
        "markers",
        "e2e_playwright: Browser UI tests (e.g. workflows page) — require local server + Chromium; excluded from default pytest run via pytest.ini",
    )
