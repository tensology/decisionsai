"""Guard: ticket modal, proposal modal and plan tabs share the app font and tab/spacing tokens."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'distr/gui/web/static/development'
AREAS = ['boards/styles.css', 'incoming/styles.css', 'planning/styles.css', 'shared/components.css']
TABS = {
    'boards/styles.css': '.kanban-ticket-tabs button {',
    'incoming/styles.css': '.proposal-tab {',
    # plan tabs outside the header keep the tab tokens; in the header they use --segment-* (test_header_fix)
    'planning/styles.css': '.plan-tabs button {',
    'threads/styles.css': '.inspector-tabs button {',
}
TAB_TOKENS = ['min-height: var(--studio-tab-height)', 'padding: var(--studio-tab-padding)', 'font: inherit',
              'font-size: var(--studio-tab-font-size)', 'font-weight: var(--studio-tab-font-weight)']


def _rule(css: str, selector: str) -> str:
    start = css.index(selector)
    return css[start:css.index('}', start)]


def test_no_second_sans_font_family_in_area_css():
    # Only the app font (inherited) or the monospace stack for timestamps/code is allowed.
    for name in AREAS:
        css = (STATIC / name).read_text()
        for decl in re.findall(r'font(?:-family)?\s*:\s*([^;}]+)', css):
            value = decl.strip()
            assert not re.search(r'apple-system|system-ui|Segoe UI|Helvetica|Arial|Inter\b|Roboto|serif(?!.*monospace)', value) or 'monospace' in value, (name, value)


def test_controls_inherit_app_font():
    shell = (STATIC / 'shell.css').read_text()
    assert 'button, input, select, textarea { color: inherit; font: inherit; }' in shell
    assert 'font: 12px/1.2 inherit' not in shell  # invalid shorthand removed


def test_tabs_use_identical_shared_tokens_in_all_areas():
    for name, selector in TABS.items():
        rule = _rule((STATIC / name).read_text(), selector)
        for token in TAB_TOKENS:
            assert token in rule, (name, token)
    tokens = (STATIC / 'shared/components.css').read_text()
    for token in ['--studio-tab-height: 36px;', '--studio-tab-font-size: 12px;', '--ticket-modal-width:', '--ticket-modal-pane-pad: 16px;']:
        assert token in tokens


def test_inspector_tabs_active_state_matches_other_tabs_and_tokens_load_first():
    threads = (STATIC / 'threads/styles.css').read_text()
    assert '.inspector-tabs button.active { border-bottom-color: var(--studio-accent); color: var(--studio-text); }' in threads
    assert 'height: var(--studio-tab-height); gap: var(--studio-tab-gap);' in _rule(threads, '.inspector-tabs {')
    styles = (STATIC / 'styles.css').read_text()
    # Tokens (shared/components.css) are imported on the same Development page before thread styles.
    assert styles.index('./shared/components.css') < styles.index('./threads/styles.css')


def test_ticket_and_proposal_modals_share_proportions():
    boards = (STATIC / 'boards/styles.css').read_text()
    incoming = (STATIC / 'incoming/styles.css').read_text()
    kanban_dialog = _rule(boards, '.kanban-ticket-dialog {')
    proposal = _rule(incoming, '.proposal-modal {')
    for token in ['var(--ticket-modal-width)', 'var(--ticket-modal-height)', 'var(--ticket-modal-radius)']:
        assert token in kanban_dialog and token in proposal, token
    assert 'var(--ticket-modal-header-pad)' in _rule(boards, '.kanban-ticket-dialog form > header {')
    assert 'var(--ticket-modal-header-pad)' in _rule(incoming, '.proposal-modal > header {')
    assert 'var(--ticket-modal-pane-pad)' in _rule(incoming, '.proposal-pane {')


def test_email_frame_uses_app_font_and_versions_bumped():
    js = (STATIC / 'incoming/index.js').read_text()
    assert 'href="/static/vendor/quicksand.css"' in js and 'body{font:14px/1.55 Quicksand,sans-serif;' in js
    assert '-apple-system' not in js
    assert re.search(r"\./incoming/index\.js\?v=[0-9]{8}-[\w-]+'", (STATIC / 'app.js').read_text())
    styles = (STATIC / 'styles.css').read_text()
    for name in ('shell', 'shared/components', 'threads/styles', 'boards/styles', 'incoming/styles'):
        assert re.search(rf"\./{name}\.css\?v=[0-9]{{8}}-[\w-]+'", styles)
    assert re.search(r"\./planning/styles\.css\?v=[0-9]{8}-[\w-]+'", styles)
    assert re.search(r'styles\.css\?v=[0-9]{8}-[\w-]+"', (ROOT / 'distr/gui/web/templates/development/index.html').read_text())
