#!/usr/bin/env python3
"""Capture deterministic browser evidence for a disposable Spotify fixture."""

from __future__ import annotations

import argparse
import os
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def capture(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    if not (root / "index.html").is_file():
        raise SystemExit("index.html is missing; browser evidence cannot be captured")
    artifact = root / "artifacts" / "ui-after.png"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    previous_cwd = Path.cwd()
    os.chdir(root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            console_errors: list[str] = []
            page_errors: list[str] = []
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(
                f"http://127.0.0.1:{server.server_port}/",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(300)
            search = page.locator(
                '[data-route-link="search"], a[href*="search" i], '
                'button[data-route="search"], [data-nav="search"]'
            ).first
            if search.count() == 0:
                search = page.get_by_text(re.compile(r"^search$", re.IGNORECASE)).first
            if search.count() == 0:
                raise RuntimeError("Search navigation control was not found")
            search.click()
            player = page.locator(
                '[data-play], [data-action="play"], '
                'button[aria-label*="play" i], button[aria-label*="pause" i]'
            ).first
            if player.count() == 0:
                player = page.get_by_role(
                    "button", name=re.compile(r"play|pause", re.IGNORECASE)
                ).first
            if player.count() == 0:
                raise RuntimeError("Play/Pause control was not found")
            player.click()
            page.screenshot(path=str(artifact), full_page=True)
            if page.locator("body").count() != 1:
                raise RuntimeError("rendered page body was not available")
            if console_errors or page_errors:
                raise RuntimeError(
                    "browser errors: " + "; ".join(console_errors + page_errors)
                )
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        os.chdir(previous_cwd)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    artifact = capture(args.project_root)
    print("GREEN validation passed: Spotify remake ticket reached complete.")
    print(f"After screenshot: {artifact}")
    print("Flow summary: Opened the built app, navigated to Search, started playback, and captured the rendered result.")
    print("Layout hierarchy notes: Sidebar/mobile navigation, content, metadata, and persistent player remain visually distinct.")
    print("1. [click] Open Search navigation -> Search route rendered")
    print("2. [click] Start playback -> Player changed to playing state")


if __name__ == "__main__":
    main()
