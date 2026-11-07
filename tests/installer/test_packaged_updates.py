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
