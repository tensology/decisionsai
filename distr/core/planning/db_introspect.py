"""Plan DB links (N5 phase 3): connection test + read-only catalog introspection.

Guarantees:
- Loopback only: ``assert_loopback_host()`` runs on every call and the driver connects to the ADDRESS IT
  RETURNS (an IP literal, never re-resolved). Unix sockets must sit in the allowed socket folders.
- The secret is read from db_secrets inside the call only; it is never logged, returned or stored, and
  error messages are fixed strings per code (never the driver text, the secret or a connection string).
- Read-only sessions by default: Postgres ``default_transaction_read_only=on`` + ``SET TRANSACTION READ ONLY``,
  autocommit off; MySQL ``SET SESSION TRANSACTION READ ONLY``. Those calls end with rollback + close.
  ``readonly=False`` is only for one additive ``ADD COLUMN`` (commit, then close). It is not used by test or introspect.
- Only parameterised catalog queries (pg_catalog / information_schema): no SELECT from user tables,
  no DDL/DML, no row data. Row counts are catalog ESTIMATES (pg reltuples, MySQL TABLES.TABLE_ROWS).
- The result is returned only (snapshot cache and ERD mapping are phase 4); only the link row's
  status / last_error / last_introspected_at are updated.
"""
from __future__ import annotations

import logging
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any, Callable

from distr.core.planning import db_discovery
from distr.core.planning.db_discovery import NonLoopbackHost
from distr.core.planning.db_secrets import get_store

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 3          # seconds (psycopg connect_timeout / PyMySQL connect_timeout)
STATEMENT_TIMEOUT_MS = 5000  # Postgres statement_timeout
READ_TIMEOUT = 5             # PyMySQL read/write timeout, seconds
MAX_TABLES = 300
MAX_COLUMNS = 20000
_FILTER = re.compile(r"^[A-Za-z0-9_$\-. ]{0,64}$")
SOCKET_DIRS = ("/tmp/", "/private/tmp/", "/var/run/mysqld/", "/opt/homebrew/var/", "/usr/local/var/")

MESSAGES = {
    "unreachable": "Could not reach the database on this Mac. Is the server running on that port or socket?",
    "timeout": "The database did not answer in time.",
    "auth_failed": "The database rejected the username or password.",
    "non_loopback": "Only databases on this Mac (127.0.0.1, ::1 or a local socket) can be used.",
    "driver_missing": "The database driver is not installed in this build.",
    "too_large": "The schema is too large to read in one go. Use a table-name filter.",
    "db_not_found": "That database does not exist on the server.",
    "failed": "Reading the database schema failed.",
    "write_failed": "Could not change the database. Nothing else was changed.",
}
HTTP_STATUS = {"non_loopback": 422, "driver_missing": 503, "too_large": 413, "unreachable": 502, "timeout": 504,
               "auth_failed": 502, "db_not_found": 502, "failed": 502, "write_failed": 502}

# The ERD editor's type list (distr/gui/web/static/development/planning/erd/field-types.js FIELD_TYPE_GROUPS).
FIELD_TYPES = ("int", "smallint", "bigint", "serial", "bigserial", "decimal", "numeric", "float", "double", "real", "money",
               "varchar", "char", "text", "string", "date", "time", "datetime", "timestamp", "timestamptz", "interval",
               "boolean", "uuid", "json", "jsonb", "blob", "bytea", "enum")


class IntrospectError(Exception):
    """A stable, secret-free failure: ``code`` is one of MESSAGES; ``str()`` is the fixed message."""

    def __init__(self, code: str):
        self.code = code if code in MESSAGES else "failed"
        super().__init__(MESSAGES[self.code])

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]


