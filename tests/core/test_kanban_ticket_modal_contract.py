from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KANBAN_JS = ROOT / "distr/gui/web/static/kanban/js/kanban.js"
KANBAN_TICKET_JS = ROOT / "distr/gui/web/static/kanban/js/kanban_ticket.js"
KANBAN_HTML = ROOT / "distr/gui/web/templates/kanban/kanban.html"


def test_ticket_modal_footer_has_save_delete_only():
    html = KANBAN_HTML.read_text(encoding="utf-8")
    js = KANBAN_JS.read_text(encoding="utf-8")
    ticket_js = KANBAN_TICKET_JS.read_text(encoding="utf-8")

    assert 'id="kb-modal-actions"' not in html
    assert 'id="kb-modal-act-workflow"' not in html
    assert 'id="kb-modal-save"' in html
    assert 'id="kb-modal-delete"' in html
    assert 'id="kb-modal-footer"' in html
    assert "kb-modal-act-workflow" not in js
    assert "kb-modal-footer" in ticket_js


def test_ticket_modal_meta_uses_label_value_flex_fields():
    html = KANBAN_HTML.read_text(encoding="utf-8")
    ticket_js = KANBAN_TICKET_JS.read_text(encoding="utf-8")
    js = KANBAN_JS.read_text(encoding="utf-8")

    assert "kb-ticket-meta-grid" in html
    assert "kb-ticket-meta-field" in html
    assert "kb-ticket-meta-label" in html
    assert "kb-ticket-meta-value" in html
    assert "ticketMetaField" in ticket_js
    assert "ticketMetaField" in js
    assert 'id="kb-modal-external-meta"' in html
    assert "kb-modal-external-meta" in ticket_js


def test_ticket_complexity_defaults_to_auto_in_create_modal():
    html = KANBAN_HTML.read_text(encoding="utf-8")
    js = KANBAN_JS.read_text(encoding="utf-8")

    select_block = html[
        html.index('id="kb-modal-ticket-complexity"') :
        html.index("</select>", html.index('id="kb-modal-ticket-complexity"'))
    ]

    assert '<option value="auto" selected>Auto' in select_block
    assert '<option value="low">I - Low</option>' in select_block
    assert 'id="kb-cet-complexity" value="auto"' in html
    assert 'complexitySelect.value = "auto"' in js
    assert '["auto", "low", "medium", "high"]' in js
    assert 'return select && select.value ? select.value : "auto"' in js
