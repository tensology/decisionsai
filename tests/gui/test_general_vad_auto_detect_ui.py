from pathlib import Path


def test_general_settings_vad_card_exposes_auto_detect_button():
    template = Path("distr/gui/web/templates/settings/sections/general.html").read_text(encoding="utf-8")
    assert 'id="vad_auto_detect_button"' in template
    assert 'id="vad_auto_detect_status"' in template


def test_general_js_wires_vad_auto_detect_flow():
    source = Path("distr/gui/web/static/settings/js/general.js").read_text(encoding="utf-8")
    assert "async function runVadAutoDetect()" in source
    assert "_playVadCalibrationSample(" in source
    assert "_applyVadThresholdValue(result.recommended)" in source


def test_audio_monitor_exports_shared_controller_for_vad_auto_detect():
    source = Path("distr/gui/web/static/settings/js/audio.js").read_text(encoding="utf-8")
    assert "window.DecisionsAudioMonitor = {" in source
    assert "getSnapshot: function()" in source
    assert "setStatusOverride: function(text)" in source
