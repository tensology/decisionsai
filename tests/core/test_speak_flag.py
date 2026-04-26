"""Unit tests for ``coerce_speak_enabled`` (web → agent speak flag)."""

from __future__ import annotations

import pytest

from distr.core.util.speak_flag import coerce_speak_enabled


def test_bool_passthrough() -> None:
    assert coerce_speak_enabled(True) is True
    assert coerce_speak_enabled(False) is False


def test_none_uses_default() -> None:
    assert coerce_speak_enabled(None, default=True) is True
    assert coerce_speak_enabled(None, default=False) is False


def test_string_variants() -> None:
    assert coerce_speak_enabled("true") is True
    assert coerce_speak_enabled("FALSE") is False
    assert coerce_speak_enabled("1") is True
    assert coerce_speak_enabled("0") is False


def test_int_one_means_speak_on() -> None:
    """Regression: ``1 is True`` is false in Python; strict checks used to silence TTS."""
    assert coerce_speak_enabled(1) is True
    assert coerce_speak_enabled(0) is False


def test_numpy_bool_regression() -> None:
    """numpy.bool_ is truthy but was not identical to True — old slot logic dropped audio."""
    np = pytest.importorskip("numpy")
    assert coerce_speak_enabled(np.bool_(True)) is True
    assert coerce_speak_enabled(np.bool_(False)) is False
