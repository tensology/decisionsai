"""Browser playback and Chat work have independent controls."""

from base64 import b64encode
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
import wave

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "distr/gui/web"
CHAT_ID = 163
TURN = {
    "turn_id": 184,
    "status": "running",
    "events": [{"event_id": "step", "event_type": "tool_started", "title": "Reviewing the plan"}],
}


@pytest.fixture
def playback_chat(page):
    page.set_default_timeout(5000)
    env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=True)
    html = env.get_template("chat/chat.html").render(base_path="/chat")
    wav = BytesIO()
    with wave.open(wav, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(8000 * 2 * 12))
    sample = b64encode(wav.getvalue()).decode("ascii")

    def serve(route):
        path = urlparse(route.request.url).path
        if path == "/chat/":
            route.fulfill(content_type="text/html", body=html)
        elif path == "/api/chats":
            route.fulfill(json={"chats": [{"id": CHAT_ID, "agent_name": "QA Agent"}, {"id": 164, "agent_name": "Other Agent"}]})
        elif path in (f"/api/chats/{CHAT_ID}", "/api/chats/164"):
            route.fulfill(json={
                "title": "Plan review" if path.endswith(str(CHAT_ID)) else "Second conversation",
                "provider": "Ollama",
                "model_name": "ornith:9b",
                "messages": [
                    {"role": "assistant", "content": "I finished reviewing the brief.", "chat_row_id": 182},
                    {"role": "user", "content": "Check the plan next.", "chat_row_id": 184},
                ],
                "active_turn": TURN,
                "turns": [TURN],
            })
        elif path.startswith("/api/"):
            route.fulfill(json={})
        else:
            asset = WEB / path.replace("/chat/static/", "/static/chat/", 1).lstrip("/")
            if path.startswith("/assets/"):
                asset = ROOT / path.lstrip("/")
            if asset.is_file():
                route.fulfill(path=asset)
            else:
                route.fulfill(status=404, body="")

    page.route("http://decisions.test/**", serve)
    page.route_web_socket("ws://decisions.test/**", lambda socket: None)
    page.goto(f"http://decisions.test/chat/?id={CHAT_ID}&from_create=1")
    expect(page.locator("#turnStopButton")).to_be_visible()
    page.evaluate(
        r"""sample => {
            const fetchApi = window.fetch.bind(window);
            window.playbackPosts = [];
            window.fetch = (url, options) => {
                if (options?.method === 'POST' && /\/(?:cancel|stop)(?:\?|$)/.test(url)) {
                    window.playbackPosts.push(url);
                    return Promise.resolve(new Response('{}'));
                }
                if (url === '/api/chats/tts/generate') {
                    const response = () => new Response(Uint8Array.from(atob(sample), c => c.charCodeAt(0)), {
                        headers: {'Content-Type': 'audio/wav'}
                    });
                    if (window.holdPlayback) {
                        return new Promise(resolve => { window.releasePlayback = () => resolve(response()); });
                    }
                    return Promise.resolve(response());
                }
                return fetchApi(url, options);
            };
        }""",
        sample,
    )
    return page


@pytest.mark.parametrize("width", [1440, 390, 768, 1280])
def test_playback_controls_leave_active_work_running(playback_chat, tmp_path, width):
    page = playback_chat
    page.set_viewport_size({"width": width, "height": 900 if width > 390 else 844})
    play_message = page.locator("#chatMessages .message-action-play").first
    player = page.locator("#ttsPlayerCard")
    audio = page.locator("#ttsPlayerAudio")
    work_stop = page.locator("#turnStopButton")
    play_message.click()
    expect(player).to_be_visible()
    expect(page.locator("#ttsPlayerPause")).to_be_visible()
    page.wait_for_function("document.querySelector('#ttsPlayerAudio').currentTime > 0.2")
    page.screenshot(path=tmp_path / "playing.png")

    page.locator("#ttsPlayerStop").click()
    expect(page.locator("#ttsPlayerPlay")).to_be_visible()
    assert audio.evaluate("audio => audio.paused && audio.currentTime === 0")
    assert page.evaluate("window.playbackPosts") == []
    expect(work_stop).to_be_visible()
    page.screenshot(path=tmp_path / "stopped.png")

    page.locator("#ttsPlayerPlay").click()
    expect(page.locator("#ttsPlayerPause")).to_be_visible()
    page.locator("#ttsPlayerClose").click()
    expect(player).to_be_hidden()
    assert audio.evaluate("audio => audio.paused && !audio.hasAttribute('src')")
    expect(play_message).to_be_focused()
    expect(play_message).to_have_css("opacity", "1")
    assert page.evaluate("window.playbackPosts") == []
    expect(work_stop).to_be_visible()
    page.screenshot(path=tmp_path / "closed.png")

    work_stop.click()
    assert page.evaluate("window.playbackPosts") == [f"/api/chats/{CHAT_ID}/cancel?turn_id=184"]
    page.evaluate("selectChat(164)")
    expect(page.locator("#chatTitle")).to_have_text("Other Agent")
    expect(work_stop).to_be_hidden()
    page.screenshot(path=tmp_path / "view-only.png")


def test_audio_generated_for_previous_chat_is_discarded(playback_chat):
    page = playback_chat
    page.evaluate("window.holdPlayback = true")
    play_message = page.locator("#chatMessages .message-action-play").first
    original_button = play_message.element_handle()
    play_message.click()
    page.wait_for_function("typeof window.releasePlayback === 'function'")
    page.evaluate("selectChat(164)")
    expect(page.locator("#chatTitle")).to_have_text("Other Agent")
    page.evaluate("window.releasePlayback()")
    page.wait_for_function("button => !button.disabled", arg=original_button)
    expect(page.locator("#ttsPlayerCard")).to_be_hidden()
    page.wait_for_function("document.querySelector('#ttsPlayerAudio').readyState === 0")
    expect(page.locator("#turnStopButton")).to_be_hidden()
    page.evaluate("cancelStream()")
    assert page.evaluate("window.playbackPosts") == []
