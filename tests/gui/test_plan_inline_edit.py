"""Guards: inline plan editing (Requirements / Interfaces markdown), no brief buttons, Data tab read-only DSL."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'distr/gui/web/static/development'
PLAN = STATIC / 'planning'


def test_brief_buttons_and_editor_removed():
    conv = (PLAN / 'conversation.js').read_text()
    css = (PLAN / 'styles.css').read_text()
    for gone in ('Edit brief', 'editBrief', 'plan-brief-bar', 'plan-brief-input', "'Outcome brief'"):
        assert gone not in conv and gone not in css, gone


def test_markdown_items_get_inline_editor_and_dsl_stays_read_only():
    conv = (PLAN / 'conversation.js').read_text()
    assert "import { createInlineEditor, addUnsavedCheck, isRevisionConflict } from './inline-edit/editor.js?v=20261103-ifaces3';" in conv
    assert "preview.append(inlineEditorFor(session, item, raw ? 'markdown-raw' : 'markdown', item.title || 'Document', () => markDocTabs(session)).el);" in conv
    mermaid = conv[conv.index("} else if (item.content_format === 'mermaid') {"):]
    mermaid = mermaid[:mermaid.index('} else {')]
    assert 'inlineEditorFor' not in mermaid and 'contenteditable' not in mermaid and 'textarea' not in mermaid
    assert "advancedButton({ text: () => item.content || '' })" in mermaid      # read-only source drawer
    chrome = (PLAN / 'canvas-chrome.js').read_text()
    assert 'aria-modal="true"' in chrome and "e.key === 'Escape'" in chrome and 'opener.focus' in chrome and 'readonly' not in chrome.lower().replace('read-only', '')
    assert '<details' not in conv and 'localStorage' not in chrome   # drawer never remembered open
    assert 'localStorage' not in conv
    # saves use the existing plan item PATCH with the revision check
    helper = conv[conv.index('function inlineEditorFor'):][:1400]
    assert "method: 'PATCH', body: { content, expected_revision: base }" in helper and 'baseRevision' in helper   # audit #1: the draft's base revision, never a fake 0
    assert '/workflows/studio/plan-items/' in helper


def test_editor_footer_tick_and_toast_wiring():
    editor = (PLAN / 'inline-edit/editor.js').read_text()
    assert 'plan-inline-tick' in editor and "'Saved'" in editor and 'file_sync_error' in editor and "toast(`Could not save:" in editor
    assert 'beforeunload' in editor and "insertText" in editor          # unload warning; paste as plain text
    toolbar = (PLAN / 'inline-edit/toolbar.js').read_text()
    for fmt in ('bold', 'underline', 'strike', 'colour', 'highlight', 'link'):
        assert f'data-fmt="{fmt}"' in toolbar
    assert "e.preventDefault(); });   // keep the text selection" in toolbar
    assert 'safeHref(' in toolbar and "link.rel = 'noopener noreferrer'" in toolbar
    assert 'queryCommandState' not in toolbar and '🔗' not in toolbar
    css = (PLAN / 'styles.css').read_text()
    assert '.md-toolbar { position: fixed;' in css and 'var(--segment-border)' in css[css.index('.md-toolbar {'):]
