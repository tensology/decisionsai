"""Responsive Settings smoke coverage with API reads stubbed and writes blocked."""

import json

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8765"
READ_FIXTURE = json.dumps({"success": True, "items": [], "data": [], "results": [], "settings": {}})


def test_settings_navigation_and_nested_tabs_are_responsive_without_writes():
    blocked_writes = []
    viewports = [390, 768, 1280, 1440]
    nested_groups = [
        ("general", "[data-general-subtab]"),
        ("thirdparty", "[data-thirdparty-subtab]"),
        ("llms", "[data-llm-subtab]"),
        ("advanced", "[data-advanced-subtab]"),
    ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width in viewports:
            page = browser.new_page(viewport={"width": width, "height": 900})

            def protect_requests(route):
                request = route.request
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    blocked_writes.append((width, request.method, request.url))
                    route.abort()
                elif "/api/" in request.url:
                    route.fulfill(status=200, content_type="application/json", body=READ_FIXTURE)
                else:
                    route.continue_()

            page.route("**/*", protect_requests)
            page.goto(f"{BASE_URL}/settings", wait_until="domcontentloaded")
            page.locator("#settings-nav .tab-button").first.wait_for()

            tabs = page.locator("#settings-nav .tab-button").evaluate_all(
                "nodes => nodes.map(node => node.dataset.tab)"
            )
            assert len(tabs) == 11

            for name in tabs:
                page.locator(f'#settings-nav .tab-button[data-tab="{name}"]').click()
                page.locator(f"#tab-{name}.active-tab-panel").wait_for()
                assert page.evaluate("document.documentElement.scrollWidth") <= width
                assert page.locator(f'#settings-nav .tab-button[data-tab="{name}"]').evaluate(
                    "node => { const r = node.getBoundingClientRect(); const n = document.querySelector('#settings-nav').getBoundingClientRect(); return r.right > n.left && r.left < n.right; }"
                )
                assert page.locator(f'#settings-nav .tab-button[data-tab="{name}"]').evaluate(
                    "node => Math.round(node.getBoundingClientRect().height) >= 44"
                )

            for parent_tab, button_selector in nested_groups:
                page.locator(f'#settings-nav .tab-button[data-tab="{parent_tab}"]').click()
                buttons = page.locator(button_selector)
                button_attribute = {
                    "general": "data-general-subtab",
                    "thirdparty": "data-thirdparty-subtab",
                    "llms": "data-llm-subtab",
                    "advanced": "data-advanced-subtab",
                }[parent_tab]
                keys = buttons.evaluate_all(
                    f"nodes => nodes.map(node => node.getAttribute('{button_attribute}'))"
                )
                assert keys
                for key in keys:
                    button = page.locator(f"[{button_attribute}='{key}']")
                    button.click()
                    assert button.get_attribute("aria-selected") == "true"
                    panel_attribute = {
                        "general": "data-general-panel",
                        "thirdparty": "data-thirdparty-panel",
                        "llms": "data-llm-panel",
                        "advanced": "data-advanced-panel",
                    }[parent_tab]
                    assert page.locator(f"[{panel_attribute}='{key}']").is_visible()
                    if width <= 860:
                        assert button.evaluate("node => Math.round(node.getBoundingClientRect().height) >= 44")
                    assert page.evaluate("document.documentElement.scrollWidth") <= width

            if width <= 860:
                page.locator('#settings-nav .tab-button[data-tab="shortcuts"]').click()
                mobile_controls = page.locator(".settings-content button").evaluate_all(
                    "nodes => nodes.filter(node => node.getClientRects().length).map(node => Math.round(node.getBoundingClientRect().height))"
                )
                assert mobile_controls and min(mobile_controls) >= 44
                clear_button = page.locator("#tab-shortcuts .hotkey-clear").first
                assert clear_button.evaluate("node => Math.round(node.getBoundingClientRect().width) >= 44 && Math.round(node.getBoundingClientRect().height) >= 44")

            if width == 390:
                page.locator('#settings-nav .tab-button[data-tab="general"]').click()
                small_fields = page.locator(
                    "#tab-general input:not([type=hidden]):not([type=checkbox]):not([type=radio]), "
                    "#tab-general select, #tab-general textarea"
                ).evaluate_all("nodes => nodes.map(node => getComputedStyle(node).fontSize)")
                assert small_fields and all(float(size.removesuffix("px")) >= 16 for size in small_fields)
                page.locator('#settings-nav .tab-button[data-tab="llms"]').click()
                page.locator('[data-llm-subtab="workflow"]').click()
                route_region = page.locator("#llms_exec_routes")
                assert route_region.get_attribute("role") == "region"
                assert route_region.get_attribute("tabindex") == "0"
                assert page.locator("#llms_exec_routing_scroll_hint").is_visible()
                assert route_region.evaluate("node => node.scrollWidth > node.clientWidth")
                assert page.evaluate("document.documentElement.scrollWidth") <= width
                table_state = route_region.evaluate("node => Array.from(node.querySelectorAll('select')).map(select => select.value)")
                headers = route_region.locator("[role=columnheader]")
                assert headers.count() == 5
                for index in range(headers.count()):
                    header = headers.nth(index)
                    header_text = header.evaluate("node => node.textContent.trim()")
                    page.evaluate(
                        "index => { const container=document.querySelector('#llms_exec_routes'), node=container.querySelectorAll('[role=columnheader]')[index], outer=container.getBoundingClientRect(), inner=node.getBoundingClientRect(); if(inner.left<outer.left) container.scrollLeft-=outer.left-inner.left; if(inner.right>outer.right) container.scrollLeft+=inner.right-outer.right; }",
                        index,
                    )
                    assert header.evaluate(
                        "(node, label) => { const r=node.getBoundingClientRect(), c=document.querySelector('#llms_exec_routes').getBoundingClientRect(); return r.left >= c.left && r.right <= c.right && node.textContent.trim() === label; }",
                        header_text,
                    )
                for selector in (".wf-exec-backend", ".wf-exec-provider", ".wf-exec-model", ".wf-exec-fallback-backend"):
                    control = route_region.locator(selector).first
                    assert control.is_visible()
                    page.evaluate(
                        "selector => { const container=document.querySelector('#llms_exec_routes'), node=container.querySelector(selector), outer=container.getBoundingClientRect(), inner=node.getBoundingClientRect(); if(inner.left<outer.left) container.scrollLeft-=outer.left-inner.left; if(inner.right>outer.right) container.scrollLeft+=inner.right-outer.right; }",
                        selector,
                    )
                    assert control.evaluate(
                        "node => { const r=node.getBoundingClientRect(), c=document.querySelector('#llms_exec_routes').getBoundingClientRect(); return r.left >= c.left && r.right <= c.right; }"
                    )
                assert route_region.evaluate("node => Array.from(node.querySelectorAll('select')).map(select => select.value)") == table_state
                page.locator('#settings-nav .tab-button[data-tab="initiative"]').click()
                health_badge = page.locator("#initiative-health-badge")
                assert health_badge.evaluate(
                    "node => { const r = node.getBoundingClientRect(); const p = document.querySelector('#tab-initiative').getBoundingClientRect(); return r.left >= p.left && r.right <= p.right; }"
                )
                assert page.evaluate("document.documentElement.scrollWidth") <= width
                page.locator('#settings-nav .tab-button[data-tab="advanced"]').click()
                page.locator('[data-advanced-subtab="directories"]').click()
                for selector in ("#exclude_types", "#reindex_button"):
                    assert page.locator(selector).evaluate(
                        "node => { const r = node.getBoundingClientRect(); const p = document.querySelector('#tab-advanced').getBoundingClientRect(); return r.left >= p.left && r.right <= p.right; }"
                    )
                assert page.evaluate("document.documentElement.scrollWidth") <= width
            page.close()

        browser.close()

    assert blocked_writes == []
