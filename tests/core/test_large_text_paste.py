import sys
from types import SimpleNamespace

from distr.core.audio.dictation import (
    _instant_type_text_macos,
    insert_text,
    should_paste_text,
)


def test_short_text_is_typed():
    assert should_paste_text("hello") is False


def test_multiline_or_long_text_is_pasted():
    assert should_paste_text("line one\nline two") is True
    assert should_paste_text("x" * 180) is True


def test_insert_large_text_uses_direct_insertion_without_clipboard(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "distr.core.audio.dictation.paste_text_blob",
        lambda text: calls.append(("paste", text)) or True,
    )
    monkeypatch.setattr(
        "distr.core.audio.dictation.type_text",
        lambda text, delay=0.01: calls.append(("type", text)) or True,
    )
    monkeypatch.setattr(
        "distr.core.audio.dictation.instant_type_text",
        lambda text, press_enter=False: calls.append(("instant", press_enter, text)) or True,
    )

    blob = "Ticket title\n\n" + ("detail " * 40)
    assert insert_text(blob, instant=True, newline_mode="shift_enter") is True
    assert calls == [("instant", False, blob)]


def test_explicit_direct_insertion_never_falls_back(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "distr.core.audio.dictation.instant_type_text",
        lambda text, press_enter=False: calls.append("instant") and False,
    )
    monkeypatch.setattr(
        "distr.core.audio.dictation.paste_text_blob",
        lambda text: calls.append("paste") or True,
    )
    monkeypatch.setattr(
        "distr.core.audio.dictation.type_text",
        lambda text, delay=0.01: calls.append("type") or True,
    )

    assert insert_text("x" * 180, instant=True) is False
    assert calls == ["instant"]


def test_insert_short_text_can_still_press_enter(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "distr.core.audio.dictation.paste_text_blob",
        lambda text: calls.append("paste") or True,
    )
    monkeypatch.setattr(
        "distr.core.audio.dictation.is_instant_dictation_enabled",
        lambda settings=None: True,
    )
    monkeypatch.setattr(
        "distr.core.audio.dictation.instant_type_text",
        lambda text, press_enter=False: calls.append(("instant", text, press_enter)) or True,
    )

    assert insert_text("send this", press_enter=True) is True
    assert calls == [("instant", "send this", True)]


def test_macos_direct_insertion_sets_selected_text_without_clipboard(monkeypatch):
    calls = []
    focused = object()
    fake_api = SimpleNamespace(
        AXUIElementCreateSystemWide=lambda: "system",
        AXUIElementCopyAttributeValue=lambda element, attribute, default: (0, focused),
        AXUIElementSetAttributeValue=lambda element, attribute, value: (
            calls.append((element, attribute, value)) or 0
        ),
        kAXErrorSuccess=0,
        kAXFocusedUIElementAttribute="focused",
        kAXSelectedTextAttribute="selected_text",
    )
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_api)

    assert _instant_type_text_macos("the whole dictation") is True
    assert calls == [(focused, "selected_text", "the whole dictation")]
