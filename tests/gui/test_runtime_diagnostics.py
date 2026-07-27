from pathlib import Path

from distr.gui.web.routes import observability


def test_runtime_diagnostics_identifies_active_storage_without_secrets(monkeypatch, tmp_path: Path) -> None:
    db_root = tmp_path / "db"
    db_root.mkdir()
    (db_root / "settings.db").write_bytes(b"database")
    (db_root / "settings.db-wal").write_bytes(b"wal")
    monkeypatch.setattr(observability, "DB_DIR", str(db_root))
    monkeypatch.setattr(observability, "DATA_DIR", str(tmp_path))

    result = observability.runtime_diagnostics()

    assert result["status"] == "ok"
    assert result["db_root"] == str(db_root.resolve())
    assert result["settings_db_bytes"] == 8
    assert result["settings_wal_bytes"] == 3
    assert "settings" not in result
    assert "environment" not in result
