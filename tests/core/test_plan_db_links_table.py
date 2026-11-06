"""plan_db_links: additive table via the model + an IF NOT EXISTS guard in run_migrations (temp DBs only)."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from distr.core.db import Base
from distr.core.db.migrations import ensure_plan_db_links_table
from distr.core.db.workflow import PlanDbLink

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"id", "project_id", "workspace_id", "engine", "host", "port", "database", "schemas", "username",
            "secret_ref", "label", "colour", "read_only", "status", "last_error", "last_introspected_at",
            "created_at", "modified_at"}


def _schema(path):
    with sqlite3.connect(path) as conn:
        return sorted(conn.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())


def test_model_has_no_password_column():
    columns = set(PlanDbLink.__table__.columns.keys())
    assert columns == EXPECTED
    assert not any(word in name for name in columns for word in ("password", "passwd", "pwd", "secret_value", "token"))


def test_prior_schema_gets_table_without_data_loss_and_rerun_is_noop(tmp_path):
    path = tmp_path / "prior.db"
    engine = create_engine(f"sqlite:///{path}")
    prior = [t for name, t in Base.metadata.tables.items() if name != "plan_db_links"]
    Base.metadata.create_all(engine, tables=prior)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO plan_workspaces (id, board_key, board_provider, board_name, status, created_at, modified_at) "
                          "VALUES (1, 'decisions:1', 'decisions', 'Kept', 'draft', '2026-01-01', '2026-01-01')"))
    before = {row for row in _schema(path)}
    assert "plan_db_links" not in inspect(engine).get_table_names()
    ensure_plan_db_links_table(engine)
    inspector = inspect(create_engine(f"sqlite:///{path}"))
    assert {c["name"] for c in inspector.get_columns("plan_db_links")} == EXPECTED
    assert "ix_plan_db_links_project" in {i["name"] for i in inspector.get_indexes("plan_db_links")}
    after = set(_schema(path))
    assert before <= after                                         # additive only: nothing existing changed
    assert {row[1] for row in after - before} == {"plan_db_links", "ix_plan_db_links_project"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT board_name FROM plan_workspaces WHERE id = 1")).scalar() == "Kept"
    ensure_plan_db_links_table(engine)                             # second run: no-op
    assert set(_schema(path)) == after


def test_engine_check_constraint(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    ensure_plan_db_links_table(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO plan_db_links (project_id, engine, host, database, schemas, username, label, read_only, status, created_at, modified_at) "
                          "VALUES (1, 'postgres', '127.0.0.1', 'app', '[]', 'me', 'Local', 1, 'new', '2026-01-01', '2026-01-01')"))
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO plan_db_links (project_id, engine, host, database, schemas, username, label, read_only, status, created_at, modified_at) "
                              "VALUES (1, 'oracle', '127.0.0.1', 'x', '[]', 'me', 'Bad', 1, 'new', '2026-01-01', '2026-01-01')"))
        raise AssertionError("engine outside postgres/mysql was accepted")
    except Exception as exc:
        assert "CHECK" in str(exc).upper() or "constraint" in str(exc).lower()


def test_fresh_app_db_gets_the_table_and_second_start_is_noop(tmp_path):
    env = {**os.environ, "DECISIONS_DB_DIR": str(tmp_path), "DECISIONS_TEST_MODE": "1", "QT_QPA_PLATFORM": "offscreen"}
    code = "from distr.core.db import init_db; init_db()"
    for _ in range(2):
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True, capture_output=True, timeout=240)
        if _ == 0:
            first = _schema(tmp_path / "settings.db")
    second = _schema(tmp_path / "settings.db")
    assert any(row[1] == "plan_db_links" for row in first)
    assert [r for r in first if "plan_db_links" in r[1]] == [r for r in second if "plan_db_links" in r[1]]
