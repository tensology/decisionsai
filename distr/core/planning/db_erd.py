"""N5 phase 4: DB link snapshots, ERD mapping, diff and explicit apply.

* Snapshot cache: the last introspection per link is kept as JSON at <plan root>/.db-snapshots/<link id>.json
  (git-ignored via a .gitignore entry written in the plan root). Only catalog structure is stored: names,
  types, nullability, keys, index names and row ESTIMATES. Never a secret, a host/user or row data.
* ERD mapping: tables become erDiagram blocks named <schema>__<table> (one ERD per link, the default) or
  <label>__<schema>__<table> (merge into an existing ERD). The `%% decisions-erd:` sidecar gains
  `db: {"links": [{"link", "snapshot_at", "relations"}]}` and per table `db: {link, schema, table, raw, rows_estimate}`.
* Diff is read-only. Apply is explicit, per selected table, revision-checked, and never touches tables
  without a db tag (hand-made), hand comments, x/y or interfaces; tables gone from the DB are marked, not deleted.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = ".db-snapshots"
SNAPSHOT_VERSION = 1
MAX_APPLY_TABLES = 300
SIDECAR_PREFIX = "%% decisions-erd:"
MERMAID_TYPE_RE = re.compile(r"^[A-Za-z_][\w\-\[\]()]*$")
_NAME = r'(?:"[^"]+"|[A-Za-z_][\w-]*)'
BLOCK_RE = re.compile(rf'^({_NAME})(?:\s*\[\s*"([^"]*)"\s*\])?\s*\{{\s*(\}})?\s*$')
REL_RE = re.compile(rf'^({_NAME})\s*(\|o|\|\||\}}o|\}}\|)(--|\.\.)(o\||\|\||o\{{|\|\{{)\s*({_NAME})\s*:\s*(.*)$')
ATTR_RE = re.compile(r'^([A-Za-z_][\w\-()\[\],.*]*?)\s+([A-Za-z_*][\w\-*]*)((?:\s+(?:PK|FK|UK)(?:\s*,\s*(?:PK|FK|UK))*)?)(?:\s+"([^"]*)")?\s*$')
GONE = "(gone from DB)"


class SnapshotMissing(LookupError):
    pass


# ---- snapshot cache ------------------------------------------------------------------------------------
def _session():
    from distr.core.planning import service
    return service.get_session()


def _link_and_root(workspace_id: int, link_id: int) -> tuple[dict, Path]:
    from distr.core.db.workflow import PlanWorkspace
    from distr.core.planning.db_links import _link
    with _session() as db:
        row = _link(db, workspace_id, link_id)
        workspace = db.get(PlanWorkspace, int(workspace_id))
        link = {"id": int(row.id), "label": row.label, "engine": row.engine, "database": row.database,
                "colour": row.colour}
        root = (workspace.root_path or "") if workspace else ""
    if not root:
        raise ValueError("This plan has no project folder, so the schema snapshot cannot be saved.")
    return link, Path(root).expanduser().resolve()


def snapshot_path(root: Path, link_id: int) -> Path:
    return root / SNAPSHOT_DIR / f"{int(link_id)}.json"


def _ensure_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    entry = f"{SNAPSHOT_DIR}/"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(line.strip() in (entry, SNAPSHOT_DIR, f"/{entry}") for line in existing.splitlines()):
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}# DecisionsAI schema snapshots (local cache, no secrets)\n{entry}\n")


def _int_or_none(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def sanitise_snapshot(raw: dict, link: dict) -> dict:
    """Allow-list copy: catalog structure only, whatever else the introspection result carried."""
    def names(values):
        return [str(v) for v in (values or [])][:200]
    tables = []
    for t in (raw.get("tables") or [])[:MAX_APPLY_TABLES]:
        columns = []
        for c in t.get("columns") or []:
            fk = c.get("fk") if isinstance(c.get("fk"), dict) else None
            columns.append({"name": str(c.get("name")), "position": _int_or_none(c.get("position")),
                            "type": str(c.get("type") or "text"), "engine_type": str(c.get("engine_type") or "")[:80],
                            "nullable": bool(c.get("nullable")), "has_default": bool(c.get("has_default")),
                            "pk": bool(c.get("pk")), "unique": bool(c.get("unique")),
                            "fk": {"schema": str(fk.get("schema")), "table": str(fk.get("table")), "column": str(fk.get("column"))} if fk else None})
        tables.append({
            "schema": str(t.get("schema")), "name": str(t.get("name")), "rows_estimate": _int_or_none(t.get("rows_estimate")),
            "columns": columns, "primary_key": names(t.get("primary_key")),
            "unique": [names(u) for u in (t.get("unique") or [])],
            "foreign_keys": [{"name": str(f.get("name")), "columns": names(f.get("columns")), "ref_schema": str(f.get("ref_schema")),
                              "ref_table": str(f.get("ref_table")), "ref_columns": names(f.get("ref_columns"))} for f in (t.get("foreign_keys") or [])],
            "indexes": [{"name": str(i.get("name")), "unique": bool(i.get("unique")), "primary": bool(i.get("primary")),
                         "columns": names(i.get("columns"))} for i in (t.get("indexes") or [])],
        })
    return {"v": SNAPSHOT_VERSION, "link_id": int(link["id"]), "engine": str(raw.get("engine") or link.get("engine") or ""),
            "database": str(raw.get("database") or ""), "schemas": names(raw.get("schemas")),
            "filter": str(raw.get("filter") or ""), "total_tables": _int_or_none(raw.get("total_tables")),
            "truncated": bool(raw.get("truncated")), "row_counts": "estimates",
            "introspected_at": str(raw.get("introspected_at") or datetime.now(timezone.utc).isoformat()), "tables": tables}


def save_snapshot(workspace_id: int, link_id: int, raw: dict) -> dict:
    link, root = _link_and_root(workspace_id, link_id)
    snap = sanitise_snapshot(raw, link)
    folder = root / SNAPSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(root)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snap, handle, indent=1, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, snapshot_path(root, link_id))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return snap


def read_snapshot(workspace_id: int, link_id: int) -> dict:
    link, root = _link_and_root(workspace_id, link_id)
    path = snapshot_path(root, link_id)
    if not path.exists():
        raise SnapshotMissing("No schema snapshot yet. Run Introspect first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SnapshotMissing("The cached schema snapshot is unreadable. Run Introspect again.") from None
    return sanitise_snapshot(data, link)


def refresh_snapshot(workspace_id: int, link_id: int, *, table_filter: str = "", connectors=None) -> dict:
    from distr.core.planning.db_introspect import MAX_TABLES, introspect_link
    _link_and_root(workspace_id, link_id)   # fail before connecting when there is nowhere to save
    raw = introspect_link(workspace_id, link_id, table_filter=table_filter, limit=MAX_TABLES, connectors=connectors)
    return save_snapshot(workspace_id, link_id, raw)


# ---- mapping ------------------------------------------------------------------------------------------
def _ident(value: str) -> str:
    clean = re.sub(r"[^\w-]", "_", str(value)).strip("_") or "x"
    return clean if re.match(r"^[A-Za-z_]", clean) else f"t_{clean}"


def entity_name(link: dict, schema: str, table: str, *, merge: bool) -> str:
    base = f"{_ident(schema)}__{_ident(table)}"
    return f"{_ident(link['label'])}__{base}" if merge else base


def mermaid_type(value: str) -> str:
    v = re.sub(r"\s+", "_", str(value or "string").strip())
    if MERMAID_TYPE_RE.match(v):
        return v
    m = re.match(r"^[A-Za-z_]\w*", v)
    return m.group(0) if m else "string"


def _field_keys(col: dict) -> list[str]:
    keys = []
    if col.get("pk"):
        keys.append("PK")
    if col.get("fk"):
        keys.append("FK")
    if col.get("unique") and not col.get("pk"):
        keys.append("UK")
    return keys


def _table_key(schema: str, table: str) -> str:
    return f"{schema}.{table}"


# ---- light erDiagram reader (enough for diff/apply; the JS parser stays the editor's source of truth) ----
def _unquote(s: str) -> str:
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s


def read_erd(text: str) -> dict:
    lines = str(text or "").split("\n")
    sidecar, sidecar_index = {}, None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith(SIDECAR_PREFIX):
            try:
                sidecar = json.loads(lines[i].strip()[len(SIDECAR_PREFIX):]) or {}
            except ValueError:
                sidecar = {}
            sidecar_index = i
            break
    tables, relations = {}, []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if i == sidecar_index:
            i += 1
            continue
        m = BLOCK_RE.match(line)
        if m:
            name = _unquote(m.group(1))
            start, fields = i, []
            if not m.group(3):
                i += 1
                while i < len(lines) and lines[i].strip() != "}":
                    a = ATTR_RE.match(lines[i].strip())
                    if a:
                        fields.append({"type": a.group(1), "name": a.group(2), "keys": [k for k in re.split(r"[\s,]+", a.group(3) or "") if k],
                                       "comment": a.group(4) or ""})
                    i += 1
            tables[name] = {"name": name, "alias": m.group(2), "start": start, "end": i, "fields": fields}
            i += 1
            continue
        r = REL_RE.match(line)
        if r:
            relations.append({"index": i, "from": _unquote(r.group(1)), "to": _unquote(r.group(5)),
                              "label": _unquote(r.group(6).strip())})
        i += 1
    if not isinstance(sidecar, dict):
        sidecar = {}
    for name, t in tables.items():
        meta = (sidecar.get("tables") or {}).get(name) or {}
        t["meta"] = meta if isinstance(meta, dict) else {}
        extras = t["meta"].get("fields") or {}
        for f in t["fields"]:
            extra = extras.get(f["name"]) or {}
            f["nullable"] = extra.get("nullable")
            if isinstance(extra.get("type"), str) and mermaid_type(extra["type"]) == f["type"]:
                f["type"] = extra["type"]
    return {"lines": lines, "sidecar": sidecar, "sidecar_index": sidecar_index, "tables": tables, "relations": relations}


def _tagged(erd: dict, link_id: int) -> dict[str, str]:
    """(schema.table) -> entity name for tables carrying this link's db tag."""
    out = {}
    for name, t in erd["tables"].items():
        tag = t["meta"].get("db")
        if isinstance(tag, dict) and tag.get("link") == int(link_id):
            out[_table_key(str(tag.get("schema")), str(tag.get("table")))] = name
    return out


