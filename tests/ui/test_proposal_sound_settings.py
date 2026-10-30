"""Exercise the real General form without modifying the user's saved preferences."""

from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_proposal_sound_toggle_save_preview_and_failure(page, width):
    saved = []
    state = {"incoming_proposal_sound_enabled": True, "notification_sound": "bell"}
    fail_save = False

    def api(route):
        nonlocal state
        request = route.request
        path = urlparse(request.url).path
        payload = {}
        if path == "/api/general":
            if request.method == "POST":
                saved.append(request.post_data_json)
                if fail_save:
                    route.fulfill(status=503, json={"detail": "Settings service unavailable"})
                    return
                state = dict(request.post_data_json)
                payload = {"success": True}
            else:
                payload = state
        elif path == "/api/tts/providers":
            payload = [{"id": "kokoro", "name": "Kokoro", "settings_key": "kokoro_voice",
                        "default_voice": "af_heart", "voices": [{"id": "af_heart", "name": "Heart"}]}]
        elif path == "/api/skins":
            payload = {"skins": [], "selected_skin": "oracle"}
        route.fulfill(status=200, json=payload)

    page.route("**/api/**", api)
    page.add_init_script("window.__sounds = []; window.Audio = class { constructor(url) { window.__sounds.push(url); } play() { return Promise.resolve(); } };")
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://127.0.0.1:8765/settings#general", wait_until="domcontentloaded")
    toggle = page.get_by_label("Sound for new proposals", exact=True)
    expect(toggle).to_be_checked()
    select = page.locator("#notification_sound")
    select.scroll_into_view_if_needed()
    folder = Path("/tmp/decisions-proposal-sound-settings")
    folder.mkdir(exist_ok=True)
    page.screenshot(path=str(folder / f"enabled-{width}.png"))
    toggle.locator("..").click()
    expect(toggle).not_to_be_checked()
    select.select_option("chime")
    page.locator("#notification_sound_preview").click()
    assert page.evaluate("window.__sounds") == ["/assets/sounds/notifications/chime.mp3"]
    page.locator("#btn_save").click()
    page.wait_for_function("() => document.body.innerText.includes('General & audio settings saved')")
    assert saved[-1]["incoming_proposal_sound_enabled"] is False
    assert saved[-1]["notification_sound"] == "chime"
    page.reload(wait_until="domcontentloaded")
    expect(toggle).not_to_be_checked()
    expect(select).to_have_value("chime")
    select.scroll_into_view_if_needed()
    page.screenshot(path=str(folder / f"disabled-{width}.png"))
    fail_save = True
    toggle.focus()
    page.keyboard.press("Space")
    expect(toggle.locator("+ .settings-toggle__switch")).to_have_css(
        "box-shadow", "rgba(249, 115, 22, 0.45) 0px 0px 0px 2px",
    )
    page.locator("#btn_save").click()
    page.wait_for_function("() => document.body.innerText.includes('Failed to save general settings')")
    expect(toggle).to_be_checked()
    assert state["incoming_proposal_sound_enabled"] is False
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    select.scroll_into_view_if_needed()
    page.screenshot(path=str(folder / f"error-{width}.png"))
