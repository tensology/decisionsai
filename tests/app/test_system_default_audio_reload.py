import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.app import main


@pytest.mark.parametrize('inputs,outputs', [
    (['System Default', 'Mic'], ['System Default']),
    (['System Default'], ['System Default', 'Headset']),
    (['System Default'], ['System Default']),
])
def test_missing_saved_devices_do_not_block_startup_or_replace_preferences(monkeypatch, inputs, outputs):
    saved = {'input_device': 'Mic', 'output_device': 'Headset', 'lock_sound': True}
    monkeypatch.setattr(main, 'load_settings_from_db', lambda: saved.copy())
    monkeypatch.setattr(main, 'get_device_choices', lambda: (inputs, outputs))
    def unexpected_dialog(**kwargs):
        raise AssertionError('Disconnected saved device must not block startup')
    monkeypatch.setattr(main, 'DeviceSelectionDialog', unexpected_dialog)
    app = SimpleNamespace(settings={})
    main.Application.select_devices(app)
    assert app.selected_input_device == 'Mic'
    assert app.selected_output_device == 'Headset'
    assert app.settings == saved


def _app_harness():
    return SimpleNamespace(
        _last_device_hash="devices-v1",
        _last_default_device_fingerprint="Built-in Output",
        _last_default_output_fingerprint="Built-in Output",
        settings={},
        selected_input_device=None,
        selected_output_device=None,
        agent_command_queue=MagicMock(),
        oracle_window=SimpleNamespace(isVisible=lambda: False),
        _sync_agent_audio_from_settings=MagicMock(),
        reload_agent_session=MagicMock(),
    )


def _patch_main_thread(monkeypatch):
    thread = object()
    monkeypatch.setattr(main, "QThread", SimpleNamespace(currentThread=lambda: thread))
    monkeypatch.setattr(
        main.QtWidgets,
        "QApplication",
        SimpleNamespace(instance=lambda: SimpleNamespace(thread=lambda: thread)),
    )


def test_macos_system_default_output_route_change_reloads_agent(monkeypatch):
    _patch_main_thread(monkeypatch)
    settings = {
        "input_device": "System Default",
        "output_device": "System Default",
        "lock_sound": True,
    }
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main, "load_settings_from_db", lambda: settings)
    monkeypatch.setattr(main, "restore_locked_devices", lambda _settings: {})
    app = _app_harness()

    main.Application._on_device_check_result(
        app,
        json.dumps(
            {
                "device_hash": "devices-v1",
                "default_fingerprint": "JBL TUNE510BT",
                "default_output_fingerprint": "JBL TUNE510BT",
            }
        ),
    )

    assert app.settings["input_device"] == "System Default"
    assert app.settings["output_device"] == "System Default"
    assert app.selected_input_device == "System Default"
    assert app.selected_output_device == "System Default"
    app.reload_agent_session.assert_called_once_with(skip_welcome=True)
    app._sync_agent_audio_from_settings.assert_not_called()


def test_non_macos_system_default_output_route_change_keeps_hot_swap(monkeypatch):
    _patch_main_thread(monkeypatch)
    settings = {
        "input_device": "System Default",
        "output_device": "System Default",
        "lock_sound": True,
    }
    monkeypatch.setattr(main.sys, "platform", "linux")
    monkeypatch.setattr(main, "load_settings_from_db", lambda: settings)
    monkeypatch.setattr(main, "restore_locked_devices", lambda _settings: {})
    app = _app_harness()

    main.Application._on_device_check_result(
        app,
        json.dumps(
            {
                "device_hash": "devices-v1",
                "default_fingerprint": "USB Headset",
                "default_output_fingerprint": "USB Headset",
            }
        ),
    )

    app.reload_agent_session.assert_not_called()
    app._sync_agent_audio_from_settings.assert_called_once_with(settings)


