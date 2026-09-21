"""Browser coverage for Fish Audio API key + voice picker.

Needs the local web UI on http://127.0.0.1:8765:

  pytest -m e2e_playwright tests/ui/test_fishaudio_settings_playwright.py --headed -v -s
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_playwright

BASE_URL = os.environ.get("DECISIONS_API_BASE", "http://127.0.0.1:8765")


def test_fishaudio_api_key_field_is_where_the_key_goes(page: Page):
    page.goto(f"{BASE_URL}/settings#thirdparty", wait_until="domcontentloaded")
    page.locator("[data-thirdparty-subtab=api_keys]").click()
    card = page.locator('[data-provider-id="fishaudio"]')
    expect(card).to_be_visible(timeout=15000)
    expect(card).to_contain_text("Fish Audio")
    card.get_by_role("button", name="Edit Fish Audio", exact=True).click()
    expect(page.locator("#fishaudio_key")).to_be_visible()
    placeholder = page.locator("#fishaudio_key").get_attribute("placeholder") or ""
    assert placeholder in {
        "Enter Fish Audio API key",
        "Saved key - paste a new key to replace",
    }
    expect(page.locator("#fishaudio_validate")).to_be_visible()
    expect(page.locator("#fishaudio_save")).to_be_visible()
    page.screenshot(path="artifacts/fishaudio-api-key-field.png", full_page=True)


def test_live_fishaudio_key_unlocks_voice_picker(page: Page):
    key = (os.environ.get("FISH_AUDIO_API_KEY") or os.environ.get("FISH_API_KEY") or "").strip()
    if not key:
        pytest.skip("Set FISH_AUDIO_API_KEY to run live Fish Audio UI save")

    page.goto(f"{BASE_URL}/settings#thirdparty", wait_until="domcontentloaded")
    page.locator("[data-thirdparty-subtab=api_keys]").click()
    page.locator('[data-provider-id="fishaudio"]').get_by_role(
        "button", name="Edit Fish Audio", exact=True
    ).click()
    page.locator("#fishaudio_enabled_switch").click()
    page.locator("#fishaudio_key").fill(key)
    page.locator("#fishaudio_validate").click()
    expect(page.locator("#fishaudio_key")).to_be_enabled(timeout=20000)
    page.locator("#fishaudio_save").click()

    page.goto(f"{BASE_URL}/settings#general", wait_until="domcontentloaded")
    page.locator("[data-general-subtab=voice]").click()
    provider = page.locator("#tts_provider")
    expect(provider).to_be_visible()
    values = provider.locator("option").evaluate_all("els => els.map(e => e.value)")
    assert "fishaudio" in values
    provider.select_option("fishaudio")
    expect(page.locator("#fishaudio_voice_options")).to_be_visible()
    voices = page.locator("#tts_voice option")
    expect(voices.first).to_be_attached()
    assert voices.count() >= 1
    page.screenshot(path="artifacts/fishaudio-voice-picker.png", full_page=True)


def test_chat_voice_provider_lists_fishaudio_when_key_is_saved(page: Page):
    page.goto(f"{BASE_URL}/chat", wait_until="domcontentloaded")
    page.wait_for_function(
        """() => {
            const el = document.getElementById('voiceProvider');
            return el && [...el.options].some(o => o.value === 'fishaudio');
        }""",
        timeout=15000,
    )
    page.screenshot(path="artifacts/fishaudio-chat-voice.png")


def test_fishaudio_shows_custom_clone_button(page: Page):
    page.goto(f"{BASE_URL}/settings#general", wait_until="domcontentloaded")
    page.get_by_role("tab", name="Voice Setup").click()
    page.wait_for_function(
        """() => {
            const el = document.getElementById('tts_provider');
            return el && [...el.options].some(o => o.value === 'fishaudio');
        }""",
        timeout=15000,
    )
    page.evaluate(
        """() => {
            const tab = document.querySelector('[data-general-subtab=\"voice\"]');
            if (tab) tab.click();
            const el = document.getElementById('tts_provider');
            el.value = 'fishaudio';
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    expect(page.locator("#add_custom_voice_btn")).to_be_visible()
    page.locator("#add_custom_voice_btn").click()
    expect(page.locator("#cv_name")).to_be_visible()
    expect(page.locator("#cv_modal_provider_label")).to_contain_text("Fish Audio")
    page.screenshot(path="artifacts/fishaudio-clone-modal.png")
