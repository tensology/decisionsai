"""Databases drawer on the Data tab (N5 phase 4): opener in the dock at 1440 / 768 / 390, drawer geometry,
password only sent to PUT .../secret and never kept in the DOM, introspect, diff picker (search, cap, apply),
add link. Mocked APIs on the fixture server (no DB, no keychain, no database server).

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_db_drawer_playwright.py
"""
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
SECRET = 'Pw-drawer$ecret-7'
ERD = 'erDiagram\n    HAND {\n        int id PK\n    }\n'
NEW_ERD = ('erDiagram\n    public__customers {\n        int id PK\n    }\n    public__orders {\n        bigint id PK\n        int customer_id FK\n    }\n'
           '    public__customers ||--o{ public__orders : "customer_id"\n'
           '%% decisions-erd: {"db":{"links":[{"link":1,"relations":["public__customers|public__orders|customer_id"],"snapshot_at":"2026-11-03T07:00:00+00:00"}],"owner":1},"tables":{},"v":1}\n')
SIZES = [(1440, 900), (768, 1024), (390, 844)]


class DbBackend(Backend):
    def __init__(self):
        super().__init__()
        self.items[1]["content"] = ERD
        self.links = [{"id": 1, "label": "Shop dev", "engine": "postgres", "host": "127.0.0.1", "port": 5432, "database": "shop", "username": "dev",
                       "schemas": ["public"], "colour": None, "status": "new", "last_error": "", "last_introspected_at": None, "has_secret": False, "secret_store": None}]
        self.calls = []

    def diff(self, applied=False):
        tables = [{"key": f"public.t{i:03d}", "schema": "public", "table": f"t{i:03d}", "entity": f"public__t{i:03d}", "columns": 2, "rows_estimate": 1} for i in range(320)]
        return {"link_id": 1, "snapshot_at": "2026-11-03T07:00:00+00:00", "merge": False, "item_id": 14 if applied else None, "revision": 1 if applied else None,
                "target": "link_erd" if applied else "new_erd", "truncated": False, "max_apply": 300, "hand_made": [],
                "added": [] if applied else [{"key": "public.customers", "schema": "public", "table": "customers", "entity": "public__customers"},
                                             {"key": "public.orders", "schema": "public", "table": "orders", "entity": "public__orders"}] + tables,
                "changed": [], "removed": [], "unchanged": [], "conflicts": [{"key": "public.hand", "schema": "public", "table": "hand", "entity": "public__hand", "reason": "A hand-made table already uses this name; it is left alone."}],
                "summary": {"added": 0 if applied else 322, "removed": 0, "changed": 0, "unchanged": 0, "conflicts": 1}}

    def route(self, route):
        req = route.request
        path = req.url.split('/api', 1)[1].split('?')[0]
        if '/db-links' not in path:
            return super().route(route)
        self.calls.append({"method": req.method, "path": path, "body": req.post_data or ""})
        base = path[: path.index('/db-links') + len('/db-links')]
        if path == base and req.method == 'GET':
            return route.fulfill(json={"items": self.links, "secret_store": "session", "max_links": 8})
        if path == base and req.method == 'POST':
            body = json.loads(req.post_data)
            link = {**self.links[0], **body, "id": 2, "label": body.get("label") or body["database"], "has_secret": False, "status": "new"}
            self.links.append(link)
            return route.fulfill(status=201, json=link)
        link_id = int(path[len(base) + 1:].split('/')[0])
        link = next(l for l in self.links if l["id"] == link_id)
        if path.endswith('/secret') and req.method == 'PUT':
            link.update(has_secret=True, secret_store="session")
            return route.fulfill(json={"ok": True, "has_secret": True})
        if path.endswith('/test'):
            link["status"] = "ok"
            return route.fulfill(json={"ok": True, "code": "ok", "link_id": link_id, "engine": "postgres", "server_version": "160004"})
        if path.endswith('/snapshot') and req.method == 'POST':
            link.update(status="ok", last_introspected_at="2026-11-03T07:00:00")
            return route.fulfill(json={"v": 1, "link_id": link_id, "tables": [{"name": "customers"}, {"name": "orders"}], "introspected_at": "2026-11-03T07:00:00+00:00"})
        if path.endswith('/diff'):
            return route.fulfill(json=self.diff(applied=any(i["id"] == 14 for i in self.items)))
        if path.endswith('/apply'):
            item = {"id": 14, "workspace_id": 5, "item_type": "erd", "title": "Data · Shop dev", "content": NEW_ERD, "content_format": "mermaid", "status": "draft", "revision_count": 1}
            self.items.append(item)
            return route.fulfill(json={"applied": [{"key": k, "action": "added"} for k in json.loads(req.post_data)["tables"]], "skipped": [], "kept_extra_fields": {},
                                       "relations_added": 1, "item": item, "target": "link_erd", "message": "Applied tables from \"Shop dev\"."})
        if req.method == 'DELETE':
            self.links.remove(link)
            return route.fulfill(json={"ok": True})
        return route.fulfill(status=404, json={"detail": "mock"})


