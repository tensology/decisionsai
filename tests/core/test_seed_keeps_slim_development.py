"""Startup legacy retirement must not archive the slim "Development" built-in (2026-11-12).

Live evidence: after Paul's 12:28 SAST restart, ensure_builtin_workflows created row 436
"Development" (development-implement-verify) and the startup migration's seed_workflows()
archived it 3 s later because it shares the legacy pipeline's name. An archived built-in
counts as present, so it was never recreated and GET /api/workflows showed only four.
"""
import contextlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import distr.core.db as core_db
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow
from distr.core.workflow import builtin


@pytest.fixture
def session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    import distr.core.workflow.loop_presets as loop_presets
    import distr.core.workflow.service as workflow_service
    import distr.core.workspace_memory.lifecycle as lifecycle
    import distr.core.workspace_memory.provision as provision
    import distr.gui.web.workflow_events as workflow_events
    for module in (core_db, workflow_service, loop_presets):
        monkeypatch.setattr(module, "get_session", get_session)
    monkeypatch.setattr(provision, "bootstrap_workflow", lambda *a, **k: "")
    monkeypatch.setattr(lifecycle, "hook_remove_workspace", lambda *a, **k: True)
    monkeypatch.setattr(workflow_events, "increment_workflow_updated", lambda: None)
    monkeypatch.setattr(builtin, "_SEEDED", False)
    return get_session


def _status(get_session, wf_id):
    with get_session() as db:
        return str(db.get(AutoWorkflow, int(wf_id)).status or "")


def test_startup_retirement_keeps_the_slim_development_builtin(session):
    from distr.core.db.seed_workflows import seed_workflows

    ids = builtin.ensure_builtin_workflows(force=True)
    assert set(ids) == set(builtin.BUILTIN_SLUGS)
    slim = ids[builtin.DEVELOPMENT_SLIM]
    with session() as db:
        legacy = AutoWorkflow(name="Development", description="legacy 11-step", status="draft")
        db.add(legacy)
        db.flush()
        legacy_id = legacy.id

    result = seed_workflows()  # what distr.core.db.migrations runs on every startup

    assert _status(session, slim) != "archived"
    assert _status(session, legacy_id) == "archived"
    assert result["retired_legacy"] == 1
    assert set(builtin.builtin_workflow_ids()) == set(builtin.BUILTIN_SLUGS)


def test_restart_order_seed_then_retire_still_lists_five(session):
    from distr.core.db.seed_workflows import seed_workflows

    builtin.ensure_builtin_workflows(force=True)
    seed_workflows()
    seed_workflows()  # idempotent across restarts
    builtin.ensure_builtin_workflows(force=True)
    visible = builtin.builtin_workflow_ids()
    assert builtin.DEVELOPMENT_SLIM in visible
    assert len(visible) == 5
