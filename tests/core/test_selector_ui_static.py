"""Static contract checks for the 2026-10-22 selector / composer UI changes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "distr/gui/web"
read = lambda p: (WEB / p).read_text(encoding="utf-8")


def test_auto_is_first_choice_in_harness_list_with_hint():
    js = read("static/development/threads/composer/index.js")
    assert "modelChoiceHtml('Auto', 'auto', context.draft.route_mode !== 'manual', 'Picks a tier per message')" in js
    assert "encodedVariant(" in js  # effort/speed hidden when model name encodes it
    assert "input.focus({ preventScroll: true })" in js  # focus restore after model switch


def test_tooltip_portal_fixed_and_scoped():
    js = read("static/shared/tooltip_portal.js")
    css = read("static/development/threads/styles.css")
    assert "data-tooltip-if-truncated" in js and "scrollWidth > target.clientWidth" in js
    rule = next(l for l in css.splitlines() if l.startswith(".studio-tooltip-portal {"))
    assert "position: fixed" in rule and "pointer-events: none" in rule


def test_ready_dot_green_and_placeholder_top():
    css = read("static/development/threads/styles.css")
    assert ".task-state.ready span { background: var(--studio-green); }" in css
    assert ".studio-composer textarea { padding: 6px 14px 4px;" in css


def test_selector_tooltips_are_short():
    html = read("templates/development/threads/model-dialog.html")
    import re
    for tip in re.findall(r'data-tooltip="([^"]*)"', html):
        assert len(tip) <= 40, tip


def test_workflow_llm_settings_endpoint_removed():
    hits = [p for p in (WEB / "routes").rglob("*.py") if "llm-settings" in p.read_text(encoding="utf-8")]
    assert not hits, hits
