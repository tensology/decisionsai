"""Ensures destructive/bulk file ops cannot skip confirmation dialogs."""

import pytest

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
