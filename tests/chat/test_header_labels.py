"""Model and voice labels remain readable at supported viewport widths."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
@pytest.mark.parametrize("voice", ["Fish Audio (Online) / Hayley Williams", "ElevenLabs / Long descriptive voice name with multilingual support"])
def test_header_labels_wrap_without_clipping(width, voice, tmp_path):
    template = (ROOT / "distr/gui/web/templates/chat/chat.html").read_text()
    start = template.index("        <div class=\"chat-settings-header\"")
    header = template[start:template.index("        <!-- Empty State:", start)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.set_content(f"""<style>body{{margin:0;font-family:Arial}}*{{box-sizing:border-box}}</style>
            <div class="chat-shell"><div class="app-container">
            <aside class="sidebar"></aside><main class="main-content chat-panel-card" style="flex:1;min-width:0">
            {header}</main></div></div>""")
        page.add_style_tag(path=str(ROOT / "distr/gui/web/static/chat/css/chat.css"))
        page.locator("#chatSettingsHeader").evaluate("el => el.style.removeProperty('display')")
        page.locator("#headerLlmSummary").evaluate("(el, text) => el.textContent = text", "Ollama / muse-glimmer:30b-mlx")
        page.locator("#headerVoiceSummary").evaluate("(el, text) => el.textContent = text", voice)
        page.locator("#voiceUsageMeter").evaluate("el => el.hidden = false")
        for selector in ("#headerLlmSummary", "#headerVoiceSummary"):
            result = page.locator(selector).evaluate("""el => {
                const style = getComputedStyle(el), box = el.getBoundingClientRect();
                const range = document.createRange(); range.selectNodeContents(el);
                return {ellipsis: style.textOverflow === 'ellipsis', lines: range.getClientRects().length,
                    fits: [...range.getClientRects()].every(r => r.left >= box.left - 1 && r.right <= box.right + 1 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1)};
            }""")
            assert not result["ellipsis"]
            assert result["fits"]
            assert page.locator(selector).evaluate("el => el.clientWidth >= 100")
            if width >= 1280 and voice.startswith("Fish Audio"):
                assert result["lines"] == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#chatSettingsHeader").screenshot(path=str(tmp_path / f"header-{width}.png"))
        browser.close()
