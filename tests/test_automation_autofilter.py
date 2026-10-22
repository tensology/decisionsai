"""autofilter 2026-10-22: status filter + List/Calendar segmented toggle on the Automations page."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "distr/gui/web/static/development/automations/index.js"
HTML = ROOT / "distr/gui/web/templates/development/automations/workspace.html"


def test_toolbar_markup_has_filter_and_segmented_toggle():
    html = HTML.read_text()
    assert 'id="scheduled-status-filter"' in html and '<select id="scheduled-status-filter"' not in html
    assert 'aria-haspopup="listbox"' in html and 'role="listbox"' in html
    assert 'class="scheduled-status-filter-label" id="scheduled-status-filter-label">Status</span>' in html
    assert 'studio-dropdown-chevron' in html
    assert 'data-scheduled-view="list"' in html and 'data-scheduled-view="calendar"' in html
    assert 'aria-pressed="true">List' in html and 'aria-pressed="false">Calendar' in html
    assert "scheduled-view-toggle-label" not in html


def test_statuses_come_from_listing_groups_only():
    js = JS.read_text()
    assert "const STATUS_ORDER = ['active', 'paused', 'completed'];" in js
    assert "new Set(typeAutomations().map(automationGroup))" in js
    body = js[js.index("function visibleAutomations()"):js.index("function typeAutomations()")]
    assert "automationGroup(automation) === state.statusFilter" in body


def test_view_toggle_syncs_aria_and_session():
    js = JS.read_text()
    assert "button.setAttribute('aria-pressed', String(active));" in js
    assert "writeSession(VIEW_KEY, state.view)" in js and "writeSession(STATUS_KEY, state.statusFilter)" in js
    assert re.search(r"view: readSession\(VIEW_KEY, 'list'\) === 'calendar'", js)
    assert "scheduled-view-toggle-label" not in js


def test_status_dropdown_counts_pills_and_keyboard():
    js = JS.read_text()
    assert 'role="option"' in js and 'aria-selected="${key === state.statusFilter}"' in js
    assert '<span class="studio-count-pill">${countFor(key)}</span>' in js
    for key in ("'Escape'", "'ArrowDown'", "'ArrowUp'", "'Enter'", "' '"):
        assert key in js
    assert "!root.contains(event.target)" in js
    assert "scheduled-group-count" not in js and "<header><h2>${label}</h2>" not in js


def test_delete_red_shared_and_wf_card_padding():
    css = (ROOT / "distr/gui/web/static/development/automations/styles.css").read_text()
    assert ".scheduled-icon-button.danger { border-color: rgba(239,68,68,.55); color: #f87171; }" in css
    wf = (ROOT / "distr/gui/web/static/development/workflows/styles.css").read_text()
    assert ".workflow-card-head { display: flex; min-width: 0; align-items: center; gap: 8px; padding: 12px; }" in wf
