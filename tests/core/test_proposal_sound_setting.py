"""Proposal alert preference survives a settings save and a fresh database read."""

import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Settings
from distr.gui.web.routes.settings._shared import GeneralSettings


def test_proposal_sound_defaults_enabled():
    path = Path(__file__).resolve().parents[2] / "distr/core/settings.py"
    spec = importlib.util.spec_from_file_location("_real_proposal_sound_settings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_SETTINGS["incoming_proposal_sound_enabled"] is True
    assert GeneralSettings().incoming_proposal_sound_enabled is True


def test_general_settings_preserves_proposal_opt_out_and_sound(monkeypatch):
    from distr.core import utils
    from distr.core.services import settings_service

    engine = create_engine("sqlite:///:memory:")
    Settings.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(utils, "Session", factory)
    monkeypatch.setattr(settings_service, "load_settings_from_db", utils.load_settings_from_db)
    monkeypatch.setattr(settings_service, "save_settings_to_db", utils.save_settings_to_db)
    monkeypatch.setattr(settings_service, "_safe_emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_service, "_signal", lambda *args: None)

    settings_service.save_general_settings(GeneralSettings(
        incoming_proposal_sound_enabled=False, notification_sound="chime",
    ))
    with factory() as db:
        stored = db.query(Settings).one()
        assert stored.incoming_proposal_sound_enabled is False
        assert stored.notification_sound == "chime"
    settings_service.save_general_settings(GeneralSettings(
        incoming_proposal_sound_enabled=True, notification_sound="none",
    ))
    loaded = utils.load_settings_from_db()
    assert loaded["incoming_proposal_sound_enabled"] is True
    assert loaded["notification_sound"] == "none"


def test_existing_database_gains_enabled_preference_without_replacing_sound(tmp_path):
    path = tmp_path / "settings.db"
    legacy_engine = create_engine(f"sqlite:///{path}")
    Table("settings", MetaData(), *[
        Column(column.name, column.type, primary_key=column.primary_key)
        for column in Settings.__table__.columns
        if column.name != "incoming_proposal_sound_enabled"
    ]).create(legacy_engine)
    legacy_engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO settings (id, notification_sound) VALUES (1, 'none')")
    result = subprocess.run(
        [sys.executable, "-c", "import distr.core.db"],
        env={**os.environ, "DECISIONS_DB_DIR": str(tmp_path), "DECISIONS_TEST_MODE": "1"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT incoming_proposal_sound_enabled, notification_sound FROM settings").fetchone() == (1, "none")
