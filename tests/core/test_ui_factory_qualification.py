from __future__ import annotations

from pathlib import Path

from distr.core.qualification import CertificationStatus, ProviderCertificationStore
from distr.core.ui_factory.qualification import qualify_local_vision_model


def test_vision_qualification_requires_structured_live_probe(tmp_path: Path):
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake image bytes")
    store = ProviderCertificationStore(tmp_path / "certifications.json")
    result = qualify_local_vision_model(
        model="local-vision",
        image_path=image,
        probe=lambda _model, _path: {
            "summary": "Usable",
            "issues": [],
            "contrast_assessment": "AA",
            "alignment_assessment": "aligned",
        },
        store=store,
    )
    assert result.status is CertificationStatus.CERTIFIED
    assert store.get("ollama", "local-vision", "vision_ui_review").status is CertificationStatus.CERTIFIED


def test_vision_qualification_marks_incomplete_probe_limited(tmp_path: Path):
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake image bytes")
    result = qualify_local_vision_model(
        model="local-vision",
        image_path=image,
        probe=lambda _model, _path: {"summary": "No structured review"},
        store=ProviderCertificationStore(tmp_path / "certifications.json"),
    )
    assert result.status is CertificationStatus.LIMITED
