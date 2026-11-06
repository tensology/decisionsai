"""N5 phase 4: snapshot cache, ERD mapping, diff and explicit apply (temp DB, fake driver, session secret store
only; no keychain, no database server)."""
import json
import logging

import pytest

from distr.core.planning import db_discovery as dd, db_erd as de, db_introspect as di, db_links as dl, db_secrets as ds, service
from distr.core.planning.validation import validate_mermaid
from tests.core.test_plan_db_discovery import RESOLVE
from tests.core.test_plan_db_introspect import MY_ANSWERS, PG_ANSWERS, REAL_ASSERT, SECRET, FakeConn
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401


@pytest.fixture
def env(plan_workspace_db, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    pinned = lambda host, resolver=None: REAL_ASSERT(host, resolver=RESOLVE)  # noqa: E731
    monkeypatch.setattr(dl, "assert_loopback_host", pinned)
    monkeypatch.setattr(dd, "assert_loopback_host", pinned)
    ds.set_store(ds.SecretStore(platform="linux", service="decisions-db-erd-test"))
    answers = {"postgres": dict(PG_ANSWERS)}
    connectors = {"postgres": lambda *a: FakeConn("postgres", answers["postgres"]), "mysql": lambda *a: FakeConn("mysql", MY_ANSWERS)}
    ws = service.ensure_workspace(board_key="decisions:67", board_provider="decisions", board_name="ERD", project_id=plan_workspace_db["project_id"])
    link = dl.create_link(ws["id"], {"engine": "postgres", "host": "127.0.0.1", "database": "shop", "username": "dev", "label": "Shop"})
    dl.set_secret(ws["id"], link["id"], SECRET)
    yield {"ws": ws, "link": link, "connectors": connectors, "answers": answers, "root": plan_workspace_db["folder"] / "planning"}
    ds.set_store(None); ds.clear_session_secrets()


def _refresh(env):
    return de.refresh_snapshot(env["ws"]["id"], env["link"]["id"], connectors=env["connectors"])


def test_snapshot_written_gitignored_without_secrets(env, caplog):
    snap = _refresh(env)
    path = env["root"] / ".db-snapshots" / f"{env['link']['id']}.json"
    assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"
    text = path.read_text()
    for forbidden in (SECRET, "127.0.0.1", '"dev"', "username", "host", "password"):
        assert forbidden not in text, forbidden
    assert ".db-snapshots/" in (env["root"] / ".gitignore").read_text().splitlines()
    _refresh(env)   # idempotent .gitignore entry
    assert (env["root"] / ".gitignore").read_text().count(".db-snapshots/") == 1
    assert [t["name"] for t in snap["tables"]] == ["customers", "orders", "secret_stuff"]
    assert de.read_snapshot(env["ws"]["id"], env["link"]["id"]) == json.loads(path.read_text())
    assert SECRET not in caplog.text


def test_snapshot_allow_list_drops_unknown_keys():
    snap = de.sanitise_snapshot({"engine": "postgres", "password": "x", "dsn": "postgresql://u@h/d",
                                 "tables": [{"schema": "s", "name": "t", "rows": [[1, 2]], "columns": [{"name": "a", "type": "int", "secret": 1}]}]},
                                {"id": 3, "engine": "postgres"})
    text = json.dumps(snap)
    assert "password" not in text and "postgresql://" not in text and '"rows"' not in text and '"secret"' not in text


def test_missing_snapshot_and_no_plan_folder(env, monkeypatch):
    with pytest.raises(de.SnapshotMissing):
        de.read_snapshot(env["ws"]["id"], env["link"]["id"])
    with pytest.raises(LookupError):
        de.diff_link(env["ws"]["id"], env["link"]["id"])
    monkeypatch.setattr(de, "_link_and_root", lambda *a: (_ for _ in ()).throw(ValueError("This plan has no project folder")))
    called = []
    monkeypatch.setitem(env["connectors"], "postgres", lambda *a: called.append(1))
    with pytest.raises(ValueError):
        _refresh(env)
    assert called == []   # never connects when there is nowhere to save


def test_mapping_and_new_erd_per_link(env):
    _refresh(env)
    d = de.diff_link(env["ws"]["id"], env["link"]["id"])
    assert d["target"] == "new_erd" and d["summary"]["added"] == 3 and d["item_id"] is None
    result = de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.customers", "public.orders"])
    content = result["item"]["content"]
    assert result["item"]["title"] == "Data · Shop" and result["relations_added"] == 1
    assert validate_mermaid(content, require_erd=True) == "er"
    assert "public__customers {" in content and "numeric balance" in content and "varchar(255) email UK" in content
    assert 'public__customers ||--o{ public__orders : "customer_id"' in content
    side = de.read_erd(content)["sidecar"]
    assert side["db"]["links"][0]["link"] == env["link"]["id"] and side["db"]["links"][0]["snapshot_at"]
    assert side["db"]["owner"] == env["link"]["id"]
    assert side["tables"]["public__customers"]["fields"]["balance"] == {"nullable": True, "type": "numeric(12,2)"}
    assert side["tables"]["public__customers"]["db"]["table"] == "customers"
    for forbidden in (SECRET, "127.0.0.1", "dev"):
        assert forbidden not in content
    # second diff targets the same link ERD; secret_stuff still to add, nothing changed
    d2 = de.diff_link(env["ws"]["id"], env["link"]["id"])
    assert d2["target"] == "link_erd" and d2["item_id"] == result["item"]["id"]
    assert d2["summary"] == {"added": 1, "removed": 0, "changed": 0, "unchanged": 2, "conflicts": 0}


def test_hand_edits_survive_and_changes_detected(env):
    _refresh(env)
    item = de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.customers", "public.orders"])["item"]
    erd = de.read_erd(item["content"])
    side = erd["sidecar"]
    side["tables"]["public__customers"].update({"x": 400, "y": 120, "interfaces": ["Checkout"]})
    lines = [l for l in item["content"].splitlines() if not l.startswith(de.SIDECAR_PREFIX)]
    lines = [l.replace("int id PK", 'int id PK "surrogate"') if "int id PK" in l and "bigint" not in l else l for l in lines]
    lines.insert(1, "    HAND_MADE {\n        int id PK\n    }")
    lines.append("    HAND_MADE ||--o{ public__orders : \"hand\"")
    edited = "\n".join(lines) + "\n" + de.SIDECAR_PREFIX + " " + json.dumps(side) + "\n"
    item = service.update_item(item["id"], content=edited, expected_revision=item["revision_count"])
    # the DB changes: email becomes nullable text, orders loses meta, a new column appears
    cols = [c for c in PG_ANSWERS[di.PG_COLUMNS] if c[2] != "meta"]
    cols = [(*c[:4], "text", "text", None, None, None, "YES", False) if c[2] == "email" else c for c in cols]
    cols.append(("public", "orders", "status", 6, "text", "text", None, None, None, "NO", False))
    env["answers"]["postgres"][di.PG_COLUMNS] = cols
    _refresh(env)
    d = de.diff_link(env["ws"]["id"], env["link"]["id"])
    changes = {c["entity"]: {(x["column"], x["change"]) for x in c["changes"]} for c in d["changed"]}
    assert ("email", "type") in changes["public__customers"] and ("email", "nullable") in changes["public__customers"]
    assert {("meta", "removed"), ("status", "added")} <= changes["public__orders"]
    assert d["hand_made"] == ["HAND_MADE"]
    # nothing written by diff
    assert service.get_workspace(env["ws"]["id"]) and next(i for i in service.get_workspace(env["ws"]["id"])["items"] if i["id"] == item["id"])["content"] == edited
    with pytest.raises(ValueError):   # apply to an existing ERD needs a revision
        de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.customers"])
    with pytest.raises(service.PlanRevisionConflict):
        de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.customers"], expected_revision=1)
    out = de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.customers", "public.orders"], expected_revision=item["revision_count"])
    content = out["item"]["content"]
    assert validate_mermaid(content, require_erd=True) == "er"
    assert 'int id PK "surrogate"' in content and "text email UK" in content and "text status" in content
    assert "jsonb meta" in content and out["kept_extra_fields"] == {"public__orders": ["meta"]}   # hand/vanished field kept
    assert "HAND_MADE {" in content and 'HAND_MADE ||--o{ public__orders : "hand"' in content
    assert content.count('public__customers ||--o{ public__orders : "customer_id"') == 1
    side = de.read_erd(content)["sidecar"]
    assert side["tables"]["public__customers"]["x"] == 400 and side["tables"]["public__customers"]["interfaces"] == ["Checkout"]
    assert "db" not in side["tables"].get("HAND_MADE", {})


def test_gone_tables_marked_not_deleted_and_conflicts(env):
    _refresh(env)
    item = de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.customers", "public.orders", "public.secret_stuff"])["item"]
    env["answers"]["postgres"][di.PG_TABLES] = lambda p: [("public", "customers", 1), ("public", "orders", 1)]
    env["answers"]["postgres"][di.PG_COUNT] = [(2,)]
    _refresh(env)
    d = de.diff_link(env["ws"]["id"], env["link"]["id"])
    assert [r["key"] for r in d["removed"]] == ["public.secret_stuff"]
    out = de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.secret_stuff"], expected_revision=item["revision_count"])
    content = out["item"]["content"]
    assert 'public__secret_stuff["(gone from DB)"] {' in content and validate_mermaid(content, require_erd=True) == "er"
    assert de.diff_link(env["ws"]["id"], env["link"]["id"])["summary"]["removed"] == 0
    # a hand-made table with the generated name is never overwritten
    hand = service.create_item(workspace_id=env["ws"]["id"], item_type="erd", content="erDiagram\n    public__customers {\n        int id PK\n    }\n")
    d = de.diff_link(env["ws"]["id"], env["link"]["id"], item_id=hand["id"])
    assert d["target"] == "merge" and d["conflicts"] == []   # merge prefixes the link label
    assert {a["entity"] for a in d["added"]} == {"Shop__public__customers", "Shop__public__orders"}


def test_merge_into_existing_erd_keeps_everything(env):
    _refresh(env)
    main = next(i for i in service.get_workspace(env["ws"]["id"])["items"] if i["item_type"] == "erd") if any(
        i["item_type"] == "erd" for i in service.get_workspace(env["ws"]["id"])["items"]) else service.create_item(workspace_id=env["ws"]["id"], item_type="erd")
    before = main["content"]
    out = de.apply_link(env["ws"]["id"], env["link"]["id"], tables=["public.orders", "public.customers"], item_id=main["id"], expected_revision=main["revision_count"])
    content = out["item"]["content"]
    assert out["target"] == "merge" and "Shop__public__orders {" in content
    for line in before.strip().splitlines():
        if not line.startswith(de.SIDECAR_PREFIX):
            assert line in content
    assert de.link_erd(env["ws"]["id"], env["link"]["id"]) is None   # a merged ERD is not the link's own ERD


def test_cap_and_selection_validation(env):
    _refresh(env)
    for bad in ([], "public.orders", ["nope.table"], [f"s.t{i}" for i in range(301)]):
        with pytest.raises(ValueError):
            de.apply_link(env["ws"]["id"], env["link"]["id"], tables=bad)


def test_mysql_mapping():
    link = {"id": 9, "label": "Legacy"}
    snap = de.sanitise_snapshot({"engine": "mysql", "introspected_at": "2026-11-03T07:00:00+00:00", "tables": [
        {"schema": "shop", "name": "orders", "columns": [
            {"name": "id", "position": 1, "type": "bigint", "pk": True, "unique": True, "nullable": False},
            {"name": "total", "position": 2, "type": "decimal(10,2)", "nullable": True},
            {"name": "kind", "position": 3, "type": "enum(retail,trade)", "nullable": False},
            {"name": "odd name", "position": 4, "type": "text", "nullable": True}]}]}, link)
    content, report = de.build_applied(snap, link, "erDiagram\n", ["shop.orders"], merge=False)
    assert "decimal total" in content and "enum kind" in content and "text odd_name" in content
    side = de.read_erd(content)["sidecar"]["tables"]["shop__orders"]["fields"]
    assert side["total"]["type"] == "decimal(10,2)" and side["kind"]["type"] == "enum(retail,trade)"
    assert validate_mermaid(content, require_erd=True) == "er"
