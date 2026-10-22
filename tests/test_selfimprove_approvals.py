import json, subprocess, sys
from pathlib import Path


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from distr.core.mempalace.selfimprove import approvals
    repo = tmp_path / "proj"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "AGENTS.md").write_text("a\nb\nc\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], cwd=repo, check=True)
    diff = "--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1,3 +1,4 @@\n a\n b\n c\n+d\n"
    return approvals, repo, diff


def test_classify():
    from distr.core.mempalace.selfimprove.approvals import classify
    assert classify("/x/SKILL.md", "")[0] == "low"
    assert classify("/x/router.py", "")[0] == "high"
    assert classify("/x/db/migrations.md", "")[0] == "high"
    assert classify("/x/foo.py", "")[0] == "high"


def test_enqueue_never_applies_and_approve_applies(tmp_path, monkeypatch):
    ap, repo, diff = _setup(tmp_path, monkeypatch)
    rec = ap.enqueue(target=str(repo / "AGENTS.md"), diff=diff, scope="s", tag="t", reason="r")
    assert rec["status"] == "pending" and rec["risk"] == "low"
    assert (repo / "AGENTS.md").read_text() == "a\nb\nc\n"
    assert ap.enqueue(target=str(repo / "AGENTS.md"), diff=diff)["id"] == rec["id"]
    out = ap.approve(rec["id"])
    assert out["status"] == "applied" and (repo / "AGENTS.md").read_text().endswith("d\n")
    head = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
    assert len(head.strip().splitlines()) == 1  # nothing committed
    subprocess.run(out["revert_cmd"].split(), check=True)
    assert (repo / "AGENTS.md").read_text() == "a\nb\nc\n"
    log = [json.loads(l) for l in ap.log_path().read_text().splitlines()]
    assert [l["action"] for l in log] == ["proposed", "approved-applied"]


def test_dirty_and_reject(tmp_path, monkeypatch):
    ap, repo, diff = _setup(tmp_path, monkeypatch)
    rec = ap.enqueue(target=str(repo / "AGENTS.md"), diff=diff)
    (repo / "AGENTS.md").write_text("a\nb\nc\n\n")
    import pytest
    with pytest.raises(ValueError):
        ap.approve(rec["id"])
    assert ap.reject(rec["id"], note="no")["status"] == "rejected"
    with pytest.raises(ValueError):
        ap.approve(rec["id"])
    with pytest.raises(ValueError):
        ap.get("../etc")
