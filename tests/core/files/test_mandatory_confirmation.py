"""Ensures destructive/bulk file ops cannot skip confirmation dialogs."""

import pytest

from distr.core.agent.services.safety import interceptor
from distr.core.files.safety import FileOperationSafety, OperationType


@pytest.fixture
def fs() -> FileOperationSafety:
    return FileOperationSafety(log_dir="/tmp/decisions_mandatory_confirmation_test_logs")


def test_delete_always_mandatory(fs: FileOperationSafety) -> None:
    assert fs.cannot_bypass_file_confirmation(operation_type="DELETE", plan=None) is True
    assert fs.cannot_bypass_file_confirmation(operation_type="delete", plan={"file_count": 1}) is True


def test_destructive_classification_mandatory(fs: FileOperationSafety) -> None:
    assert fs.cannot_bypass_file_confirmation(
        classified_operation_type=OperationType.DESTRUCTIVE,
    ) is True


def test_bulk_plan_mandatory(fs: FileOperationSafety) -> None:
    plan = {"file_count": fs.MAX_FILES_WITHOUT_EXTRA_CONFIRMATION + 1}
    assert fs.cannot_bypass_file_confirmation(operation_type="MOVE", plan=plan) is True


def test_small_write_may_bypass(fs: FileOperationSafety) -> None:
    plan = {
        "will_delete": False,
        "high_risk": False,
        "file_count": 3,
        "files_to_modify": 1,
    }
    assert fs.cannot_bypass_file_confirmation(operation_type="WRITE", plan=plan) is False


def test_will_delete_flag_mandatory(fs: FileOperationSafety) -> None:
    plan = {"will_delete": True, "file_count": 1}
    assert fs.cannot_bypass_file_confirmation(operation_type="WRITE", plan=plan) is True


def test_reportlab_new_pdf_is_auto_approved(
    fs: FileOperationSafety,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    target = tmp_path / "90_day_tracker.pdf"
    code = (
        "from reportlab.pdfgen import canvas\n"
        f'c = canvas.Canvas("{target}")\n'
        "c.save()\n"
    )
    monkeypatch.setattr(interceptor, "get_file_safety", lambda: fs)
    monkeypatch.setattr(
        interceptor,
        "_request_confirmation_via_queue",
        lambda *args, **kwargs: pytest.fail("new document creation requested confirmation"),
    )
    monkeypatch.setattr(
        interceptor,
        "confirm_file_operations_with_plan",
        lambda *args, **kwargs: pytest.fail("new document creation requested confirmation"),
    )

    allowed, plan = interceptor.check_and_confirm_code_execution(
        code,
        "python",
        "Write a 90-day tracker PDF",
        event_queue=object(),
        confirmation_results_dict={},
    )

    assert allowed is True
    assert plan["files_to_create"] == 1
    assert plan["will_overwrite"] is False


def test_direct_new_document_write_is_auto_approved(
    fs: FileOperationSafety,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    target = tmp_path / "notes.docx"
    monkeypatch.setattr(interceptor, "get_file_safety", lambda: fs)
    monkeypatch.setattr(
        interceptor,
        "confirm_file_operations_with_plan",
        lambda *args, **kwargs: pytest.fail("new document creation requested confirmation"),
    )

    allowed, plan = interceptor.check_and_confirm_direct_file_operation(
        "WRITE",
        str(target),
        task="Create a document",
    )

    assert allowed is True
    assert plan["files_to_create"] == 1
    assert plan["will_overwrite"] is False


def test_new_document_in_blocked_root_still_requires_confirmation(
    fs: FileOperationSafety,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = (
        "from reportlab.pdfgen import canvas\n"
        'c = canvas.Canvas("/System/tracker.pdf")\n'
        "c.save()\n"
    )
    monkeypatch.setattr(interceptor, "get_file_safety", lambda: fs)
    monkeypatch.setattr(
        interceptor,
        "_request_confirmation_via_queue",
        lambda *args, **kwargs: (False, args[5]),
    )

    allowed, plan = interceptor.check_and_confirm_code_execution(
        code,
        "python",
        "Write a PDF in a protected folder",
        event_queue=object(),
        confirmation_results_dict={},
    )

    assert allowed is False
    assert plan["high_risk"] is True
