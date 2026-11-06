"""Plan DB links (N5 phase 2): workspace-scoped CRUD. No connection test and no introspection here.

Passwords never touch the database: only ``secret_ref`` (an opaque store reference) is saved, and
payloads expose just ``has_secret`` / ``secret_store``. Audit log lines carry ids only.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
from typing import Any

from distr.core.planning.db_discovery import NonLoopbackHost, assert_loopback_host
from distr.core.planning.db_secrets import get_store

logger = logging.getLogger(__name__)

ENGINES = {"postgres": 5432, "mysql": 3306}
MAX_LINKS_PER_PROJECT = 8
ARCHIVED = "archived"
SOCKET_DIRS = ("/tmp/", "/private/tmp/", "/var/run/mysqld/", "/opt/homebrew/var/", "/usr/local/var/")
FIELDS = {"engine", "host", "port", "database", "username", "label", "schemas", "colour"}
_NAME = re.compile(r"^[A-Za-z0-9_$.\-]{1,63}$")
_USER = re.compile(r"^[A-Za-z0-9_.@\-]{1,63}$")
_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class LinkConflict(ValueError):
    """Duplicate or over-limit link (HTTP 409)."""


def _session():
    from distr.core.planning import service
    return service.get_session()


def normalise_host(host: Any) -> str:
    """Return the literal loopback form to store: an IP (never a DNS name) or ``unix:/abs/path``."""
    value = str(host or "").strip()
    if value.startswith("unix:"):
        path = value[5:]
        if not path.startswith("/") or ".." in path.split("/") or _CONTROL.search(path) or not path.startswith(SOCKET_DIRS):
            raise NonLoopbackHost("Unix sockets must be an absolute path under /tmp, /var/run/mysqld or the Homebrew var folder.")
        return f"unix:{path}"
    addresses = assert_loopback_host(value)
    def literal(address):
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return ip
    ips = [literal(a) for a in addresses]
    v4 = [ip for ip in ips if ip.version == 4]
    return str(v4[0] if v4 else ips[0])


def _clean(data: dict, *, partial: bool, current: dict | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Send the link as a JSON object.")
    lowered = {str(k).lower() for k in data}
    if any(word in key for key in lowered for word in ("password", "passwd", "secret", "token")):
        raise ValueError("Passwords are never sent with the link. Set it with PUT .../db-links/{id}/secret.")
    unknown = set(data) - FIELDS
    if unknown:
        raise ValueError(f"Unknown link fields: {', '.join(sorted(map(str, unknown)))}.")
    merged = {**(current or {}), **data}
    out: dict[str, Any] = {}
    engine = merged.get("engine")
    if engine not in ENGINES:
        raise ValueError("engine must be postgres or mysql.")
    out["engine"] = engine
    out["host"] = normalise_host(merged.get("host"))
    port = merged.get("port")
    if out["host"].startswith("unix:"):
        port = None
    elif port is None:
        port = ENGINES[engine]
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ValueError("port must be a whole number from 1 to 65535.")
    out["port"] = port
    database, username = merged.get("database"), merged.get("username")
    if not isinstance(database, str) or not _NAME.match(database):
        raise ValueError("database must be 1-63 letters, digits, _ $ . or -.")
    if not isinstance(username, str) or not _USER.match(username):
        raise ValueError("username must be 1-63 letters, digits, _ . @ or -.")
    out["database"], out["username"] = database, username
    label = merged.get("label") or f"{engine} · {database}"
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80 or _CONTROL.search(label):
        raise ValueError("label must be 1-80 characters without control characters.")
    out["label"] = label.strip()
    schemas = merged.get("schemas")
    if schemas is None:
        schemas = ["public"] if engine == "postgres" else []
    if isinstance(schemas, str):
        try:
            schemas = json.loads(schemas)
        except ValueError:
            schemas = None
    if not isinstance(schemas, list) or len(schemas) > 20 or not all(isinstance(s, str) and _SCHEMA.match(s) for s in schemas):
        raise ValueError("schemas must be a list of up to 20 schema names.")
    out["schemas"] = list(dict.fromkeys(schemas))
    colour = merged.get("colour")
    if colour is not None and (not isinstance(colour, str) or not _COLOUR.match(colour)):
        raise ValueError("colour must look like #3b82f6.")
    out["colour"] = colour
    return out


def _payload(row) -> dict[str, Any]:
    store = get_store()
    has = store.has(row.id) if row.secret_ref else False
    return {
        "id": int(row.id), "workspace_id": row.workspace_id, "project_id": int(row.project_id),
        "engine": row.engine, "host": row.host, "port": row.port, "database": row.database,
        "schemas": json.loads(row.schemas or "[]"), "username": row.username, "label": row.label,
        "colour": row.colour, "read_only": True, "status": row.status, "last_error": row.last_error or "",
        "last_introspected_at": row.last_introspected_at.isoformat() if row.last_introspected_at else None,
        "has_secret": bool(has),
        "secret_store": (row.secret_ref or "").split(":", 1)[0] if has else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "modified_at": row.modified_at.isoformat() if row.modified_at else None,
    }


def _workspace(db, workspace_id):
    from distr.core.db.workflow import PlanWorkspace
    workspace = db.get(PlanWorkspace, int(workspace_id))
    if workspace is None:
        raise LookupError("Plan workspace not found.")
    return workspace


def _link(db, workspace_id, link_id):
    from distr.core.db.workflow import PlanDbLink
    row = db.get(PlanDbLink, int(link_id))
    if row is None or row.workspace_id != int(workspace_id) or row.status == ARCHIVED:
        raise LookupError("Database link not found in this plan.")
    return row


def _check_unique(db, project_id, clean, exclude_id=None):
    from distr.core.db.workflow import PlanDbLink
    rows = db.query(PlanDbLink).filter(PlanDbLink.project_id == project_id, PlanDbLink.status != ARCHIVED).all()
    key = (clean["engine"], clean["host"], clean["port"], clean["database"], clean["username"])
    if any((r.engine, r.host, r.port, r.database, r.username) == key and r.id != exclude_id for r in rows):
        raise LinkConflict("This database is already linked to the project.")
    return rows


def list_links(workspace_id: int) -> dict[str, Any]:
    from distr.core.db.workflow import PlanDbLink
    with _session() as db:
        _workspace(db, workspace_id)
        rows = db.query(PlanDbLink).filter(PlanDbLink.workspace_id == int(workspace_id), PlanDbLink.status != ARCHIVED).order_by(PlanDbLink.id).all()
        return {"items": [_payload(r) for r in rows], "secret_store": get_store().backend(), "max_links": MAX_LINKS_PER_PROJECT}


def create_link(workspace_id: int, data: dict) -> dict[str, Any]:
    from distr.core.db.workflow import PlanDbLink
    clean = _clean(data, partial=False)
    with _session() as db:
        workspace = _workspace(db, workspace_id)
        if not workspace.project_id:
            raise ValueError("Link this plan to a project before adding databases.")
        rows = _check_unique(db, workspace.project_id, clean)
        if len(rows) >= MAX_LINKS_PER_PROJECT:
            raise LinkConflict(f"A project can link at most {MAX_LINKS_PER_PROJECT} databases.")
        row = PlanDbLink(project_id=int(workspace.project_id), workspace_id=int(workspace.id), engine=clean["engine"],
                         host=clean["host"], port=clean["port"], database=clean["database"], username=clean["username"],
                         label=clean["label"], schemas=json.dumps(clean["schemas"]), colour=clean["colour"],
                         read_only=True, status="new")
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("plan_db_link created id=%s workspace=%s project=%s", row.id, workspace.id, workspace.project_id)
        return _payload(row)


def update_link(workspace_id: int, link_id: int, data: dict) -> dict[str, Any]:
    with _session() as db:
        row = _link(db, workspace_id, link_id)
        current = {"engine": row.engine, "host": row.host, "port": row.port, "database": row.database,
                   "username": row.username, "label": row.label, "schemas": json.loads(row.schemas or "[]"), "colour": row.colour}
        if row.host.startswith("unix:") and "host" not in (data or {}) and "port" in (data or {}):
            raise ValueError("A unix-socket link has no port.")
        clean = _clean(data, partial=True, current=current)
        _check_unique(db, row.project_id, clean, exclude_id=row.id)
        target_changed = any(clean[k] != current[k] for k in ("engine", "host", "port", "database", "username"))
        row.engine, row.host, row.port = clean["engine"], clean["host"], clean["port"]
        row.database, row.username, row.label = clean["database"], clean["username"], clean["label"]
        row.schemas, row.colour = json.dumps(clean["schemas"]), clean["colour"]
        if target_changed:
            row.status, row.last_error = "new", None
        db.commit()
        db.refresh(row)
        logger.info("plan_db_link updated id=%s workspace=%s", row.id, row.workspace_id)
        return _payload(row)


def archive_link(workspace_id: int, link_id: int) -> dict[str, Any]:
    with _session() as db:
        row = _link(db, workspace_id, link_id)
        row.status = ARCHIVED
        had_ref = bool(row.secret_ref)
        row.secret_ref = None
        db.commit()
        removed = get_store().delete(row.id) if had_ref else False
        logger.info("plan_db_link archived id=%s workspace=%s secret_removed=%s", row.id, row.workspace_id, bool(removed))
        return {"id": int(row.id), "archived": True, "secret_deleted": bool(removed)}


def set_secret(workspace_id: int, link_id: int, secret: Any) -> dict[str, Any]:
    store = get_store()
    with _session() as db:
        row = _link(db, workspace_id, link_id)
        used = store.set(row.id, secret)
        row.secret_ref = f"{used}:{store.service}/{int(row.id)}"
        row.status, row.last_error = "new", None
        db.commit()
        logger.info("plan_db_link secret set id=%s store=%s", row.id, used)
        return {"store": used, "has_secret": store.has(row.id)}
