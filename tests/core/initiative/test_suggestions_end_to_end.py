"""End-to-end: observe -> assess -> gate -> record -> deliver, twice.

The behaviour that matters is not that a suggestion can be made, but that it is
made once and then stops.
"""

import time

import pytest
from sqlalchemy import create_engine

from distr.core.initiative import (
    assessor,
    notice_dedup,
    service as initiative_service,
    situation_log,
    suggestion_store,
)


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """A whole pipeline on temp storage, with the model and delivery stubbed."""
    engine = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    monkeypatch.setattr(suggestion_store, "engine", engine)
    suggestion_store.ensure_tables()

    monkeypatch.setattr(situation_log, "_ENTRIES", [])
    # Recorded at the real clock: summarise() windows on time.time().
    situation_log.record(
        {"line": "desktop: Warp", "app": "Warp", "title": "initiative", "screens": []},
        now=time.time(),
    )

    dedup_file = tmp_path / "dedup.json"
    monkeypatch.setattr(notice_dedup, "_path", lambda: dedup_file)

    delivered: list[tuple[str, bool]] = []

    def fake_place(nudges, choice, settings, **kwargs):
        delivered.append((choice.channel, bool(nudges)))
        return choice.channel

    import distr.core.initiative.channel_select as channel_select
    monkeypatch.setattr(channel_select, "place", fake_place)

    return {"delivered": delivered, "settings": {"initiative_recommendations": 1}}


def _stub_assessment(monkeypatch, text, confidence):
    def fake_assess(digest, **kwargs):
        return {
            "worthy": True,
            "confidence": confidence,
            "kind": "dropped-thread",
            "text": text,
            "provider": "openai",
            "model": "gpt-4o",
        }

    monkeypatch.setattr(assessor, "assess", fake_assess)


def test_a_confident_suggestion_interrupts_exactly_once(wired, monkeypatch):
    _stub_assessment(monkeypatch, "Have you considered closing the loop on that ticket?", 0.95)

    initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    assert wired["delivered"] == [("spoken", True)]
    assert len(suggestion_store.recent()) == 1

    # Second cycle: same situation, same wording. It must not interrupt again.
    initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    assert wired["delivered"] == [("spoken", True)]
    assert len(suggestion_store.recent()) == 1


def test_a_merely_decent_suggestion_stays_quiet(wired, monkeypatch):
    _stub_assessment(monkeypatch, "Something mildly interesting", 0.7)
    initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    assert wired["delivered"] == [("in_app", True)]


def test_a_weak_suggestion_is_never_delivered(wired, monkeypatch):
    _stub_assessment(monkeypatch, "Barely worth it", 0.2)
    initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    assert wired["delivered"] == []
    assert suggestion_store.recent() == []


def test_the_master_switch_off_means_no_model_call(wired, monkeypatch):
    called = []

    def fake_assess(digest, **kwargs):
        called.append(digest)
        return None

    monkeypatch.setattr(assessor, "assess", fake_assess)
    initiative_service._maybe_suggest_from_desktop_awareness({"initiative_recommendations": 0})
    assert called == []


def test_a_muted_kind_is_dropped_even_if_the_model_returns_it(wired, monkeypatch):
    suggestion_store.mute_kind("dropped-thread")
    _stub_assessment(monkeypatch, "You muted this kind", 0.99)
    initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    assert wired["delivered"] == []
    assert suggestion_store.recent() == []


def test_muted_kinds_are_offered_to_the_assessor(wired, monkeypatch):
    suggestion_store.mute_kind("naming")
    seen = {}

    def fake_assess(digest, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(assessor, "assess", fake_assess)
    initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    assert seen.get("avoid_kinds") == ["naming"]


def test_no_situation_means_no_suggestion(wired, monkeypatch):
    situation_log._ENTRIES.clear()
    _stub_assessment(monkeypatch, "Should never be asked", 0.99)
    initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    assert wired["delivered"] == []


def test_a_failing_assessor_means_silence(wired, monkeypatch):
    def boom(digest, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(assessor, "assess", boom)
    # The cycle swallows it; nothing is delivered and nothing is recorded.
    try:
        initiative_service._maybe_suggest_from_desktop_awareness(wired["settings"])
    except RuntimeError:
        pass
    assert suggestion_store.recent() == []
