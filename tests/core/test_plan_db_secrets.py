"""DB link secrets: temp keychain round trip, secret never in argv or logs, session fallback, hostile strings."""
import logging
import subprocess

import pytest

from distr.core.planning import db_secrets as ds
from tests.core.plan_db_keychain import Recorder, temp_keychain, unique_service  # noqa: F401

SECRET = "pa ss$(touch /tmp/x);`id`&|'q'{}%w0rd"


@pytest.fixture(autouse=True)
def _clean_session():
    ds.clear_session_secrets()
    yield
    ds.clear_session_secrets()


def _login_has(service):
    return subprocess.run(["security", "find-generic-password", "-s", service], capture_output=True, timeout=5).returncode == 0


def test_keychain_round_trip_secret_never_in_argv_or_logs(temp_keychain, caplog):
    caplog.set_level(logging.DEBUG)
    rec, service = Recorder(), unique_service()
    store = ds.SecretStore(keychain=temp_keychain, runner=rec, service=service)
    assert store.backend() == "keychain"
    assert store.set(41, SECRET) == "keychain"
    assert store.has(41) is True
    assert store.get(41) == SECRET
    assert store.ref(41) == f"keychain:{service}/41"
    assert store.delete(41) is True and store.has(41) is False and store.get(41) is None
    assert rec.calls and all(not c["shell"] for c in rec.calls)
    for call in rec.calls:
        assert all(SECRET not in str(a) for a in call["args"]), call["args"]
        assert call["args"][-1] == temp_keychain or call["args"][1:] == ["-i"]
    secret_calls = [c for c in rec.calls if c["input"] and SECRET in c["input"]]
    assert len(secret_calls) == 1 and secret_calls[0]["args"][1:] == ["-i"]     # only the write carries it, on stdin
    assert SECRET not in caplog.text and "41" in caplog.text
    assert not _login_has(service)                                               # nothing reached the login keychain


def test_missing_keychain_file_never_falls_back_to_login(tmp_path):
    rec, service = Recorder(), unique_service()
    store = ds.SecretStore(keychain=str(tmp_path / "missing.keychain-db"), runner=rec, service=service)
    assert store.backend() == "session"
    assert store.set(7, "abc123") == "session"
    assert rec.calls == [] and store.has(7) and store.get(7) == "abc123"
    assert not _login_has(service)


@pytest.mark.parametrize("kwargs", [{"platform": "linux"}, {"security": None, "platform": "linux"}])
def test_session_fallback_when_not_macos_or_no_security(kwargs, tmp_path):
    store = ds.SecretStore(keychain=None, service=unique_service(), **kwargs)
    assert store.backend() == "session"
    assert store.set(3, "x-y") == "session" and store.get(3) == "x-y"
    ds.clear_session_secrets()                    # "restart": session secrets are gone
    assert store.has(3) is False


def test_failing_security_switches_to_session(temp_keychain, caplog):
    def broken(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "boom")
    store = ds.SecretStore(keychain=temp_keychain, runner=broken, service=unique_service())
    assert store.set(9, "s3cret-value") == "session"
    assert store.backend() == "session" and store.get(9) == "s3cret-value"
    assert "s3cret-value" not in caplog.text


@pytest.mark.parametrize("bad", ['quo"te', "back\\slash", "new\nline", "nul\x00", "tab\tx", "del\x7f", "",
                                 'x" -U "/Users/paul/Library/Keychains/login.keychain-db', "a" * 1025, None, 123])
def test_hostile_or_invalid_secrets_are_refused_without_running_security(bad, temp_keychain, caplog):
    rec = Recorder()
    store = ds.SecretStore(keychain=temp_keychain, runner=rec, service=unique_service())
    with pytest.raises(ds.SecretError) as info:
        store.set(5, bad)
    assert rec.calls == []
    if isinstance(bad, str) and bad:
        assert bad not in str(info.value) and bad not in caplog.text


def test_account_must_be_a_link_id(temp_keychain):
    store = ds.SecretStore(keychain=temp_keychain, service=unique_service())
    for account in ("abc", "1;2", "", "-1", "1 2"):
        with pytest.raises(ds.SecretError):
            store.set(account, "ok")
