import pytest
from distr.core.packaged_support import newer_release


def manifest(version="2.9.6"):
    return {"version": version, "architecture": "arm64", "url": "https://decisions.tensology.com/downloads/DecisionsAI-2.9.6-arm64.dmg", "sha256": "a" * 64}


def test_stable_versions_compare_numerically():
    assert newer_release("2.9.5", manifest("2.10.0"))
    assert not newer_release("2.9.5", manifest("2.9.5"))
    assert not newer_release("2.9.5", manifest("2.9.4"))


@pytest.mark.parametrize("field,value", [("url", "http://decisions.tensology.com/app.dmg"), ("url", "https://evil.example/app.dmg"), ("version", "latest"), ("sha256", ""), ("architecture", "x86_64")])
def test_rejects_untrusted_or_incomplete_manifest(field, value):
    data = manifest()
    data[field] = value
    with pytest.raises(ValueError):
        newer_release("2.9.5", data)


def test_model_download_requires_checksum_and_activates_only_in_user_data(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import threading
    from distr.core import packaged_stt as stt
    payload = b"test-whisper-model"
    model = {"url": "https://decisions.tensology.com/downloads/models/ggml-base.en.bin", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    monkeypatch.setattr(stt, "_root", lambda: tmp_path / "models")
    def fetch(url, **kwargs):
        return io.BytesIO(json.dumps({"whisper": model}).encode() if url == stt.MANIFEST else payload)
    monkeypatch.setattr(stt.urllib.request, "urlopen", fetch)
    progress = []
    path = stt.download_model("whisper", lambda done, total: progress.append((done, total)), threading.Event())
    assert path == str(tmp_path / "models/ggml-base.en.bin")
    assert stt.selected_model()["engine"] == "whisper"
    assert progress[-1] == (len(payload), len(payload))
    model["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        stt.download_model("whisper", lambda *args: None, threading.Event())
    assert stt.selected_model()["engine"] == "whisper"


def test_cancelled_download_does_not_activate_model(tmp_path, monkeypatch):
    import hashlib
    import io
    import json
    import threading
    from distr.core import packaged_stt as stt
    payload = b"test"
    meta = {"url": "https://decisions.tensology.com/model.bin", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    monkeypatch.setattr(stt, "_root", lambda: tmp_path)
    monkeypatch.setattr(stt.urllib.request, "urlopen", lambda url, **kwargs: io.BytesIO(json.dumps({"whisper": meta}).encode() if url == stt.MANIFEST else payload))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        stt.download_model("whisper", lambda *args: None, cancel)
    assert stt.selected_model() is None


def test_selection_cannot_escape_user_models(tmp_path, monkeypatch):
    import json
    from distr.core import packaged_stt as stt
    root = tmp_path / "models"
    root.mkdir()
    (tmp_path / "outside.bin").write_bytes(b"not-a-model")
    (root / "selection.json").write_text(json.dumps({"engine": "whisper", "relative_path": "../outside.bin"}))
    monkeypatch.setattr(stt, "_root", lambda: root)
    assert stt.selected_model() is None