# ---- diff ---------------------------------------------------------------------------------------------
def _compare(table: dict, existing: dict) -> list[dict]:
    changes = []
    have = {f["name"]: f for f in existing["fields"]}
    want = {_ident(c["name"]) if not re.match(r"^[A-Za-z_*][\w\-*]*$", c["name"]) else c["name"]: c for c in table["columns"]}
    for name, col in want.items():
        f = have.get(name)
        if f is None:
            changes.append({"column": name, "change": "added", "to": col["type"]})
            continue
        if str(f["type"]).lower() != str(col["type"]).lower():
            changes.append({"column": name, "change": "type", "from": f["type"], "to": col["type"]})
        if f.get("nullable") is not None and bool(f["nullable"]) != bool(col["nullable"]):
            changes.append({"column": name, "change": "nullable", "from": bool(f["nullable"]), "to": bool(col["nullable"])})
        if sorted(f["keys"]) != sorted(_field_keys(col)):
            changes.append({"column": name, "change": "keys", "from": sorted(f["keys"]), "to": sorted(_field_keys(col))})
    for name in have:
        if name not in want:
            changes.append({"column": name, "change": "removed"})
    return changes


def diff(snapshot: dict, link: dict, erd_text: str, *, merge: bool) -> dict:
    erd = read_erd(erd_text)
    tagged = _tagged(erd, link["id"])
    added, changed, unchanged, conflicts = [], [], [], []
    in_snapshot = set()
    for t in snapshot["tables"]:
        key = _table_key(t["schema"], t["name"])
        in_snapshot.add(key)
        entry = {"key": key, "schema": t["schema"], "table": t["name"], "columns": len(t["columns"]), "rows_estimate": t["rows_estimate"]}
        name = tagged.get(key)
        if name is None:
            target = entity_name(link, t["schema"], t["name"], merge=merge)
            if target in erd["tables"]:
                conflicts.append({**entry, "entity": target, "reason": "A hand-made table already uses this name; it is left alone."})
            else:
                added.append({**entry, "entity": target})
            continue
        changes = _compare(t, erd["tables"][name])
        gone_marked = GONE in str(erd["tables"][name]["alias"] or "")
        if changes or gone_marked:
            changed.append({**entry, "entity": name, "changes": changes + ([{"change": "back_in_db"}] if gone_marked else [])})
        else:
            unchanged.append({**entry, "entity": name})
    removed = [{"key": key, "entity": name, "schema": key.split(".", 1)[0], "table": key.split(".", 1)[1],
                "already_marked": GONE in str(erd["tables"][name]["alias"] or "")}
               for key, name in sorted(tagged.items()) if key not in in_snapshot]
    hand_made = sorted(n for n, t in erd["tables"].items() if not isinstance(t["meta"].get("db"), dict))
    return {"link_id": link["id"], "snapshot_at": snapshot["introspected_at"], "merge": merge,
            "added": added, "removed": removed, "changed": changed, "unchanged": unchanged, "conflicts": conflicts,
            "hand_made": hand_made, "truncated": snapshot.get("truncated", False), "max_apply": MAX_APPLY_TABLES,
            "summary": {"added": len(added), "removed": len([r for r in removed if not r["already_marked"]]),
                        "changed": len(changed), "unchanged": len(unchanged), "conflicts": len(conflicts)}}


