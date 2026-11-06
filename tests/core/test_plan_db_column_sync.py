"""Adding a Data-area field creates one nullable column on the linked database.

The live customer database is not contacted. SQLite proves the SQL. A fake driver proves the
existing link connection is the one that runs it, and that row reads stay read-only.
"""
import sqlite3

import pytest

from distr.core.planning import db_discovery as dd, db_erd as de, db_introspect as di, db_links as dl, db_secrets as ds, service
from tests.core.test_plan_db_discovery import RESOLVE
from tests.core.test_plan_db_introspect import PG_ANSWERS, REAL_ASSERT, SECRET, FakeConn, FakeCursor
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401


class DescCursor(FakeCursor):
    def execute(self, sql, params=None):
        super().execute(sql, params)
        self.description = [("id",), ("note",)] if str(sql).startswith("SELECT *") else [("count",)]


class DescConn(FakeConn):
    def cursor(self):
        return DescCursor(self)


@pytest.fixture
def env(plan_workspace_db, monkeypatch):
    pinned = lambda host, resolver=None: REAL_ASSERT(host, resolver=RESOLVE)  # noqa: E731
    monkeypatch.setattr(dl, "assert_loopback_host", pinned)
    monkeypatch.setattr(dd, "assert_loopback_host", pinned)
    ds.set_store(ds.SecretStore(platform="linux", service="decisions-db-column-test"))
    connectors = {"postgres": lambda *a: FakeConn("postgres", dict(PG_ANSWERS)), "mysql": lambda *a: FakeConn("mysql", {})}
    ws = service.ensure_workspace(board_key="decisions:69", board_provider="decisions", board_name="Columns", project_id=plan_workspace_db["project_id"])
    link = dl.create_link(ws["id"], {"engine": "postgres", "host": "127.0.0.1", "database": "shop", "username": "dev", "label": "Shop"})
    dl.set_secret(ws["id"], link["id"], SECRET)
    yield {"ws": ws, "link": link, "connectors": connectors}
    ds.set_store(None)
    ds.clear_session_secrets()


def test_sqlite_add_column_and_page_do_not_drop_the_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (id integer)")
    conn.executemany("INSERT INTO orders (id) VALUES (?)", [(i,) for i in range(1, 6)])
    injected = 'x"; DROP TABLE orders; --'
    conn.execute(de.add_column_sql("sqlite", "main", "orders", "note", "string"))
    conn.execute(de.add_column_sql("sqlite", "main", "orders", injected, "text"))
    names = [row[1] for row in conn.execute("PRAGMA table_info(orders)")]
    assert names[0] == "id" and "note" in names and injected in names
    assert [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["orders"]
    count_sql, page_sql = de.rows_sql("sqlite", "main", "orders")
    assert ";" not in count_sql and ";" not in page_sql
    assert conn.execute(count_sql).fetchone()[0] == 5
    assert [row[0] for row in conn.execute(page_sql, (2, 2))] == [3, 4]
    with pytest.raises(ValueError):
        de.add_column_sql("sqlite", "main", "orders", "note", "text;drop table orders")


def test_write_options_are_not_read_only():
    assert "default_transaction_read_only=on" in di.postgres_options(True)
    assert "default_transaction_read_only" not in di.postgres_options(False)
    assert "statement_timeout" in di.postgres_options(False)


def test_add_column_uses_the_link_and_updates_the_snapshot(env, caplog):
    de.refresh_snapshot(env["ws"]["id"], env["link"]["id"], connectors=env["connectors"])
    made = []

    def connector(*args):
        conn = FakeConn("postgres", {})
        made.append((args, conn))
        return conn

    out = de.add_linked_column(env["ws"]["id"], env["link"]["id"], "public", "orders", "note", "string", connectors={"postgres": connector})
    assert out == {"ok": True, "already_present": False, "schema": "public", "table": "orders", "column": "note", "field_type": "string"}
    assert made[0][0][-1] is False
    conn = made[0][1]
    assert conn.committed == 1
    assert [sql for sql, _params in conn.statements if sql.startswith("ALTER TABLE")] == [de.add_column_sql("postgres", "public", "orders", "note", "string")]
    assert "SET TRANSACTION READ ONLY" not in [sql for sql, _params in conn.statements]
    assert SECRET not in caplog.text
    orders = next(table for table in de.read_snapshot(env["ws"]["id"], env["link"]["id"])["tables"] if table["name"] == "orders")
    assert any(column["name"] == "note" and column["type"] == "string" and column["nullable"] for column in orders["columns"])


def test_existing_column_and_bad_type_do_not_alter(env):
    made = []

    def connector(*args):
        conn = FakeConn("postgres", {de.COLUMN_EXISTS: [(1,)]})
        made.append(conn)
        return conn

    out = de.add_linked_column(env["ws"]["id"], env["link"]["id"], "public", "orders", "note", "string", connectors={"postgres": connector})
    assert out["already_present"] is True
    assert not any(sql.startswith("ALTER") for sql, _params in made[0].statements)
    called = []
    with pytest.raises(ValueError):
        de.add_linked_column(env["ws"]["id"], env["link"]["id"], "public", "orders", "note", "text;drop", connectors={"postgres": lambda *args: called.append(1)})
    assert called == []


def test_row_page_stays_read_only(env):
    count_sql, page_sql = de.rows_sql("postgres", "public", "orders")
    answers = {count_sql: [(5,)], page_sql: [(3, "n3"), (4, b"abcd")]}
    made = []

    def connector(target, port, database, username, secret):
        conn = DescConn("postgres", answers)
        made.append(conn)
        return conn

    out = de.read_linked_rows(env["ws"]["id"], env["link"]["id"], "public", "orders", page=2, page_size=2, connectors={"postgres": connector})
    assert out["columns"] == ["id", "note"]
    assert out["rows"] == [[3, "n3"], [4, "4 bytes"]]
    assert out["page"] == 2 and out["page_size"] == 2 and out["total"] == 5
    assert made[0].committed == 0 and made[0].statements[0][0] == "SET TRANSACTION READ ONLY"
    assert made[0].statements[-1] == (page_sql, (2, 2))
    assert "2" not in page_sql
