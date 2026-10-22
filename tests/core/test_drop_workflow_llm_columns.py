from sqlalchemy import create_engine, text

from distr.core.db.migrations import _drop_settings_columns


def _cols(engine):
    with engine.connect() as conn:
        return [r[1] for r in conn.execute(text("PRAGMA table_info(settings)")).fetchall()]


def test_drop_settings_columns_is_idempotent_and_keeps_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'s.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE settings (id INTEGER PRIMARY KEY, project_cli_low_model VARCHAR, workflow_llm_provider VARCHAR, workflow_llm_model VARCHAR)"))
        conn.execute(text("INSERT INTO settings VALUES (1, 'minimax', 'openrouter', 'x')"))
        conn.commit()
    _drop_settings_columns(engine, ("workflow_llm_provider", "workflow_llm_model"))
    _drop_settings_columns(engine, ("workflow_llm_provider", "workflow_llm_model"))
    assert _cols(engine) == ["id", "project_cli_low_model"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT project_cli_low_model FROM settings")).scalar() == "minimax"


def test_no_code_readds_workflow_llm_columns():
    from pathlib import Path
    src = Path("distr/core/db/migrations.py").read_text()
    assert '("workflow_llm_provider", "\'\'")' not in src
    from distr.core.db import Settings
    assert "workflow_llm_provider" not in Settings.__table__.columns
