"""Legacy Hermes state is not stranded when current tables already exist."""

from sqlalchemy import create_engine, inspect, text

from distr.core.db.migrations import _migrate_legacy_hermes_schema_to_orchestrator


def test_legacy_state_merges_into_existing_orchestrator_schema(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(
        "distr.core.automation.scheduler.ensure_automation_schema",
        lambda: None,
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY,
                hermes_enabled INTEGER,
                orchestrator_enabled INTEGER,
                hermes_orchestrator_provider TEXT,
                orchestrator_provider TEXT
            )
        """))
        conn.execute(text("INSERT INTO settings VALUES (1, 0, 1, 'legacy-provider', '')"))
        conn.execute(text("""
            CREATE TABLE kanban_boards (
                id INTEGER PRIMARY KEY,
                hermes_policy TEXT,
                orchestrator_policy TEXT
            )
        """))
        conn.execute(text("INSERT INTO kanban_boards VALUES (1, '{\"mode\":\"own\"}', '')"))
        conn.execute(text("""
            CREATE TABLE hermes_events (
                id INTEGER PRIMARY KEY,
                event_uid TEXT UNIQUE,
                parent_event_id INTEGER,
                summary TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE orchestrator_events (
                id INTEGER PRIMARY KEY,
                event_uid TEXT UNIQUE,
                parent_event_id INTEGER,
                summary TEXT
            )
        """))
        conn.execute(text("INSERT INTO orchestrator_events VALUES (1, 'current', NULL, 'current')"))
        conn.execute(text("INSERT INTO hermes_events VALUES (1, 'legacy-parent', NULL, 'parent')"))
        conn.execute(text("INSERT INTO hermes_events VALUES (2, 'legacy-child', 1, 'child')"))

        conn.execute(text("CREATE TABLE hermes_visual_baseline_sets (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE orchestrator_visual_baseline_sets (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO orchestrator_visual_baseline_sets VALUES (1, 'current')"))
        conn.execute(text("INSERT INTO hermes_visual_baseline_sets VALUES (1, 'legacy')"))
        conn.execute(text("""
            CREATE TABLE hermes_visual_baseline_screens (
                id INTEGER PRIMARY KEY, baseline_set_id INTEGER, screen_name TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE orchestrator_visual_baseline_screens (
                id INTEGER PRIMARY KEY, baseline_set_id INTEGER, screen_name TEXT
            )
        """))
        conn.execute(text("INSERT INTO orchestrator_visual_baseline_screens VALUES (1, 1, 'current')"))
        conn.execute(text("INSERT INTO hermes_visual_baseline_screens VALUES (1, 1, 'legacy')"))

    _migrate_legacy_hermes_schema_to_orchestrator(engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert not any(table.startswith("hermes_") for table in tables)
    assert "hermes_enabled" not in {column["name"] for column in inspector.get_columns("settings")}
    assert "hermes_policy" not in {column["name"] for column in inspector.get_columns("kanban_boards")}

    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT orchestrator_enabled, orchestrator_provider FROM settings"
        )).one() == (0, "legacy-provider")
        assert conn.execute(text(
            "SELECT orchestrator_policy FROM kanban_boards"
        )).scalar_one() == '{"mode":"own"}'
        parent_id = conn.execute(text(
            "SELECT id FROM orchestrator_events WHERE event_uid='legacy-parent'"
        )).scalar_one()
        assert conn.execute(text(
            "SELECT parent_event_id FROM orchestrator_events WHERE event_uid='legacy-child'"
        )).scalar_one() == parent_id
        legacy_set_id = conn.execute(text(
            "SELECT id FROM orchestrator_visual_baseline_sets WHERE name='legacy'"
        )).scalar_one()
        assert conn.execute(text(
            "SELECT baseline_set_id FROM orchestrator_visual_baseline_screens WHERE screen_name='legacy'"
        )).scalar_one() == legacy_set_id

    # The migration is safe to rerun after legacy state has been removed.
    _migrate_legacy_hermes_schema_to_orchestrator(engine)


def test_schema_mismatch_rolls_back_without_dropping_legacy_data(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mismatch.db'}")
    monkeypatch.setattr(
        "distr.core.automation.scheduler.ensure_automation_schema",
        lambda: None,
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE hermes_events (id INTEGER PRIMARY KEY, event_uid TEXT, legacy_only TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE orchestrator_events (id INTEGER PRIMARY KEY, event_uid TEXT)"
        ))
        conn.execute(text("INSERT INTO hermes_events VALUES (1, 'legacy', 'preserve-me')"))

    _migrate_legacy_hermes_schema_to_orchestrator(engine)

    assert "hermes_events" in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT legacy_only FROM hermes_events WHERE event_uid='legacy'"
        )).scalar_one() == "preserve-me"
        assert conn.execute(text("SELECT count(*) FROM orchestrator_events")).scalar_one() == 0
