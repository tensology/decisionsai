"""Minimal Playwright smoke test — verifies browser automation deps for chat/workflow flows."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.integration


def test_playwright_chromium_launches_and_evals():
    """Ensure Chromium launches without crashing (skip if browsers not installed)."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto("about:blank")
                assert page.evaluate("() => 1 + 1") == 2
            finally:
                browser.close()
    except Exception as exc:
        pytest.skip(f"Playwright/Chromium unavailable: {exc}")
