from pathlib import Path


def test_browser_bridge_creates_isolated_artifact_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("distr.core.browser_bridge.BROWSER_BRIDGE_ROOT", tmp_path)

    from distr.core.browser_bridge import create_browser_artifact_session

    first = create_browser_artifact_session(surface="playwright", project_id=1, workflow_id=2)
    second = create_browser_artifact_session(surface="playwright", project_id=1, workflow_id=2)

    assert first.session_id != second.session_id
    assert first.artifact_dir != second.artifact_dir
    assert Path(first.artifact_dir).is_dir()
    assert Path(second.artifact_dir).is_dir()
    assert first.console_log_path.endswith("console.json")
    assert first.screenshot_path("result.png").startswith(first.artifact_dir)


def test_browser_bridge_records_snapshot_with_orchestration_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr("distr.core.browser_bridge.BROWSER_BRIDGE_ROOT", tmp_path)
    emitted = []
    monkeypatch.setattr(
        "distr.core.browser_bridge.emit_orchestration_event",
        lambda **kwargs: emitted.append(kwargs) or 123,
    )

    from distr.core.browser_bridge import create_browser_artifact_session, record_browser_snapshot

    session = create_browser_artifact_session(
        surface="playwright",
        project_id=1,
        workflow_id=2,
        run_id=3,
        step_id=4,
        execution_session_id=5,
    )
    screenshot = Path(session.screenshot_path("result.png"))
    screenshot.write_bytes(b"fakepng")

    event_id = record_browser_snapshot(
        session,
        status="completed",
        summary="Captured browser state.",
        url="http://localhost:3000",
        screenshot_path=str(screenshot),
        console_logs={"errors": []},
    )

    assert event_id == 123
    assert emitted[0]["event_type"] == "browser_snapshot_captured"
    assert emitted[0]["project_id"] == 1
    assert emitted[0]["workflow_id"] == 2
    assert emitted[0]["run_id"] == 3
    assert emitted[0]["step_id"] == 4
    assert emitted[0]["execution_session_id"] == 5
    assert emitted[0]["payload"]["surface"] == "browser"
    assert emitted[0]["payload"]["subtype"] == "browser_snapshot_captured"
    assert emitted[0]["payload"]["url"] == "http://localhost:3000"
