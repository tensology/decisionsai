"""Notification sound catalogue: fixed slugs, real files, silent option."""

from __future__ import annotations

from distr.core import notification_sounds as sounds


def test_every_choice_has_a_real_file_except_silent():
    for item in sounds.NOTIFICATION_SOUNDS:
        if item["slug"] == "none":
            assert item["file"] == ""
            continue
        path = sounds.sounds_dir() / item["file"]
        assert path.is_file(), f"missing sound file: {path}"


def test_unknown_choice_falls_back_to_the_bell():
    assert sounds.normalize("nonsense") == "bell"
    assert sounds.normalize("") == "bell"
    assert sounds.normalize(None) == "bell"


def test_known_choice_is_preserved_case_insensitively():
    assert sounds.normalize("CHIME") == "chime"
    assert sounds.normalize("gentle") == "gentle"


def test_silent_has_no_url():
    assert sounds.sound_url("none") == ""


def test_bell_resolves_to_a_served_url():
    assert sounds.sound_url("bell").startswith("/assets/sounds/notifications/")


def test_catalog_urls_are_servable_paths():
    for item in sounds.catalog():
        if item["slug"] == "none":
            assert item["url"] == ""
        else:
            assert item["url"].startswith("/assets/sounds/notifications/")
