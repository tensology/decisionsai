import contextlib
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base


def _write_png(path, color, size=(8, 8), patch=None):
    from PIL import Image

    image = Image.new("RGB", size, color)
    if patch:
        x0, y0, x1, y1, patch_color = patch
        for x in range(x0, x1):
            for y in range(y0, y1):
                image.putpixel((x, y), patch_color)
    image.save(path)


def _factory(tmp_path):
    import distr.core.db.orchestrator  # noqa: F401

    db_path = tmp_path / "hermes_ui_feedback.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_record_ui_feedback_label_emits_event_and_learning_signal(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorLearnedRule
    from distr.core.orchestrator import record_ui_feedback_label

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        event_id = record_ui_feedback_label(
            label="spacing off",
            reason="The controls feel too loose.",
            ticket_id=12,
            board_id=7,
            project_id=3,
            screenshot_paths=["/tmp/after.png"],
        )

        with get_session() as session:
            event = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == event_id).one()
            rule = session.query(OrchestratorLearnedRule).filter(OrchestratorLearnedRule.scope_id == 7).one()

    assert event.event_type == "ui_feedback_labeled"
    assert event.status == "rejected"
    assert "spacing" in event.summary.lower()
    assert rule.rule_type == "ui_feedback"
    assert "spacing" in rule.summary.lower()


def test_visual_taste_summary_aggregates_ui_feedback_labels(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import build_visual_taste_summary, record_ui_feedback_label

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_ui_feedback_label(
            label="approved",
            reason="Dense table layout worked well.",
            board_id=7,
            screenshot_paths=["/tmp/approved.png"],
        )
        record_ui_feedback_label(
            label="spacing off",
            reason="The toolbar had too much vertical padding.",
            board_id=7,
            screenshot_paths=["/tmp/spacing.png"],
        )
        record_ui_feedback_label(
            label="flow bad",
            reason="It took too many clicks to complete the happy path.",
            board_id=7,
        )

        summary = build_visual_taste_summary(board_id=7)

    assert summary["scope"] == "board"
    assert summary["scope_id"] == 7
    assert summary["total_feedback"] == 3
    assert summary["approval_count"] == 1
    assert summary["rejection_count"] == 2
    assert summary["labels"]["approved"]["count"] == 1
    assert summary["labels"]["spacing_off"]["count"] == 1
    assert summary["labels"]["flow_bad"]["count"] == 1
    assert "Dense table layout" in summary["labels"]["approved"]["recent_reasons"][0]
    assert "vertical padding" in summary["labels"]["spacing_off"]["recent_reasons"][0]


def test_visual_taste_context_formats_reusable_preferences(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import build_visual_taste_context, record_ui_feedback_label

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_ui_feedback_label(
            label="approved",
            reason="Compact controls with clear hierarchy.",
            board_id=7,
        )
        record_ui_feedback_label(
            label="hierarchy unclear",
            reason="Primary action was buried among secondary buttons.",
            board_id=7,
        )

        context = build_visual_taste_context(board_id=7)

    assert "[VISUAL TASTE MEMORY]" in context
    assert "approved" in context.lower()
    assert "hierarchy" in context.lower()
    assert "Compact controls" in context
    assert "Primary action" in context


def test_board_activity_includes_visual_taste_summary(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import list_board_activity, record_ui_feedback_label

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_ui_feedback_label(
            label="style inconsistent",
            reason="Buttons used mismatched visual weight.",
            board_id=7,
        )

        activity = list_board_activity(7)

    assert activity["visual_taste"]["total_feedback"] == 1
    assert activity["visual_taste"]["labels"]["inconsistent_styling"]["count"] == 1


def test_record_ui_quality_validation_fails_when_artifacts_missing(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorValidationRecord
    from distr.core.orchestrator import record_ui_quality_validation

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_id = record_ui_quality_validation(
            artifacts={"before_screenshot": "/tmp/before.png"},
            ticket_id=12,
            board_id=7,
            project_id=3,
        )

        with get_session() as session:
            validation = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == record_id)
                .one()
            )

    assert validation.validation_type == "ui_quality"
    assert validation.verdict == "fail"
    assert "after_screenshot" in validation.observed
    assert "flow_summary" in validation.observed


def test_ui_quality_validation_appends_visual_taste_context(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorValidationRecord
    from distr.core.orchestrator import record_ui_feedback_label, record_ui_quality_validation

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_ui_feedback_label(
            label="approved",
            reason="Compact, scan-friendly panels worked well.",
            board_id=7,
        )
        record_id = record_ui_quality_validation(
            artifacts={
                "after_screenshot": "/tmp/after.png",
                "before_unavailable_reason": "No baseline.",
                "flow_summary": "Opened the panel and completed the happy path.",
                "happy_path_steps": ["open panel", "submit"],
                "click_count": 2,
                "layout_hierarchy_notes": "Kept the panel hierarchy compact and the submit action visually primary.",
            },
            board_id=7,
            standards_context="Ticket-specific UI standards.",
        )

        with get_session() as session:
            validation = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == record_id)
                .one()
            )

    assert validation.verdict == "pass"
    assert "Ticket-specific UI standards." in validation.standards_context
    assert "[VISUAL TASTE MEMORY]" in validation.standards_context
    assert "Compact, scan-friendly" in validation.standards_context


def test_ui_quality_validation_requires_checks_for_repeated_rejection_reasons(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorValidationRecord
    from distr.core.orchestrator import record_ui_feedback_label, record_ui_quality_validation

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    complete_artifacts = {
        "after_screenshot": "/tmp/after.png",
        "before_unavailable_reason": "No previous screenshot.",
        "flow_summary": "Opened the panel and completed the happy path.",
        "happy_path_steps": ["open panel", "submit"],
        "click_count": 2,
        "layout_hierarchy_notes": "Submit remains the primary action.",
    }

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_ui_feedback_label(label="spacing off", reason="Toolbar padding was loose.", board_id=7)
        record_ui_feedback_label(label="spacing off", reason="Rows had inconsistent gaps.", board_id=7)

        failed_id = record_ui_quality_validation(
            artifacts=complete_artifacts,
            board_id=7,
        )
        passed_id = record_ui_quality_validation(
            artifacts={
                **complete_artifacts,
                "taste_checks": {
                    "spacing_off": "Reduced toolbar padding and normalized row gaps against prior feedback.",
                },
            },
            board_id=7,
        )

        with get_session() as session:
            failed = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == failed_id)
                .one()
            )
            passed = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == passed_id)
                .one()
            )

    assert failed.verdict == "fail"
    assert "taste_check:spacing_off" in failed.observed
    assert passed.verdict == "pass"


