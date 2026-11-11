"""Building tickets must use the plan the person has explicitly saved."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_plan_workspace_playwright import plan_server

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_build_requires_saved_document_even_on_another_tab(page, plan_server, width):
    page.set_viewport_size({"width": width, "height": 960})
    page.goto(plan_server)
    page.get_by_role("button", name="Documents", exact=True).click()
    page.get_by_role("button", name="Markdown", exact=True).click()
    editor = page.locator("textarea.plan-inline-raw")
    draft = editor.input_value() + "\nFR-099: Keep my new requirement.\n"
    editor.fill(draft)
    page.get_by_role("button", name="Interfaces", exact=True).click()
    builds = []
    page.on("request", lambda req: builds.append(req.url) if req.method == "POST" and req.url.endswith("/build") else None)
    page.get_by_role("button", name="Build tickets", exact=True).click()
    expect(page.locator(".plan-error")).to_contain_text("Save")
    assert builds == []
    page.get_by_role("button", name="Documents", exact=True).click()
    expect(editor).to_have_value(draft)
    out = Path("/tmp/decisions-development-usability")
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / f"build-unsaved-{width}.png"))
    page.locator(".plan-inline-save").click()
    expect(page.locator(".plan-inline-state")).to_have_text("Saved")
    page.get_by_role("button", name="Build tickets", exact=True).click()
    expect(page.get_by_role("list", name="Generated tasks", exact=True)).to_contain_text("Persist customer details")
    assert len(builds) == 1


@pytest.mark.parametrize('outcome', ['failed', 'saved', 'saved-with-newer-draft'])
def test_document_draft_survives_pending_save_and_tab_switch(page, plan_server, outcome):
    page.goto(plan_server)
    page.get_by_role("button", name="Documents", exact=True).click()
    page.get_by_role("button", name="Markdown", exact=True).click()
    editor = page.locator("textarea.plan-inline-raw")
    draft = editor.input_value() + "\nFR-099: Preserve pending save.\n"
    editor.fill(draft)
    held = []
    page.route("**/plan-items/*", lambda route: held.append(route) if route.request.method == "PATCH" else route.continue_())
    page.locator(".plan-inline-save").click()
    expect(page.locator(".plan-inline-state")).to_have_text("Saving…")
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.get_by_role("button", name="Documents", exact=True).click()
    expect(editor).to_have_value(draft)
    assert len(held) == 1
    if outcome == 'saved-with-newer-draft':
        draft += '\nFR-100: Keep editing while the earlier save finishes.\n'
        editor.fill(draft)
    if outcome == 'failed':
        held[0].fulfill(status=500, json={"detail": "Save unavailable"})
    else:
        held[0].continue_()
        expect(page.locator('.plan-inline-state')).to_have_text('Unsaved changes' if outcome == 'saved-with-newer-draft' else 'Saved')
        editor.fill(draft + '\nFR-100: Save from the new revision.\n')
        page.locator('.plan-inline-save').click()
        expect(page.locator('.plan-inline-state')).to_have_text('Saving…')
        held[1].continue_()
        expect(page.locator('.plan-inline-state')).to_have_text('Saved')
        draft += '\nFR-100: Save from the new revision.\n'
    expect(editor).to_have_value(draft)


def test_rebuild_requires_saved_page_description(page, plan_server):
    page.goto(plan_server)
    page.locator('.iface-node').first.dblclick()
    page.get_by_role('button', name='Planning', exact=True).click()
    page.get_by_role('button', name='Edit markdown', exact=True).click()
    page.locator('.plan-page-description textarea').fill('Keep the reviewed page description.')
    page.get_by_label('Back to all pages', exact=True).click()
    page.locator('.iface-panel-close').click()
    calls = []
    page.on('request', lambda req: calls.append(req.url) if req.url.endswith('/rebuild-interfaces') else None)
    page.get_by_role('button', name='Rebuild', exact=True).click()
    expect(page.locator('.plan-error')).to_contain_text('Save')
    assert calls == []