# ---- apply --------------------------------------------------------------------------------------------
def _block(name: str, alias: str | None, table: dict, keep_comments: dict[str, str], extra_fields: list[dict]) -> tuple[list[str], dict]:
    head = name + (f'["{alias}"]' if alias else "")
    out, field_meta, raw = [f"    {head} {{"], {}, {}
    for col in sorted(table["columns"], key=lambda c: (c["position"] or 0, c["name"])):
        fname = col["name"] if re.match(r"^[A-Za-z_*][\w\-*]*$", col["name"]) else _ident(col["name"])
        keys = _field_keys(col)
        token = mermaid_type(col["type"])
        comment = keep_comments.get(fname, "")
        out.append(f"        {token} {fname}" + (f" {', '.join(keys)}" if keys else "") + (f' "{comment}"' if comment else ""))
        extra = {"nullable": bool(col["nullable"])}
        if token != col["type"]:
            extra["type"] = col["type"]
        field_meta[fname] = extra
        if col["engine_type"]:
            raw[fname] = col["engine_type"]
    for f in extra_fields:   # hand-added fields that the DB does not have: kept, never dropped
        out.append(f"        {mermaid_type(f['type'])} {f['name']}" + (f" {', '.join(f['keys'])}" if f["keys"] else "")
                   + (f' "{f["comment"]}"' if f["comment"] else ""))
    out.append("    }")
    return out, {"fields": field_meta, "raw": raw}