def test_visual_baseline_set_is_stored_and_listed_by_board(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import (
        create_visual_baseline_set,
        get_visual_baseline_set,
        list_visual_baseline_sets,
    )

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = create_visual_baseline_set(
            name="Gold Admin",
            board_id=7,
            description="Reference screens from the user's preferred admin UI.",
            screens=[
                {
                    "screen_name": "Dashboard",
                    "screenshot_path": "/gold/dashboard.png",
                    "flow_name": "daily overview",
                    "notes": "Dense, scan-friendly cards.",
                }
            ],
        )

        baseline = get_visual_baseline_set(baseline_set_id=baseline_id)
        listed = list_visual_baseline_sets(board_id=7)

    assert baseline["name"] == "Gold Admin"
    assert baseline["scope"] == "board"
    assert baseline["scope_id"] == 7
    assert baseline["screens"][0]["screen_name"] == "Dashboard"
    assert listed[0]["id"] == baseline_id


def test_visual_baseline_can_copy_screenshots_into_stable_storage(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import create_visual_baseline_set, get_visual_baseline_set

    source_path = tmp_path / "source-dashboard.png"
    _write_png(source_path, (24, 36, 48))
    storage_dir = tmp_path / "baseline-store"
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = create_visual_baseline_set(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Dashboard", "screenshot_path": str(source_path)}],
            copy_screenshots=True,
            storage_dir=storage_dir,
        )
        baseline = get_visual_baseline_set(baseline_set_id=baseline_id)

    stored_path = baseline["screens"][0]["screenshot_path"]
    assert stored_path != str(source_path)
    assert stored_path.startswith(str(storage_dir))
    assert Path(stored_path).exists()
    assert Path(stored_path).read_bytes() == source_path.read_bytes()
    assert baseline["screens"][0]["metadata"]["source_screenshot_path"] == str(source_path)


def test_visual_baseline_upsert_adds_and_replaces_screens_in_existing_set(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import get_visual_baseline_set, upsert_visual_baseline_screens

    dashboard_path = tmp_path / "dashboard.png"
    settings_path = tmp_path / "settings.png"
    settings_updated_path = tmp_path / "settings-updated.png"
    _write_png(dashboard_path, (24, 36, 48))
    _write_png(settings_path, (40, 44, 48))
    _write_png(settings_updated_path, (80, 84, 88))
    storage_dir = tmp_path / "baseline-store"
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = upsert_visual_baseline_screens(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Dashboard", "screenshot_path": str(dashboard_path)}],
            copy_screenshots=True,
            storage_dir=storage_dir,
        )
        same_baseline_id = upsert_visual_baseline_screens(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Settings", "screenshot_path": str(settings_path)}],
            copy_screenshots=True,
            storage_dir=storage_dir,
        )
        replaced_baseline_id = upsert_visual_baseline_screens(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Settings", "screenshot_path": str(settings_updated_path)}],
            copy_screenshots=True,
            storage_dir=storage_dir,
        )
        baseline = get_visual_baseline_set(baseline_set_id=baseline_id)

    assert baseline_id == same_baseline_id == replaced_baseline_id
    assert [screen["screen_name"] for screen in baseline["screens"]] == ["Dashboard", "Settings"]
    settings = baseline["screens"][1]
    assert settings["metadata"]["source_screenshot_path"] == str(settings_updated_path)
    assert Path(settings["screenshot_path"]).exists()
    assert Path(settings["screenshot_path"]).read_bytes() == settings_updated_path.read_bytes()


def test_visual_baseline_readiness_reports_missing_screenshot_files(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import create_visual_baseline_set, inspect_visual_baseline_readiness

    existing_path = tmp_path / "dashboard-baseline.png"
    _write_png(existing_path, (24, 36, 48))
    missing_path = tmp_path / "settings-baseline.png"
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = create_visual_baseline_set(
            name="Gold Admin",
            board_id=7,
            screens=[
                {"screen_name": "Dashboard", "screenshot_path": str(existing_path)},
                {"screen_name": "Settings", "screenshot_path": str(missing_path)},
            ],
        )
        readiness = inspect_visual_baseline_readiness(baseline_set_id=baseline_id)

    assert readiness["verdict"] == "fail"
    assert readiness["ready"] is False
    assert readiness["baseline_count"] == 1
    assert readiness["screen_count"] == 2
    assert readiness["existing_screen_count"] == 1
    assert readiness["missing_screen_count"] == 1
    assert readiness["missing"][0]["screen_name"] == "Settings"
    assert readiness["missing"][0]["screenshot_path"] == str(missing_path)
    assert readiness["baselines"][0]["screens"][0]["exists"] is True
    assert readiness["baselines"][0]["screens"][1]["exists"] is False


def test_visual_baseline_readiness_passes_when_all_screenshot_files_exist(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import create_visual_baseline_set, inspect_visual_baseline_readiness

    existing_path = tmp_path / "dashboard-baseline.png"
    _write_png(existing_path, (24, 36, 48))
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = create_visual_baseline_set(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Dashboard", "screenshot_path": str(existing_path)}],
        )
        readiness = inspect_visual_baseline_readiness(baseline_set_id=baseline_id)

    assert readiness["verdict"] == "pass"
    assert readiness["ready"] is True
    assert readiness["missing"] == []


def test_ui_quality_validation_records_visual_baseline_failure(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorValidationRecord
    from distr.core.orchestrator import create_visual_baseline_set, record_ui_quality_validation

    baseline_path = tmp_path / "dashboard-baseline.png"
    _write_png(baseline_path, (24, 36, 48))
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = create_visual_baseline_set(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Dashboard", "screenshot_path": str(baseline_path)}],
        )
        record_id = record_ui_quality_validation(
            artifacts={
                "before_screenshot": "/tmp/before.png",
                "after_screenshot": "/tmp/after.png",
                "baseline_screen_name": "Dashboard",
                "flow_summary": "Opened dashboard and reviewed key metrics.",
                "happy_path_steps": ["open dashboard", "review metrics"],
                "click_count": 1,
                "layout_hierarchy_notes": "Metric card hierarchy should match the reference.",
                "visual_diffs": [
                    {
                        "screen_name": "Dashboard",
                        "status": "fail",
                        "failure_reason": "Metric card hierarchy regressed from the reference.",
                    }
                ],
            },
            board_id=7,
            baseline_set_id=baseline_id,
        )

        with get_session() as session:
            validation = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == record_id)
                .one()
            )

    assert validation.verdict == "fail"
    assert "Metric card hierarchy" in validation.observed
    assert "visual_baseline" in validation.payload


def test_ui_quality_validation_generates_visual_baseline_diff(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorValidationRecord
    from distr.core.orchestrator import create_visual_baseline_set, record_ui_quality_validation

    baseline_path = tmp_path / "dashboard-baseline.png"
    candidate_path = tmp_path / "dashboard-after.png"
    _write_png(baseline_path, (24, 36, 48))
    _write_png(candidate_path, (24, 36, 48), patch=(0, 0, 4, 4, (240, 240, 240)))
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = create_visual_baseline_set(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Dashboard", "screenshot_path": str(baseline_path)}],
        )
        record_id = record_ui_quality_validation(
            artifacts={
                "before_screenshot": str(baseline_path),
                "after_screenshot": str(candidate_path),
                "baseline_screen_name": "Dashboard",
                "flow_summary": "Opened dashboard and reviewed key metrics.",
                "happy_path_steps": ["open dashboard", "review metrics"],
                "click_count": 1,
                "visual_diff_threshold": 0.1,
            },
            board_id=7,
            baseline_set_id=baseline_id,
        )

        with get_session() as session:
            validation = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == record_id)
                .one()
            )

    assert validation.verdict == "fail"
    assert "changed pixels" in validation.observed
    assert "visual_baseline" in validation.payload


def test_ui_quality_validation_uses_baseline_selected_in_artifacts(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorValidationRecord
    from distr.core.orchestrator import create_visual_baseline_set, record_ui_quality_validation

    baseline_path = tmp_path / "dashboard-baseline.png"
    candidate_path = tmp_path / "dashboard-after.png"
    _write_png(baseline_path, (24, 36, 48))
    _write_png(candidate_path, (24, 36, 48), patch=(0, 0, 4, 4, (240, 240, 240)))
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        create_visual_baseline_set(
            name="Gold Admin",
            board_id=7,
            screens=[{"screen_name": "Dashboard", "screenshot_path": str(baseline_path)}],
        )
        record_id = record_ui_quality_validation(
            artifacts={
                "before_screenshot": str(baseline_path),
                "after_screenshot": str(candidate_path),
                "visual_baseline_name": "Gold Admin",
                "baseline_screen_name": "Dashboard",
                "flow_summary": "Opened dashboard and reviewed key metrics.",
                "happy_path_steps": ["open dashboard", "review metrics"],
                "click_count": 1,
                "visual_diff_threshold": 0.1,
            },
            board_id=7,
        )

        with get_session() as session:
            validation = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == record_id)
                .one()
            )

    assert validation.verdict == "fail"
    assert "changed pixels" in validation.observed
    assert "visual_baseline" in validation.payload


def test_approved_baseline_copy_feeds_later_ui_validation(tmp_path):
    from unittest.mock import patch

    from distr.core.db.orchestrator import OrchestratorValidationRecord
    from distr.core.orchestrator import (
        inspect_visual_baseline_readiness,
        record_ui_quality_validation,
        upsert_visual_baseline_screens,
    )

    approved_path = tmp_path / "approved-dashboard.png"
    later_path = tmp_path / "later-dashboard.png"
    drifted_path = tmp_path / "drifted-dashboard.png"
    _write_png(approved_path, (24, 36, 48))
    _write_png(later_path, (24, 36, 48))
    _write_png(drifted_path, (24, 36, 48), patch=(0, 0, 4, 4, (240, 240, 240)))
    storage_dir = tmp_path / "baseline-store"
    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch("distr.core.db.get_session", get_session):
        baseline_id = upsert_visual_baseline_screens(
            name="Gold Admin",
            board_id=7,
            screens=[{
                "screen_name": "Dashboard",
                "screenshot_path": str(approved_path),
                "metadata": {"feedback_event_id": 123},
            }],
            copy_screenshots=True,
            storage_dir=storage_dir,
        )
        readiness = inspect_visual_baseline_readiness(baseline_set_id=baseline_id)
        record_id = record_ui_quality_validation(
            artifacts={
                "before_screenshot": str(approved_path),
                "after_screenshot": str(later_path),
                "visual_baseline_name": "Gold Admin",
                "baseline_screen_name": "Dashboard",
                "flow_summary": "Opened dashboard and reviewed key metrics.",
                "happy_path_steps": ["open dashboard", "review metrics"],
                "click_count": 1,
                "layout_hierarchy_notes": "Matched the approved dashboard density and card hierarchy.",
                "visual_diff_threshold": 0.01,
            },
            board_id=7,
        )
        failed_record_id = record_ui_quality_validation(
            artifacts={
                "before_screenshot": str(approved_path),
                "after_screenshot": str(drifted_path),
                "visual_baseline_name": "Gold Admin",
                "baseline_screen_name": "Dashboard",
                "flow_summary": "Opened dashboard and reviewed key metrics.",
                "happy_path_steps": ["open dashboard", "review metrics"],
                "click_count": 1,
                "layout_hierarchy_notes": "Changed the dashboard card region.",
                "visual_diff_threshold": 0.01,
            },
            board_id=7,
        )

        with get_session() as session:
            validation = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == record_id)
                .one()
            )
            failed_validation = (
                session.query(OrchestratorValidationRecord)
                .filter(OrchestratorValidationRecord.id == failed_record_id)
                .one()
            )

    assert readiness["ready"] is True
    assert readiness["baselines"][0]["screens"][0]["exists"] is True
    assert readiness["baselines"][0]["screens"][0]["screenshot_path"].startswith(str(storage_dir))
    assert validation.verdict == "pass"
    assert "All required UI quality artifacts are present." in validation.observed
    assert '"visual_baseline"' in validation.payload
    assert failed_validation.verdict == "fail"
    assert "changed pixels" in failed_validation.observed
