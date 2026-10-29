"""Browser regressions use fake devices and never change the real audio route."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def audio_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<select id="input_device"></select><select id="output_device"></select>'
                         '<input type="checkbox" id="remember_audio_settings">'
                         '<table><tbody id="audio_output_tbody"></tbody></table>'
                         '<table><tbody id="audio_input_tbody"></tbody></table>')
        # Avoid automatic initialisation so asynchronous responses are controlled.
        script = (ROOT / 'distr/gui/web/static/settings/js/audio.js').read_text()
        script = script[:script.index("if (document.readyState === 'loading')")]
        page.add_script_tag(content=script)
        page.evaluate('''() => {
            window.showNotification = () => {};
            window.devices = {input_devices: [{name:'System Default'}, {name:'Headset'}],
                              output_devices: [{name:'System Default'}, {name:'Headset'}, {name:'Headset Pro'}]};
            populateAudioDevicesFromData(devices);
            document.getElementById('input_device').value = 'Headset';
            document.getElementById('output_device').value = 'Headset';
            window.saved = [];
            window.fetch = async (url, options) => {
                if (options && options.method === 'POST') saved.push(JSON.parse(options.body));
                return {ok:true, json:async () => ({success:true})};
            };
        }''')
        yield page
        browser.close()


def test_disconnect_refresh_and_save_preserve_exact_preferences(audio_page):
    page = audio_page
    page.evaluate('''() => populateAudioDevicesFromData({
        input_devices:[{name:'System Default'}], output_devices:[{name:'System Default'}, {name:'Headset Pro'}]
    })''')
    assert page.locator('#output_device').input_value() == 'Headset'
    assert 'disconnected' in page.locator('#output_device option:checked').inner_text()
    assert page.locator('#audio_output_tbody .selected').count() == 0
    page.evaluate('saveAudioSettings()')
    assert page.evaluate('saved[0].output_device') == 'Headset'
    assert page.evaluate('saved[0].input_device') == 'Headset'
    page.evaluate('populateAudioDevicesFromData(devices)')
    assert page.locator('#output_device option:checked').inner_text() == 'Headset'
    assert page.locator('#audio_output_tbody tr.selected').count() == 1


def test_late_settings_load_does_not_erase_unsaved_selection(audio_page):
    page = audio_page
    page.evaluate('''() => {
        window.fetch = async url => url.endsWith('/devices')
            ? {ok:true,json:async()=>devices}
            : await new Promise(resolve => { window.finishLoad = () => resolve({ok:true,json:async()=>({
                input_device:'System Default',output_device:'System Default'
            })}); });
        window.loading = loadAudioSettings();
    }''')
    page.wait_for_function('typeof finishLoad === "function"')
    page.select_option('#output_device', 'Headset Pro')
    page.evaluate('_markAudioEdited(); finishLoad()')
    page.evaluate('loading')
    assert page.locator('#output_device').input_value() == 'Headset Pro'


def test_late_save_does_not_erase_newer_edit_or_double_submit(audio_page):
    page = audio_page
    page.evaluate('''() => {
        window.fetch = async () => await new Promise(resolve => {
            window.finishSave = () => resolve({ok:true,json:async()=>({success:true})});
        });
        window.saving = saveAudioSettings();
    }''')
    page.select_option('#output_device', 'Headset Pro')
    page.evaluate('_markAudioEdited(); saveAudioSettings(); finishSave()')
    page.evaluate('saving')
    assert page.locator('#output_device').input_value() == 'Headset Pro'
    assert page.evaluate('_audioDirty') is True


def test_failed_save_preserves_edits_and_allows_retry(audio_page):
    page = audio_page
    page.evaluate('''() => {
        _markAudioEdited();
        window.fetch = async () => ({ok:false,json:async()=>({detail:'Unavailable'})});
    }''')
    page.evaluate('saveAudioSettings()')
    assert page.locator('#output_device').input_value() == 'Headset'
    assert page.evaluate('_audioDirty && !_audioSaving') is True
