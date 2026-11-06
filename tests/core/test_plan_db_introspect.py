"""N5 phase 3: connection test + read-only introspection. Fakes only for the drivers (no real database
server is contacted or started); a throwaway TCP listener proves loopback pinning and timeouts. Secrets live
in the session store only (no keychain is touched by this file)."""
import json
import logging
import re
import socket
import threading
import time
from pathlib import Path

import pytest

from distr.core.planning import db_discovery as dd, db_introspect as di, db_links as dl, db_secrets as ds, service
from tests.core.test_plan_db_discovery import RESOLVE
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401

SECRET = "Intro$pect secret 9"
REAL_ASSERT = dd.assert_loopback_host
USER_TABLES = ("customers", "orders", "secret_stuff")
CATALOG = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.I)
WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|CALL|LOCK|VACUUM|MERGE|REPLACE|LOAD|HANDLER|DO)\b", re.I)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise self.conn.error
        self.rows = self.conn.answer(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConn:
    def __init__(self, engine, answers, fail_on=None, error=None):
        self.engine, self.answers, self.fail_on, self.error = engine, answers, fail_on, error
        self.statements, self.rolled_back, self.closed, self.committed = [], 0, 0, 0

    def cursor(self):
        return FakeCursor(self)

    def answer(self, sql, params):
        for key, rows in self.answers.items():
            if sql == key:
                return rows(params) if callable(rows) else rows
        return []

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1

    def commit(self):  # must never be called
        self.committed += 1


PG_ANSWERS = {
    di.PG_VERSION: [("160004",)],
    di.PG_COUNT: [(3,)],
    di.PG_TABLES: lambda p: [("public", "customers", 1200), ("public", "orders", -1), ("public", "secret_stuff", 4)][: p[2]],
    di.PG_COLUMNS: [
        ("public", "customers", "id", 1, "integer", "int4", None, 32, 0, "NO", True),
        ("public", "customers", "email", 2, "character varying", "varchar", 255, None, None, "NO", False),
        ("public", "customers", "balance", 3, "numeric", "numeric", None, 12, 2, "YES", True),
        ("public", "customers", "tags", 4, "ARRAY", "_text", None, None, None, "YES", False),
        ("public", "orders", "id", 1, "bigint", "int8", None, 64, 0, "NO", True),
        ("public", "orders", "customer_id", 2, "integer", "int4", None, 32, 0, "NO", False),
        ("public", "orders", "placed_at", 3, "timestamp with time zone", "timestamptz", None, None, None, "NO", True),
        ("public", "orders", "paid", 4, "boolean", "bool", None, None, None, "NO", True),
        ("public", "orders", "meta", 5, "jsonb", "jsonb", None, None, None, "YES", False),
    ],
    di.PG_CONSTRAINTS: [
        ("public", "customers", "customers_pkey", "p", ["id"], None, None, []),
        ("public", "customers", "customers_email_key", "u", ["email"], None, None, []),
        ("public", "orders", "orders_pkey", "p", ["id"], None, None, []),
        ("public", "orders", "orders_customer_fk", "f", ["customer_id"], "public", "customers", ["id"]),
    ],
    di.PG_INDEXES: [("public", "customers", "customers_pkey", True, True, ["id"]), ("public", "orders", "orders_placed_idx", False, False, ["placed_at"])],
}
MY_NAMES = lambda n: ", ".join(["%s"] * n)  # noqa: E731
MY_ANSWERS = {
    di.MY_VERSION: [("8.4.2",)],
    di.MY_COUNT: [(2,)],
    di.MY_TABLES: [("customers", 50), ("orders", 900)],
    di.MY_COLUMNS.format(names=MY_NAMES(2)): [
        ("customers", "id", 1, "int", "int unsigned", None, 10, 0, "NO", False),
        ("customers", "active", 2, "tinyint", "tinyint(1)", None, 3, 0, "NO", True),
        ("customers", "kind", 3, "enum", "enum('retail','trade')", 6, None, None, "NO", True),
        ("orders", "id", 1, "bigint", "bigint", None, 19, 0, "NO", False),
        ("orders", "customer_id", 2, "int", "int unsigned", None, 10, 0, "NO", False),
        ("orders", "total", 3, "decimal", "decimal(10,2)", None, 10, 2, "YES", False),
        ("orders", "notes", 4, "longtext", "longtext", 4294967295, None, None, "YES", False),
        ("orders", "made", 5, "datetime", "datetime", None, None, None, "NO", True),
    ],
    di.MY_CONSTRAINTS.format(names=MY_NAMES(2)): [
        ("customers", "PRIMARY", "PRIMARY KEY", "id", 1, None, None, None),
        ("orders", "PRIMARY", "PRIMARY KEY", "id", 1, None, None, None),
        ("orders", "orders_customer_fk", "FOREIGN KEY", "customer_id", 1, "shop", "customers", "id"),
    ],
    di.MY_INDEXES.format(names=MY_NAMES(2)): [("customers", "PRIMARY", 0, 1, "id"), ("orders", "PRIMARY", 0, 1, "id"), ("orders", "orders_customer_fk", 1, 1, "customer_id")],
}


@pytest.fixture
def ws(plan_workspace_db, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    resolve_calls = []
    def pinned(host, resolver=None):
        resolve_calls.append(host)
        return REAL_ASSERT(host, resolver=RESOLVE)
    monkeypatch.setattr(dl, "assert_loopback_host", pinned)
    monkeypatch.setattr(dd, "assert_loopback_host", pinned)
    ds.set_store(ds.SecretStore(platform="linux", service="decisions-db-link-test"))      # session store only
    workspace = service.ensure_workspace(board_key="decisions:65", board_provider="decisions", board_name="Introspect", project_id=plan_workspace_db["project_id"])
    yield workspace, resolve_calls
    ds.set_store(None)
    ds.clear_session_secrets()


def _link(ws, engine="postgres", **extra):
    workspace, _ = ws
    link = dl.create_link(workspace["id"], {"engine": engine, "host": "127.0.0.1", "database": "shop", "username": "dev", **extra})
    dl.set_secret(workspace["id"], link["id"], SECRET)
    return workspace["id"], link["id"]


def _recorder(engine, answers, **kw):
    made = []
    def connector(target, port, database, username, secret):
        made.append({"target": target, "port": port, "database": database, "username": username, "secret": secret})
        conn = FakeConn(engine, answers, **kw)
        made[-1]["conn"] = conn
        return conn
    return made, {engine: connector}


def _assert_read_only(conn, user_values=()):
    assert conn.statements, "no statements"
    first = conn.statements[0][0]
    assert first in ("SET TRANSACTION READ ONLY", "SET SESSION TRANSACTION READ ONLY")
    for sql, params in conn.statements:
        head = sql.split(None, 1)[0].upper()
        assert head in ("SELECT", "SET", "SHOW"), sql
        if head == "SET":
            assert re.fullmatch(r"SET (SESSION )?TRANSACTION READ ONLY", sql), sql
        assert not WRITE.search(sql), sql
        for relation in CATALOG.findall(sql):
            if relation.lower().startswith(("unnest",)):
                continue
            assert relation.lower().startswith(("pg_catalog.", "information_schema.")), (relation, sql)
        for value in (*USER_TABLES, *user_values):
            assert value not in sql, (value, sql)                                  # values are bound, never inlined
        assert ";" not in sql
    assert conn.rolled_back >= 1 and conn.closed >= 1 and conn.committed == 0


def test_postgres_introspection_shape_and_read_only(ws, caplog):
    wid, lid = _link(ws)
    made, connectors = _recorder("postgres", PG_ANSWERS)
    out = di.introspect_link(wid, lid, table_filter="cust", connectors=connectors)
    conn = made[0]["conn"]
    _assert_read_only(conn, user_values=("cust",))
    assert made[0]["target"] == {"address": "127.0.0.1"} and made[0]["port"] == 5432 and made[0]["secret"] == SECRET
    assert out["total_tables"] == 3 and out["returned"] == 3 and out["truncated"] is False and out["row_counts"] == "estimates"
    t = {x["name"]: x for x in out["tables"]}
    assert t["customers"]["rows_estimate"] == 1200 and t["orders"]["rows_estimate"] is None        # -1 = never analysed
    cols = {c["name"]: c for c in t["customers"]["columns"]}
    assert cols["id"] == {"name": "id", "position": 1, "engine_type": "int4", "type": "int", "nullable": False, "has_default": True, "pk": True, "unique": True, "fk": None}
    assert cols["email"]["type"] == "varchar(255)" and cols["email"]["unique"] is True
    assert cols["balance"]["type"] == "numeric(12,2)" and cols["tags"]["type"] == "text" and cols["tags"]["engine_type"] == "_text"
    ocols = {c["name"]: c for c in t["orders"]["columns"]}
    assert ocols["customer_id"]["fk"] == {"schema": "public", "table": "customers", "column": "id"}
    assert [ocols[k]["type"] for k in ("id", "placed_at", "paid", "meta")] == ["bigint", "timestamptz", "boolean", "jsonb"]
    assert t["orders"]["indexes"] == [{"name": "orders_placed_idx", "unique": False, "primary": False, "columns": ["placed_at"]}]
    assert t["secret_stuff"]["columns"] == []
    params = [p for _, p in conn.statements if p]
    assert (["public"], "%cust%") in params and (["public"], "%cust%", 300) in params
    assert SECRET not in json.dumps(out) and SECRET not in caplog.text
    row = dl.list_links(wid)["items"][0]
    assert row["status"] == "ok" and row["last_error"] == "" and row["last_introspected_at"]


def test_mysql_introspection_shape_and_read_only(ws, caplog):
    wid, lid = _link(ws, engine="mysql")
    made, connectors = _recorder("mysql", MY_ANSWERS)
    out = di.introspect_link(wid, lid, connectors=connectors)
    conn = made[0]["conn"]
    _assert_read_only(conn)
    assert conn.statements[0][0] == "SET SESSION TRANSACTION READ ONLY"
    assert made[0]["port"] == 3306
    t = {x["name"]: x for x in out["tables"]}
    c = {x["name"]: x for x in t["customers"]["columns"]}
    assert (c["id"]["type"], c["active"]["type"], c["kind"]["type"]) == ("int", "boolean", "enum(retail,trade)")
    o = {x["name"]: x for x in t["orders"]["columns"]}
    assert (o["total"]["type"], o["notes"]["type"], o["made"]["type"]) == ("decimal(10,2)", "text", "datetime")
    assert o["customer_id"]["fk"] == {"schema": "shop", "table": "customers", "column": "id"} and o["id"]["pk"] is True
    assert t["orders"]["rows_estimate"] == 900 and out["schemas"] == ["shop"]
    assert {i["name"]: i["unique"] for i in t["orders"]["indexes"]} == {"PRIMARY": True, "orders_customer_fk": False}
    in_params = [p for s, p in conn.statements if "IN (" in s]
    assert all(p == ("shop", "customers", "orders") for p in in_params) and len(in_params) == 3
    assert SECRET not in json.dumps(out) and SECRET not in caplog.text


def test_mapped_types_are_all_erd_field_types():
    js = Path("distr/gui/web/static/development/planning/erd/field-types.js").read_text()
    listed = set(re.findall(r"'([a-z]+)'", js.split("FIELD_TYPE_GROUPS", 1)[1].split("];", 1)[0]))
    assert set(di.FIELD_TYPES) == listed
    samples = [("postgres", {"udt_name": u}) for u in [*di._PG_TYPES, "numeric", "varchar", "bpchar", "geometry", "_int4"]]
    samples += [("mysql", {"data_type": d, "column_type": d}) for d in [*di._MY_TYPES, "decimal", "geometry", "bit"]]
    for engine, col in samples:
        base = re.match(r"^[a-z]+", di.map_type(engine, col)).group(0)
        assert base in listed, (engine, col)


def test_cap_filter_and_limit(ws):
    wid, lid = _link(ws)
    made, connectors = _recorder("postgres", {**PG_ANSWERS, di.PG_COUNT: [(4210,)]})
    out = di.introspect_link(wid, lid, table_filter="a_b", limit=2, connectors=connectors)
    assert out["total_tables"] == 4210 and out["returned"] == 2 and out["truncated"] is True
    assert (["public"], "%a\\_b%", 2) in [p for _, p in made[0]["conn"].statements]
    for bad in ({"table_filter": "x'; DROP TABLE t"}, {"table_filter": "x" * 65}, {"limit": 301}, {"limit": 0}, {"limit": "5"}):
        with pytest.raises(ValueError):
            di.introspect_link(wid, lid, connectors=connectors, **bad)
    assert len(made) == 1                                                       # invalid input never connects


def test_too_large(ws, monkeypatch):
    wid, lid = _link(ws)
    monkeypatch.setattr(di, "MAX_COLUMNS", 3)
    made, connectors = _recorder("postgres", PG_ANSWERS)
    with pytest.raises(di.IntrospectError) as info:
        di.introspect_link(wid, lid, connectors=connectors)
    assert info.value.code == "too_large" and made[0]["conn"].rolled_back == 1
    assert dl.list_links(wid)["items"][0]["status"] == "too_large"


def test_test_link_ok(ws):
    wid, lid = _link(ws)
    made, connectors = _recorder("postgres", PG_ANSWERS)
    out = di.test_link(wid, lid, connectors=connectors)
    assert out == {"ok": True, "code": "ok", "link_id": lid, "engine": "postgres", "server_version": "160004"}
    _assert_read_only(made[0]["conn"])
    assert [s for s, _ in made[0]["conn"].statements] == ["SET TRANSACTION READ ONLY", di.PG_VERSION]
    row = dl.list_links(wid)["items"][0]
    assert row["status"] == "ok" and row["last_introspected_at"] is None            # a test is not an introspection


class PgError(Exception):
    pass


class MyError(Exception):
    pass


@pytest.mark.parametrize("engine,exc,code", [
    ("postgres", PgError(f'connection failed: FATAL: password authentication failed for user "dev" password={SECRET}'), "auth_failed"),
    ("postgres", PgError('connection failed: FATAL: database "shop" does not exist'), "db_not_found"),
    ("postgres", PgError("connection timeout expired"), "timeout"),
    ("postgres", PgError("connection failed: Connection refused"), "unreachable"),
    ("postgres", ConnectionRefusedError(61, "refused"), "unreachable"),
    ("mysql", MyError(1045, f"Access denied for user 'dev'@'localhost' (using password: YES) {SECRET}"), "auth_failed"),
    ("mysql", MyError(1049, "Unknown database 'shop'"), "db_not_found"),
    ("mysql", MyError(2003, "Can't connect to MySQL server on '127.0.0.1' ([Errno 61] Connection refused)"), "unreachable"),
    ("mysql", MyError(2013, "Lost connection to MySQL server during query (timed out)"), "timeout"),
    ("postgres", RuntimeError(("weird " + SECRET + " postgresql://" + "dev:" + SECRET + "@127.0.0.1/shop")), "failed"),
])
def test_errors_map_to_stable_codes_without_leaks(ws, caplog, engine, exc, code):
    wid, lid = _link(ws, engine=engine)
    def connector(*args):
        raise exc
    with pytest.raises(di.IntrospectError) as info:
        di.test_link(wid, lid, connectors={engine: connector})
    err = info.value
    assert err.code == code and str(err) == di.MESSAGES[code]
    assert err.__cause__ is None and err.__suppress_context__ is True
    assert SECRET not in str(err) and "postgresql://" not in str(err) and SECRET not in caplog.text
    row = dl.list_links(wid)["items"][0]
    assert row["status"] == code and row["last_error"] == di.MESSAGES[code]


def test_query_failure_still_rolls_back(ws):
    wid, lid = _link(ws)
    made, connectors = _recorder("postgres", PG_ANSWERS, fail_on="information_schema.columns", error=PgError("canceling statement due to statement timeout"))
    with pytest.raises(di.IntrospectError) as info:
        di.introspect_link(wid, lid, connectors=connectors)
    assert info.value.code == "timeout"
    assert made[0]["conn"].rolled_back == 1 and made[0]["conn"].closed == 1


def test_non_loopback_never_connects(ws, monkeypatch):
    wid, lid = _link(ws)
    monkeypatch.setattr(dd, "assert_loopback_host", lambda host, resolver=None: REAL_ASSERT("db.example.com", resolver=RESOLVE))
    made, connectors = _recorder("postgres", PG_ANSWERS)
    with pytest.raises(di.IntrospectError) as info:
        di.test_link(wid, lid, connectors=connectors)
    assert info.value.code == "non_loopback" and made == []


def test_connects_to_the_address_assert_loopback_returned(ws, monkeypatch):
    wid, lid = _link(ws)
    monkeypatch.setattr(dd, "assert_loopback_host", lambda host, resolver=None: ["127.0.0.9"])   # what the check returned
    made, connectors = _recorder("postgres", PG_ANSWERS)
    di.test_link(wid, lid, connectors=connectors)
    assert made[0]["target"] == {"address": "127.0.0.9"}


def test_unix_socket_target(ws):
    wid, lid = _link(ws, host="unix:/tmp/.s.PGSQL.5433")
    made, connectors = _recorder("postgres", PG_ANSWERS)
    di.test_link(wid, lid, connectors=connectors)
    assert made[0]["target"] == {"socket": "/tmp/.s.PGSQL.5433"} and made[0]["port"] is None


def test_no_secret_connects_without_password(ws):
    workspace, _ = ws
    link = dl.create_link(workspace["id"], {"engine": "postgres", "host": "127.0.0.1", "database": "trust", "username": "dev"})
    made, connectors = _recorder("postgres", PG_ANSWERS)
    di.test_link(workspace["id"], link["id"], connectors=connectors)
    assert made[0]["secret"] is None


def test_driver_missing(ws, monkeypatch):
    wid, lid = _link(ws)
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (_ for _ in ()).throw(ImportError("x")) if name == "psycopg" else real(name, *a, **k))
    with pytest.raises(di.IntrospectError) as info:
        di.test_link(wid, lid)
    assert info.value.code == "driver_missing"


# ---- Real drivers against a throwaway TCP listener (no database server): pinning + timeouts --------------
@pytest.fixture
def silent_listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    accepted, stop = [], threading.Event()
    def run():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, addr = server.accept()
                accepted.append((conn, addr))                                      # never answers
            except OSError:
                continue
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    yield server.getsockname()[1], accepted
    stop.set(); thread.join(2)
    for conn, _ in accepted:
        conn.close()
    server.close()


@pytest.mark.parametrize("engine", ["postgres", "mysql"])
def test_real_driver_times_out_on_pinned_loopback_listener(ws, silent_listener, engine, caplog):
    port, accepted = silent_listener
    wid, lid = _link(ws, engine=engine, port=port, host="localhost")
    started = time.monotonic()
    with pytest.raises(di.IntrospectError) as info:
        di.test_link(wid, lid)
    elapsed = time.monotonic() - started
    assert info.value.code == "timeout", info.value.code
    assert elapsed < di.CONNECT_TIMEOUT + di.READ_TIMEOUT + 3, elapsed
    assert accepted and accepted[0][1][0] == "127.0.0.1"                         # connected to the pinned literal
    assert SECRET not in caplog.text


def test_real_driver_refused_port_is_unreachable(ws):
    probe = socket.socket(); probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]; probe.close()
    wid, lid = _link(ws, engine="mysql", port=port)
    with pytest.raises(di.IntrospectError) as info:
        di.test_link(wid, lid)
    assert info.value.code == "unreachable"