def test_macos_named_output_route_change_keeps_hot_swap(monkeypatch):
    _patch_main_thread(monkeypatch)
    settings = {
        "input_device": "System Default",
        "output_device": "Studio Speakers",
        "lock_sound": True,
    }
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main, "load_settings_from_db", lambda: settings)
    monkeypatch.setattr(main, "restore_locked_devices", lambda _settings: {})
    app = _app_harness()

    main.Application._on_device_check_result(
        app,
        json.dumps(
            {
                "device_hash": "devices-v1",
                "default_fingerprint": "USB Headset",
                "default_output_fingerprint": "USB Headset",
            }
        ),
    )

    app.reload_agent_session.assert_not_called()
    app._sync_agent_audio_from_settings.assert_called_once_with(settings)


def test_unchanged_default_fingerprint_does_not_reload_or_hot_swap(monkeypatch):
    _patch_main_thread(monkeypatch)
    settings = {
        "input_device": "System Default",
        "output_device": "System Default",
        "lock_sound": True,
    }
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main, "load_settings_from_db", lambda: settings)
    app = _app_harness()

    main.Application._on_device_check_result(
        app,
        json.dumps(
            {
                "device_hash": "devices-v1",
                "default_fingerprint": "Built-in Output",
                "default_output_fingerprint": "Built-in Output",
            }
        ),
    )

    app.reload_agent_session.assert_not_called()
    app._sync_agent_audio_from_settings.assert_not_called()


def test_macos_input_only_default_change_does_not_reload_agent(monkeypatch):
    _patch_main_thread(monkeypatch)
    settings = {
        "input_device": "System Default",
        "output_device": "System Default",
        "lock_sound": True,
    }
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main, "load_settings_from_db", lambda: settings)
    monkeypatch.setattr(main, "restore_locked_devices", lambda _settings: {})
    app = _app_harness()

    main.Application._on_device_check_result(
        app,
        json.dumps(
            {
                "device_hash": "devices-v1",
                "default_fingerprint": "New Microphone|Built-in Output",
                "default_output_fingerprint": "Built-in Output",
            }
        ),
    )

    app.reload_agent_session.assert_not_called()
    app._sync_agent_audio_from_settings.assert_called_once_with(settings)


def test_named_headset_disconnect_reloads_fresh_macos_catalog_without_rewriting_preferences(monkeypatch):
    _patch_main_thread(monkeypatch)
    settings = {"input_device": "Headset", "output_device": "Headset", "lock_sound": False}
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main, "load_settings_from_db", lambda: settings.copy())
    monkeypatch.setattr(main, "restore_locked_devices", lambda _: {})
    monkeypatch.setattr(main, "detect_devices", lambda: ([], [], [], []))
    app = _app_harness()
    main.Application._on_device_check_result(app, json.dumps({
        "device_hash": "headset-removed", "default_fingerprint": "Speakers",
        "default_output_fingerprint": "Speakers",
    }))
    app.reload_agent_session.assert_called_once_with(skip_welcome=True)
    assert settings["output_device"] == "Headset"


def test_named_devices_are_monitored_without_remember_setting(monkeypatch):
    app = SimpleNamespace(_device_check_enabled=True, oracle_window=object(),
                          _on_device_check_result=lambda _: None)
    worker = MagicMock()
    pool = MagicMock()
    monkeypatch.setattr(main, "DeviceCheckWorker", lambda: worker)
    monkeypatch.setattr(main, "QThreadPool", SimpleNamespace(globalInstance=lambda: pool))
    main.Application.check_audio_device_changes(app)
    pool.start.assert_called_once_with(worker)
    main.Application.check_audio_device_changes(app)
    assert pool.start.call_count == 1  # A slow probe must not stack workers.


def test_non_macos_catalog_change_forces_named_device_refresh(monkeypatch):
    _patch_main_thread(monkeypatch)
    settings = {"input_device": "Headset", "output_device": "Headset", "lock_sound": False}
    monkeypatch.setattr(main.sys, "platform", "linux")
    monkeypatch.setattr(main, "load_settings_from_db", lambda: settings)
    monkeypatch.setattr(main, "restore_locked_devices", lambda _: {})
    monkeypatch.setattr(main, "detect_devices", lambda: ([], [], [], []))
    app = _app_harness()
    main.Application._on_device_check_result(app, json.dumps({"device_hash": "headset-removed"}))
    app._sync_agent_audio_from_settings.assert_called_once_with(settings, force_refresh=True)
    app.reload_agent_session.assert_not_called()