@pytest.fixture
def backend(page):
    b = DbBackend()
    _install_api(page, [])
    page.route("**/api/**", b.route)
    OUT.mkdir(parents=True, exist_ok=True)
    return b


def box(page, selector):
    b = page.locator(selector).first.bounding_box()
    assert b is not None, selector
    return {k: round(v, 1) for k, v in b.items()}


def _open(page, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(PLAN_URL)
    page.evaluate("() => sessionStorage.clear()")
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for(state='visible', timeout=10000)
    page.wait_for_timeout(300)


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}")
def test_opener_in_dock_and_drawer_geometry(page, backend, size):
    _open(page, size)
    opener, dock, canvas = box(page, '.plan-databases-open'), box(page, '.erd-dock-left'), box(page, '.erd-viewport')
    advanced = box(page, '.plan-advanced-open')
    assert dock['x'] - 0.5 <= opener['x'] and opener['x'] + opener['width'] <= dock['x'] + dock['width'] + 0.5
    assert opener['x'] >= advanced['x'] + advanced['width'] - 0.5 or opener['y'] >= advanced['y'] + advanced['height'] - 0.5   # no overlap
    assert dock['x'] + dock['width'] <= canvas['x'] + canvas['width'] + 0.5 and dock['y'] + dock['height'] <= canvas['y'] + canvas['height'] + 0.5
    page.locator('.plan-databases-open').click()
    page.locator('.plan-db-link').first.wait_for()
    page.wait_for_timeout(250)
    d = box(page, '.plan-drawer')
    assert abs(d['x'] + d['width'] - size[0]) <= 1 and d['height'] >= size[1] - 1 and d['width'] <= size[0]
    for sel in ('.plan-db-link', '.plan-db-actions'):
        b = box(page, sel)
        assert b['x'] >= d['x'] - 0.5 and b['x'] + b['width'] <= d['x'] + d['width'] + 0.5, (sel, b, d)
    assert page.evaluate("() => document.querySelector('.plan-db').scrollWidth <= document.querySelector('.plan-db').clientWidth + 1")
    page.screenshot(path=str(OUT / f'n5p4-drawer-{size[0]}.png'))
    for _ in range(6):
        page.keyboard.press('Tab')
        assert page.evaluate("() => !!document.activeElement.closest('.plan-drawer')")
    page.keyboard.press('Escape')
    page.wait_for_timeout(250)
    assert page.locator('.plan-drawer').count() == 0
    assert page.evaluate("() => document.activeElement.classList.contains('plan-databases-open')")


