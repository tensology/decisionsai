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


def _ensure_pyqt_minimal_stubs() -> None:
    """Allow ``distr.core.signals`` (and similar) to import without PyQt6."""
    qc = sys.modules.get("PyQt6.QtCore")
    if qc is not None and getattr(qc, "_decisions_stub", False):
        return
    qtcore = types.ModuleType("PyQt6.QtCore")
    qtcore._decisions_stub = True

    class QObject:
        """Placeholder base for SignalManager."""

    def pyqtSignal(*_a, **_kw):
        return MagicMock()

    qtcore.QObject = QObject
    qtcore.pyqtSignal = pyqtSignal
    sys.modules.setdefault("PyQt6", types.ModuleType("PyQt6"))
    sys.modules["PyQt6.QtCore"] = qtcore


_ensure_pyqt_minimal_stubs()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: warm_tool_cache(), real fuzzy matching — slower; optional deps only",
    )
