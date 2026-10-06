from distr.core.audio.dictation import insert_text, should_paste_text


def test_short_text_is_typed():
    assert should_paste_text("hello") is False


def test_multiline_or_long_text_is_pasted():
    assert should_paste_text("line one\nline two") is True
    assert should_paste_text("x" * 180) is True


def test_insert_large_text_pastes_and_does_not_press_enter(monkeypatch):
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
        "distr.core.audio.dictation._instant_type_text_macos",
        lambda text, press_enter=False: calls.append(("instant", press_enter, text)) or True,
    )

    blob = "Ticket title\n\n" + ("detail " * 40)
    assert insert_text(blob, press_enter=True, newline_mode="shift_enter") is True
    assert calls == [("paste", blob)]


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
