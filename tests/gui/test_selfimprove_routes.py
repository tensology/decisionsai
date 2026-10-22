"""TestClient coverage for /api/selfimprove (router not live until the next restart)."""
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client_and_rec(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from distr.core.mempalace.selfimprove import approvals
    from distr.gui.web.routes.selfimprove import router
    repo = tmp_path / "proj"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "AGENTS.md").write_text("a\nb\nc\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], cwd=repo, check=True)
    diff = "--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1,3 +1,4 @@\n a\n b\n c\n+d\n"
    rec = approvals.enqueue(target=str(repo / "AGENTS.md"), diff=diff, scope="s", tag="t", reason="r")
    app = FastAPI(); app.include_router(router)
    return TestClient(app), rec, repo


def test_list_approve_and_404(client_and_rec):
    c, rec, repo = client_and_rec
    r = c.get("/api/selfimprove/proposals")
    assert r.status_code == 200 and [p["id"] for p in r.json()["proposals"]] == [rec["id"]]
    assert c.post("/api/selfimprove/proposals/nope123/approve").status_code in (404, 409)
    r = c.post(f"/api/selfimprove/proposals/{rec['id']}/approve", json={})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert (repo / "AGENTS.md").read_text().endswith("d\n")
    assert c.get("/api/selfimprove/proposals").json()["proposals"] == []
    assert len(c.get("/api/selfimprove/proposals?status=all").json()["proposals"]) == 1
    assert c.post(f"/api/selfimprove/proposals/{rec['id']}/approve").status_code == 409


def test_reject_and_path_traversal(client_and_rec):
    c, rec, _ = client_and_rec
    r = c.post(f"/api/selfimprove/proposals/{rec['id']}/reject", json={"note": "no"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert c.post(f"/api/selfimprove/proposals/{rec['id']}/reject").status_code == 409
    assert c.post("/api/selfimprove/proposals/..%2Fetc/reject").status_code in (404, 409)


def test_server_mounts_router_and_writes_need_token():
    src = open("distr/gui/web/server.py", encoding="utf-8").read()
    assert "selfimprove_router" in src
