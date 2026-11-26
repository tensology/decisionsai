"""The suggestion gate is where restraint lives: test the rules directly."""

import pytest

from distr.core.initiative import suggestion_gate as sg


def _c(text, confidence, kind="idea"):
    return {"text": text, "confidence": confidence, "kind": kind}


def test_below_the_speak_bar_is_silence():
    inbox, interrupts = sg.gate(
        [_c("weak", 0.2)], interrupt_threshold=0.85, budget_remaining=5
    )
    assert inbox == []
    assert interrupts == []


def test_between_the_bars_lands_in_the_inbox_only():
    inbox, interrupts = sg.gate(
        [_c("decent", 0.7)], interrupt_threshold=0.85, budget_remaining=5
    )
    assert [s["text"] for s in inbox] == ["decent"]
    assert interrupts == []


def test_above_the_interrupt_bar_interrupts():
    inbox, interrupts = sg.gate(
        [_c("great", 0.95)], interrupt_threshold=0.85, budget_remaining=5
    )
    assert [s["text"] for s in inbox] == ["great"]
    assert [s["text"] for s in interrupts] == ["great"]


def test_no_budget_means_no_interrupt_but_still_an_inbox_entry():
    inbox, interrupts = sg.gate(
        [_c("great", 0.95)], interrupt_threshold=0.85, budget_remaining=0
    )
    assert len(inbox) == 1
    assert interrupts == []


def test_at_most_one_interrupt_per_cycle():
    inbox, interrupts = sg.gate(
        [_c("a", 0.99), _c("b", 0.98), _c("c", 0.97)],
        interrupt_threshold=0.85,
        budget_remaining=9,
    )
    assert len(inbox) == 3
    assert [s["text"] for s in interrupts] == ["a"]


def test_results_are_highest_confidence_first():
    inbox, _ = sg.gate(
        [_c("low", 0.6), _c("high", 0.9), _c("mid", 0.75)],
        interrupt_threshold=0.99,
        budget_remaining=1,
    )
    assert [s["text"] for s in inbox] == ["high", "mid", "low"]


def test_seen_suggestions_are_dropped():
    dropped = {"idea:already said"}
    inbox, interrupts = sg.gate(
        [_c("already said", 0.99), _c("fresh", 0.9)],
        interrupt_threshold=0.85,
        budget_remaining=5,
        seen=lambda key: key in dropped,
    )
    assert [s["text"] for s in inbox] == ["fresh"]
    assert [s["text"] for s in interrupts] == ["fresh"]


def test_a_broken_seen_predicate_fails_closed():
    def boom(key):
        raise RuntimeError("dedup store unavailable")

    inbox, interrupts = sg.gate(
        [_c("great", 0.99)], interrupt_threshold=0.85, budget_remaining=5, seen=boom
    )
    assert inbox == []
    assert interrupts == []


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "not a dict",
        {"confidence": 0.9},                      # no text
        {"text": "   ", "confidence": 0.9},       # blank text
        {"text": "x", "confidence": None},        # no confidence
        {"text": "x", "confidence": "abc"},       # junk confidence
        {"text": "x", "confidence": float("nan")},
    ],
)
def test_malformed_candidates_are_dropped(candidate):
    inbox, interrupts = sg.gate(
        [candidate], interrupt_threshold=0.5, budget_remaining=5
    )
    assert inbox == []
    assert interrupts == []


def test_confidence_is_clamped_into_range():
    inbox, _ = sg.gate(
        [_c("over", 5.0), _c("under", -3.0)],
        interrupt_threshold=0.85,
        budget_remaining=5,
    )
    assert [s["text"] for s in inbox] == ["over"]
    assert inbox[0]["confidence"] == 1.0


def test_an_unusable_threshold_never_interrupts():
    inbox, interrupts = sg.gate(
        [_c("great", 0.99)], interrupt_threshold="nonsense", budget_remaining=5
    )
    assert len(inbox) == 1
    assert interrupts == []


def test_candidates_are_not_mutated():
    candidate = _c("keep me", 0.9)
    original = dict(candidate)
    sg.gate([candidate], interrupt_threshold=0.5, budget_remaining=5)
    assert candidate == original
    assert "key" not in candidate


def test_suggestion_key_normalises_whitespace_and_case():
    a = sg.suggestion_key({"kind": "Idea", "text": "  Have   you thought?  "})
    b = sg.suggestion_key({"kind": "idea", "text": "have you thought?"})
    assert a == b
