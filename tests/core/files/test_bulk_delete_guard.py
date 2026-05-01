"""Policy tests: bulk deletion blocked in execute_code static scan."""

from distr.core.files.user_library_guard import (
    EXECUTE_CODE_BULK_DELETE_REFUSAL,
    scan_execute_code_forbidden_bulk_delete,
)


def test_single_remove_allowed():
    assert scan_execute_code_forbidden_bulk_delete("os.remove('/tmp/a.txt')") is None


def test_rmtree_blocked():
    msg = scan_execute_code_forbidden_bulk_delete("import shutil; shutil.rmtree('/tmp/x')")
    assert msg == EXECUTE_CODE_BULK_DELETE_REFUSAL


def test_rm_rf_blocked():
    msg = scan_execute_code_forbidden_bulk_delete('subprocess.run(["rm","-rf","/tmp/x"])')
    assert msg == EXECUTE_CODE_BULK_DELETE_REFUSAL


def test_listdir_loop_blocked():
    code = "for n in os.listdir(d):\n    os.remove(os.path.join(d, n))"
    assert scan_execute_code_forbidden_bulk_delete(code) == EXECUTE_CODE_BULK_DELETE_REFUSAL


def test_two_explicit_removes_blocked():
    code = "os.remove('a')\nos.remove('b')"
    assert scan_execute_code_forbidden_bulk_delete(code) == EXECUTE_CODE_BULK_DELETE_REFUSAL


def test_find_delete_blocked():
    code = "subprocess.run(['find', '/tmp', '-delete'])"
    assert scan_execute_code_forbidden_bulk_delete(code) == EXECUTE_CODE_BULK_DELETE_REFUSAL