# ---- SQL: every statement is a constant; values are always bound parameters -------------------------
PG_SESSION = ("SET TRANSACTION READ ONLY",)
PG_VERSION = "SELECT current_setting('server_version_num')"
PG_TABLE_WHERE = ("FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                  "WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(%s) AND c.relname ILIKE %s")
PG_COUNT = f"SELECT count(*) {PG_TABLE_WHERE}"
PG_TABLES = f"SELECT n.nspname, c.relname, c.reltuples::bigint {PG_TABLE_WHERE} ORDER BY n.nspname, c.relname LIMIT %s"
PG_COLUMNS = ("SELECT table_schema, table_name, column_name, ordinal_position, data_type, udt_name, character_maximum_length, "
              "numeric_precision, numeric_scale, is_nullable, (column_default IS NOT NULL) AS has_default "
              "FROM information_schema.columns WHERE table_schema = ANY(%s) AND table_name = ANY(%s) "
              "ORDER BY table_schema, table_name, ordinal_position")
PG_CONSTRAINTS = ("SELECT n.nspname, c.relname, con.conname, con.contype, "
                  "ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) "
                  "JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum ORDER BY k.ord)::text[], "
                  "fn.nspname, fc.relname, "
                  "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord) "
                  "JOIN pg_catalog.pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum ORDER BY k.ord)::text[] "
                  "FROM pg_catalog.pg_constraint con JOIN pg_catalog.pg_class c ON c.oid = con.conrelid "
                  "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                  "LEFT JOIN pg_catalog.pg_class fc ON fc.oid = con.confrelid LEFT JOIN pg_catalog.pg_namespace fn ON fn.oid = fc.relnamespace "
                  "WHERE con.contype IN ('p', 'f', 'u') AND n.nspname = ANY(%s) AND c.relname = ANY(%s) ORDER BY n.nspname, c.relname, con.conname")
PG_INDEXES = ("SELECT n.nspname, t.relname, i.relname, ix.indisunique, ix.indisprimary, "
              "ARRAY(SELECT a.attname FROM unnest(ix.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord) "
              "JOIN pg_catalog.pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum ORDER BY k.ord)::text[] "
              "FROM pg_catalog.pg_index ix JOIN pg_catalog.pg_class t ON t.oid = ix.indrelid "
              "JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace "
              "WHERE n.nspname = ANY(%s) AND t.relname = ANY(%s) ORDER BY n.nspname, t.relname, i.relname")

MY_SESSION = ("SET SESSION TRANSACTION READ ONLY",)
MY_VERSION = "SELECT VERSION()"
MY_TABLE_WHERE = "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME LIKE %s"
MY_COUNT = f"SELECT COUNT(*) {MY_TABLE_WHERE}"
MY_TABLES = f"SELECT TABLE_NAME, TABLE_ROWS {MY_TABLE_WHERE} ORDER BY TABLE_NAME LIMIT %s"
MY_COLUMNS = ("SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, COLUMN_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, "
              "NUMERIC_SCALE, IS_NULLABLE, (COLUMN_DEFAULT IS NOT NULL) AS HAS_DEFAULT FROM information_schema.COLUMNS "
              "WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({names}) ORDER BY TABLE_NAME, ORDINAL_POSITION")
MY_CONSTRAINTS = ("SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, t.CONSTRAINT_TYPE, k.COLUMN_NAME, k.ORDINAL_POSITION, "
                  "k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
                  "FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.TABLE_CONSTRAINTS t "
                  "ON t.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND t.TABLE_NAME = k.TABLE_NAME AND t.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
                  "WHERE k.TABLE_SCHEMA = %s AND k.TABLE_NAME IN ({names}) ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION")
MY_INDEXES = ("SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
              "WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({names}) ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX")


# ---- Type mapping into the ERD field-type list ---------------------------------------------------------
_PG_TYPES = {"int2": "smallint", "int4": "int", "int8": "bigint", "float4": "real", "float8": "double", "money": "money",
             "text": "text", "citext": "text", "name": "text", "date": "date", "time": "time", "timetz": "time",
             "timestamp": "timestamp", "timestamptz": "timestamptz", "interval": "interval", "bool": "boolean",
             "uuid": "uuid", "json": "json", "jsonb": "jsonb", "bytea": "bytea"}
_MY_TYPES = {"smallint": "smallint", "mediumint": "int", "int": "int", "integer": "int", "bigint": "bigint", "float": "float",
             "double": "double", "real": "double", "char": "char", "varchar": "varchar", "tinytext": "text", "text": "text",
             "mediumtext": "text", "longtext": "text", "date": "date", "time": "time", "datetime": "datetime",
             "timestamp": "timestamp", "year": "int", "json": "json", "tinyblob": "blob", "blob": "blob", "mediumblob": "blob",
             "longblob": "blob", "binary": "blob", "varbinary": "blob", "enum": "enum", "set": "text", "tinyint": "smallint"}


def map_type(engine: str, col: dict) -> str:
    """Engine column -> an ERD field type (base from FIELD_TYPES, optional parameter)."""
    if engine == "postgres":
        udt = str(col.get("udt_name") or "").lower()
        if udt == "numeric":
            p, s = col.get("numeric_precision"), col.get("numeric_scale")
            return f"numeric({int(p)},{int(s or 0)})" if p else "numeric"
        if udt in ("varchar", "bpchar"):
            n = col.get("character_maximum_length")
            base = "varchar" if udt == "varchar" else "char"
            return f"{base}({int(n)})" if n else base
        return _PG_TYPES.get(udt, "text")
    data_type = str(col.get("data_type") or "").lower()
    column_type = str(col.get("column_type") or "").lower()
    if data_type in ("tinyint", "bit") and re.match(r"^(tinyint|bit)\(1\)", column_type):
        return "boolean"
    if data_type in ("decimal", "numeric"):
        p, s = col.get("numeric_precision"), col.get("numeric_scale")
        return f"decimal({int(p)},{int(s or 0)})" if p else "decimal"
    if data_type in ("varchar", "char"):
        n = col.get("character_maximum_length")
        return f"{data_type}({int(n)})" if n else data_type
    if data_type == "enum":
        values = re.findall(r"'((?:[^']|'')*)'", column_type)
        safe = [v for v in values if re.match(r"^[A-Za-z0-9_\-]{1,40}$", v)]
        return f"enum({','.join(safe)})" if safe and len(safe) == len(values) and len(safe) <= 50 else "enum"
    return _MY_TYPES.get(data_type, "text")


# ---- Connections ---------------------------------------------------------------------------------------
def _target(host: str) -> dict[str, str]:
    """Pinned target: {'address': <IP literal>} or {'socket': <path>}; raises IntrospectError('non_loopback')."""
    if host.startswith("unix:"):
        path = host[5:]
        if not path.startswith("/") or ".." in path.split("/") or not path.startswith(SOCKET_DIRS):
            raise IntrospectError("non_loopback")
        return {"socket": path}
    try:
        addresses = db_discovery.assert_loopback_host(host)
    except NonLoopbackHost:
        raise IntrospectError("non_loopback") from None
    return {"address": addresses[0]}


def postgres_options(readonly: bool = True) -> str:
    """Session options. Read-only is the default; column add turns that flag off and still sets a statement timeout."""
    timeout = f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"
    if readonly:
        return f"-c default_transaction_read_only=on {timeout}"
    return timeout


def connect_postgres(target: dict, port: int | None, database: str, username: str, secret: str | None, readonly: bool = True):
    try:
        import psycopg
    except ImportError:
        raise IntrospectError("driver_missing") from None
    kwargs: dict[str, Any] = {"dbname": database, "user": username, "connect_timeout": CONNECT_TIMEOUT, "autocommit": False,
                              "application_name": "decisions-plan-introspect",
                              "options": postgres_options(readonly)}
    if "socket" in target:
        directory, name = os.path.split(target["socket"])
        found = re.match(r"^\.s\.PGSQL\.(\d{1,5})$", name)
        kwargs.update(host=directory if found else target["socket"], port=int(found.group(1)) if found else (port or 5432))
    else:
        kwargs.update(hostaddr=target["address"], host=target["address"], port=port or 5432)
    if secret:
        kwargs["password"] = secret
    return psycopg.connect(**kwargs)


def connect_mysql(target: dict, port: int | None, database: str, username: str, secret: str | None, readonly: bool = True):
    try:
        import pymysql
    except ImportError:
        raise IntrospectError("driver_missing") from None
    kwargs: dict[str, Any] = {"user": username, "password": secret or "", "database": database, "connect_timeout": CONNECT_TIMEOUT,
                              "read_timeout": READ_TIMEOUT, "write_timeout": READ_TIMEOUT, "autocommit": False,
                              "charset": "utf8mb4", "local_infile": False, "client_flag": 0}
    if "socket" in target:
        kwargs["unix_socket"] = target["socket"]
    else:
        kwargs.update(host=target["address"], port=port or 3306)
    return pymysql.connect(**kwargs)


CONNECTORS: dict[str, Callable] = {"postgres": connect_postgres, "mysql": connect_mysql}


def classify(engine: str, exc: BaseException) -> str:
    """Driver/socket exception -> stable code. Only the class, the MySQL error number and well-known
    fragments are inspected; the text is never surfaced."""
    if isinstance(exc, IntrospectError):
        return exc.code
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "unreachable"
    args = getattr(exc, "args", ()) or ()
    number = args[0] if args and isinstance(args[0], int) else None
    text = " ".join(str(a) for a in args).lower() if args else str(exc).lower()
    if engine == "mysql" and number is not None:
        if number in (1044, 1045, 1698, 2059, 1251):
            return "auth_failed"
        if number == 1049:
            return "db_not_found"
        if number == 2013 or "timed out" in text:
            return "timeout"
        if number in (2002, 2003, 2005, 2006):
            return "unreachable"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "password" in text or "authentication" in text or "no pg_hba.conf entry" in text or "role" in text and "does not exist" in text:
        return "auth_failed"
    if "database" in text and "does not exist" in text:
        return "db_not_found"
    if "refused" in text or "could not connect" in text or "connection failed" in text or "no such file" in text or "can't connect" in text:
        return "unreachable"
    return "failed"


def _session():
    from distr.core.planning import service
    return service.get_session()


def _read_link(workspace_id: int, link_id: int) -> dict[str, Any]:
    import json
    from distr.core.planning.db_links import _link
    with _session() as db:
        row = _link(db, workspace_id, link_id)
        return {"id": int(row.id), "engine": row.engine, "host": row.host, "port": row.port, "database": row.database,
                "username": row.username, "schemas": json.loads(row.schemas or "[]"), "has_ref": bool(row.secret_ref)}


def _record(workspace_id: int, link_id: int, *, code: str, introspected: bool = False) -> None:
    """Update ONLY this link row: status, last_error (fixed message) and last_introspected_at."""
    from distr.core.planning.db_links import _link
    with _session() as db:
        row = _link(db, workspace_id, link_id)
        row.status = "ok" if code == "ok" else code
        row.last_error = None if code == "ok" else MESSAGES[code]
        if introspected:
            row.last_introspected_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()


def _with_connection(workspace_id: int, link_id: int, work: Callable, *, introspected: bool, connectors=None, readonly: bool = True, kind: str | None = None):
    """Open the linked database. Read-only callers keep the 5-argument connector. A write passes ``False`` as a sixth argument."""
    link = _read_link(workspace_id, link_id)
    connector = (connectors or CONNECTORS)[link["engine"]]
    label = kind or ("introspect" if introspected else "test")
    conn = None
    try:
        target = _target(link["host"])
        secret = get_store().get(link["id"]) if link["has_ref"] else None
        try:
            args = (target, link["port"], link["database"], link["username"], secret)
            conn = connector(*args) if readonly else connector(*args, False)
        finally:
            secret = None
        session = ()
        if readonly:
            session = PG_SESSION if link["engine"] == "postgres" else MY_SESSION
        with conn.cursor() as cur:
            for statement in session:
                cur.execute(statement)
            result = work(cur, link)
        if not readonly:
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - every failure becomes a fixed, secret-free code
        code = classify(link["engine"], exc)
        if label == "add_column" and code == "failed":
            code = "write_failed"
        logger.info("plan_db_link %s failed id=%s code=%s kind=%s", label, link["id"], code, type(exc).__name__)
        _finish(conn)
        _record(workspace_id, link_id, code=code)
        raise IntrospectError(code) from None
    _finish(conn)
    _record(workspace_id, link_id, code="ok", introspected=introspected)
    logger.info("plan_db_link %s ok id=%s", label, link["id"])
    return result


def _finish(conn) -> None:
    if conn is None:
        return
    for step in ("rollback", "close"):
        try:
            getattr(conn, step)()
        except Exception:  # noqa: BLE001
            pass


def test_link(workspace_id: int, link_id: int, *, connectors=None) -> dict[str, Any]:
    def work(cur, link):
        cur.execute(PG_VERSION if link["engine"] == "postgres" else MY_VERSION)
        row = cur.fetchone()
        version = str(row[0])[:40] if row else ""
        return {"ok": True, "code": "ok", "link_id": link["id"], "engine": link["engine"], "server_version": version}
    return _with_connection(workspace_id, link_id, work, introspected=False, connectors=connectors)


def _like(filter_text: str) -> str:
    escaped = filter_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def introspect_link(workspace_id: int, link_id: int, *, table_filter: str = "", limit: int = MAX_TABLES, connectors=None) -> dict[str, Any]:
    if not isinstance(table_filter, str) or not _FILTER.match(table_filter):
        raise ValueError("filter must be up to 64 letters, digits, spaces or _ $ - .")
    if type(limit) is not int or not 1 <= limit <= MAX_TABLES:
        raise ValueError(f"limit must be a whole number from 1 to {MAX_TABLES}.")
    pattern = _like(table_filter.strip())

    def work(cur, link):
        engine = link["engine"]
        schemas = (link["schemas"] or ["public"]) if engine == "postgres" else [link["database"]]
        if engine == "postgres":
            cur.execute(PG_COUNT, (schemas, pattern))
            total = int(cur.fetchone()[0])
            cur.execute(PG_TABLES, (schemas, pattern, limit))
            tables = [{"schema": r[0], "name": r[1], "rows_estimate": int(r[2]) if r[2] is not None and int(r[2]) >= 0 else None} for r in cur.fetchall()]
        else:
            cur.execute(MY_COUNT, (schemas[0], pattern))
            total = int(cur.fetchone()[0])
            cur.execute(MY_TABLES, (schemas[0], pattern, limit))
            tables = [{"schema": schemas[0], "name": r[0], "rows_estimate": int(r[1]) if r[1] is not None else None} for r in cur.fetchall()]
        out = {"link_id": link["id"], "engine": engine, "database": link["database"], "schemas": schemas, "filter": table_filter,
               "total_tables": total, "returned": len(tables), "truncated": total > len(tables), "tables": [],
               "row_counts": "estimates", "introspected_at": datetime.now(timezone.utc).isoformat()}
        if not tables:
            return out
        names = sorted({t["name"] for t in tables})
        by_key = {(t["schema"], t["name"]): {**t, "columns": [], "primary_key": [], "foreign_keys": [], "unique": [], "indexes": []} for t in tables}
        if engine == "postgres":
            _pg_details(cur, schemas, names, by_key)
        else:
            _my_details(cur, schemas[0], names, by_key)
        out["tables"] = list(by_key.values())
        return out
    return _with_connection(workspace_id, link_id, work, introspected=True, connectors=connectors)


def _check_size(rows) -> list:
    rows = list(rows)
    if len(rows) > MAX_COLUMNS:
        raise IntrospectError("too_large")
    return rows


def _finalise_columns(table: dict) -> None:
    pk = set(table["primary_key"])
    single_unique = {cols[0] for cols in table["unique"] if len(cols) == 1}
    fks = {fk["columns"][0]: fk for fk in table["foreign_keys"] if len(fk["columns"]) == 1}
    for col in table["columns"]:
        col["pk"] = col["name"] in pk
        col["unique"] = col["name"] in single_unique or (len(pk) == 1 and col["pk"])
        fk = fks.get(col["name"])
        col["fk"] = {"schema": fk["ref_schema"], "table": fk["ref_table"], "column": fk["ref_columns"][0]} if fk and fk["ref_columns"] else None


def _pg_details(cur, schemas, names, by_key):
    cur.execute(PG_COLUMNS, (schemas, names))
    for r in _check_size(cur.fetchall()):
        table = by_key.get((r[0], r[1]))
        if table is None:
            continue
        col = {"data_type": r[4], "udt_name": r[5], "character_maximum_length": r[6], "numeric_precision": r[7], "numeric_scale": r[8]}
        table["columns"].append({"name": r[2], "position": int(r[3]), "engine_type": str(r[5] or r[4]), "type": map_type("postgres", col),
                                 "nullable": str(r[9]).upper() == "YES", "has_default": bool(r[10])})
    cur.execute(PG_CONSTRAINTS, (schemas, names))
    for r in cur.fetchall():
        table = by_key.get((r[0], r[1]))
        if table is None:
            continue
        cols = list(r[4] or [])
        kind = r[3].decode() if isinstance(r[3], bytes) else str(r[3])
        if kind == "p":
            table["primary_key"] = cols
        elif kind == "u":
            table["unique"].append(cols)
        elif kind == "f":
            table["foreign_keys"].append({"name": r[2], "columns": cols, "ref_schema": r[5], "ref_table": r[6], "ref_columns": list(r[7] or [])})
    cur.execute(PG_INDEXES, (schemas, names))
    for r in cur.fetchall():
        table = by_key.get((r[0], r[1]))
        if table is not None:
            table["indexes"].append({"name": r[2], "unique": bool(r[3]), "primary": bool(r[4]), "columns": list(r[5] or [])})
    for table in by_key.values():
        _finalise_columns(table)


def _my_details(cur, schema, names, by_key):
    marks = ", ".join(["%s"] * len(names))
    params = (schema, *names)
    cur.execute(MY_COLUMNS.format(names=marks), params)
    for r in _check_size(cur.fetchall()):
        table = by_key.get((schema, r[0]))
        if table is None:
            continue
        col = {"data_type": r[3], "column_type": r[4], "character_maximum_length": r[5], "numeric_precision": r[6], "numeric_scale": r[7]}
        table["columns"].append({"name": r[1], "position": int(r[2]), "engine_type": str(r[4] or r[3]), "type": map_type("mysql", col),
                                 "nullable": str(r[8]).upper() == "YES", "has_default": bool(r[9])})
    cur.execute(MY_CONSTRAINTS.format(names=marks), params)
    groups: dict[tuple, dict] = {}
    for r in cur.fetchall():
        table = by_key.get((schema, r[0]))
        if table is None:
            continue
        g = groups.setdefault((r[0], r[1]), {"table": table, "type": str(r[2]).upper(), "columns": [], "ref_schema": r[5], "ref_table": r[6], "ref_columns": []})
        g["columns"].append(r[3])
        if r[7]:
            g["ref_columns"].append(r[7])
    for (table_name, name), g in groups.items():
        table = g["table"]
        if g["type"] == "PRIMARY KEY":
            table["primary_key"] = g["columns"]
        elif g["type"] == "UNIQUE":
            table["unique"].append(g["columns"])
        elif g["type"] == "FOREIGN KEY":
            table["foreign_keys"].append({"name": name, "columns": g["columns"], "ref_schema": g["ref_schema"], "ref_table": g["ref_table"], "ref_columns": g["ref_columns"]})
    cur.execute(MY_INDEXES.format(names=marks), params)
    indexes: dict[tuple, dict] = {}
    for r in cur.fetchall():
        table = by_key.get((schema, r[0]))
        if table is None:
            continue
        idx = indexes.setdefault((r[0], r[1]), {"table": table, "name": r[1], "unique": not bool(int(r[2])), "primary": r[1] == "PRIMARY", "columns": []})
        idx["columns"].append(r[4])
    for idx in indexes.values():
        idx.pop("table")["indexes"].append(idx)
    for table in by_key.values():
        _finalise_columns(table)