def build_applied(snapshot: dict, link: dict, erd_text: str, selected: list[str], *, merge: bool, owner: bool = False) -> tuple[str, dict]:
    if not isinstance(selected, list) or not all(isinstance(s, str) for s in selected):
        raise ValueError("tables must be a list of schema.table names.")
    selected = list(dict.fromkeys(selected))
    if not selected:
        raise ValueError("Pick at least one table to apply.")
    if len(selected) > MAX_APPLY_TABLES:
        raise ValueError(f"Apply at most {MAX_APPLY_TABLES} tables at a time. Narrow the selection with the picker.")
    erd = read_erd(erd_text)
    tagged = _tagged(erd, link["id"])
    by_key = {_table_key(t["schema"], t["name"]): t for t in snapshot["tables"]}
    unknown = [s for s in selected if s not in by_key and s not in tagged]
    if unknown:
        raise ValueError(f"Not in the snapshot or this ERD: {', '.join(unknown[:5])}.")
    lines = list(erd["lines"])
    sidecar = dict(erd["sidecar"]) if erd["sidecar"] else {"v": 1}
    sidecar.setdefault("v", 1)
    meta_tables = dict(sidecar.get("tables") or {})
    db_meta = dict(sidecar.get("db") or {})
    links_meta = [dict(x) for x in (db_meta.get("links") or []) if isinstance(x, dict)]
    mine = next((x for x in links_meta if x.get("link") == link["id"]), None)
    if mine is None:
        mine = {"link": link["id"]}
        links_meta.append(mine)
    generated = set(mine.get("relations") or [])
    replace: dict[int, tuple[int, list[str]]] = {}
    appended: list[str] = []
    applied, skipped, kept_extra = [], [], {}
    entity_of = dict(tagged)
    for key in selected:
        table = by_key.get(key)
        name = tagged.get(key)
        if table is None:   # gone from the DB: mark, never delete
            t = erd["tables"][name]
            alias = str(t["alias"] or "")
            if GONE not in alias:
                header = lines[t["start"]]
                indent = header[: len(header) - len(header.lstrip())]
                m = BLOCK_RE.match(header.strip())
                label = (alias + " " if alias else "") + GONE
                replace[t["start"]] = (t["start"], [f'{indent}{m.group(1)}["{label}"] {{' + (" }" if m.group(3) else "")])
            meta = dict(meta_tables.get(name) or {})
            meta["db"] = {**(meta.get("db") or {}), "gone": True}
            meta_tables[name] = meta
            applied.append({"key": key, "entity": name, "action": "marked_gone"})
            continue
        if name is None:
            name = entity_name(link, table["schema"], table["name"], merge=merge)
            if name in erd["tables"]:
                skipped.append({"key": key, "entity": name, "reason": "name used by a hand-made table"})
                continue
            entity_of[key] = name
            block, extra = _block(name, None, table, {}, [])
            appended.extend(block)
            action = "added"
            meta = {"interfaces": []}
        else:
            current = erd["tables"][name]
            alias = (str(current["alias"] or "").replace(GONE, "").strip()) or None
            comments = {f["name"]: f["comment"] for f in current["fields"] if f["comment"]}
            db_cols = {(c["name"] if re.match(r"^[A-Za-z_*][\w\-*]*$", c["name"]) else _ident(c["name"])) for c in table["columns"]}
            extras = [f for f in current["fields"] if f["name"] not in db_cols]
            if extras:
                kept_extra[name] = [f["name"] for f in extras]
            block, extra = _block(name, alias, table, comments, extras)
            replace[current["start"]] = (current["end"], block)
            action = "updated"
            meta = dict(meta_tables.get(name) or {})
        old_fields = dict(meta.get("fields") or {})
        meta["fields"] = {**{k: v for k, v in old_fields.items() if k in kept_extra.get(name, [])}, **extra["fields"]}
        meta["db"] = {"link": link["id"], "schema": table["schema"], "table": table["name"], "raw": extra["raw"],
                      "rows_estimate": table["rows_estimate"]}
        meta_tables[name] = meta
        applied.append({"key": key, "entity": name, "action": action})
    # FK relations: regenerate this link's generated relations that touch an applied table; hand relations stay.
    touched = {a["entity"] for a in applied if a["action"] != "marked_gone"}
    drop_lines = {r["index"] for r in erd["relations"] if f"{r['from']}|{r['to']}|{r['label']}" in generated and (r["from"] in touched or r["to"] in touched)}
    generated = {g for g in generated if not (g.split("|")[0] in touched or g.split("|")[1] in touched)}
    existing_rel = {f"{r['from']}|{r['to']}|{r['label']}" for r in erd["relations"] if r["index"] not in drop_lines}
    rel_lines = []
    for key, table in by_key.items():
        child = entity_of.get(key)
        if child is None:
            continue
        for col in table["columns"]:
            fk = col.get("fk")
            parent = entity_of.get(_table_key(fk["schema"], fk["table"])) if fk else None
            if not parent or (child not in touched and parent not in touched):
                continue
            label = col["name"]
            rk = f"{parent}|{child}|{label}"
            if rk in existing_rel or rk in generated:
                continue
            left = "|o" if col["nullable"] else "||"
            rel_lines.append(f'    {parent} {left}--o{{ {child} : "{label}"')
            generated.add(rk)
    mine["relations"] = sorted(generated)
    mine["snapshot_at"] = snapshot["introspected_at"]
    mine["label"] = link["label"]
    db_meta["links"] = links_meta
    if owner:
        db_meta["owner"] = link["id"]   # this ERD belongs to the link (one ERD per link)
    sidecar["db"] = db_meta
    sidecar["tables"] = meta_tables
    out: list[str] = []
    i = 0
    while i < len(lines):
        if i == erd["sidecar_index"] or i in drop_lines:
            i += 1
            continue
        if i in replace:
            end, block = replace[i]
            out.extend(block)
            i = end + 1
            continue
        out.append(lines[i])
        i += 1
    while out and not out[-1].strip():
        out.pop()
    if not any(l.strip().startswith("erDiagram") for l in out):
        out.insert(0, "erDiagram")
    out.extend(appended)
    out.extend(rel_lines)
    out.append(f"{SIDECAR_PREFIX} {json.dumps(sidecar, separators=(',', ':'), sort_keys=True)}")
    return "\n".join(out) + "\n", {"applied": applied, "skipped": skipped, "kept_extra_fields": kept_extra,
                                  "relations_added": len(rel_lines)}


