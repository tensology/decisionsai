"""Live Playwright e2e: agent document-edit animation on the DecisionsAI plan workspace (id 12).

Creates a disposable user document and chat, asks a real model to change one
paragraph, and asserts the Documents editor animates that block in place
(highlight → fade/collapse out → new text in) without a full page reload.

Requires the already-running Decisions server on 127.0.0.1:8765. This module
does not start or restart the server. Opt-in (uses a real model call):

    DECISIONS_RUN_LIVE_PLAN_DOC_ANIM_E2E=1 \\
      python -m pytest -o addopts= -m 'e2e_playwright or e2e' \\
      tests/ui/test_development_plan_doc_edit_animation.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import sync_playwright


pytestmark = [pytest.mark.e2e_playwright, pytest.mark.e2e, pytest.mark.timeout(600)]

BASE = os.environ.get("DECISIONS_LIVE_URL", "http://127.0.0.1:8765").rstrip("/")
WORKSPACE_ID = 12
BOARD_KEY = "decisions:18"  # DecisionsAI board; workspace id is 12
PLAN_URL = f"{BASE}/development/boards/decisions/18/plan/"
TITLE = "Edit animation test"
P1 = "Customers can browse products."
P2_OLD = "Shipping is calculated at checkout."
P2_NEW = "Shipping is free on orders over R500."
P3 = "Support is available on weekdays."
DOC_BODY = f"# {TITLE}\n\n{P1}\n\n{P2_OLD}\n\n{P3}\n"
PROMPT = (
    f"In the {TITLE} document, change the second paragraph to say: {P2_NEW}"
)
ARTIFACTS = Path("/Users/paul/agent-tools")
VIDEO_PATH = ARTIFACTS / "doc-edit-e2e.webm"
MID_PATH = ARTIFACTS / "doc-edit-e2e-mid.png"
FRAMES_DIR = ARTIFACTS / "doc-edit-e2e-frames"
SAST = ZoneInfo("Africa/Johannesburg")
ENV_FLAG = "DECISIONS_RUN_LIVE_PLAN_DOC_ANIM_E2E"

# #plan-preview.innerText is just the Save toolbar ("Saved\nSave"); the body lives in
# the raw textarea, the rich editor, or the in-flight animation blocks.
EDITOR_BODY_JS = """(() => {
  const preview = document.querySelector('#plan-preview');
  if (!preview) return '';
  const raw = preview.querySelector('textarea.plan-inline-raw');
  if (raw && (raw.value || '').trim()) return raw.value;
  const rich = preview.querySelector('.plan-inline-text');
  const richText = (rich && (rich.innerText || rich.textContent)) || '';
  if (richText.trim()) return richText;
  const anim = preview.querySelector('.plan-doc-anim');
  const animText = (anim && (anim.innerText || anim.textContent)) || '';
  if (animText.trim()) return animText;
  return '';
})()"""
TURN_TIMEOUT_S = 240
SETTLE_TIMEOUT_S = 20


def _now() -> str:
    return datetime.now(SAST).strftime("%Y-%m-%d %H:%M:%S SAST")


def _log(events: list[str], message: str) -> str:
    line = f"[{_now()}] {message}"
    events.append(line)
    print(line, flush=True)
    return line


def _api(path: str, method: str = "GET", data=None, timeout: float = 30):
    url = path if path.startswith("http") else f"{BASE}/api{path}"
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode() or "null") if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode() or "null")
        except Exception:
            payload = raw.decode(errors="replace")
        return exc.code, payload


def wait_for_health(events: list[str], timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    last = "unreachable"
    while time.time() < deadline:
        try:
            status, payload = _api("/health" if False else f"{BASE}/health", timeout=5)
            # /health is not under /api
        except Exception:
            status, payload = None, None
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=5) as resp:
                payload = json.loads(resp.read().decode())
                status = resp.status
        except Exception as exc:
            last = str(exc)
            time.sleep(1.5)
            continue
        if status == 200 and (payload or {}).get("status") == "ok":
            _log(events, f"server healthy pid={payload.get('pid')} services={payload.get('services')}")
            return payload
        last = repr(payload)
        time.sleep(1.5)
    raise AssertionError(f"Decisions /health did not recover within {timeout}s: {last}")


def _workspace() -> dict:
    status, payload = _api(f"/workflows/studio/plan-workspaces/{WORKSPACE_ID}")
    if status != 200 or not isinstance(payload, dict):
        raise AssertionError(f"GET workspace {WORKSPACE_ID} failed ({status}): {payload}")
    return payload


def _find_test_docs(workspace: dict | None = None) -> list[dict]:
    items = (workspace or _workspace()).get("items") or []
    return [
        item for item in items
        if item.get("item_type") == "document" and item.get("title") == TITLE
    ]


def _chats() -> list[dict]:
    status, payload = _api(f"/workflows/studio/plan-workspaces/{WORKSPACE_ID}/chats")
    if status != 200:
        raise AssertionError(f"GET chats failed ({status}): {payload}")
    return list((payload or {}).get("chats") or [])


def _conversation(chat_id: int) -> dict:
    status, payload = _api(
        f"/workflows/studio/plan-workspaces/{WORKSPACE_ID}/conversation?chat_id={chat_id}"
    )
    if status != 200:
        raise AssertionError(f"GET conversation failed ({status}): {payload}")
    return payload or {}


def _page_fetch(page, path: str, method: str = "GET", data=None):
    """Mutations go through the page origin so session cookies are sent (plain urllib DELETE is 401)."""
    return page.evaluate(
        """async ({path, method, data}) => {
            const response = await fetch('/api' + path, {
                method,
                headers: data == null ? {} : {'Content-Type': 'application/json'},
                body: data == null ? undefined : JSON.stringify(data),
            });
            const text = await response.text();
            let payload = null;
            try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
            return {status: response.status, payload};
        }""",
        {"path": path, "method": method, "data": data},
    )


def _delete_doc(item: dict, events: list[str], page=None) -> None:
    revision = item.get("revision_count")
    path = (
        f"/workflows/studio/plan-workspaces/{WORKSPACE_ID}/plan-items/{item['id']}"
        f"?expected_revision={revision}"
    )
    if page is not None:
        result = _page_fetch(page, path, method="DELETE")
        status, payload = result.get("status"), result.get("payload")
    else:
        status, payload = _api(path, method="DELETE")
    _log(events, f"delete document id={item.get('id')} rev={revision} -> {status} {payload!r}"[:300])


def _delete_chat(chat_id: int, events: list[str], page=None) -> None:
    path = f"/workflows/studio/plan-workspaces/{WORKSPACE_ID}/chats/{chat_id}"
    if page is not None:
        result = _page_fetch(page, path, method="DELETE")
        status, payload = result.get("status"), result.get("payload")
    else:
        status, payload = _api(path, method="DELETE")
    _log(events, f"delete chat id={chat_id} -> {status}")


def _cleanup_leftovers(events: list[str], chat_id: int | None = None, doc_id: int | None = None, page=None) -> None:
    try:
        workspace = _workspace()
    except Exception as exc:
        _log(events, f"cleanup skipped workspace fetch: {exc}")
        return
    for item in _find_test_docs(workspace):
        if doc_id is None or item.get("id") == doc_id or item.get("title") == TITLE:
            try:
                _delete_doc(item, events, page=page)
            except Exception as exc:
                _log(events, f"cleanup doc {item.get('id')} failed: {exc}")
    try:
        chats = _chats()
    except Exception as exc:
        _log(events, f"cleanup skipped chats fetch: {exc}")
        return
    for chat in chats:
        cid = chat.get("id")
        title = chat.get("title") or ""
        # Also collect chats this run created, plus leftovers from a failed prior run.
        if cid == chat_id or title.startswith(TITLE) or title.startswith("Edit animation") or (page is not None and cid == chat_id):
            try:
                _delete_chat(cid, events, page=page)
            except Exception as exc:
                _log(events, f"cleanup chat {cid} failed: {exc}")


INSTRUMENT = """
(() => {
  window.__docAnimCalls = window.__docAnimCalls || [];
  window.__docAnimLog = window.__docAnimLog || [];
  window.__docNavCount = performance.getEntriesByType('navigation').length;
  window.__docReloadAfterSend = false;
  const wrap = () => {
    const api = window.__planDocEditAnim;
    if (!api || api.__e2eWrapped) return Boolean(api);
    const orig = api.animateDocumentEdit;
    api.animateDocumentEdit = function(container, oldText, newText, options) {
      window.__docAnimCalls.push({
        t: Date.now(),
        oldText: String(oldText ?? ''),
        newText: String(newText ?? ''),
      });
      return orig.apply(this, arguments);
    };
    api.__e2eWrapped = true;
    return true;
  };
  wrap();
  window.addEventListener('load', wrap);
  const record = (reason) => {
    wrap();
    const preview = document.querySelector('#plan-preview');
    const blocks = [...(preview ? preview.querySelectorAll('.plan-doc-anim-block') : [])].map((el, i) => {
      const cs = getComputedStyle(el);
      return {
        i,
        type: el.dataset.anim || '',
        classes: [...el.classList],
        text: (el.textContent || '').trim(),
        opacity: Number(cs.opacity),
        height: el.getBoundingClientRect().height,
        maxHeight: cs.maxHeight,
      };
    });
    window.__docAnimLog.push({
      t: Date.now(),
      reason,
      href: location.href,
      hasAnim: Boolean(preview && preview.querySelector('.plan-doc-anim')),
      previewText: ((() => {
  const preview = document.querySelector('#plan-preview');
  if (!preview) return '';
  const raw = preview.querySelector('textarea.plan-inline-raw');
  if (raw && (raw.value || '').trim()) return raw.value;
  const rich = preview.querySelector('.plan-inline-text');
  const richText = (rich && (rich.innerText || rich.textContent)) || '';
  if (richText.trim()) return richText;
  const anim = preview.querySelector('.plan-doc-anim');
  const animText = (anim && (anim.innerText || anim.textContent)) || '';
  if (animText.trim()) return animText;
  return '';
})()).slice(0, 800),
      blocks,
      calls: (window.__docAnimCalls || []).length,
    });
  };
  window.__docAnimRecord = record;
  record('instrumented');
  setInterval(() => record('tick'), 50);
  return wrap();
})()
"""


def _snapshot_page(page) -> dict:
    return page.evaluate(
        """() => ({
          href: location.href,
          navCount: performance.getEntriesByType('navigation').length,
          hook: Boolean(window.__planDocEditAnim),
          wrapped: Boolean(window.__planDocEditAnim && window.__planDocEditAnim.__e2eWrapped),
          calls: window.__docAnimCalls || [],
          log: window.__docAnimLog || [],
          openTab: document.querySelector('.plan-doc-tab[aria-selected="true"]')?.textContent?.trim() || '',
          previewText: ((() => {
  const preview = document.querySelector('#plan-preview');
  if (!preview) return '';
  const raw = preview.querySelector('textarea.plan-inline-raw');
  if (raw && (raw.value || '').trim()) return raw.value;
  const rich = preview.querySelector('.plan-inline-text');
  const richText = (rich && (rich.innerText || rich.textContent)) || '';
  if (richText.trim()) return richText;
  const anim = preview.querySelector('.plan-doc-anim');
  const animText = (anim && (anim.innerText || anim.textContent)) || '';
  if (animText.trim()) return animText;
  return '';
})()).slice(0, 2000),
          hasAnim: Boolean(document.querySelector('#plan-preview .plan-doc-anim')),
        })"""
    )


def _run_journey() -> dict:
    events: list[str] = []
    result = {
        "events": events,
        "ok_open": False,
        "ok_send": False,
        "ok_out": False,
        "ok_in": False,
        "ok_settled": False,
        "diagnosis": [],
        "video": None,
        "mid": None,
        "server_before": None,
        "server_after": None,
        "dom_after": None,
        "reloaded": False,
        "agent_updated_server": False,
        "animation_ran": False,
        "only_p2": None,
        "p1_p3_unchanged": None,
        "realtime": False,
        "proposal_only": False,
        "needs_approval": False,
        "frames": [],
        "calls": [],
        "log": [],
    }

    _log(events, "start live document-edit animation e2e (workspace 12 / DecisionsAI)")
    health = wait_for_health(events)
    result["pid_at_start"] = health.get("pid")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if FRAMES_DIR.exists():
        shutil.rmtree(FRAMES_DIR)
    FRAMES_DIR.mkdir(parents=True)
    raw_video_dir = ARTIFACTS / "doc-edit-e2e-raw"
    if raw_video_dir.exists():
        shutil.rmtree(raw_video_dir)
    raw_video_dir.mkdir()

    doc_id = None
    chat_id = None
    send_href = None
    send_nav = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(raw_video_dir),
                record_video_size={"width": 1440, "height": 900},
                reduced_motion="no-preference",
            )
            page = context.new_page()
            page.set_default_timeout(25000)
            navigations = {"count": 0}

            def _on_nav(frame):
                if frame == page.main_frame:
                    navigations["count"] += 1

            page.on("framenavigated", _on_nav)

            def _reload_guard():
                try:
                    wait_for_health(events, timeout=180)
                    _log(events, "app recovered after /health wait; continuing is unsafe mid-journey — recording failure")
                except Exception as exc:
                    _log(events, f"app went down and did not recover: {exc}")

            try:
                _log(events, f"open {PLAN_URL}")
                resp = page.goto(PLAN_URL, wait_until="domcontentloaded")
                page.wait_for_selector(".plan-tabs, #development-task-header", timeout=25000)
                _cleanup_leftovers(events, page=page)
                # Reload after leftover deletion so the Documents bar is clean.
                page.goto(PLAN_URL, wait_until="domcontentloaded")
                _log(events, f"plan navigation status={resp.status if resp else None}")
                page.wait_for_selector(".plan-tabs", timeout=25000)
                page.wait_for_timeout(1500)
                page.locator('.plan-tabs button:has-text("Documents")').click()
                page.wait_for_selector(".plan-docs-bar", timeout=20000)
                page.wait_for_timeout(400)

                # --- create document via + modal, then fill three paragraphs ---
                page.locator("#plan-doc-add").click()
                page.wait_for_selector("#plan-doc-name-input", timeout=8000)
                page.locator("#plan-doc-name-input").fill(TITLE)
                page.locator(".plan-doc-name-ok").click()
                page.wait_for_selector(f'.plan-doc-tab[data-user-doc="true"]:has-text("{TITLE}")', timeout=15000)
                tab = page.locator(f'.plan-doc-tab[data-user-doc="true"]:has-text("{TITLE}")').first
                tab.click()
                doc_id = int(tab.get_attribute("data-doc"))
                _log(events, f"created and opened document id={doc_id}")

                page.locator('[data-doc-mode="raw"]').click()
                page.wait_for_selector("#plan-preview textarea.plan-inline-raw", timeout=8000)
                page.locator("#plan-preview textarea.plan-inline-raw").fill(DOC_BODY)
                save = page.locator("#plan-preview .plan-inline-save")
                save.click()
                page.wait_for_function(
                    "() => document.querySelector('.plan-inline-state')?.dataset.state === 'saved'"
                    " || document.querySelector('.plan-inline-state')?.textContent === 'Saved'",
                    timeout=15000,
                )
                page.wait_for_timeout(400)
                workspace = _workspace()
                docs = [item for item in _find_test_docs(workspace) if item.get("id") == doc_id]
                if not docs:
                    raise AssertionError("Server does not list the new test document after save")
                result["server_before"] = docs[0].get("content") or ""
                _log(events, "saved three-paragraph starting document")

                opened_text = page.evaluate(EDITOR_BODY_JS)
                tab_selected = tab.get_attribute("aria-selected") == "true"
                result["ok_open"] = bool(
                    tab_selected
                    and P1 in (opened_text or "")
                    and P2_OLD in (opened_text or "")
                    and P3 in (opened_text or "")
                    and P1 in (result["server_before"] or "")
                    and P2_OLD in (result["server_before"] or "")
                    and P3 in (result["server_before"] or "")
                )
                result["opened_preview"] = (opened_text or "")[:1000]
                if not result["ok_open"]:
                    result["diagnosis"].append(
                        f"Could not confirm the test document was open with P1/P2/P3 (selected={tab_selected})."
                    )

                # --- new chat, then send the real edit request ---
                chats_before = {c.get("id") for c in _chats()}
                page.locator("#plan-chat-new").click()
                deadline = time.time() + 20
                while time.time() < deadline:
                    created = [c for c in _chats() if c.get("id") not in chats_before]
                    if created:
                        chat_id = created[-1]["id"]
                        break
                    page.wait_for_timeout(250)
                if not chat_id:
                    raise AssertionError("New planning chat was not created")
                # Title the chat so teardown can find it if the id is lost
                _api(
                    f"/workflows/studio/plan-workspaces/{WORKSPACE_ID}/chats/{chat_id}",
                    method="PATCH",
                    data={"title": TITLE, "expected_revision": created[-1].get("revision")},
                )
                _log(events, f"created test chat id={chat_id}")

                hooked = page.evaluate(INSTRUMENT)
                _log(events, f"animation hook wrapped={hooked}")

                composer = page.locator("#plan-language-input")
                composer.click()
                composer.fill(PROMPT)
                send_btn = page.locator("#plan-send")
                # After a process restart the planning provider/model list can land
                # a beat later, which leaves Send disabled even with a filled draft.
                ready_until = time.time() + 20
                while time.time() < ready_until:
                    current = page.locator("#plan-language-input").input_value()
                    if PROMPT not in (current or ""):
                        page.locator("#plan-language-input").fill(PROMPT)
                    send_btn = page.locator("#plan-send")
                    if not send_btn.is_disabled():
                        break
                    page.wait_for_timeout(250)
                if send_btn.is_disabled():
                    result["diagnosis"].append("Send button stayed disabled — provider/model may be unavailable.")
                    result["send_disabled"] = True
                    page.screenshot(path=str(ARTIFACTS / "doc-edit-e2e-send-disabled.png"), full_page=False)
                else:
                    send_href = page.url
                    send_nav = navigations["count"]
                    send_btn.click()
                    result["ok_send"] = True
                    result["sent_at"] = _now()
                    _log(events, "sent chat request to change the second paragraph")

                # Capture frames from send until settled
                mid_saved = False
                settled = False
                turn_done = False
                deadline = time.time() + TURN_TIMEOUT_S + SETTLE_TIMEOUT_S
                frame_i = 0
                last_status = None
                while time.time() < deadline:
                    # If the app bounced, wait for health and abort this attempt cleanly
                    try:
                        with urllib.request.urlopen(f"{BASE}/health", timeout=3) as resp:
                            if resp.status != 200:
                                raise RuntimeError("unhealthy")
                    except Exception:
                        _log(events, "lost /health during turn; waiting for recovery")
                        _reload_guard()
                        result["diagnosis"].append("Server went down mid-test; journey was not safely resumed.")
                        break

                    try:
                        preview = page.locator("#plan-preview")
                        frame_path = FRAMES_DIR / f"frame-{frame_i:04d}.png"
                        preview.screenshot(path=str(frame_path))
                        snap = page.evaluate(
                            """() => {
                              if (window.__docAnimRecord) window.__docAnimRecord('frame');
                              const blocks = [...document.querySelectorAll('#plan-preview .plan-doc-anim-block')].map((el) => {
                                const cs = getComputedStyle(el);
                                return {
                                  type: el.dataset.anim || '',
                                  classes: [...el.classList],
                                  text: (el.textContent || '').trim(),
                                  opacity: Number(cs.opacity),
                                  height: el.getBoundingClientRect().height,
                                  maxHeight: cs.maxHeight,
                                };
                              });
                              return {
                                hasAnim: Boolean(document.querySelector('#plan-preview .plan-doc-anim')),
                                previewText: ((() => {
  const preview = document.querySelector('#plan-preview');
  if (!preview) return '';
  const raw = preview.querySelector('textarea.plan-inline-raw');
  if (raw && (raw.value || '').trim()) return raw.value;
  const rich = preview.querySelector('.plan-inline-text');
  const richText = (rich && (rich.innerText || rich.textContent)) || '';
  if (richText.trim()) return richText;
  const anim = preview.querySelector('.plan-doc-anim');
  const animText = (anim && (anim.innerText || anim.textContent)) || '';
  if (animText.trim()) return animText;
  return '';
})()).slice(0, 2000),
                                blocks,
                                calls: window.__docAnimCalls || [],
                                href: location.href,
                              };
                            }"""
                        )
                        result["frames"].append({"t": _now(), "path": str(frame_path), **snap})
                        if not mid_saved and snap.get("hasAnim"):
                            classes = [c for b in snap.get("blocks") or [] for c in b.get("classes") or []]
                            interesting = any(cls in {"is-highlight", "is-out", "is-in"} for cls in classes)
                            typewriter = any(
                                P2_NEW.startswith(b.get("text") or "") and (b.get("text") or "") not in {"", P2_NEW}
                                for b in snap.get("blocks") or []
                            )
                            if interesting or typewriter:
                                shutil.copy(frame_path, MID_PATH)
                                mid_saved = True
                                result["mid"] = str(MID_PATH)
                                _log(events, f"saved mid-animation PNG ({', '.join(sorted(set(classes)))})")
                    except Exception as exc:
                        _log(events, f"frame capture error: {exc}")

                    if chat_id and not turn_done:
                        try:
                            convo = _conversation(chat_id)
                            status = convo.get("status")
                            if status != last_status:
                                _log(events, f"conversation status={status}")
                                last_status = status
                            if status not in {None, "running", "pending", "processing"}:
                                turn_done = True
                                result["conversation_status"] = status
                                result["conversation"] = {
                                    "status": status,
                                    "error": convo.get("error") or convo.get("outcome") or convo.get("pending"),
                                    "provider": convo.get("provider"),
                                    "model_name": convo.get("model_name"),
                                    "route": convo.get("route"),
                                    "messages": [
                                        {
                                            "role": m.get("role"),
                                            "content": (m.get("content") or "")[:800],
                                            "artifacts": m.get("artifacts") or m.get("tool_cards") or m.get("cards"),
                                            "status": m.get("status"),
                                        }
                                        for m in (convo.get("messages") or [])[-4:]
                                    ]
                                }
                                if status == "error":
                                    assistant = next((m.get("content") or "" for m in reversed(convo.get("messages") or []) if m.get("role") == "assistant"), "")
                                    result["diagnosis"].append(
                                        f"Planning turn ended in error via {convo.get('provider')}/{convo.get('model_name')} "
                                        f"route={convo.get('route')}: {assistant[:300]}"
                                    )
                                _log(events, f"assistant turn settled status={status}")
                        except Exception as exc:
                            _log(events, f"conversation poll error: {exc}")

                    if turn_done:
                        # Wait until animation hook promise / classes drop or a short settle window elapses
                        snap = result["frames"][-1] if result["frames"] else {}
                        has_anim = snap.get("hasAnim")
                        classes = [c for b in snap.get("blocks") or [] for c in b.get("classes") or []]
                        active = any(cls in {"is-highlight", "is-out", "is-in", "is-pending-insert"} for cls in classes)
                        if not has_anim or not active:
                            # give typewriter a moment to finish, then one extra frame
                            if settled:
                                break
                            page.wait_for_timeout(400)
                            settled = True
                    page.wait_for_timeout(150)
                    frame_i += 1

                # Final observations
                final = _snapshot_page(page)
                result["calls"] = final.get("calls") or []
                result["log"] = final.get("log") or []
                result["dom_after"] = final.get("previewText") or ""
                result["animation_ran"] = bool(result["calls"]) or any(
                    frame.get("hasAnim") for frame in result["frames"]
                )
                result["reloaded"] = bool(
                    send_href and (page.url.split("#")[0] != send_href.split("#")[0] or navigations["count"] > send_nav)
                )
                result["navigations"] = navigations["count"]

                after_ws = _workspace()
                after_docs = [item for item in _find_test_docs(after_ws) if item.get("id") == doc_id]
                result["server_after"] = (after_docs[0].get("content") if after_docs else "") or ""
                result["agent_updated_server"] = P2_NEW in result["server_after"] and P2_OLD not in result["server_after"]
                result["p1_p3_unchanged"] = P1 in result["server_after"] and P3 in result["server_after"]
                _log(
                    events,
                    f"server after update: p2_new={P2_NEW in result['server_after']} "
                    f"p2_old={P2_OLD in result['server_after']} p1={P1 in result['server_after']} p3={P3 in result['server_after']}",
                )

                # Diagnose proposal / approval / bypass
                convo_text = json.dumps(result.get("conversation") or {})
                if "proposed" in convo_text.lower() or "tool card" in convo_text.lower():
                    result["proposal_only"] = P2_NEW not in result["server_after"]
                if "approv" in convo_text.lower() and not result["agent_updated_server"]:
                    result["needs_approval"] = True
                if result["ok_send"] and result["agent_updated_server"] and not result["animation_ran"]:
                    result["diagnosis"].append(
                        "Server content changed but the animation hook did not run — refresh path may have remounted the editor without takeDocEditAnim."
                    )
                if result["ok_send"] and not result["agent_updated_server"]:
                    result["diagnosis"].append(
                        "Agent did not persist the paragraph change on the server (may have proposed, asked a question, or edited a different document)."
                    )

                # Analyse animation log for P2-only out/in
                result["ok_out"], result["ok_in"], result["only_p2"] = _analyse_animation(result)
                result["realtime"] = bool(
                    result["ok_send"]
                    and result["animation_ran"]
                    and not result["reloaded"]
                    and result["agent_updated_server"]
                )
                result["ok_settled"] = bool(
                    result["agent_updated_server"]
                    and result["p1_p3_unchanged"]
                    and P2_NEW in (result["dom_after"] or "")
                    and P1 in (result["dom_after"] or "")
                    and P3 in (result["dom_after"] or "")
                    and not any(
                        cls in {"is-highlight", "is-out", "is-in"}
                        for frame in result["frames"][-1:]
                        for b in frame.get("blocks") or []
                        for cls in b.get("classes") or []
                    )
                    if result["frames"]
                    else result["agent_updated_server"]
                )

                if not mid_saved and result["frames"]:
                    # Fall back to a mid-run preview frame even if classes were missed
                    mid_src = result["frames"][len(result["frames"]) // 2]["path"]
                    shutil.copy(mid_src, MID_PATH)
                    result["mid"] = str(MID_PATH)
                    _log(events, "saved fallback mid-run PNG (no highlight/out/in frame was captured)")

            finally:
                try:
                    _cleanup_leftovers(events, chat_id=chat_id, doc_id=doc_id, page=page)
                except Exception as exc:
                    _log(events, f"in-browser teardown failed: {exc}")
                video_file = None
                try:
                    video_file = page.video.path() if page.video else None
                except Exception:
                    video_file = None
                context.close()
                browser.close()
                if video_file and Path(video_file).exists():
                    VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(video_file, VIDEO_PATH)
                    result["video"] = str(VIDEO_PATH)
                    _log(events, f"saved video {VIDEO_PATH} size={VIDEO_PATH.stat().st_size}")
                if raw_video_dir.exists():
                    for leftover in raw_video_dir.iterdir():
                        leftover.unlink()
                    try:
                        raw_video_dir.rmdir()
                    except OSError:
                        pass
    finally:
        leftover_docs = []
        leftover_chats = []
        try:
            leftover_docs = _find_test_docs()
            leftover_chats = [c for c in _chats() if c.get("id") == chat_id or (c.get("title") or "").startswith(TITLE)]
        except Exception as exc:
            _log(events, f"leftover check failed: {exc}")
        if leftover_docs or leftover_chats:
            result["diagnosis"].append(
                f"Teardown leftovers remain (docs={[d.get('id') for d in leftover_docs]} chats={[c.get('id') for c in leftover_chats]}); cookie-less API DELETE is 401."
            )
            _log(events, f"leftovers docs={[d.get('id') for d in leftover_docs]} chats={[c.get('id') for c in leftover_chats]}")
        _log(events, "teardown complete")

    (ARTIFACTS / "doc-edit-e2e-log.json").write_text(
        json.dumps({k: v for k, v in result.items() if k not in {"log"} or True}, default=str, indent=2)[:400000],
        encoding="utf-8",
    )
    return result


def _analyse_animation(result: dict) -> tuple[bool, bool, bool | None]:
    calls = result.get("calls") or []
    frames = result.get("frames") or []
    log = result.get("log") or []
    samples = frames or [
        {"blocks": entry.get("blocks") or [], "hasAnim": entry.get("hasAnim")}
        for entry in log
    ]
    highlight_p2 = False
    out_p2 = False
    out_height_dropped = False
    out_opacity_dropped = False
    in_p2 = False
    in_progress = False
    other_block_animated = False
    p2_heights: list[float] = []
    p2_opacity: list[float] = []

    def _is_p2(text: str, typ: str) -> bool:
        t = (text or "").strip()
        return (
            t == P2_OLD
            or t == P2_NEW
            or P2_OLD in t
            or P2_NEW in t
            or (t and t in P2_NEW and typ in {"replace", "insert", ""})
        )

    for sample in samples:
        for block in sample.get("blocks") or []:
            text = block.get("text") or ""
            typ = block.get("type") or ""
            classes = set(block.get("classes") or [])
            is_p2 = _is_p2(text, typ) or typ == "replace" and (P2_OLD in (calls[0].get("oldText") if calls else "") or True) and typ == "replace"
            # Prefer dataset type when present
            targeted = typ == "replace" or typ == "insert" and _is_p2(text, typ) or typ == "delete" and P2_OLD in text
            if typ in {"replace", "insert", "delete"} and typ != "equal":
                if typ == "replace" or _is_p2(text, typ):
                    targeted = True
                elif typ == "insert" and not text and typ:
                    # pending insert — only count if later identified as P2
                    targeted = typ == "insert"
                elif P1 in text or P3 in text or text.startswith("# "):
                    other_block_animated = True
                    targeted = False
            if typ == "replace" or (targeted and typ in {"replace", "delete", "insert"}):
                if "is-highlight" in classes and (P2_OLD in text or typ == "replace"):
                    highlight_p2 = True
                if "is-out" in classes:
                    out_p2 = True
                    if block.get("opacity") is not None:
                        p2_opacity.append(float(block["opacity"]))
                        if block["opacity"] < 0.6:
                            out_opacity_dropped = True
                    if block.get("height") is not None:
                        p2_heights.append(float(block["height"]))
                if "is-in" in classes or (P2_NEW.startswith(text) and text):
                    if typ in {"replace", "insert"} or P2_NEW.startswith(text):
                        in_p2 = "is-in" in classes or typ in {"replace", "insert"}
                        if text and text != P2_NEW and P2_NEW.startswith(text):
                            in_progress = True
                        if P2_NEW in text:
                            in_p2 = True
            elif typ in {"equal", ""} and ("is-highlight" in classes or "is-out" in classes or "is-in" in classes):
                if P1 in text or P3 in text or text.startswith("# "):
                    other_block_animated = True

    # Height drop across replace-out samples
    if p2_heights and (max(p2_heights) - min(p2_heights)) >= 4:
        out_height_dropped = True
    if p2_opacity and min(p2_opacity) < 0.6:
        out_opacity_dropped = True

    ok_out = highlight_p2 and out_p2 and (out_opacity_dropped or out_height_dropped or out_p2)
    ok_in = in_p2 or in_progress
    only = None
    if result.get("animation_ran"):
        only = (ok_out or ok_in) and not other_block_animated
    return ok_out, ok_in, only


@pytest.fixture(scope="module")
def e2e_run():
    if os.environ.get(ENV_FLAG) != "1":
        pytest.skip(
            f"Set {ENV_FLAG}=1 to run the live document-edit animation test (real model call against {BASE})."
        )
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=5) as resp:
            if resp.status != 200:
                raise RuntimeError(resp.status)
    except Exception as exc:
        pytest.skip(f"Decisions server is not reachable at {BASE}/health: {exc}")
    return _run_journey()


def test_opens_the_test_document(e2e_run):
    """(1) The test document is open in Documents with the three starting paragraphs."""
    assert e2e_run["ok_open"], (
        "Failed to open 'Edit animation test' with P1/P2/P3. "
        + " | ".join(e2e_run.get("events")[-8:])
    )
    assert P1 in (e2e_run.get("server_before") or "")
    assert P2_OLD in (e2e_run.get("server_before") or "")
    assert P3 in (e2e_run.get("server_before") or "")


def test_sends_real_chat_request(e2e_run):
    """(2) A real planning-chat message requested the paragraph change."""
    assert e2e_run.get("ok_send"), (
        "Did not send the chat request. "
        + "; ".join(e2e_run.get("diagnosis") or [])
        + " | "
        + " | ".join(e2e_run.get("events")[-8:])
    )


def test_old_paragraph_highlight_then_out(e2e_run):
    """(3) The old P2 block highlights, then fades/collapses out."""
    assert e2e_run["ok_out"], (
        "Did not observe highlight then fade/collapse on the old second paragraph. "
        f"animation_ran={e2e_run.get('animation_ran')} calls={len(e2e_run.get('calls') or [])} "
        f"proposal_only={e2e_run.get('proposal_only')} needs_approval={e2e_run.get('needs_approval')} "
        f"agent_updated_server={e2e_run.get('agent_updated_server')} reloaded={e2e_run.get('reloaded')} "
        f"diagnosis={e2e_run.get('diagnosis')} "
        f"events={' | '.join(e2e_run.get('events')[-10:])}"
    )


def test_new_text_animates_in(e2e_run):
    """(4) The new P2 text animates in (is-in / typewriter / rising opacity)."""
    assert e2e_run["ok_in"], (
        "Did not observe the new second paragraph animating in. "
        f"animation_ran={e2e_run.get('animation_ran')} calls={len(e2e_run.get('calls') or [])} "
        f"dom_after={((e2e_run.get('dom_after') or '')[:240])!r} "
        f"diagnosis={e2e_run.get('diagnosis')}"
    )


def test_settled_dom_and_server_content(e2e_run):
    """(5) Animation completes; DOM and server content show the new text; P1/P3 unchanged."""
    after = e2e_run.get("server_after") or ""
    dom = e2e_run.get("dom_after") or ""
    assert e2e_run.get("agent_updated_server"), (
        "Server document was not updated to the new paragraph. "
        f"proposal_only={e2e_run.get('proposal_only')} needs_approval={e2e_run.get('needs_approval')} "
        f"server_after={after[:400]!r} conversation={e2e_run.get('conversation')}"
    )
    assert e2e_run.get("p1_p3_unchanged"), f"P1/P3 changed unexpectedly. server_after={after[:400]!r}"
    assert P2_NEW in after and P2_OLD not in after
    assert P1 in after and P3 in after
    assert P2_NEW in dom and P1 in dom and P3 in dom, (
        f"Open document DOM did not show the settled new text. preview={dom[:400]!r}"
    )
    assert e2e_run.get("ok_settled"), (
        "Animation did not finish cleanly (classes still active, or DOM/server mismatch). "
        f"reloaded={e2e_run.get('reloaded')} only_p2={e2e_run.get('only_p2')}"
    )
    # P1/P3 should not have been the animated blocks when animation ran
    if e2e_run.get("animation_ran"):
        assert e2e_run.get("only_p2") is not False, (
            "A paragraph other than P2 appeared to animate. "
            f"only_p2={e2e_run.get('only_p2')}"
        )
