"""Plan workspace verification pass (fixture server, mocked APIs): tab visuals + objective geometry, one
chat panel across tabs, Documents footer flush at the bottom, no stray outline, remembered chat route.

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_verify_playwright.py
"""
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
WIDTHS = [(1440, 900), (768, 1024), (390, 844)]
TABS = ['Interfaces', 'Data', 'Documents']
MEASURE = r"""() => {
  const q = (s) => document.querySelector(s);
  const box = (n) => { if (!n) return null; const r = n.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  const conv = q('.plan-conversation'); conv.__verifyId ||= Math.random();
  const overflowing = [...document.querySelectorAll('.plan-detail *')].filter((n) => {
    const c = getComputedStyle(n); if (c.overflowX !== 'visible' || !n.getClientRects().length) return false;
    if (n.closest('.erd-stage')) return false;   // the transformed ERD stage is meant to exceed its viewport
    return n.scrollWidth > n.clientWidth + 1 && n.clientWidth > 0;
  }).map((n) => n.className).slice(0, 5);
  return { convId: conv.__verifyId, conv: box(conv), header: box(q('.plan-conversation .plan-messages')), composer: box(q('#plan-language')),
           artifacts: box(q('#plan-artifacts')), docScrollW: document.documentElement.scrollWidth, vw: innerWidth, overflowing };
}"""


def _setup(page, w, h):
    b = Backend()
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": w, "height": h})
    seen = {"errors": [], "console": [], "failed": []}
    page.on("pageerror", lambda e: seen["errors"].append(str(e)))
    page.on("console", lambda m: seen["console"].append(f"{m.type}: {m.text[:160]}") if m.type in ('error', 'warning') else None)
    page.on("requestfailed", lambda r: seen["failed"].append(r.url[:140]))
    page.on("response", lambda r: seen["failed"].append(f"{r.status} {r.url[:140]}") if r.status >= 400 else None)
    page.goto(PLAN_URL)
    page.locator('.plan-conversation').wait_for()
    page.wait_for_timeout(600)
    return b, seen


def _tab(page, name):
    page.get_by_role("button", name=name, exact=True).click()
    page.wait_for_timeout(600)


def _not_ws(items):   # the fixture server refuses the realtime websocket (403) by design; everything else must be clean
    return [x for x in items if '/api/ws' not in x and 'WebSocket' not in x]


@pytest.mark.parametrize("w,h", WIDTHS)
def test_tabs_visual_and_one_chat_panel(page, w, h):
    _, seen = _setup(page, w, h)
    page.fill('#plan-language-input', 'draft kept across tabs')
    rows = []
    for name in TABS + ['Interfaces']:
        _tab(page, name)
        m = page.evaluate(MEASURE)
        m["tab"] = name
        rows.append(m)
        if len(rows) <= 3:
            page.screenshot(path=str(OUT / f'verify-{name.lower()}-{w}.png'))
        assert m["docScrollW"] <= m["vw"], m                    # no horizontal page overflow
        assert not m["overflowing"], m                          # nothing clipped / spilling sideways
        assert page.input_value('#plan-language-input') == 'draft kept across tabs'
    first = rows[0]
    for m in rows[1:]:   # same DOM node (never re-mounted) and the same geometry on every tab
        assert m["convId"] == first["convId"] and m["conv"] == first["conv"] and m["composer"] == first["composer"], (first, m)
    print('MEASURE', w, json.dumps(rows))
    assert not seen["errors"], seen["errors"]
    assert not _not_ws(seen["console"]), seen["console"]
    assert not _not_ws(seen["failed"]), seen["failed"]


