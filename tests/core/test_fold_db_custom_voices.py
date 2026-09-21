from distr.gui.web.routes.settings.voices import fold_db_custom_voices


def test_fold_db_custom_voices_drops_api_duplicate_keeps_db_id():
    voices = [
        {"id": "mine-1", "name": "⭐ Hayley Williams", "custom": True, "custom_source": "fishaudio_api"},
        {"id": "lib-1", "name": "Sarah"},
    ]
    rows = [(10, "Hayley Williams", "mine-1", "ready")]
    out = fold_db_custom_voices(voices, rows)
    hayley = [v for v in out if "Hayley" in v["name"]]
    assert len(hayley) == 1
    assert hayley[0]["custom_voice_id"] == 10
    assert any(v["id"] == "lib-1" for v in out)