def test_password_test_introspect_diff_apply(page, backend):
    _open(page, (1440, 900))
    page.locator('.plan-databases-open').click()
    page.locator('.plan-db-link').first.wait_for()
    page.get_by_role('button', name='Set password').click()
    field = page.locator('input[name="secret"]')
    assert field.get_attribute('type') == 'password' and field.get_attribute('autocomplete') == 'new-password'
    field.fill(SECRET)
    page.screenshot(path=str(OUT / 'n5p4-password.png'))
    page.get_by_role('button', name='Save password').click()
    page.get_by_text('Password saved.').wait_for()
    puts = [c for c in backend.calls if c["path"].endswith('/secret')]
    assert len(puts) == 1 and puts[0]["method"] == 'PUT' and json.loads(puts[0]["body"]) == {"secret": SECRET}
    assert page.locator('input[name="secret"]').count() == 0
    assert SECRET not in page.content()
    assert page.evaluate("(s) => !JSON.stringify(sessionStorage).includes(s) && !JSON.stringify(localStorage).includes(s)", SECRET)
    assert 'password saved (session)' in page.locator('.plan-db-meta').first.inner_text()
    page.get_by_role('button', name='Test').click()
    page.get_by_text('Shop dev: connected (server 160004).').wait_for()
    assert page.locator('.plan-db-chip').first.inner_text() == 'Connected'
    page.get_by_role('button', name='Introspect').click()
    page.get_by_text('Shop dev: 2 table(s) saved to the snapshot.').wait_for()
    page.get_by_role('button', name='Review diff').click()
    page.locator('.plan-db-picker .plan-db-row').first.wait_for()
    # 322 added: default selection capped at 300; conflict row disabled
    assert page.locator('.plan-db-count').inner_text().startswith('300 selected')
    assert page.locator('.plan-db-row.kind-conflict input').is_disabled()
    page.get_by_role('button', name='Select shown').click()
    assert page.locator('.plan-db-count').inner_text().startswith('322 selected')
    assert page.locator('.plan-db-count.over').count() == 1 and page.locator('[data-act="apply"]').is_disabled()
    page.screenshot(path=str(OUT / 'n5p4-diff-over-cap.png'))
    page.get_by_role('button', name='Clear').click()
    page.locator('.plan-db-search').fill('ord')
    page.wait_for_timeout(100)
    assert page.evaluate("() => document.activeElement.classList.contains('plan-db-search')")   # typing keeps focus
    assert page.locator('.plan-db-picker .plan-db-row').count() == 1
    page.locator('.plan-db-row input[type="checkbox"]').check()
    page.locator('.plan-db-search').fill('cust')
    page.locator('.plan-db-row input[type="checkbox"]').check()
    page.locator('.plan-db-search').fill('')
    assert page.locator('.plan-db-count').inner_text().startswith('2 selected')
    picker, drawer, foot = box(page, '.plan-db-picker'), box(page, '.plan-drawer'), box(page, '.plan-db-apply')
    assert foot['y'] + foot['height'] <= drawer['y'] + drawer['height'] + 1 and picker['x'] >= drawer['x']
    page.screenshot(path=str(OUT / 'n5p4-diff-picker.png'))
    page.locator('[data-act="apply"]').click()
    page.get_by_text('Applied 2 table(s).').wait_for()
    applies = [json.loads(c["body"]) for c in backend.calls if c["path"].endswith('/apply')]
    assert applies == [{"tables": ["public.orders", "public.customers"], "item_id": None, "expected_revision": None}]
    # the Data tab now shows the new per-link ERD
    page.keyboard.press('Escape')
    page.wait_for_timeout(300)
    assert page.locator('#plan-artifact-select').input_value() == '14'
    assert page.locator('.erd-table').count() == 2
    page.screenshot(path=str(OUT / 'n5p4-applied-erd.png'))
    every = json.dumps(backend.calls)
    assert every.count(SECRET) == 1   # only the one PUT /secret body


def test_add_link_form_sends_secret_separately(page, backend):
    _open(page, (768, 1024))
    page.locator('.plan-databases-open').click()
    page.locator('.plan-db-link').first.wait_for()
    page.get_by_role('button', name='Add link').click()
    form = page.locator('.plan-db-form')
    form.locator('select[name="engine"]').select_option('mysql')
    form.locator('input[name="database"]').fill('legacy')
    form.locator('input[name="username"]').fill('reader')
    form.locator('input[name="port"]').fill('3306')
    form.locator('input[name="secret"]').fill(SECRET)
    page.screenshot(path=str(OUT / 'n5p4-add-link.png'))
    form.get_by_role('button', name='Add link').click()
    page.locator('.plan-db-link').nth(1).wait_for()
    post = next(c for c in backend.calls if c["method"] == 'POST' and c["path"].endswith('/db-links'))
    assert SECRET not in post["body"] and json.loads(post["body"])["engine"] == 'mysql' and json.loads(post["body"])["port"] == 3306
    puts = [c for c in backend.calls if c["path"].endswith('/2/secret')]
    assert len(puts) == 1 and json.loads(puts[0]["body"]) == {"secret": SECRET}
    assert SECRET not in page.content()
    page.evaluate("() => { window.__asked = []; if (window.DecisionsAPI) window.DecisionsAPI.confirm = async (o) => { window.__asked.push(o.title); return true; }; }")
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('.plan-db-link').nth(1).get_by_role('button', name='Archive').click()
    page.wait_for_timeout(300)
    assert page.locator('.plan-db-link').count() == 1
    assert any(c["method"] == 'DELETE' and c["path"].endswith('/db-links/2') for c in backend.calls)
