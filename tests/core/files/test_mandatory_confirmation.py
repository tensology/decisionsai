"""Ensures destructive/bulk file ops cannot skip confirmation dialogs."""

import pytest

from distr.core.agent.services.safety import interceptor
from distr.core.agent.tools.files.file_operations import FileOperationsTool
from distr.core.agent.tools.system.execute_code import ExecuteCodeTool
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


def test_reportlab_existing_pdf_still_requires_confirmation(
    fs: FileOperationSafety,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    target = tmp_path / "90_day_tracker.pdf"
    target.write_bytes(b"existing")
    code = (
        "from reportlab.pdfgen import canvas\n"
        f'c = canvas.Canvas("{target}")\n'
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
        "Replace a 90-day tracker PDF",
        event_queue=object(),
        confirmation_results_dict={},
    )

    assert allowed is False
    assert plan["will_overwrite"] is True


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


def test_execute_code_creates_new_path_file_without_confirmation(tmp_path) -> None:
    target = tmp_path / "notes.txt"

    result = ExecuteCodeTool()._execute_code_once(
        f"from pathlib import Path\nPath({str(target)!r}).write_text('created')",
        "Create a notes file",
    )

    assert target.read_text() == "created"
    assert "cancelled" not in result.lower()


def test_telegram_reportlab_pdf_creation_end_to_end(tmp_path) -> None:
    target = tmp_path / "90_day_tracker.pdf"
    code = (
        "from reportlab.pdfgen import canvas\n"
        f"c = canvas.Canvas({str(target)!r})\n"
        "c.drawString(72, 720, '90 Day Tracker')\n"
        "c.save()\n"
        f"result = {str(target)!r}\n"
    )

    result = ExecuteCodeTool()._run(
        code=code,
        description="Create the requested 90-day tracker PDF",
        is_telegram_request=True,
    )

    assert target.read_bytes().startswith(b"%PDF")
    assert "ACTION REQUIRED: Call send_file_to_telegram" in result


def test_execute_code_blocks_unconfirmed_small_edit(tmp_path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("original")

    result = ExecuteCodeTool()._execute_code_once(
        f"from pathlib import Path\nPath({str(target)!r}).write_text('changed')",
        "Make a small edit",
    )

    assert target.read_text() == "original"
    assert "Modify 1 existing file(s)." in result


def test_execute_code_applies_small_edit_after_confirmation(tmp_path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("original")
    confirmation_results = {}
    requests = []

    class ConfirmingEventQueue:
        def put(self, item, block=False) -> None:
            requests.append(item)
            if item[0] == "file_operation_confirmation_request":
                confirmation_results[item[1]["confirmation_id"]] = {"confirmed": True}

    result = ExecuteCodeTool(
        event_queue=ConfirmingEventQueue(),
        confirmation_results_dict=confirmation_results,
    )._execute_code_once(
        f"from pathlib import Path\nPath({str(target)!r}).write_text('changed')",
        "Make a small edit",
    )

    assert target.read_text() == "changed"
    assert requests[0][0] == "file_operation_confirmation_request"
    assert requests[0][1]["plan"]["will_overwrite"] is True
    assert "cancelled" not in result.lower()


def test_execute_code_blocks_recursive_folder_delete_before_execution(tmp_path) -> None:
    folder = tmp_path / "keep"
    folder.mkdir()
    (folder / "one.txt").write_text("one")
    (folder / "two.txt").write_text("two")

    result = ExecuteCodeTool()._execute_code_once(
        f"import shutil\nshutil.rmtree({str(folder)!r})",
        "Create a document",
    )

    assert folder.is_dir()
    assert "bulk deletion is disabled" in result


def test_execute_code_blocks_unconfirmed_single_file_delete(tmp_path) -> None:
    target = tmp_path / "keep.txt"
    target.write_text("keep")

    result = ExecuteCodeTool()._execute_code_once(
        f"import os\nos.remove({str(target)!r})",
        "Create a document",
    )

    assert target.read_text() == "keep"
    assert "Delete 1 file(s)." in result


def test_execute_code_blocks_bulk_file_creation_without_confirmation(tmp_path) -> None:
    targets = [tmp_path / f"file-{index}.txt" for index in range(11)]
    code = "\n".join(
        f"open({str(target)!r}, 'w').write('created')" for target in targets
    )

    result = ExecuteCodeTool()._execute_code_once(code, "Create a batch of files")

    assert not any(target.exists() for target in targets)
    assert "Create 11 new file(s)." in result


def test_file_operations_create_read_and_refuse_directory_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    target = tmp_path / "notes.txt"
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "keep.txt").write_text("keep")
    tool = FileOperationsTool()
    monkeypatch.setattr(tool, "_resolve_folder_path", lambda path: path)

    assert tool._run("create", str(target), content="hello").startswith("File created:")
    assert tool._run("read", str(target)) == "File content:\nhello"
    delete_result = tool._run("delete", str(folder))

    assert folder.is_dir()
    assert "Directory deletion through DecisionsAI is disabled" in delete_result
