"""Continuous-mode glow must be a pure function of hands-free mode.

Regression guard for the self-toggling glow: hooks with glow disabled
(thinking / tts_response / talking) must not switch off the hands-free
indicator, and speech/barge-in must not drive it.
"""

from types import SimpleNamespace

from distr.gui.oracle.window import OracleWindow


class _Dispatcher:
    def __init__(self, current, responses):
        self.current = current
        self.responses = responses

    def get_current_hook(self):
        return self.current

    def get_event_response(self, hook):
        return self.responses.get(hook)


class _Glow:
    def __init__(self):
        self.applied = []
        self.stopped = 0

    def apply(self, response):
        self.applied.append(response)

    def stop(self):
        self.stopped += 1


class _Harness:
    def __init__(self, *, is_hands_free, dispatcher, glow):
        self.is_hands_free = is_hands_free
        self._event_dispatcher = dispatcher
        self._glow_engine = glow


def _responses():
    return {
        "hands_free_listening": SimpleNamespace(glow=True, name="hands_free"),
        "thinking": SimpleNamespace(glow=False, name="thinking"),
        "tts_response": SimpleNamespace(glow=False, name="tts"),
        "ptt_active": SimpleNamespace(glow=True, name="ptt"),
        "idle": SimpleNamespace(glow=False, name="idle"),
    }


def test_mode_glow_persists_while_thinking_hook_is_visible():
    glow = _Glow()
    harness = _Harness(
        is_hands_free=True,
        dispatcher=_Dispatcher("thinking", _responses()),
        glow=glow,
    )
    OracleWindow._apply_ambient_hands_free_glow(harness)
    assert [r.name for r in glow.applied] == ["hands_free"]


def test_mode_glow_persists_while_talking_hook_has_no_response():
    glow = _Glow()
    responses = _responses()
    responses.pop("talking", None)  # 'talking' is undefined in the oracle skin
    harness = _Harness(
        is_hands_free=True,
        dispatcher=_Dispatcher("talking", responses),
        glow=glow,
    )
    OracleWindow._apply_ambient_hands_free_glow(harness)
    assert [r.name for r in glow.applied] == ["hands_free"]


def test_mode_glow_does_not_apply_when_hands_free_is_off():
    glow = _Glow()
    harness = _Harness(
        is_hands_free=False,
        dispatcher=_Dispatcher("idle", _responses()),
        glow=glow,
    )
    OracleWindow._apply_ambient_hands_free_glow(harness)
    assert glow.applied == []


def test_other_glowing_hook_keeps_priority_over_mode_glow():
    glow = _Glow()
    harness = _Harness(
        is_hands_free=True,
        dispatcher=_Dispatcher("ptt_active", _responses()),
        glow=glow,
    )
    OracleWindow._apply_ambient_hands_free_glow(harness)
    assert glow.applied == []


def test_refresh_clears_glow_when_mode_off_and_visible_hook_has_none():
    glow = _Glow()
    harness = _Harness(
        is_hands_free=False,
        dispatcher=_Dispatcher("thinking", _responses()),
        glow=glow,
    )
    OracleWindow._refresh_visible_glow(harness)
    assert glow.stopped == 1
    assert glow.applied == []


def test_helpers_are_safe_without_glow_engine():
    """Lightweight embedders/tests may omit the glow engine entirely."""
    class Bare:
        is_hands_free = True
        _event_dispatcher = _Dispatcher("thinking", _responses())

    # Must not raise.
    OracleWindow._apply_ambient_hands_free_glow(Bare())
    OracleWindow._refresh_visible_glow(Bare())


def test_non_glowing_hook_keeps_mode_glow_instead_of_stopping_it():
    """thinking/tts_response must not stop the mode glow (no restart)."""
    resp = _responses()
    harness = _Harness(
        is_hands_free=True,
        dispatcher=_Dispatcher("thinking", resp),
        glow=_Glow(),
    )
    effective = OracleWindow._effective_glow_response(harness, resp["thinking"])
    assert effective is resp["hands_free_listening"]


def test_effective_glow_returns_response_when_hands_free_off():
    resp = _responses()
    harness = _Harness(
        is_hands_free=False,
        dispatcher=_Dispatcher("thinking", resp),
        glow=_Glow(),
    )
    assert OracleWindow._effective_glow_response(harness, resp["thinking"]) is resp["thinking"]


def test_effective_glow_keeps_other_glowing_hook_priority():
    resp = _responses()
    harness = _Harness(
        is_hands_free=True,
        dispatcher=_Dispatcher("ptt_active", resp),
        glow=_Glow(),
    )
    assert OracleWindow._effective_glow_response(harness, resp["ptt_active"]) is resp["ptt_active"]