def _erd_items(workspace_id: int) -> list[dict]:
    from distr.core.planning.service import get_workspace
    workspace = get_workspace(int(workspace_id)) or {}
    return [i for i in workspace.get("items") or [] if i.get("item_type") == "erd" and i.get("status") != "archived"]


def _owner(item: dict):
    db_meta = read_erd(item.get("content") or "")["sidecar"].get("db") or {}
    return db_meta.get("owner") if isinstance(db_meta, dict) else None


def link_erd(workspace_id: int, link_id: int) -> dict | None:
    """The ERD owned by this link (one ERD per link): sidecar db.owner == link id."""
    for item in _erd_items(workspace_id):
        if _owner(item) == int(link_id):
            return item
    return None


def _target_item(workspace_id: int, link_id: int, item_id: int | None) -> tuple[dict | None, bool]:
    if item_id is None:
        return link_erd(workspace_id, link_id), False
    if type(item_id) is not int:
        raise ValueError("item_id must be an ERD id or null.")
    item = next((i for i in _erd_items(workspace_id) if int(i["id"]) == item_id), None)
    if item is None:
        raise LookupError("ERD not found in this plan.")
    return item, _owner(item) != int(link_id)


def diff_link(workspace_id: int, link_id: int, *, item_id: int | None = None) -> dict:
    link, _root = _link_and_root(workspace_id, link_id)
    snapshot = read_snapshot(workspace_id, link_id)
    item, merge = _target_item(workspace_id, link_id, item_id)
    result = diff(snapshot, link, item.get("content") if item else "erDiagram\n", merge=merge)
    result["item_id"] = int(item["id"]) if item else None
    result["revision"] = int(item["revision_count"]) if item else None
    result["target"] = "merge" if merge else ("link_erd" if item else "new_erd")
    return result


