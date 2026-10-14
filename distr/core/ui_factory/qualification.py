"""Qualification helpers for local vision models used by UI review."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from distr.core.qualification import CertificationStatus, ProviderCertification, ProviderCertificationStore


def qualify_local_vision_model(
    *,
    model: str,
    image_path: str | Path,
    probe: Callable[[str, Path], dict[str, Any]],
    store: ProviderCertificationStore | None = None,
) -> ProviderCertification:
    image = Path(image_path).expanduser().resolve()
    checked_at = datetime.now(timezone.utc).isoformat()
    evidence: dict[str, Any] = {"image_path": str(image), "live_probe": True}
    status = CertificationStatus.UNAVAILABLE
    if not image.is_file():
        evidence["error"] = "qualification image does not exist"
    else:
        try:
            result = probe(model, image)
            evidence["result"] = result
            required = {"summary", "issues", "contrast_assessment", "alignment_assessment"}
            status = (
                CertificationStatus.CERTIFIED
                if required.issubset(result) and isinstance(result.get("issues"), list)
                else CertificationStatus.LIMITED
            )
        except Exception as exc:
            evidence["error"] = f"{type(exc).__name__}: {exc}"
    certification = ProviderCertification(
        provider="ollama",
        model=model,
        capability="vision_ui_review",
        status=status,
        checked_at=checked_at,
        evidence=evidence,
    )
    return (store or ProviderCertificationStore()).record(certification)
