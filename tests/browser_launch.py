"""Shared Chromium launch for tests that drive their own sync_playwright().

Playwright 1.58's bundled headless Chromium (145) on macOS crashes the renderer
when an emoji is painted after other text on the page ("Target crashed"); a
five-line repro outside the app does it, and installed Google Chrome does not.
The create-agent dialog test renders the real "⭐ Sky" voice label, so it
crashed every time. Prefer the installed Chrome channel there; set
DECISIONS_TEST_BROWSER_CHANNEL="" to force the bundled build (or another channel).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def chromium_channel() -> str | None:
    configured = os.environ.get("DECISIONS_TEST_BROWSER_CHANNEL")
    if configured is not None:
        return configured or None
    if sys.platform == "darwin" and Path("/Applications/Google Chrome.app").exists():
        return "chrome"
    return None


def launch_chromium(playwright, **kwargs):
    channel = chromium_channel()
    if channel and "channel" not in kwargs:
        kwargs["channel"] = channel
    return playwright.chromium.launch(**kwargs)