def apply_link(workspace_id: int, link_id: int, *, tables: list, item_id: int | None = None, expected_revision: int | None = None) -> dict:
    from distr.core.planning.service import create_item, update_item
    from distr.core.planning.validation import validate_mermaid
    link, _root = _link_and_root(workspace_id, link_id)
    snapshot = read_snapshot(workspace_id, link_id)
    item, merge = _target_item(workspace_id, link_id, item_id)
    if item is not None and type(expected_revision) is not int:
        raise ValueError("expected_revision is required when applying to an existing ERD.")
    content, report = build_applied(snapshot, link, item.get("content") if item else "erDiagram\n", tables, merge=merge, owner=item is None)
    note = f"Applied {len(report['applied'])} table(s) from database link \"{link['label']}\" (snapshot {snapshot['introspected_at']})."
    if item is None:
        validate_mermaid(content, require_erd=True)
        saved = create_item(workspace_id=int(workspace_id), item_type="erd", title=f"Data · {link['label']}",
                            content=content, content_format="mermaid", source="db_link", instruction=note)
    else:
        saved = update_item(int(item["id"]), content=content, expected_revision=expected_revision, source="db_link",
                            instruction=note, workspace_id=int(workspace_id))
        if saved is None:
            raise LookupError("ERD not found in this plan.")
    return {**report, "item": saved, "target": "merge" if merge else "link_erd", "message": note}


# ---- additive column + read-only rows -----------------------------------------------------------------
# Creating a field on the diagram does not touch the database until the ERD save asks for one ADD COLUMN.
# Viewing rows is a separate read-only SELECT. Neither path opens its own connection.
_TYPE_RE = re.compile(r"^([a-z_]+)(?:\(([^)]*)\))?$")
_ENUM_PARAM = re.compile(r"[a-z0-9_-]{1,40}(?:,[a-z0-9_-]{1,40}){0,49}")
MAX_PAGE_SIZE = 100
MAX_PAGE = 10000
COLUMN_EXISTS = "SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name = %s"


