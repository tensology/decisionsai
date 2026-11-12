"""An agent-written client message never sits inside the work response bubble.

Incoming-origin threads show the work result, then Message Client as its own action.
The quoted wording becomes the Message Client seed; the stored response is unchanged.
Held/ineligible results keep Message Client absent (source_reply_eligible: false).
"""
import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import URL

pytestmark = pytest.mark.e2e_playwright

REPLY = ("100% done. The banner means the sync was stopped before anything was sent.\n\n"
         "**Reply to send Greg on WhatsApp:**\n\n"
         "> Hi Greg, that red banner means the sync was stopped before anything was sent.\n"
         "> Reload the page and press Sync again.\n\n"
         "Nothing reached device 2477.\n\nStatus: completed")


def _render(page, eligible=True, has_source=True, run=None, trailing_user=False, source_type='incoming_proposal', follow_up=False):
    page.goto(URL.split('/development/')[0] + '/robots.txt')
    page.set_content('<div id="studio-empty"></div><main id="conversation"><div id="message-list"></div></main>')
    page.evaluate("""async ([reply, eligible, hasSource, run, trailingUser, sourceType, followUp]) => {
        const {createThreadsTranscript} = await import('/static/development/threads/transcript/index.js?v=' + Date.now());
        const messages = [{role:'user',chat_row_id:1,content:'Clarify meaning of referenced message'},
                          ...(followUp ? [{role:'assistant',chat_row_id:4,content:'First answer'},{role:'user',chat_row_id:5,content:'And the other device?'}] : []),
                          {role:'assistant',chat_row_id:2,content:reply,source_reply_eligible:eligible}];
        if (trailingUser) messages.push({role:'user',chat_row_id:3,content:'One more thing'});
        const context = {currentChat:{id:17,has_reply_source:hasSource,development:{source_type:sourceType},messages,turns:[]},currentRun:run||{}};
        const actions = {escapeHtml:v=>String(v||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),bindConversationActions:()=>{},isActiveAgentRun:r=>['initializing','queued','running','waiting','paused'].includes(String(r?.status||''))};
        const transcript = createThreadsTranscript({context,actions,el:id=>document.getElementById(id)});
        transcript.renderConversation(context.currentChat);
        window.t = {context, reply};
    }""", [REPLY, eligible, has_source, run, trailing_user, source_type, follow_up])


