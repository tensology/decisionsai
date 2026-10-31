"""Notification sound catalogue: fixed slugs, real files, silent option."""

from __future__ import annotations

from distr.core import notification_sounds as sounds
import hashlib
import json


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


def test_full_crossroads_catalogue_matches_retained_original_audio():
    manifest = json.loads((sounds.sounds_dir() / "catalog.json").read_text())
    assert manifest["source"]["commit"] == "768e0ed0c3e47f3c09a63733e8e47c48847b291b"
    crossroads = [item for item in sounds.NOTIFICATION_SOUNDS if item.get("source_filename")]
    assert len(crossroads) == 49
    assert len({item["slug"] for item in crossroads}) == 49
    for item in crossroads:
        assert hashlib.sha256((sounds.sounds_dir() / item["file"]).read_bytes()).hexdigest() == item["sha256"]
    store_ring = next(item for item in crossroads if item["slug"] == "store-ring")
    assert store_ring["label"] == "Store Ring"
    assert store_ring["sha256"] == "521a0aa40df894932976e65b7632d26e1f95d85ded4b3036bcb394a8884925ed"


def test_legacy_sound_preferences_keep_the_same_audio():
    for legacy, filename in {"chime": "chime.mp3", "dong": "dong.mp3", "gentle": "gentle.mp3",
                             "tone": "tone.mp3", "elegant": "elegant.mp3"}.items():
        assert sounds.normalize(legacy) == legacy
        assert sounds.sound_url(legacy).endswith("/" + filename)
        assert any(item["slug"] == sounds.resolve_slug(legacy) for item in sounds.NOTIFICATION_SOUNDS)
