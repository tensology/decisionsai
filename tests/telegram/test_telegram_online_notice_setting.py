def test_telegram_online_notice_defaults_disabled():
    import importlib.util
    from pathlib import Path

    from distr.core.db import Settings
    from distr.gui.web.routes.settings._shared import GeneralSettings

    settings_path = Path(__file__).resolve().parents[2] / "distr" / "core" / "settings.py"
    spec = importlib.util.spec_from_file_location("_decisions_real_settings_for_test", settings_path)
    settings_mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(settings_mod)

    assert settings_mod.DEFAULT_SETTINGS["telegram_send_online_notice"] is False
    assert GeneralSettings().telegram_send_online_notice is False
    assert "telegram_send_online_notice" in Settings.__table__.columns


def test_telegram_online_notice_helper_defaults_to_disabled(monkeypatch):
    import distr.core.integrations.telegram.manager as manager_mod

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {},
    )

    assert manager_mod.telegram_online_notice_enabled() is False


def test_telegram_online_notice_helper_respects_disabled_setting(monkeypatch):
    import distr.core.integrations.telegram.manager as manager_mod

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"telegram_send_online_notice": False},
    )

    assert manager_mod.telegram_online_notice_enabled() is False


def test_telegram_online_notice_helper_fails_closed(monkeypatch):
    import distr.core.integrations.telegram.manager as manager_mod

    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", boom)

    assert manager_mod.telegram_online_notice_enabled() is False


def test_online_notice_claim_is_durable_and_rate_limited(monkeypatch):
    import uuid

    import distr.core.integrations.telegram.manager as manager_mod

    monkeypatch.setattr(manager_mod, "telegram_online_notice_enabled", lambda: True)
    monkeypatch.setattr(
        manager_mod,
        "_TELEGRAM_ONLINE_NOTICE_STATE_KEY",
        f"test_telegram_online_notice_{uuid.uuid4().hex}",
    )

    assert manager_mod.claim_telegram_online_notice(now=1000.0) is True
    assert manager_mod.claim_telegram_online_notice(now=1001.0) is False
    assert manager_mod.claim_telegram_online_notice(
        now=1000.0 + manager_mod._TELEGRAM_ONLINE_NOTICE_COOLDOWN_S
    ) is True
