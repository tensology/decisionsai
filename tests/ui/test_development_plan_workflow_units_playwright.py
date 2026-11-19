"""Development Plan and Workflows modules in a real browser with a fake host.

Uses the disposable Plan server only to serve static files; nothing touches a live app.
"""
from __future__ import annotations

from playwright.sync_api import Page, expect
import pytest

from tests.ui.test_plan_workspace_playwright import plan_server  # noqa: F401  (fixture)

pytestmark = pytest.mark.e2e_playwright


def test_board_without_project_gets_a_plan_screen(page: Page, plan_server):  # noqa: F811
    page.goto(plan_server)
    page.wait_for_load_state("networkidle")
    page.evaluate("""async () => {
        const { createPlanning } = await import('/static/development/planning/index.js');
        document.body.innerHTML = '<main id="unit-plan"></main>';
        window.unitCalls = [];
        const host = {
            api: async () => { window.unitCalls.push('api'); return { items: [] }; },
            escapeHtml: (v) => String(v).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
            openBoardPlan: () => {}, openHome: () => window.unitCalls.push('home'),
            openBoardLink: (key) => window.unitCalls.push(`link:${key}`), toast: (m) => window.unitCalls.push(`toast:${m}`),
        };
        window.unitPlanning = createPlanning(host, document.getElementById('unit-plan'));
        await window.unitPlanning.openBoard({ key: 'decisions:9', provider: 'decisions', name: 'My Board' }, []);
    }""")
    screen = page.locator("[data-plan-unlinked]")
    expect(screen).to_contain_text("My Board has no project folder yet")
    page.get_by_role("button", name="Link a project folder").click()
    page.get_by_role("button", name="Back to Plans").click()
    calls = page.evaluate("window.unitCalls")
    assert "link:decisions:9" in calls and "home" in calls
    assert not [c for c in calls if c.startswith("toast:") or c == "api"], calls
    # The Plans home lists the unlinked board with a Set up action instead of hiding it.
    page.evaluate("""() => window.unitPlanning.openHome([{ key: 'decisions:9', provider: 'decisions', name: 'My Board' }], [])""")
    expect(page.locator('[data-plan-board="decisions:9"]').get_by_role("button", name="Set up")).to_be_visible()


def test_workflow_cards_search_and_url(page: Page, plan_server):  # noqa: F811
    page.goto(plan_server)
    page.wait_for_load_state("networkidle")
    page.evaluate("""async () => {
        const { createWorkflows } = await import('/static/development/workflows/index.js');
        document.body.innerHTML = `<input type="search" id="workflow-search">
            <div id="workflow-home"><div id="workflow-list"></div></div><div id="workflow-detail" class="hidden"></div>`;
        const steps = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1, position: i, name: `Step ${i + 1} name` }));
        const workflows = [
            { id: 11, name: 'Ship a ticket', steps: steps(4), workflow_input: { linked_project_id: 3 } },
            { id: 12, name: 'Weekly cleanup', steps: steps(1) },
        ];
        window.unitLocations = [];
        const runs = { 11: { id: 5, workflow_id: 11, status: 'failed', completed_at: '2026-11-18T10:00:00Z' } };
        const module = createWorkflows({
            context: { boards: [], projects: [{ id: 3, name: 'Auction site' }], providers: [], runs: [], skills: [], workflows, workspaceMode: 'workflows' },
            actions: {
                api: async (path) => { const id = Number(path.split('/')[2]); return path.includes('/runs') ? [] : workflows.find((w) => w.id === id); },
                escapeHtml: (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
                runForWorkflow: (id) => runs[id] || null, statusLabel: (s) => s.charAt(0).toUpperCase() + s.slice(1),
                setLocation: (path) => window.unitLocations.push(path), toast: () => {}, refreshShell: async () => {},
                confirmAction: async () => true, catalogModels: () => [], ensureProviderCatalogs: async () => {}, loadChat: async () => {},
            },
            el: (id) => document.getElementById(id),
        });
        document.getElementById('workflow-search').addEventListener('input', () => {});
        window.unitWorkflows = module;
        module.renderWorkflowWorkspace();
        // bindEvents wires many editor controls this unit page does not have; wire only search like it does.
        document.getElementById('workflow-search').addEventListener('input', (event) => {
            const list = document.getElementById('workflow-list');
            list.querySelectorAll('[data-workflow-card]').forEach((card) => {
                card.hidden = !String(card.dataset.workflowSearch).includes(event.target.value.toLowerCase());
            });
        });
    }""")
    ship = page.locator('[data-workflow-card="11"]')
    weekly = page.locator('[data-workflow-card="12"]')
    expect(ship).to_contain_text("Auction site")
    expect(ship).to_contain_text("Last run failed")
    expect(ship).to_contain_text("First")
    expect(ship).to_contain_text("3 more steps")
    expect(ship).not_to_contain_text("Current")
    expect(weekly).not_to_contain_text("Current")
    assert ship.get_attribute("data-workflow-search").find("auction site") >= 0
    page.locator("#workflow-search").fill("auction")
    expect(weekly).to_be_hidden()
    expect(ship).to_be_visible()
    page.evaluate("window.unitWorkflows.selectWorkflow(11)")
    page.wait_for_function("window.unitLocations.length > 0")
    assert page.evaluate("window.unitWorkflows.currentPath()") == "/development/workflows/11/"
    assert page.evaluate("window.unitLocations")[0] == "/development/workflows/11/"
    page.evaluate("window.unitWorkflows.showWorkflowRoute(0)")
    assert page.evaluate("window.unitWorkflows.currentPath()") == "/development/workflows/"
