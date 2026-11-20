"""Layout of WhatsApp proposal markers using synthetic messages only.

The page is a local fixture. It never calls the Mac Incoming server and never
mutates proposals. Run:

    python3 tests/ui/test_incoming_proposal_marker_layout.py

or, on Python 3.12.8+:

    pytest -m e2e_playwright tests/ui/test_incoming_proposal_marker_layout.py -q
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e_playwright

ROOT = Path(__file__).resolve().parents[2]
INDEX_JS = ROOT / "distr" / "gui" / "web" / "static" / "development" / "incoming" / "index.js"
FIXTURE = "/tests/ui/fixtures/incoming_proposal_marker_layout.html"
SHOTS = Path(os.environ.get("INCOMING_MARKER_SHOTS", "/tmp/incoming-proposal-marker-proof"))


def check_frontend_syntax() -> None:
    """Parse the Incoming module. The layout fix does not change this file."""
    subprocess.run(["node", "--check", str(INDEX_JS)], check=True)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return


def _server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    previous = os.getcwd()
    os.chdir(ROOT)
    thread.start()
    return httpd, previous


def _launch(playwright):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    chrome = os.environ.get("CHROME_PATH")
    if chrome:
        return playwright.chromium.launch(executable_path=chrome, args=args)
    return playwright.chromium.launch(args=args)


def _assert_timeline(report: dict) -> None:
    container_w = report["container"]["w"]
    assert report["container"]["scrollW"] <= report["container"]["clientW"] + 1, report["container"]
    assert report["overlaps"] == [], report["overlaps"]
    assert report["mediaOverlaps"] == [], report["mediaOverlaps"]
    assert "synthetic-other-thread" not in " ".join(report["order"])
    for gap in report["gaps"]:
        minimum = 12 if gap["markerGap"] else 4
        assert gap["gap"] >= minimum, gap
    assert any(item["tag"] == "VIDEO" and item["h"] > 40 and item["w"] > 40 for item in report["media"]), report["media"]
    outgoing = [bubble for bubble in report["bubbles"] if bubble["id"] == "wa-m2"]
    assert outgoing and outgoing[0]["alignSelf"] == "flex-end", outgoing
    assert any(bubble["id"] == "wa-m3" and "uncovered" in bubble["text"] for bubble in report["bubbles"])
    for bubble in report["bubbles"]:
        assert bubble["outside"] is False, bubble
        assert bubble["w"] <= container_w * 0.78 + 4, bubble
    assert report["markers"], "expected proposal markers"
    for marker in report["markers"]:
        assert marker["whiteSpace"] == "normal", marker
        assert marker["overflow"] == "visible", marker
        assert not marker["maxWidth"].endswith("420px"), marker["maxWidth"]
        assert marker["outside"] is False, marker
        assert marker["scrollW"] <= marker["clientW"] + 2, marker
        assert marker["scrollH"] <= marker["clientH"] + 2, marker
        assert marker["anchor"]["found"] and marker["anchor"]["visible"], marker
        assert marker["text"].startswith("Proposal: SYNTHETIC "), marker["text"]
        assert marker["text"].endswith(f" {marker['kind']}"), marker["text"]
        minimum = container_w * (0.62 if container_w >= 700 else 0.55)
        assert marker["spanW"] >= minimum, (marker["spanW"], container_w)
        if container_w >= 800:
            assert marker["spanW"] > 420, (marker["spanW"], container_w)
    if report["case"] == "one":
        assert report["order"] == [
            "wa-m1",
            "wa-m2",
            "start:synthetic-proposal-1",
            "wa-m3",
            "end:synthetic-proposal-1",
            "wa-m4",
            "wa-m5",
        ], report["order"]
    else:
        assert report["order"] == [
            "start:synthetic-solo",
            "wa-m1",
            "end:synthetic-solo",
            "wa-m2",
            "start:synthetic-proposal-1",
            "start:synthetic-proposal-2",
            "start:synthetic-proposal-3",
            "start:synthetic-proposal-4",
            "wa-m3",
            "end:synthetic-proposal-1",
            "end:synthetic-proposal-2",
            "end:synthetic-proposal-3",
            "end:synthetic-proposal-4",
            "wa-m4",
            "wa-m5",
        ], report["order"]


def _assert_actions(page) -> None:
    buttons = page.locator("#incoming-proposals-list button")
    count = buttons.count()
    assert count >= 3
    seen = []
    for index in range(count):
        button = buttons.nth(index)
        button.scroll_into_view_if_needed()
        handle = button.element_handle()
        hit = page.evaluate(
            """(el) => {
                const rect = el.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                const node = document.elementFromPoint(x, y);
                return { ok: !!node && (node === el || el.contains(node)), tag: node && node.tagName };
            }""",
            handle,
        )
        assert hit["ok"], hit
        button.focus()
        assert page.evaluate("(el) => document.activeElement === el", handle)
        page.keyboard.press("Enter")
        seen.append(button.inner_text() or button.get_attribute("aria-label"))
    hits = page.evaluate("window.__actionHits")
    assert len([hit for hit in hits if hit["type"] == "keydown"]) == count, hits
    assert all(label for label in seen)


def _run_case(page, base: str, layout_case: str, viewport: dict, shot_name: str) -> dict:
    page.set_viewport_size(viewport)
    page.goto(f"{base}{FIXTURE}?case={layout_case}", wait_until="networkidle")
    page.wait_for_function("window.__ready === true")
    if layout_case == "many" and viewport["width"] >= 1000:
        _assert_actions(page)
        count = page.locator("#incoming-proposals-count").inner_text()
        cards = page.locator("#incoming-proposals-list .proposal-title").all_inner_texts()
        titles = page.evaluate("window.__fixture.titles")
        assert count == "6", count
        assert set(cards) == {titles["soloTitle"], *titles["longTitles"], titles["otherTitle"]}
        assert "synthetic-other-thread" in page.locator("[data-incoming-proposal-open]").evaluate_all(
            "(nodes) => nodes.map((node) => node.getAttribute('data-incoming-proposal-open'))"
        )
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.locator("#incoming-proposals-list").screenshot(path=str(SHOTS / "proposal-actions.png"))
    report = page.evaluate("window.__fixture.measureTimeline()")
    _assert_timeline(report)
    box = page.locator(".incoming-whatsapp-messages")
    if layout_case == "many":
        page.locator('[data-proposal-id="synthetic-proposal-1"][data-proposal-marker="start"]').scroll_into_view_if_needed()
    else:
        page.locator('[data-proposal-id="synthetic-proposal-1"][data-proposal-marker="start"]').scroll_into_view_if_needed()
    SHOTS.mkdir(parents=True, exist_ok=True)
    box.screenshot(path=str(SHOTS / f"{shot_name}.png"))
    return report


def test_incoming_index_syntax():
    check_frontend_syntax()


@pytest.mark.parametrize(
    ("layout_case", "viewport", "shot_name"),
    [
        ("one", {"width": 1680, "height": 1000}, "one-desktop"),
        ("many", {"width": 1680, "height": 1000}, "many-desktop"),
        ("one", {"width": 390, "height": 800}, "one-narrow"),
        ("many", {"width": 390, "height": 800}, "many-narrow"),
        ("many", {"width": 2032, "height": 1072}, "many-screenshot-viewport"),
    ],
)
def test_proposal_marker_layout(layout_case, viewport, shot_name):
    from playwright.sync_api import sync_playwright

    httpd, previous = _server()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            page = browser.new_page()
            try:
                _run_case(page, base, layout_case, viewport, shot_name)
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        os.chdir(previous)


def main() -> int:
    from playwright.sync_api import sync_playwright

    check_frontend_syntax()
    print("syntax ok", INDEX_JS.name)
    cases = [
        ("one", {"width": 1680, "height": 1000}, "one-desktop"),
        ("many", {"width": 1680, "height": 1000}, "many-desktop"),
        ("one", {"width": 390, "height": 800}, "one-narrow"),
        ("many", {"width": 390, "height": 800}, "many-narrow"),
        ("many", {"width": 2032, "height": 1072}, "many-screenshot-viewport"),
    ]
    httpd, previous = _server()
    failures = 0
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            try:
                for layout_case, viewport, shot_name in cases:
                    page = browser.new_page()
                    try:
                        report = _run_case(page, base, layout_case, viewport, shot_name)
                    except Exception as error:
                        failures += 1
                        print(f"FAIL {shot_name}: {error}")
                    else:
                        print(
                            f"PASS {shot_name} markers={len(report['markers'])} "
                            f"column={round(report['container']['w'])} "
                            f"span={round(report['markers'][0]['spanW'])}"
                        )
                    finally:
                        page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        os.chdir(previous)
    print(f"cases={len(cases)} failures={failures} shots={SHOTS}")
    return failures


if __name__ == "__main__":
    sys.exit(main())