@pytest.mark.parametrize("w,h", WIDTHS)
@pytest.mark.parametrize("long", [False, True])
def test_requirements_footer_flush_at_bottom(page, w, h, long):
    backend, _ = _setup(page, w, h)
    if long:
        backend.items[0]["content"] = "# Long\n\n" + "\n\n".join(f"Paragraph {i} with some requirement text." for i in range(80))
        page.reload(); page.locator('.plan-conversation').wait_for()
    _tab(page, 'Documents')
    footer = page.locator('.plan-inline-footer')
    footer.wait_for()
    preview = page.evaluate("() => { const r = document.querySelector('#plan-preview').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }")
    fb = footer.bounding_box()
    body = page.locator('.plan-inline-body').bounding_box()
    assert abs((fb['y'] + fb['height']) - preview[3]) <= 1, (fb, preview)
    assert abs(fb['width'] - (preview[2] - preview[0])) <= 2, (fb, preview)   # full width of the pane
    assert fb['y'] >= body['y'] + body['height'] - 1, (fb, body)             # under the scroller, not beside it
    if long:
        scroll = page.evaluate("() => { const p = document.querySelector('.plan-inline-body'); return [p.scrollHeight, p.clientHeight]; }")
        assert scroll[0] > scroll[1]                              # content scrolls above the footer
        page.evaluate("() => { document.querySelector('.plan-inline-body').scrollTop = 400; }")
        fb2 = footer.bounding_box()
        assert abs((fb2['y'] + fb2['height']) - preview[3]) <= 1
    page.screenshot(path=str(OUT / f'verify-requirements-footer{"-long" if long else ""}-{w}.png'))
    print('MEASURE footer', w, long, json.dumps({"footer": fb, "preview": preview}))


@pytest.mark.parametrize("w,h", WIDTHS)
def test_no_stray_outline_but_focus_visible(page, w, h):
    _setup(page, w, h)
    _tab(page, 'Documents')
    text = page.locator('.plan-inline-text')
    style = lambda: text.evaluate("(n) => { const c = getComputedStyle(n); return { outline: c.outlineStyle, border: c.borderTopColor, shadow: c.boxShadow }; }")
    s = style()
    assert s["outline"] == 'none' and s["border"] in ('rgba(0, 0, 0, 0)', 'transparent') and s["shadow"] == 'none', s
    text.hover()
    assert style()["border"] in ('rgba(0, 0, 0, 0)', 'transparent')
    # nothing else in the artifacts pane draws an outline while unfocused
    stray = page.evaluate("() => [...document.querySelectorAll('#plan-artifacts *')].filter((n) => n !== document.activeElement && getComputedStyle(n).outlineStyle !== 'none').map((n) => n.className)")
    assert stray == [], stray
    text.focus()
    focused = style()
    assert focused["outline"] == 'none' and focused["shadow"] == 'none', focused   # no orange left bar on the document
    page.evaluate("() => document.querySelector('.plan-doc-tab[aria-selected=\"true\"]').focus({ focusVisible: true })")
    tab_outline = page.locator('.plan-doc-tab[aria-selected="true"]').evaluate("(n) => getComputedStyle(n).outlineStyle")
    assert tab_outline == 'solid'                                 # keyboard outline on the tab stays


def test_chat_route_is_remembered_and_preselected(page):
    _setup(page, 1440, 900)
    page.route("**/api/llms/**", lambda route: route.fulfill(json={
        "providers": [{"id": "ollama", "name": "Ollama"}, {"id": "openai", "name": "OpenAI"}],
        "models": [{"id": "gpt-test", "name": "GPT Test"}] if "provider=openai" in route.request.url else [{"id": "llama", "name": "Llama"}]}))
    page.reload(); page.locator('.plan-conversation').wait_for()
    page.wait_for_function("() => document.querySelectorAll('#plan-provider option').length > 1")
    assert page.input_value('#plan-provider') == ''
    page.select_option('#plan-provider', 'openai')
    page.wait_for_function("() => [...document.querySelectorAll('#plan-model option')].some((o) => o.value === 'gpt-test')")
    page.select_option('#plan-model', 'gpt-test')
    page.reload(); page.locator('.plan-conversation').wait_for()
    page.wait_for_function("() => document.querySelector('#plan-model').value === 'gpt-test'", timeout=5000)
    assert page.input_value('#plan-provider') == 'openai'
    # still changeable per chat, and the change becomes the new default
    page.select_option('#plan-provider', 'ollama')
    page.wait_for_function("() => [...document.querySelectorAll('#plan-model option')].some((o) => o.value === 'llama')")
    page.select_option('#plan-model', 'llama')
    stored = json.loads(page.evaluate("() => localStorage.getItem('decisions.plan.route')"))
    assert stored["global"] == {"provider": "ollama", "model": "llama"} and stored["workspaces"]["5"] == stored["global"]