def _check_ident(name: str, what: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 128 or any(ord(c) < 32 for c in name):
        raise ValueError(f"Invalid {what}.")
    return name


def quote_ident(engine: str, name: str) -> str:
    """Quote one identifier. The name is never spliced in raw."""
    _check_ident(name, "identifier")
    if engine == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def _qualified(engine: str, schema: str, table: str) -> str:
    table_sql = quote_ident(engine, table)
    if engine == "sqlite" or not schema:
        return table_sql
    return f"{quote_ident(engine, schema)}.{table_sql}"


def _parse_type(field_type: str) -> tuple[str, str | None]:
    from distr.core.planning.db_introspect import FIELD_TYPES
    raw = str(field_type or "").strip().lower().replace(" ", "")
    match = _TYPE_RE.fullmatch(raw)
    if not match or match.group(1) not in FIELD_TYPES:
        raise ValueError("Unknown field type.")
    base, param = match.group(1), match.group(2)
    if param is not None and base not in ("varchar", "char", "decimal", "numeric", "float", "time", "timestamp", "timestamptz", "enum"):
        raise ValueError("Unknown field type.")
    if param is not None and any(c in param for c in "';\"\\"):
        raise ValueError("Unknown field type.")
    return base, param


def _length(param: str | None, default: int) -> int:
    if param is None or param == "":
        return default
    if not re.fullmatch(r"\d{1,5}", param):
        raise ValueError("Unknown field type.")
    number = int(param)
    if not 1 <= number <= 65535:
        raise ValueError("Unknown field type.")
    return number


def _decimal_spec(param: str | None) -> str:
    if param is None or param == "":
        return ""
    if not re.fullmatch(r"\d{1,4}(?:,\d{1,4})?", param):
        raise ValueError("Unknown field type.")
    return param


def _erd_token(base: str, param: str | None) -> str:
    """Type token stored on the diagram. ``string`` stays ``string`` even though the column is ``text``."""
    if param is None or param == "":
        return base
    if base in ("varchar", "char"):
        return f"{base}({_length(param, 255 if base == 'varchar' else 1)})"
    if base in ("decimal", "numeric"):
        spec = _decimal_spec(param)
        return f"{base}({spec})" if spec else base
    if base == "enum":
        if not _ENUM_PARAM.fullmatch(param):
            raise ValueError("Unknown field type.")
        return f"enum({param})"
    if re.fullmatch(r"\d{1,4}(?:,\d{1,4})?", param):
        return f"{base}({param})"
    raise ValueError("Unknown field type.")


def column_sql(engine: str, field_type: str) -> tuple[str, str]:
    """``(sql type, diagram type)``. The SQL type is a whitelist, never the raw field text."""
    if engine not in ("postgres", "mysql", "sqlite"):
        raise ValueError("Unknown database engine.")
    base, param = _parse_type(field_type)
    erd = _erd_token(base, param)
    if base in ("string", "text", "enum"):
        sql = "text"
    elif base in ("varchar", "char"):
        sql = f"{base}({_length(param, 255 if base == 'varchar' else 1)})"
    elif base in ("decimal", "numeric"):
        spec = _decimal_spec(param)
        if engine == "sqlite":
            sql = "real"
        else:
            kind = "numeric" if engine == "postgres" else "decimal"
            sql = f"{kind}({spec})" if spec else kind
    elif base in ("int", "serial"):
        sql = "int" if engine == "mysql" else "integer"
    elif base == "smallint":
        sql = "integer" if engine == "sqlite" else "smallint"
    elif base in ("bigint", "bigserial"):
        sql = "integer" if engine == "sqlite" else "bigint"
    elif base in ("float", "double", "real", "money"):
        if engine == "sqlite":
            sql = "real"
        elif base == "money":
            sql = "numeric(19,4)" if engine == "postgres" else "decimal(19,4)"
        elif engine == "mysql":
            sql = "double"
        else:
            sql = "real" if base == "real" else "double precision"
    elif base == "boolean":
        sql = {"postgres": "boolean", "mysql": "tinyint(1)", "sqlite": "integer"}[engine]
    elif base == "date":
        sql = "text" if engine == "sqlite" else "date"
    elif base == "time":
        sql = "text" if engine == "sqlite" else "time"
    elif base == "datetime":
        sql = {"postgres": "timestamp", "mysql": "datetime", "sqlite": "text"}[engine]
    elif base == "timestamp":
        sql = "text" if engine == "sqlite" else "timestamp"
    elif base == "timestamptz":
        sql = {"postgres": "timestamptz", "mysql": "datetime", "sqlite": "text"}[engine]
    elif base == "interval":
        sql = {"postgres": "interval", "mysql": "varchar(64)", "sqlite": "text"}[engine]
    elif base == "uuid":
        sql = {"postgres": "uuid", "mysql": "char(36)", "sqlite": "text"}[engine]
    elif base == "json":
        sql = "text" if engine == "sqlite" else "json"
    elif base == "jsonb":
        sql = {"postgres": "jsonb", "mysql": "json", "sqlite": "text"}[engine]
    elif base in ("blob", "bytea"):
        sql = {"postgres": "bytea", "mysql": "blob", "sqlite": "blob"}[engine]
    else:
        raise ValueError("Unknown field type.")
    return sql, erd


def add_column_sql(engine: str, schema: str, table: str, column: str, field_type: str) -> str:
    """One nullable ADD COLUMN. No default, no NOT NULL, so existing rows stay valid."""
    sql_type, _erd = column_sql(engine, field_type)
    return f"ALTER TABLE {_qualified(engine, schema, table)} ADD COLUMN {quote_ident(engine, column)} {sql_type}"


def rows_sql(engine: str, schema: str, table: str) -> tuple[str, str]:
    """Count and one page. Limit and offset are placeholders, never inlined."""
    rel = _qualified(engine, schema, table)
    placeholder = "?" if engine == "sqlite" else "%s"
    # ponytail: OFFSET rereads skipped rows. Keyset paging if a table is large enough that page 10000 is slow.
    return (f"SELECT COUNT(*) FROM {rel}", f"SELECT * FROM {rel} ORDER BY 1 LIMIT {placeholder} OFFSET {placeholder}")


def _cell(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return None if value != value else value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (bytes, bytearray)):
        return f"{len(value)} bytes"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:500]


