"""Planning fixes: bundled trees stay out of the scan, captured pages fill empty cues,
failed captures are not retried on every open, and unchanged sources skip the open-time scan."""
import json
import time
from pathlib import Path

from distr.core.planning import iface_shots, project_scan, service


def test_build_output_and_vendored_packs_are_not_scanned(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("x = 1\n")
    release = tmp_path / "installer" / "release" / "pack"
    release.mkdir(parents=True)
    (release / "models.py").write_text("class Cart: pass\n")
    vendored = tmp_path / "plugins" / "shop"
    vendored.mkdir(parents=True)
    (vendored / project_scan._VENDOR_MARKER).write_text("{}")
    (vendored / "models.py").write_text("class Customer: pass\n")
    names = {p.relative_to(tmp_path).as_posix() for p in project_scan._source_files(tmp_path)}
    assert "app/models.py" in names
    assert not any(n.startswith(("installer/release", "plugins/shop")) for n in names)


def test_captured_page_fills_only_an_empty_cue(tmp_path: Path):
    planning = tmp_path / "planning"
    planning.mkdir()
    cue = {"fields": [{"label": "Username"}], "buttons": ["Log in"]}
    (planning / "admin.json").write_text(json.dumps({"url": "https://example.test/admin/", "cues": cue}))
    (planning / "home.json").write_text(json.dumps({"url": "https://example.test/", "cues": cue}))
    source_cue = {"fields": [{"label": "Search"}]}
    context = {"routes": [{"url": "/admin/"}, {"url": "/"}], "template_cues": {"/": source_cue}}
    assert project_scan._apply_captured_cues(tmp_path, context) == 1
    assert context["template_cues"]["/admin/"]["observed"] is True
    assert context["template_cues"]["/admin/"]["fields"] == cue["fields"]
    assert context["template_cues"]["/"] is source_cue


def test_failed_capture_is_remembered_then_forgotten(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(iface_shots, "_ATTEMPT_FILE", tmp_path / "attempts.json")
    assert iface_shots._recent_attempt(4, "admin") is None
    iface_shots._record_attempt(4, "admin", "timeout")
    assert iface_shots._recent_attempt(4, "admin")["reason"] == "timeout"
    monkeypatch.setattr(iface_shots, "_RETRY_SECONDS", 0)
    assert iface_shots._recent_attempt(4, "admin") is None
    iface_shots._record_attempt(4, "admin", None)
    assert json.loads((tmp_path / "attempts.json").read_text()) == {}


def test_browser_env_drops_dyld_paths(monkeypatch):
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
    env = iface_shots._browser_env()
    assert not any(key.startswith("DYLD_") for key in env)


def test_open_skips_scan_while_sources_are_unchanged(tmp_path: Path, monkeypatch):
    (tmp_path / "views.py").write_text("x = 1\n")
    monkeypatch.setattr(service, "_SCAN_STAMP_DIR", tmp_path / "stamps")
    monkeypatch.setattr(service, "_project_root_for", lambda project_id: tmp_path.resolve())
    assert service._scan_is_current(9, 1) is False
    service._write_scan_stamp(9, 1)
    assert service._scan_is_current(9, 1) is True
    time.sleep(0.01)
    (tmp_path / "views.py").write_text("x = 22\n")
    assert service._scan_is_current(9, 1) is False
