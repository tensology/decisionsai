"""mddtoken 2026-10-22: /api/automations/model-defaults requires the internal token on every method.

Runs in a subprocess with a throwaway HOME + DECISIONS_DB_DIR so the real settings DB and
token files are never touched and import-time paths are isolated.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
from fastapi import FastAPI
from fastapi.testclient import TestClient
from distr.gui.web.routes.automations import create_routes
from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token

app = FastAPI()
app.include_router(create_routes(), prefix="/api")
c = TestClient(app)
url = "/api/automations/model-defaults"
tok = get_internal_api_token()
assert tok
good = {INTERNAL_AUTH_HEADER: tok}
bad = {INTERNAL_AUTH_HEADER: "wrong"}
body = {"backend": "codex", "model_provider": "openai", "model": "gpt-x"}
for m in ("get", "put", "post", "patch"):
    kw = {} if m == "get" else {"json": body}
    assert c.request(m.upper(), url, **kw).status_code == 401, m
    assert c.request(m.upper(), url, headers=bad, **kw).status_code == 401, m
    r = c.request(m.upper(), url, headers=good, **kw)
    assert r.status_code == 200, (m, r.status_code, r.text[:200])
assert c.get(url, headers=good).json()["backend"] == "codex"
print("ALL_OK")
'''


def test_model_defaults_requires_internal_token():
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        env = {**os.environ, "HOME": tmp, "DECISIONS_DB_DIR": os.path.join(tmp, "db"),
               "DECISIONS_TEST_MODE": "1", "PYTHONPATH": str(ROOT)}
        for k in ("DECISIONSAI_INTERNAL_API_TOKEN", "DECISIONS_INTERNAL_API_TOKEN"):
            env.pop(k, None)
        p = subprocess.run([sys.executable, "-c", SCRIPT], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=180)
        assert "ALL_OK" in p.stdout, (p.stdout[-1500:], p.stderr[-3000:])


def test_route_source_guards_every_method():
    src = (ROOT / "distr/gui/web/routes/automations.py").read_text()
    assert '@router.get("/automations/model-defaults", dependencies=[Depends(require_internal_token_dependency)])' in src
    assert 'methods=["PUT", "POST", "PATCH"]' in src
