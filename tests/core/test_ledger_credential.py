"""Owner-only ledger credential. Synthetic files only; the secret is never printed."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _stub_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    sys.modules[name] = module


_stub_package("distr.gui.web", ROOT / "distr/gui/web")

from distr.gui.web.security import (
    INTERNAL_AUTH_HEADER,
    LEDGER_AUTH_HEADER,
    LedgerProvisionError,
    get_internal_api_token,
    ledger_header_authorizes_write,
    provision_ledger_operator_token,
    require_internal_token_request,
)

ORIGINAL_API_DOC = "c9a466a98b8aee8356c7666709a2b936b00f64dbc5efcea29299c6ae37840f14"
ORIGINAL_README = "e170d387856b4ace4a7f4a4c253415c7ae2302c7be0323fb7c82b99e05c4b0f8"
DOC_BULLET = ""


@pytest.fixture()
def credential_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    internal = tmp_path / "internal_api_token"
    ledger = tmp_path / "time_ledger_operator_token"
    internal.write_text("internal-test-credential-value-0123456789\n", encoding="utf-8")
    internal.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DECISIONSAI_INTERNAL_TOKEN_FILE", str(internal))
    monkeypatch.setenv("DECISIONSAI_LEDGER_TOKEN_FILE", str(ledger))
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "internal-test-credential-value-0123456789")
    return {"home": home, "internal": internal, "ledger": ledger}


def test_provision_requires_owner_boundary_and_hides_the_secret(credential_home, capsys):
    with pytest.raises(LedgerProvisionError) as missing:
        provision_ledger_operator_token(confirm="nope")
    assert missing.value.code == 2
    assert not credential_home["ledger"].exists()
    credential_home["internal"].chmod(0o644)
    with pytest.raises(LedgerProvisionError) as loose:
        provision_ledger_operator_token(confirm="provision-time-ledger")
    assert loose.value.code == 3
    assert not credential_home["ledger"].exists()
    credential_home["internal"].chmod(0o600)
    created = provision_ledger_operator_token(confirm="provision-time-ledger")
    secret = credential_home["ledger"].read_text(encoding="utf-8").strip()
    assert created["created"] is True and created["secret_printed"] is False
    assert secret not in str(created)
    assert stat.S_IMODE(credential_home["ledger"].stat().st_mode) == 0o600
    again = provision_ledger_operator_token(confirm="provision-time-ledger")
    assert again["already_present"] is True
    assert credential_home["ledger"].read_text(encoding="utf-8").strip() == secret
    assert secret not in capsys.readouterr().out


def test_cli_output_does_not_contain_the_secret(credential_home):
    env = os.environ.copy()
    env.update({
        "HOME": str(credential_home["home"]),
        "DECISIONSAI_INTERNAL_TOKEN_FILE": str(credential_home["internal"]),
        "DECISIONSAI_LEDGER_TOKEN_FILE": str(credential_home["ledger"]),
        "DECISIONSAI_INTERNAL_API_TOKEN": "internal-test-credential-value-0123456789",
    })
    completed = subprocess.run(
        [sys.executable, str(ROOT / "distr/gui/web/security.py"), "provision-time-ledger", "--confirm", "provision-time-ledger"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    secret = credential_home["ledger"].read_text(encoding="utf-8").strip()
    assert secret not in completed.stdout
    assert secret not in completed.stderr
    assert "secret_printed: false" in completed.stdout
    internal = credential_home["internal"].read_text(encoding="utf-8")
    assert internal.strip() not in completed.stdout


def test_concurrent_provision_keeps_one_secret(credential_home):
    results = []
    errors = []

    def once():
        try:
            results.append(provision_ledger_operator_token(confirm="provision-time-ledger"))
        except LedgerProvisionError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert sum(1 for row in results if row["created"]) == 1
    assert sum(1 for row in results if row["already_present"]) == 1
    assert all(row["secret_printed"] is False for row in results)


def test_ledger_credential_is_scoped_and_internal_clients_still_pass(credential_home):
    provision_ledger_operator_token(confirm="provision-time-ledger")
    secret = credential_home["ledger"].read_text(encoding="utf-8").strip()
    app = FastAPI()

    @app.middleware("http")
    async def same_boundary(request: Request, call_next):
        path = request.url.path
        method = request.method
        if path.startswith("/api/") and method != "GET" and path != "/api/harness/events":
            if not ledger_header_authorizes_write(path, method, request.headers.get(LEDGER_AUTH_HEADER, "")):
                try:
                    require_internal_token_request(request)
                except Exception as exc:
                    status = getattr(exc, "status_code", 401)
                    return JSONResponse({"detail": "Unauthorized"}, status_code=status)
        return await call_next(request)

    @app.post("/api/workflows/studio/time-ledger/backups")
    def ledger_backup():
        return {"ok": True}

    @app.post("/api/chats")
    def other_write():
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/api/workflows/studio/time-ledger/backups").status_code == 401
    assert client.post("/api/workflows/studio/time-ledger/backups", headers={LEDGER_AUTH_HEADER: "wrong"}).status_code == 401
    assert client.post("/api/chats", headers={LEDGER_AUTH_HEADER: secret}).status_code == 401
    allowed = client.post("/api/workflows/studio/time-ledger/backups", headers={LEDGER_AUTH_HEADER: secret})
    assert allowed.status_code == 200 and secret not in allowed.text
    internal = client.post("/api/chats", headers={INTERNAL_AUTH_HEADER: get_internal_api_token()})
    assert internal.status_code == 200
    still = client.post(
        "/api/workflows/studio/time-ledger/backups",
        headers={INTERNAL_AUTH_HEADER: get_internal_api_token()},
    )
    assert still.status_code == 200
    credential_home["ledger"].unlink()
    assert client.post("/api/workflows/studio/time-ledger/backups", headers={LEDGER_AUTH_HEADER: secret}).status_code == 401
    assert client.post("/api/chats", headers={INTERNAL_AUTH_HEADER: get_internal_api_token()}).status_code == 200


def test_docs_keep_the_installed_guide_bytes():
    import hashlib
    text = (ROOT / "docs/api.md").read_text(encoding="utf-8")
    assert "\n## Time ledger operator credential\n" in text
    start = text.index("\n## Time ledger operator credential\n")
    end = text.index("\n## Known gaps\n")
    restored = text[:start] + text[end:]
    assert hashlib.sha256(restored.encode("utf-8")).hexdigest() == ORIGINAL_API_DOC
    readme = (ROOT / "README.md").read_bytes()
    prefix = readme.split(b"\n## Time ledger operator credential\n", 1)[0]
    assert hashlib.sha256(prefix).hexdigest() == ORIGINAL_README
