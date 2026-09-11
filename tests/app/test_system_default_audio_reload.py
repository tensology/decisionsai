import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.app import main


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