@pytest.mark.parametrize('width', [390, 1440])
def test_client_draft_leaves_bubble_and_message_client_follows_response(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    _render(page)
    bubble = page.locator('.studio-message.assistant .message-markdown')
    expect(bubble).to_contain_text('The banner means the sync was stopped')
    expect(bubble).to_contain_text('Nothing reached device 2477.')
    expect(bubble).not_to_contain_text('Reply to send Greg')
    expect(bubble).not_to_contain_text('Hi Greg')
    button = page.locator('.studio-message.assistant .source-reply-button')
    expect(button).to_have_text('Message Client')
    # The action is outside and after the response text.
    assert page.evaluate("""() => {
        const md = document.querySelector('.studio-message.assistant .message-markdown');
        const btn = document.querySelector('.studio-message.assistant .source-reply-button');
        return !md.contains(btn) && Boolean(md.compareDocumentPosition(btn) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    seed = page.evaluate("document.querySelector('.studio-message.assistant').closest('[data-client-draft]')?.dataset.clientDraft || ''")
    assert seed.startswith('Hi Greg, that red banner') and 'press Sync again' in seed
    assert page.evaluate('window.t.context.currentChat.messages[1].content === window.t.reply')


def test_held_result_strips_draft_but_keeps_message_client_absent(page):
    _render(page, eligible=False)
    expect(page.locator('.studio-message.assistant .message-markdown')).not_to_contain_text('Hi Greg')
    expect(page.locator('.source-reply-button')).to_have_count(0)


def test_threads_without_a_reply_source_render_unchanged(page):
    _render(page, has_source=False)
    expect(page.locator('.studio-message.assistant .message-markdown')).to_contain_text('Hi Greg')
    expect(page.locator('.source-reply-button')).to_have_count(0)


CALLOUT = '.studio-message.assistant [data-client-callout]'


@pytest.mark.parametrize('run', [None, {'status': 'completed'}])
def test_incoming_reply_ends_with_callout_under_the_response(page, run):
    _render(page, run=run)
    expect(page.locator(CALLOUT)).to_have_count(1)
    assert page.evaluate("""() => {
        const md = document.querySelector('.studio-message.assistant .message-markdown');
        const slot = document.querySelector('.studio-message.assistant [data-client-callout]');
        const footer = document.querySelector('.studio-message.assistant .message-footer');
        return !md.contains(slot) && Boolean(footer.compareDocumentPosition(slot) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")


@pytest.mark.parametrize('status', ['running', 'queued', 'waiting', 'paused', 'blocked', 'failed', 'cancelled'])
def test_no_callout_until_the_whole_workflow_has_finished(page, status):
    _render(page, run={'status': status, 'workflow_run_id': 5})
    expect(page.locator(CALLOUT)).to_have_count(0)


def test_no_callout_for_held_results_plain_threads_or_superseded_replies(page):
    _render(page, eligible=False)
    expect(page.locator(CALLOUT)).to_have_count(0)
    _render(page, has_source=False)
    expect(page.locator(CALLOUT)).to_have_count(0)
    _render(page, trailing_user=True)
    expect(page.locator(CALLOUT)).to_have_count(0)


def test_ordinary_turns_get_no_callout_only_the_manual_action(page):
    # Not created from Incoming, or a later turn in the ongoing thread: no draft is requested.
    for kwargs in ({'source_type': 'proposal'}, {'source_type': ''}, {'follow_up': True}):
        _render(page, **kwargs)
        expect(page.locator(CALLOUT)).to_have_count(0)
        expect(page.locator('.studio-message.assistant .source-reply-button').last).to_have_text('Message Client')


def _open(page, inline):
    page.goto(URL.split('/development/')[0] + '/robots.txt')
    page.set_content('<div id="slot"></div><button id="opener">Message Client</button>')
    page.evaluate("""async (inline) => {
        const {openSourceReply} = await import('/static/development/threads/source-reply.js?v=' + Date.now());
        window.calls = [];
        const api = async (path, options = {}) => {
            window.calls.push([options.method || 'GET', path]);
            if (!options.method) return {sources:[{key:'wa',label:'Greg Gallagher',channel:'whatsapp',thread_id:'t'}], drafts:[]};
            if (path.endsWith('/source-replies')) return {id:9,source_key:'wa',status:'draft',text:'Hi Greg, reload and press Sync again.',target:{channel:'whatsapp'}};
            return {};
        };
        openSourceReply({chatId:17, responseId:2, api, toast:()=>{}, opener:document.getElementById('opener'),
                         ...(inline ? {mount:document.getElementById('slot')} : {})});
    }""", inline)


def test_incoming_callout_is_inline_with_approve_and_rewrite(page):
    _open(page, True)
    callout = page.locator('#slot .client-reply-callout')
    expect(callout).to_contain_text('Ready to send this?')
    expect(callout.locator('textarea').first).to_have_value('Hi Greg, reload and press Sync again.')
    expect(callout.locator('[data-approve]')).to_have_text('Approve and send')
    expect(callout.locator('[data-rewrite]')).to_be_enabled()
    expect(page.locator('dialog')).to_have_count(0)
    assert page.evaluate('window.calls.filter(c => c[0] === "POST").length') == 1
    callout.get_by_role('button', name='Not now').click()
    expect(callout).to_have_count(0)
    # Stepping aside keeps the draft for Message Client; nothing is cancelled or sent.
    assert page.evaluate('window.calls.filter(c => c[0] === "POST").length') == 1


def test_manual_message_client_opens_a_modal(page):
    _open(page, False)
    dialog = page.locator('dialog.source-reply-dialog')
    expect(dialog).to_be_visible()
    assert page.evaluate("document.querySelector('dialog.source-reply-dialog').matches(':modal')")
    expect(dialog.locator('h2')).to_have_text('Message Client')
    expect(page.locator('#slot .client-reply-callout')).to_have_count(0)
