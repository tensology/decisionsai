from pathlib import Path
from tests.development_assets import development_assets, development_template


ROOT = Path(__file__).resolve().parents[2]


def test_development_drop_targets_are_scoped_to_sidebar_and_composer():
    template = development_template()
    javascript = development_assets(".js")

    assert 'id="sidebar-drop-overlay"' in template
    assert 'id="composer-drop-overlay"' in template
    assert template.index('id="composer-drop-overlay"') < template.index('</form>', template.index('id="studio-composer"'))
    assert "const composer = el('studio-composer')" in javascript
    assert "const sidebar = el('studio-sidebar')" in javascript
    assert "const target = el('studio-shell')" not in javascript
    assert "insideDevelopmentDropZone(event.target)" in javascript


def test_development_drop_feedback_and_capability_gate_are_wired():
    javascript = development_assets(".js")
    stylesheet = development_assets(".css")

    assert "transferContainsUnsupportedImage" in javascript
    assert "This model cannot read images" in javascript
    assert "Folders cannot be attached here" in javascript
    assert "preview_url: previewUrl" in javascript
    assert ".composer-drop-overlay.is-invalid" in stylesheet
    assert ".sidebar-drop-overlay.is-invalid" in stylesheet
    assert ".context-chip-preview" in stylesheet
