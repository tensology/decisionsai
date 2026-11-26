"""The chat box's Chat / Library tabs, and the screen they resolve to."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABS = ROOT / 'distr/gui/web/static/development/planning/panel-tabs.js'
CONVERSATION = ROOT / 'distr/gui/web/static/development/planning/conversation.js'
STYLES = ROOT / 'distr/gui/web/static/development/planning/styles.css'


def _run(body: str) -> str:
    script = f"import * as tabs from {TABS.as_uri()!r};\n" + body
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_chat_is_first_and_library_second():
    out = _run("console.log(JSON.stringify(tabs.PANEL_TABS.map(t => [t.id, t.label])));")
    assert json.loads(out) == [['chat', 'Chat'], ['library', 'Library']]


def test_the_screen_follows_the_interfaces_tab():
    out = _run("""
    console.log(JSON.stringify([
      tabs.libraryScreenFor({ tab: 'wireframes', selection: { wireframes: 12 } }),
      tabs.libraryScreenFor({ tab: 'wireframes', selection: { wireframes: 'discovered' } }),
      tabs.libraryScreenFor({ tab: 'wireframes', selection: {} }),
      tabs.libraryScreenFor({ tab: 'erd', selection: { wireframes: 12 } }),
      tabs.libraryScreenFor({ tab: 'requirements' }),
    ]));
    """)
    assert json.loads(out) == ['page', 'sitemap', 'sitemap', 'page', 'page']


def test_the_screen_never_throws_on_junk():
    out = _run("console.log(JSON.stringify([tabs.libraryScreenFor(null), tabs.libraryScreenFor({}), tabs.libraryScreenFor('x')]));")
    assert json.loads(out) == ['page', 'page', 'page']


def test_an_unknown_tab_normalises_to_chat():
    out = _run("console.log(JSON.stringify([tabs.normalizePanelTab('library'), tabs.normalizePanelTab('nonsense'), tabs.normalizePanelTab(null)]));")
    assert json.loads(out) == ['library', 'chat', 'chat']


def test_the_tab_strip_marks_the_active_tab():
    out = _run("console.log(JSON.stringify([tabs.panelTabsHtml('chat'), tabs.panelTabsHtml('library')]));")
    chat_html, library_html = json.loads(out)
    assert 'data-panel-tab="chat"' in chat_html and 'aria-selected="true"' in chat_html
    assert chat_html.index('data-panel-tab="chat"') < chat_html.index('data-panel-tab="library"')
    assert 'aria-selected="true"' in library_html
    # the chat tab is not the active one in the library strip
    assert library_html.count('aria-selected="true"') == 1


# ------------------------------------------------------------------ the panel

def test_the_chat_panel_has_both_tabs_and_a_library_host():
    source = CONVERSATION.read_text(encoding='utf-8')
    assert 'plan-panel-tabs' in source
    assert 'panelTabsHtml(' in source
    assert 'id="plan-library"' in source
    # the chat body is wrapped so it can be hidden without being rebuilt
    assert 'id="plan-chat-body"' in source
    assert 'mountLibraryPanel' in source
    assert 'mountPlanPanelTabs' in source


def test_switching_tabs_hides_rather_than_rebuilds_the_chat():
    """Hiding keeps a half-typed message and the scroll position."""
    source = TABS.read_text(encoding='utf-8')
    assert '.hidden = current !== ' in source
    assert 'innerHTML' not in source.split('export function applyPanelTab')[1].split('export function')[0]


def test_the_library_is_hidden_until_its_tab_is_chosen():
    source = CONVERSATION.read_text(encoding='utf-8')
    assert 'id="plan-library" role="tabpanel" aria-label="Library" hidden' in source


def test_the_styles_hide_the_inactive_panel():
    css = STYLES.read_text(encoding='utf-8')
    assert '.plan-panel-tab.active' in css
    assert '.plan-library[hidden]' in css
    assert '.plan-chat-body[hidden]' in css
