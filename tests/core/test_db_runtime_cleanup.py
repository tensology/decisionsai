from distr.core.db import get_session
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession


def test_runtime_cleanup_removes_orphaned_ide_bridge_sessions(monkeypatch):
    from distr.core.db import _cleanup_orphaned_test_ide_sessions

    monkeypatch.delenv("DECISIONS_TEST_MODE", raising=False)

    with get_session() as session:
        row = ProjectExecutionSession(
            project_id=999999,
            route_type="ide_bridge",
            route_backend="cursor",
            status="running",
            input_packet="",
        )
        session.add(row)
        session.flush()
        event = ProjectExecutionEvent(
            session_id=row.id,
            event_type="cursor_prompt_submitted",
            status="observed",
            message="test event",
        )
        session.add(event)
        session.commit()
        row_id = row.id
        event_id = event.id

    _cleanup_orphaned_test_ide_sessions()

    with get_session() as session:
        assert session.get(ProjectExecutionSession, row_id) is None
        assert session.get(ProjectExecutionEvent, event_id) is None


def test_runtime_cleanup_keeps_existing_project_sessions(monkeypatch, tmp_path):
    from distr.core.db import _cleanup_orphaned_test_ide_sessions
    from distr.core.db.projects import Project

    monkeypatch.delenv("DECISIONS_TEST_MODE", raising=False)

    project_dir = tmp_path / "real-project"
    project_dir.mkdir()
    with get_session() as session:
        project = Project(
            name="Real Project",
            folder_location=str(project_dir),
            coding_backend="codex",
        )
        session.add(project)
        session.flush()
        row = ProjectExecutionSession(
            project_id=project.id,
            route_type="ide_bridge",
            route_backend="codex",
            status="running",
            input_packet="",
        )
        session.add(row)
        session.commit()
        row_id = row.id

    _cleanup_orphaned_test_ide_sessions()

    with get_session() as session:
        assert session.get(ProjectExecutionSession, row_id) is not None
