import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.hermes import HermesVisualBaselineSet


def _session_ctx_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def session_ctx():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return session_ctx, factory


def test_visual_baseline_tool_is_registered_for_voice_routing():
    from distr.core.agent.tool_intents import forced_tool_names_for_text
    from distr.core.agent.tools.loader import _get_tool_definitions

    assert ("VisualBaselineTool", {}) in _get_tool_definitions()
    assert "visual_baseline" in forced_tool_names_for_text("create a visual baseline called Gold Admin")
    assert "visual_baseline" in forced_tool_names_for_text("list my visual baselines")
    assert "visual_baseline" in forced_tool_names_for_text("are my visual baselines ready")
    assert "visual_baseline" in forced_tool_names_for_text("save this screenshot as a gold standard screen")


def test_visual_baseline_tool_creates_gets_and_lists_baselines(monkeypatch):
    from distr.core.agent.tools.step_runner.workflow_tools import VisualBaselineTool

    session_ctx, factory = _session_ctx_factory()
    monkeypatch.setattr("distr.core.hermes.get_session", session_ctx)
    monkeypatch.setattr("distr.core.db.get_session", session_ctx)

    tool = VisualBaselineTool()
    created = tool._run(
        action="create",
        name="Gold Admin",
        board_id=7,
        description="Paul-approved admin UI reference.",
        screens=[
            {
                "screen_name": "Dashboard",
                "screenshot_path": "/gold/dashboard.png",
                "flow_name": "overview",
                "notes": "Dense, scan-friendly layout.",
            }
        ],
    )

    assert "Gold Admin" in created
    assert "REFERENCE" in created

    with factory() as db:
        baseline = db.query(HermesVisualBaselineSet).one()
        baseline_id = baseline.id
        assert baseline.name == "Gold Admin"
        assert baseline.scope == "board"
        assert baseline.scope_id == 7

    listed = tool._run(action="list", board_id=7)
    assert "Gold Admin" in listed
    assert "/gold/dashboard.png" in listed

    got = tool._run(action="get", baseline_id=baseline_id)
    assert "Dashboard" in got
    assert "Dense, scan-friendly" in got


def test_visual_baseline_tool_can_store_durable_screenshot_copy(monkeypatch, tmp_path):
    from distr.core.agent.tools.step_runner.workflow_tools import VisualBaselineTool

    session_ctx, _factory = _session_ctx_factory()
    monkeypatch.setattr("distr.core.hermes.get_session", session_ctx)
    monkeypatch.setattr("distr.core.db.get_session", session_ctx)

    source_path = tmp_path / "dashboard.png"
    source_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    storage_dir = tmp_path / "baseline-store"

    tool = VisualBaselineTool()
    created = tool._run(
        action="create",
        name="Gold Admin",
        board_id=7,
        copy_screenshots=True,
        storage_dir=str(storage_dir),
        screens=[{"screen_name": "Dashboard", "screenshot_path": str(source_path)}],
    )

    assert "Gold Admin" in created
    assert str(storage_dir) in created
    assert str(source_path) not in created


def test_visual_baseline_tool_reports_readiness(monkeypatch, tmp_path):
    from distr.core.agent.tools.step_runner.workflow_tools import VisualBaselineTool

    session_ctx, _factory = _session_ctx_factory()
    monkeypatch.setattr("distr.core.hermes.get_session", session_ctx)
    monkeypatch.setattr("distr.core.db.get_session", session_ctx)

    existing_path = tmp_path / "dashboard.png"
    existing_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    missing_path = tmp_path / "settings.png"

    tool = VisualBaselineTool()
    tool._run(
        action="create",
        name="Gold Admin",
        board_id=7,
        screens=[
            {"screen_name": "Dashboard", "screenshot_path": str(existing_path)},
            {"screen_name": "Settings", "screenshot_path": str(missing_path)},
        ],
    )

    readiness = tool._run(action="readiness", board_id=7)

    assert "not ready" in readiness.lower()
    assert "REFERENCE" in readiness
    assert "Settings" in readiness
    assert str(missing_path) in readiness
