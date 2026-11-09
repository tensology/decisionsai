"""Synthetic Reports layout check. Not a live Mac session and not a provider call.

Serves the fixture page and records screenshots plus geometry assertions.
"""
from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path("/opt/cursor/artifacts/reports-visual")
URL = "http://127.0.0.1:8766/tests/ui/fixtures/reports_visual.html"


def assert_true(log, name, ok, detail):
    log.append({"check": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def measure(page, kind):
    return page.evaluate(
        """(kind) => {
            const box = (node) => {
                if (!node) return null;
                const r = node.getBoundingClientRect();
                return {top: r.top, left: r.left, right: r.right, bottom: r.bottom, width: r.width, height: r.height};
            };
            const card = document.querySelector('.reports-calendar-card');
            const pieCard = document.querySelector('.reports-pie-card');
            const month = document.querySelector('.reports-month');
            const pie = document.querySelector('.reports-pie svg');
            const legend = document.querySelector('.reports-pie ul');
            const rule = document.querySelector('.reports-pie-rule');
            const pieCardNode = document.querySelector('.reports-pie-card');
            const shell = document.querySelector('.reports-day-shell');
            const side = document.querySelector('.reports-day-side');
            const listItem = document.querySelector('.reports-day-list li');
            const grid = document.querySelector('.reports-day-shell .reports-timegrid');
            const zoom = document.querySelector('.reports-hour-zoom');
            const hours = document.querySelector('.reports-hours');
            const scroller = document.querySelector('.reports-timegrid-scroll');
            const blocks = [...document.querySelectorAll('.reports-block')].map((node) => {
                const r = node.getBoundingClientRect();
                return {top: r.top, left: r.left, right: r.right, bottom: r.bottom, font: getComputedStyle(node).fontSize};
            });
            const view = document.querySelector('#reports-period-view');
            const segs = document.querySelector('.reports-segs');
            const segButtons = [...document.querySelectorAll('.reports-seg')];
            const workspace = document.querySelector('.reports-workspace');
            const stats = document.querySelector('.reports-stats-tab');
            const projectAccess = (() => {
                const scroller = document.querySelector('.reports-pie-card');
                const svg = document.querySelector('.reports-pie svg');
                const legend = document.querySelector('.reports-pie ul');
                const rows = legend ? [...legend.querySelectorAll('li')] : [];
                const row = rows[0] || null;
                const last = rows.length ? rows[rows.length - 1] : null;
                if (!scroller || !svg || !legend || scroller.getBoundingClientRect().height < 1) return null;
                const overflowY = getComputedStyle(scroller).overflowY;
                const canScroll = overflowY === 'auto' || overflowY === 'scroll';
                const revealFully = (node) => {
                    let box = node.getBoundingClientRect();
                    let card = scroller.getBoundingClientRect();
                    if (box.bottom > card.bottom) scroller.scrollTop += box.bottom - card.bottom;
                    box = node.getBoundingClientRect();
                    card = scroller.getBoundingClientRect();
                    if (box.top < card.top) scroller.scrollTop -= card.top - box.top;
                };
                scroller.scrollTop = 0;
                const restLegend = legend.getBoundingClientRect();
                const restCard = scroller.getBoundingClientRect();
                const restOverlap = Math.min(restLegend.bottom, restCard.bottom) - Math.max(restLegend.top, restCard.top);
                revealFully(svg);
                const svgShown = svg.getBoundingClientRect();
                const cardAtSvg = scroller.getBoundingClientRect();
                const svgFullyVisible = svgShown.height > 40
                    && svgShown.top >= cardAtSvg.top - 1
                    && svgShown.bottom <= cardAtSvg.bottom + 1;
                scroller.scrollTop = 0;
                if (row) revealFully(row);
                const rowShown = (row || legend).getBoundingClientRect();
                const cardAtList = scroller.getBoundingClientRect();
                const legendVisible = rowShown.height >= 12
                    && rowShown.top >= cardAtList.top - 1
                    && rowShown.bottom <= cardAtList.bottom + 1;
                scroller.scrollTop = 0;
                let lastRowFullyVisible = false;
                let lastRowText = '';
                if (last) {
                    revealFully(last);
                    const shown = last.getBoundingClientRect();
                    const card = scroller.getBoundingClientRect();
                    lastRowText = last.textContent.replace(/\s+/g, ' ').trim();
                    lastRowFullyVisible = shown.height >= 12
                        && shown.top >= card.top - 1
                        && shown.bottom <= card.bottom + 1;
                }
                const legendContent = legend.scrollHeight;
                scroller.scrollTop = 0;
                return {
                    paneWidth: view ? view.getBoundingClientRect().width : 0,
                    overflowY,
                    canScroll,
                    cardClient: scroller.clientHeight,
                    cardScroll: scroller.scrollHeight,
                    legendContent,
                    legendClient: legend.clientHeight,
                    legendVisible,
                    restOverlap,
                    rowCount: rows.length,
                    lastRowText,
                    lastRowFullyVisible,
                    svgFullyVisible,
                    svgHeight: svgShown.height,
                    svgWidth: svgShown.width
                };
            })();
            const headings = [...document.querySelectorAll('.reports-stats-tab h3')].map((node) => node.textContent.trim());
            const headers = [...document.querySelectorAll('#reports-results th')].map((node) => node.textContent.trim());
            return {
                kind,
                viewport: {width: innerWidth, height: innerHeight},
                refresh: document.querySelectorAll('#reports-refresh').length,
                noise: document.body.innerText.includes('Analysis route was not included') || document.body.innerText.includes('Not Found'),
                card: box(card),
                pieCard: box(pieCard),
                month: box(month),
                pie: box(pie),
                legend: box(legend),
                rule: box(rule),
                pieText: pieCardNode ? pieCardNode.innerText : '',
                shell: box(shell),
                side: box(side),
                listItem: box(listItem),
                grid: box(grid),
                zoom: box(zoom),
                hours: box(hours),
                hourPx: document.querySelector('.reports-day-shell')?.dataset.hourPx || document.querySelector('.reports-timegrid')?.dataset.hourPx || null,
                colHeads: document.querySelectorAll('.reports-col-head').length,
                blockCount: document.querySelectorAll('.reports-block').length,
                zoomCount: document.querySelectorAll('.reports-hour-zoom').length,
                zoomInsideTimegrid: !!(zoom && zoom.parentElement && zoom.parentElement.classList.contains('reports-timegrid') && !zoom.closest('.reports-day-side')),
                scroll: scroller ? {client: scroller.clientHeight, scroll: scroller.scrollHeight} : null,
                blocks,
                view: box(view),
                paneWidth: view ? view.getBoundingClientRect().width : 0,
                projectAccess,
                segs: box(segs),
                segsOverflow: segs ? segs.scrollWidth - segs.clientWidth : null,
                segFont: segButtons[0] ? getComputedStyle(segButtons[0]).fontSize : null,
                segLabels: segButtons.map((node) => node.textContent),
                segButtonOverflow: segButtons.filter((node) => node.scrollWidth > node.clientWidth + 1).map((node) => node.textContent),
                workspaceOverflow: workspace ? workspace.scrollWidth - workspace.clientWidth : null,
                statsWidth: stats ? stats.getBoundingClientRect().width : null,
                headings,
                statLabels: [...document.querySelectorAll('#reports-stats-view td')].map((cell) => cell.dataset.label || ''),
                headers,
                historyText: document.querySelector('#reports-results')?.innerText || '',
                outerScroll: document.documentElement.scrollHeight - innerHeight
            };
        }""",
        kind,
    )


def checks_for(sample):
    failures = []

    def check(name, ok, detail):
        if not ok:
            failures.append(f"{name}: {detail}")

    check("refresh is gone", sample["refresh"] == 0, sample["refresh"])
    check("no missing-route banner", not sample.get("noise"), sample.get("noise"))
    view = sample.get("view") or {}
    card = sample.get("card") or {}
    pie_card = sample.get("pieCard") or {}
    if sample["kind"].startswith("month") or sample["kind"].startswith("week") or sample["kind"].startswith("day"):
        if view and card and pie_card:
            panel_bottom = max(card["bottom"], pie_card["bottom"])
            check("panels reach the view bottom", view["bottom"] - panel_bottom < 24, round(view["bottom"] - panel_bottom, 1))
            check("view reaches viewport", sample["viewport"]["height"] - view["bottom"] < 36, round(sample["viewport"]["height"] - view["bottom"], 1))
        pie = sample.get("pie")
        legend = sample.get("legend")
        access = sample.get("projectAccess") or {}
        pane = access.get("paneWidth") or sample.get("paneWidth") or sample["viewport"]["width"]
        stacked = bool(card and pie_card and card.get("width") and pie_card.get("top", 0) > card.get("bottom", 0) - 8)
        side_by_side = bool(card and pie_card and card.get("width") and pie_card.get("left", 0) >= card.get("right", 0) - 2)
        if stacked:
            check("narrow stack puts the calendar first", card["top"] < pie_card["top"], round(pie_card["top"] - card["top"], 1))
        if side_by_side and sample["viewport"]["width"] >= 1000 and pane >= 860:
            check("calendar is left of the project panel", card["right"] <= pie_card["left"] + 2, round(pie_card["left"] - card["right"], 1))
            check("calendar is wider than the project panel", card["width"] > pie_card["width"], round(card["width"] - pie_card["width"], 1))
            check("project panel stays usable", pie_card["width"] >= 320, round(pie_card["width"], 1))
        if pie and pie.get("width") and side_by_side and pane >= 860:
            check("desktop chart is 240px", abs(pie["width"] - 240) <= 2, round(pie["width"], 1))
        if access.get("svgWidth") and stacked:
            check("narrow chart stays 200px", abs(access["svgWidth"] - 200) <= 2, access.get("svgWidth"))
        pie_text = sample.get("pieText") or ""
        rule = sample.get("rule") or {}
        if pie_card and pie_card.get("height"):
            check("chart paragraph is gone", "in each project on this chart" not in pie_text, pie_text[:180])
            check("divider is visible", rule.get("width", 0) > 40 and rule.get("height", 0) >= 1 and rule.get("top", 0) >= pie_card.get("top", 0) - 1 and rule.get("bottom", 0) <= pie_card.get("bottom", 0) + 1, rule)
        if pie_card and pie_card.get("height"):
            check("project list has layout height", access.get("legendContent", 0) >= 24, access.get("legendContent"))
            check("project list can be scrolled into view", access.get("legendVisible") is True, access)
            check("last project row can be scrolled fully into view", access.get("lastRowFullyVisible") is True and "Epsilon" in (access.get("lastRowText") or ""), access.get("lastRowText"))
            check("chart is fully inside the project card", access.get("svgFullyVisible") is True, access)
            if stacked:
                check("stacked project card can reach its list", access.get("canScroll") or access.get("legendClient", 0) >= 24, access.get("overflowY"))
        segs = sample.get("segs") or {}
        if segs.get("width"):
            check("day week month control is wide enough to read", segs["width"] >= 240, round(segs["width"], 1))
            check("day week month labels are 14px", sample.get("segFont") == "14px", sample.get("segFont"))
            check("day week month control does not overflow", (sample.get("segsOverflow") or 0) <= 1, sample.get("segsOverflow"))
            check("day week month labels are not clipped", not sample.get("segButtonOverflow"), sample.get("segButtonOverflow"))
            check("selector labels stay Day Week Month", sample.get("segLabels") == ["Day", "Week", "Month"], sample.get("segLabels"))
        if sample.get("workspaceOverflow") is not None:
            check("reports workspace has no horizontal overflow", sample["workspaceOverflow"] <= 1, sample["workspaceOverflow"])
        if pie and legend and legend.get("top") is not None and pie.get("bottom") is not None and access.get("legendContent", 0) > 0:
            check("project list sits under the chart", legend["top"] >= pie["bottom"] - 8, round(legend["top"] - pie["bottom"], 1))
        if legend and pie_card.get("bottom") and legend.get("height", 0) > 0 and not access.get("canScroll"):
            check("legend stays in the pie card", legend["bottom"] <= pie_card["bottom"] + 2, round(legend["bottom"] - pie_card["bottom"], 1))
    if sample["kind"].startswith("month") and sample.get("month") and card:
        check("month has no day activity column", not sample.get("side"), sample.get("side"))
        check("month has no hour zoom", sample.get("zoomCount") == 0, sample.get("zoomCount"))
        check("month grid fills the card", card["bottom"] - sample["month"]["bottom"] < 22, round(card["bottom"] - sample["month"]["bottom"], 1))
    if sample["kind"].startswith("day") and sample.get("shell") and sample.get("zoom") and sample.get("side") and sample.get("grid"):
        shell, zoom, side, grid = sample["shell"], sample["zoom"], sample["side"], sample["grid"]
        check("day zoom is inside the time grid", sample.get("zoomInsideTimegrid") is True, sample.get("zoomInsideTimegrid"))
        check("day zoom sits at the bottom of the time grid", zoom["bottom"] <= grid["bottom"] + 2 and zoom["top"] >= grid["top"] - 2, zoom)
        check("day shell fills the card", card and card["bottom"] - shell["bottom"] < 22, card and round(card["bottom"] - shell["bottom"], 1))
        wide = sample["viewport"]["width"] >= 1200
        columns = side["right"] <= grid["left"] + 2 and side["top"] < grid["bottom"] and grid["top"] < side["bottom"]
        if columns:
            check("day zoom is in the timeline column, not under the activity list", zoom["left"] >= side["right"] - 2 and zoom["left"] >= grid["left"] - 2, round(zoom["left"] - side["right"], 1))
            check("day activity list is wider than the hour timeline", side["width"] > grid["width"] + 40, round(side["width"] - grid["width"], 1))
            check("hour timeline stays wide enough to use", grid["width"] >= 240, round(grid["width"], 1))
            check("day list items use the wider column", (sample.get("listItem") or {}).get("width", 0) >= side["width"] - 2, sample.get("listItem"))
        if wide and columns:
            check("activity list is left of the timeline", side["right"] <= grid["left"] + 2, round(grid["left"] - side["right"], 1))
            check("timeline reaches the shell bottom", shell["bottom"] - grid["bottom"] < 8, round(shell["bottom"] - grid["bottom"], 1))
            if shell["width"] >= 900:
                check("desktop day list is well above the old 280 cap", side["width"] >= 480, round(side["width"], 1))
        elif not columns:
            list_visible = side["height"] > 24 and side["width"] > 40
            check("activity list remains visible", list_visible, side)
            check("zoom remains visible", zoom["width"] > 20 and zoom["height"] > 16, zoom)
    if sample["kind"].startswith("week"):
        check("week has no day activity column", not sample.get("side"), sample.get("side"))
        check("week zoom is inside the time grid", sample.get("zoomInsideTimegrid") is True, sample.get("zoomInsideTimegrid"))
        week_zoom = sample.get("zoom") or {}
        check("week zoom is visible", week_zoom.get("width", 0) > 20 and week_zoom.get("height", 0) > 16, week_zoom)
        check("week shows seven days", sample.get("colHeads") == 7, sample.get("colHeads"))
        blocks = sample.get("blocks") or []
        overlap = 0
        for i, left in enumerate(blocks):
            for right in blocks[i + 1:]:
                ix = min(left["right"], right["right"]) - max(left["left"], right["left"])
                iy = min(left["bottom"], right["bottom"]) - max(left["top"], right["top"])
                if ix > 2 and iy > 2:
                    overlap += 1
        check("week blocks do not paint over each other", overlap == 0, overlap)
        fonts = {block["font"] for block in blocks}
        check("week block text is 12px", not fonts or fonts == {"12px"}, sorted(fonts))
    if sample["kind"].startswith("stats"):
        check("stats uses the workspace width", sample["statsWidth"] and sample["statsWidth"] > sample["viewport"]["width"] * 0.7, sample["statsWidth"])
        headings = sample.get("headings") or []
        check("project breakdown comes before the cost note", headings[:2] == ["Projects", "Costs"], headings)
        if sample["viewport"]["width"] <= 980:
            labels = sample.get("statLabels") or []
            check("stats cells keep field names", len(labels) > 0 and all(labels), labels)
    if sample["kind"].startswith("history"):
        check("history has one table without a source column", "Source" not in sample.get("headers", []), sample.get("headers"))
        text = sample.get("historyText") or ""
        check("missing description stays unknown", "Unknown" in text, "no Unknown text")
        check("closed timer status is recorded", "Recorded" in text, "no Recorded text")
        check("history heading is recent activity", "Recent activity" in text, text[:180])
        check("history cap paragraph is gone", "no offset or cursor" not in text, "cap text still shown")
    if sample["kind"] == "day-zoom":
        scroll = sample.get("scroll") or {}
        check("zoomed timeline can scroll", scroll.get("scroll", 0) > scroll.get("client", 0) + 20, scroll)
    return failures


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 8766), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shots = [
        ("month-1440x1100", "view=month", 1440, 1100),
        ("month-1440x640", "view=month", 1440, 640),
        ("month-390x844", "view=month", 390, 844),
        ("month-768x700", "view=month", 768, 700),
        ("month-1024x800", "view=month", 1024, 800),
        ("week-1440x900", "view=week", 1440, 900),
        ("week-1024x800", "view=week", 1024, 800),
        ("day-1440x1000", "view=day&hour=36", 1440, 1000),
        ("day-zoom-1440x900", "view=day&hour=64", 1440, 900),
        ("day-390x844", "view=day&hour=36", 390, 844),
        ("day-768x900", "view=day&hour=36", 768, 900),
        ("day-1024x800", "view=day&hour=36", 1024, 800),
        ("month-pane720-1200x800", "view=month", 1200, 800, 720),
        ("day-pane720-1200x800", "view=day&hour=36", 1200, 800, 720),
        ("stats-1440x900", "view=month&tab=stats", 1440, 900),
        ("stats-390x844", "view=month&tab=stats", 390, 844),
        ("history-1440x800", "view=month&tab=runs", 1440, 800),
        ("history-390x844", "view=month&tab=runs", 390, 844),
    ]
    log = []
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        for shot in shots:
            name, query, width, height = shot[:4]
            pane = shot[4] if len(shot) > 4 else None
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(f"{URL}?{query}", wait_until="networkidle")
            page.wait_for_selector("[data-reports-ready='1']")
            if pane:
                page.evaluate(
                    """(pane) => {
                        const stage = document.querySelector('#stage');
                        const bar = document.createElement('aside');
                        bar.id = 'synthetic-sidebar';
                        bar.textContent = 'Synthetic studio sidebar';
                        const side = window.innerWidth - pane;
                        Object.assign(bar.style, {
                            position: 'fixed', left: '0', top: '0', bottom: '0', width: side + 'px',
                            boxSizing: 'border-box', background: '#070b24', color: '#fde68a',
                            font: '12px/1.3 sans-serif', padding: '12px'
                        });
                        document.body.prepend(bar);
                        Object.assign(stage.style, {
                            marginLeft: side + 'px', width: pane + 'px', height: '100vh', boxSizing: 'border-box'
                        });
                    }""",
                    pane,
                )
                page.wait_for_timeout(50)
            sample = measure(page, name.split("-")[0] if not name.startswith("day-zoom") else "day-zoom")
            sample["kind"] = "day-zoom" if name.startswith("day-zoom") else name.split("-")[0]
            sample["name"] = name
            page.screenshot(path=str(OUT / f"synthetic-{name}.png"), animations="disabled")
            if name in {"month-390x844", "month-768x700", "day-390x844", "day-768x900", "month-pane720-1200x800"}:
                page.evaluate(
                    """() => {
                        const scroller = document.querySelector('.reports-pie-card');
                        const last = scroller && scroller.querySelector('.reports-pie li:last-child');
                        if (!scroller || !last) return;
                        scroller.scrollTop = 0;
                        let box = last.getBoundingClientRect();
                        let card = scroller.getBoundingClientRect();
                        if (box.bottom > card.bottom) scroller.scrollTop += box.bottom - card.bottom;
                        box = last.getBoundingClientRect();
                        card = scroller.getBoundingClientRect();
                        if (box.top < card.top) scroller.scrollTop -= card.top - box.top;
                    }"""
                )
                page.screenshot(path=str(OUT / f"synthetic-{name}-last-row.png"), animations="disabled")
                page.evaluate("() => { const node = document.querySelector('.reports-pie-card'); if (node) node.scrollTop = 0; }")
            found = checks_for(sample)
            failures.extend(f"{name}: {item}" for item in found)
            log.append({"name": name, "failures": found, "sample": sample})
            page.close()
        # Zoom control actually changes the hour scale.
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{URL}?view=day&hour=36", wait_until="networkidle")
        page.wait_for_selector(".reports-hour-zoom")
        before = page.locator(".reports-hours").bounding_box()["height"]
        page.locator("[data-hour-zoom='1']").click()
        page.wait_for_function("() => document.querySelector('.reports-day-shell')?.dataset.hourPx === '48'")
        after = page.locator(".reports-hours").bounding_box()["height"]
        page.screenshot(path=str(OUT / "synthetic-day-zoom-click-1440x900.png"), animations="disabled")
        if not after > before + 100:
            failures.append(f"zoom click did not grow the hour scale: {before} -> {after}")
        log.append({"name": "day-zoom-click", "before": before, "after": after})
        page.close()
        # Week zoom lives in the week time grid, then a day opened from that week keeps its zoom there too.
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{URL}?view=week", wait_until="networkidle")
        page.wait_for_selector(".reports-timegrid > .reports-hour-zoom")
        week = page.evaluate(
            """() => {
                const zoom = document.querySelector('.reports-hour-zoom');
                const grid = document.querySelector('.reports-timegrid');
                const gz = zoom.getBoundingClientRect();
                const gg = grid.getBoundingClientRect();
                return {
                    heads: document.querySelectorAll('.reports-col-head').length,
                    blocks: document.querySelectorAll('.reports-block').length,
                    hourPx: grid.dataset.hourPx,
                    hours: document.querySelector('.reports-hours').getBoundingClientRect().height,
                    zoomInside: zoom.parentElement === grid,
                    zoomInGrid: gz.left >= gg.left - 1 && gz.right <= gg.right + 1 && gz.bottom <= gg.bottom + 1,
                    scroll: {client: grid.querySelector('.reports-timegrid-scroll').clientHeight, scroll: grid.querySelector('.reports-timegrid-scroll').scrollHeight}
                };
            }"""
        )
        page.screenshot(path=str(OUT / "synthetic-week-before-zoom-1440x900.png"), animations="disabled")
        page.locator(".reports-timegrid [data-hour-zoom='1']").click()
        page.wait_for_function("() => document.querySelector('.reports-timegrid')?.dataset.hourPx === '36'")
        week_after = page.evaluate(
            """() => {
                const scroll = document.querySelector('.reports-timegrid-scroll');
                return {
                    heads: document.querySelectorAll('.reports-col-head').length,
                    blocks: document.querySelectorAll('.reports-block').length,
                    hourPx: document.querySelector('.reports-timegrid').dataset.hourPx,
                    hours: document.querySelector('.reports-hours').getBoundingClientRect().height,
                    scroll: {client: scroll.clientHeight, scroll: scroll.scrollHeight},
                    overflowX: document.querySelector('.reports-workspace').scrollWidth - document.querySelector('.reports-workspace').clientWidth
                };
            }"""
        )
        page.screenshot(path=str(OUT / "synthetic-week-zoomed-1440x900.png"), animations="disabled")
        page.locator(".reports-col-head").first.click()
        page.wait_for_selector(".reports-day-shell .reports-timegrid > .reports-hour-zoom")
        opened = page.evaluate(
            """() => {
                const zoom = document.querySelector('.reports-hour-zoom');
                const grid = document.querySelector('.reports-day-shell .reports-timegrid');
                const side = document.querySelector('.reports-day-side');
                const gz = zoom.getBoundingClientRect();
                const gg = grid.getBoundingClientRect();
                const gs = side.getBoundingClientRect();
                return {
                    zoomInside: zoom.parentElement === grid && !side.contains(zoom),
                    zoomLeft: gz.left, gridLeft: gg.left, sideRight: gs.right,
                    sideW: gs.width, gridW: gg.width,
                    hourPx: document.querySelector('.reports-day-shell').dataset.hourPx,
                    listText: side.innerText
                };
            }"""
        )
        page.screenshot(path=str(OUT / "synthetic-week-opened-day-1440x900.png"), animations="disabled")
        day_before = page.locator(".reports-day-shell .reports-hours").bounding_box()["height"]
        page.locator(".reports-day-shell [data-hour-zoom='1']").click()
        page.wait_for_function("() => document.querySelector('.reports-day-shell')?.dataset.hourPx === '48'")
        day_after = page.locator(".reports-day-shell .reports-hours").bounding_box()["height"]
        page.locator("[data-cal-view='week']").click()
        page.wait_for_selector(".reports-timegrid > .reports-hour-zoom")
        back = page.evaluate(
            """() => ({
                heads: document.querySelectorAll('.reports-col-head').length,
                side: document.querySelectorAll('.reports-day-side').length,
                zoomInside: document.querySelector('.reports-hour-zoom').parentElement.classList.contains('reports-timegrid'),
                hourPx: document.querySelector('.reports-timegrid').dataset.hourPx
            })"""
        )
        probe_ok = (
            week["heads"] == 7 and week["blocks"] > 0 and week["hourPx"] == "28" and week["zoomInside"] and week["zoomInGrid"]
            and week_after["heads"] == 7 and week_after["blocks"] == week["blocks"] and week_after["hours"] > week["hours"] + 100
            and week_after["scroll"]["scroll"] > week_after["scroll"]["client"] and week_after["overflowX"] <= 1
            and opened["zoomInside"] and opened["zoomLeft"] >= opened["sideRight"] - 2 and opened["sideW"] > opened["gridW"] + 40
            and day_after > day_before + 100
            and back["heads"] == 7 and back["side"] == 0 and back["zoomInside"] and back["hourPx"] == "36"
        )
        if not probe_ok:
            failures.append(f"week-to-day zoom probe failed: {week} {week_after} {opened} {day_before}->{day_after} {back}")
        log.append({"name": "week-to-day-zoom", "week": week, "weekAfter": week_after, "openedDay": opened, "dayHours": [day_before, day_after], "back": back})
        page.close()
        browser.close()
    server.shutdown()
    (OUT / "geometry.json").write_text(json.dumps({"failures": failures, "log": log}, indent=2))
    print(json.dumps({"shots": len(shots) + 1, "failures": failures}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
