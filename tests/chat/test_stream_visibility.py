"""Live replies must remain after history even when old timestamps are ahead."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.parametrize("mode", ["started", "tokens_only", "finished_only"])
def test_stream_stays_after_history_through_completion(mode):
    root = Path(__file__).resolve().parents[2]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="chatMessages"></div><textarea id="messageInput"></textarea><button id="sendButton"></button>')
            page.add_script_tag(path=str(root / "distr/gui/web/static/chat/js/chat.js"))
            result = page.evaluate("""mode => {
                currentChatId = loadedChatId = 1;
                window.fetch = async () => new Response('{}');
                const future = Date.now() + 7200000;
                renderMessages([
                    {role: 'user', content: 'Old question', timestamp: future, chat_row_id: 2},
                    {role: 'assistant', content: 'Old answer', timestamp: future, chat_row_id: 2},
                    {role: 'user', content: 'Current question', timestamp: Date.now(), chat_row_id: 3}
                ]);
                if (mode === 'started') handleChatEventStreamStarted({chat_id: 1});
                if (mode !== 'finished_only') handleChatEventStreamToken({chat_id: 1, token: 'First '});
                const first = chatMessages.lastElementChild.querySelector('.message-text')?.textContent;
                if (mode !== 'finished_only') handleChatEventStreamToken({chat_id: 1, token: 'second.'});
                const second = chatMessages.lastElementChild.querySelector('.message-text')?.textContent;
                handleChatEventStreamFinished({chat_id: 1, response_text: 'First second.'});
                return {first, second, final: chatMessages.lastElementChild.querySelector('.message-text')?.textContent,
                    count: [...chatMessages.querySelectorAll('.message.assistant')].filter(e => e.textContent.includes('First second.')).length};
            }""", mode)
            assert result["final"] == "First second."
            assert result["count"] == 1
            if mode != "finished_only":
                assert result["first"] == "First "
                assert result["second"] == "First second."
        finally:
            browser.close()


def test_dictation_is_visible_without_an_assistant_response():
    root = Path(__file__).resolve().parents[2]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="chatMessages"></div><textarea id="messageInput"></textarea><button id="sendButton"></button>')
            page.add_script_tag(path=str(root / "distr/gui/web/static/chat/js/chat.js"))
            result = page.evaluate("""() => {
                currentChatId = loadedChatId = 1;
                renderMessages([
                    {role: 'user', content: 'History', timestamp: Date.now() + 7200000, chat_row_id: 2}
                ]);
                handleChatEventToolExecuted({
                    chat_id: 1, id: 'dictation-test', tool_name: 'context_note',
                    title: 'Context', status: 'completed', result_summary: 'Voice preview test',
                    result_detail: 'Voice preview test', activity_style: 'passive'
                });
                return {
                    lastIsDictation: chatMessages.lastElementChild.textContent.includes('Voice preview test'),
                    assistants: chatMessages.querySelectorAll('.message.assistant').length
                };
            }""")
            assert result == {"lastIsDictation": True, "assistants": 0}
        finally:
            browser.close()


@pytest.mark.parametrize("message_first", [False, True])
def test_voice_preview_is_committed_in_place_without_message_flicker(message_first):
    root = Path(__file__).resolve().parents[2]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="chatMessages"></div><textarea id="messageInput"></textarea><button id="sendButton"></button>')
            page.add_script_tag(path=str(root / "distr/gui/web/static/chat/js/chat.js"))
            result = page.evaluate("""messageFirst => {
                currentChatId = loadedChatId = 1;
                window.fetch = async () => new Response('{}');
                renderMessages([{role: 'user', content: 'Old question', timestamp: Date.now() + 7200000, chat_row_id: 2}]);
                showTranscriptionStatus('Voice question', false, false, false);
                const preview = document.getElementById('transcriptionStatus');
                if (!messageFirst) showTranscriptionStatus('', true, true, false);
                handleChatEventMessageAdded({chat_id: 1, chat_row_id: 3, role: 'user', content: 'Voice question'});
                if (messageFirst) showTranscriptionStatus('', true, true, false);
                handleChatEventStreamStarted({chat_id: 1});
                handleChatEventStreamToken({chat_id: 1, token: 'Answer'});
                const user = findRenderedMessageByIdentity('user', 3);
                return {sameNode: user === preview, beforeReply: user?.nextElementSibling?.id,
                    users: [...chatMessages.querySelectorAll('.message.user')].filter(e => e.textContent.includes('Voice question')).length};
            }""", message_first)
            assert result == {"sameNode": True, "beforeReply": "streamingAssistantMessage", "users": 1}
        finally:
            browser.close()
