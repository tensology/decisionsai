"""Throwaway macOS keychain for DB-secret tests. Never the login keychain; deleted afterwards."""
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid

import pytest


def _search_list():
    return subprocess.run(["security", "list-keychains"], capture_output=True, text=True, timeout=5).stdout


@pytest.fixture
def temp_keychain():
    if sys.platform != "darwin" or not shutil.which("security"):
        pytest.skip("macOS security CLI not available")
    before = _search_list()
    directory = tempfile.mkdtemp(prefix="dbsec")
    path = os.path.join(directory, "decisions-test.keychain-db")
    password = secrets.token_hex(16)
    script = f'create-keychain -p "{password}" "{path}"\nunlock-keychain -p "{password}" "{path}"\nset-keychain-settings "{path}"\n'
    subprocess.run(["security", "-i"], input=script, capture_output=True, text=True, timeout=10, check=True)
    assert os.path.isfile(path)
    try:
        yield path
    finally:
        subprocess.run(["security", "delete-keychain", path], capture_output=True, timeout=10)
        shutil.rmtree(directory, ignore_errors=True)
        assert _search_list() == before          # the user's keychain search list is untouched


def unique_service():
    return f"decisions-db-link-test-{uuid.uuid4().hex[:12]}"


class Recorder:
    """Wraps subprocess.run and records every argv / stdin given to `security`."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append({"args": list(args), "input": kwargs.get("input"), "shell": kwargs.get("shell", False)})
        return subprocess.run(args, **kwargs)