def _desc_name(item) -> str:
    name = getattr(item, "name", None)
    return str(name if name else item[0])


def _remember_column(workspace_id: int, link_id: int, schema: str, table: str, column: str, erd_type: str, sql_type: str) -> None:
    """Keep the cached snapshot aligned so the next diff does not call the new field removed. Local file only."""
    try:
        snap = read_snapshot(workspace_id, link_id)
    except SnapshotMissing:
        return
    for entry in snap["tables"]:
        if entry["schema"] != schema or entry["name"] != table:
            continue
        if any(c["name"] == column for c in entry["columns"]):
            return
        position = max((c.get("position") or 0) for c in entry["columns"]) + 1 if entry["columns"] else 1
        entry["columns"].append({"name": column, "position": position, "type": erd_type, "engine_type": sql_type[:80],
                                  "nullable": True, "has_default": False, "pk": False, "unique": False, "fk": None})
        save_snapshot(workspace_id, link_id, snap)
        return


def add_linked_column(workspace_id: int, link_id: int, schema: str, table: str, column: str, field_type: str, *, connectors=None) -> dict:
    """Create one nullable column on the linked table. A column that already exists is left as it is."""
    from distr.core.planning.db_introspect import _with_connection
    schema = _check_ident(schema, "schema")
    table = _check_ident(table, "table")
    column = _check_ident(column, "column")
    _parse_type(field_type)

    def work(cur, link):
        sql_type, erd = column_sql(link["engine"], field_type)
        cur.execute(COLUMN_EXISTS, (schema, table, column))
        if cur.fetchone():
            return {"ok": True, "already_present": True, "schema": schema, "table": table, "column": column, "field_type": erd}
        cur.execute(add_column_sql(link["engine"], schema, table, column, field_type))
        return {"ok": True, "already_present": False, "schema": schema, "table": table, "column": column,
                "field_type": erd, "sql_type": sql_type}

    result = _with_connection(workspace_id, link_id, work, introspected=False, connectors=connectors, readonly=False, kind="add_column")
    if not result["already_present"]:
        try:
            _remember_column(workspace_id, link_id, schema, table, column, result["field_type"], result["sql_type"])
        except Exception:  # noqa: BLE001 - the column is already committed; a stale snapshot must not fail the save
            logger.info("plan_db_link add_column snapshot not updated id=%s", link_id)
    result.pop("sql_type", None)
    return result


def read_linked_rows(workspace_id: int, link_id: int, schema: str, table: str, *, page: int = 1, page_size: int = 50, connectors=None) -> dict:
    """One page of rows from a linked table. Read-only. No writes."""
    from distr.core.planning.db_introspect import _with_connection
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError(f"page must be a whole number from 1 to {MAX_PAGE}.")
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be a whole number from 1 to {MAX_PAGE_SIZE}.")
    schema = _check_ident(schema, "schema")
    table = _check_ident(table, "table")
    offset = (page - 1) * page_size

    def work(cur, link):
        count_sql, page_sql = rows_sql(link["engine"], schema, table)
        cur.execute(count_sql)
        total = int(cur.fetchone()[0])
        cur.execute(page_sql, (page_size, offset))
        columns = [_desc_name(item) for item in (cur.description or ())]
        rows = [[_cell(value) for value in row] for row in cur.fetchall()]
        return {"ok": True, "schema": schema, "table": table, "columns": columns, "rows": rows,
                "page": page, "page_size": page_size, "total": total}

    return _with_connection(workspace_id, link_id, work, introspected=False, connectors=connectors, readonly=True, kind="rows")
