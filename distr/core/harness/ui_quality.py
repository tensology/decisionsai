"""UI quality evidence and feedback helpers for Hermes."""

from __future__ import annotations

from pathlib import Path
from typing import Any


REQUIRED_UI_ARTIFACT_FIELDS = (
    "after_screenshot",
    "flow_summary",
    "happy_path_steps",
    "click_count",
    "layout_hierarchy_notes",
)

FEEDBACK_LABELS = {
    "approved",
    "flow_bad",
    "spacing_off",
    "hierarchy_unclear",
    "inconsistent_styling",
    "too_many_clicks",
    "rejected_other",
}

LABEL_ALIASES = {
    "approve": "approved",
    "approved": "approved",
    "flow bad": "flow_bad",
    "bad flow": "flow_bad",
    "spacing off": "spacing_off",
    "hierarchy unclear": "hierarchy_unclear",
    "inconsistent styling": "inconsistent_styling",
    "style inconsistent": "inconsistent_styling",
    "too many clicks": "too_many_clicks",
}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _taste_check_present(data: dict[str, Any], label: str) -> bool:
    checks = data.get("taste_checks")
    if isinstance(checks, dict) and _present(checks.get(label)):
        return True
    return _present(data.get(f"{label}_check"))


def _taste_requirements(taste_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    summary = taste_summary or {}
    labels = summary.get("labels") if isinstance(summary.get("labels"), dict) else {}
    requirements: list[dict[str, Any]] = []
    for label, details in labels.items():
        if not isinstance(details, dict) or bool(details.get("approved")):
            continue
        count = int(details.get("count") or 0)
        if count < 2:
            continue
        normalized = normalize_feedback_label(str(label))
        requirements.append({
            "label": normalized,
            "count": count,
            "recent_reasons": list(details.get("recent_reasons") or [])[:3],
        })
    return sorted(requirements, key=lambda item: (-int(item["count"]), item["label"]))


def evaluate_ui_artifacts(
    artifacts: dict[str, Any] | None,
    *,
    taste_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether a UI result has enough evidence to be completed."""
    data = artifacts or {}
    missing = []
    for field in REQUIRED_UI_ARTIFACT_FIELDS:
        value = data.get(field)
        if field == "layout_hierarchy_notes":
            value = value or data.get("layout_notes") or data.get("hierarchy_notes")
        if not _present(value):
            missing.append(field)
    if not _present(data.get("before_screenshot")) and not _present(data.get("before_unavailable_reason")):
        missing.append("before_screenshot_or_reason")
    taste_requirements = _taste_requirements(taste_summary)
    for requirement in taste_requirements:
        label = str(requirement["label"])
        if not _taste_check_present(data, label):
            missing.append(f"taste_check:{label}")
    return {
        "verdict": "pass" if not missing else "fail",
        "missing": missing,
        "required": list(REQUIRED_UI_ARTIFACT_FIELDS)
        + ["before_screenshot_or_reason"]
        + [f"taste_check:{item['label']}" for item in taste_requirements],
        "taste_requirements": taste_requirements,
        "artifacts": data,
    }


def _screenshot_candidates(artifacts: dict[str, Any]) -> dict[str, str]:
    candidates: dict[str, str] = {}
    by_screen = artifacts.get("screenshots_by_screen")
    if isinstance(by_screen, dict):
        for name, path in by_screen.items():
            if _present(name) and _present(path):
                candidates[str(name).strip().lower()] = str(path).strip()
    screen_name = artifacts.get("baseline_screen_name") or artifacts.get("screen_name")
    after = artifacts.get("after_screenshot")
    if _present(screen_name) and _present(after):
        candidates[str(screen_name).strip().lower()] = str(after).strip()
    return candidates


def _visual_diff_results(artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    diffs = artifacts.get("visual_diffs") or artifacts.get("visual_comparisons") or []
    if not isinstance(diffs, list):
        return results
    for item in diffs:
        if not isinstance(item, dict):
            continue
        name = item.get("screen_name") or item.get("baseline_screen_name") or item.get("name")
        if _present(name):
            results[str(name).strip().lower()] = item
    return results


def _load_rgb_image(path: str):
    from PIL import Image

    return Image.open(path).convert("RGB")


def _mismatch_ratio(baseline_path: str, candidate_path: str) -> tuple[float, dict[str, Any]]:
    from PIL import ImageChops

    baseline_image = _load_rgb_image(baseline_path)
    candidate_image = _load_rgb_image(candidate_path)
    baseline_size = baseline_image.size
    candidate_size = candidate_image.size
    width = min(baseline_size[0], candidate_size[0])
    height = min(baseline_size[1], candidate_size[1])
    if width <= 0 or height <= 0:
        return 1.0, {"baseline_size": baseline_size, "candidate_size": candidate_size, "compared_size": (width, height)}
    baseline_crop = baseline_image.crop((0, 0, width, height))
    candidate_crop = candidate_image.crop((0, 0, width, height))
    total = width * height
    diff = ImageChops.difference(baseline_crop, candidate_crop)
    histogram = diff.convert("L").histogram()
    changed = total - histogram[0]
    dimension_penalty = 0.0
    if baseline_size != candidate_size:
        baseline_area = max(1, baseline_size[0] * baseline_size[1])
        candidate_area = max(1, candidate_size[0] * candidate_size[1])
        dimension_penalty = abs(baseline_area - candidate_area) / max(baseline_area, candidate_area)
    ratio = max(changed / total, dimension_penalty)
    return ratio, {
        "baseline_size": list(baseline_size),
        "candidate_size": list(candidate_size),
        "compared_size": [width, height],
        "changed_pixels": changed,
        "total_pixels": total,
        "dimension_penalty": dimension_penalty,
    }


def generate_visual_diffs(
    *,
    baseline: dict[str, Any] | None,
    candidates: dict[str, str] | None,
    threshold: float = 0.03,
) -> list[dict[str, Any]]:
    """Generate visual diff results from baseline and candidate screenshot paths."""
    base = baseline or {}
    candidate_map = {
        str(name).strip().lower(): str(path).strip()
        for name, path in (candidates or {}).items()
        if _present(name) and _present(path)
    }
    diffs: list[dict[str, Any]] = []
    for screen in base.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        name = str(screen.get("screen_name") or "").strip()
        key = name.lower()
        baseline_path = str(screen.get("screenshot_path") or "").strip()
        candidate_path = candidate_map.get(key, "")
        diff: dict[str, Any] = {
            "screen_name": name,
            "baseline_screenshot": baseline_path,
            "candidate_screenshot": candidate_path,
            "threshold": threshold,
        }
        if not baseline_path or not Path(baseline_path).exists():
            diff.update({
                "status": "fail",
                "mismatch_ratio": 1.0,
                "failure_reason": f"Baseline screenshot is missing for '{name}'.",
            })
        elif not candidate_path or not Path(candidate_path).exists():
            diff.update({
                "status": "fail",
                "mismatch_ratio": 1.0,
                "failure_reason": f"Candidate screenshot is missing for '{name}'.",
            })
        else:
            try:
                ratio, metrics = _mismatch_ratio(baseline_path, candidate_path)
                status = "fail" if ratio > threshold else "pass"
                diff.update({
                    "status": status,
                    "mismatch_ratio": ratio,
                    "metrics": metrics,
                })
                if status == "fail":
                    diff["failure_reason"] = (
                        f"{round(ratio * 100, 2)}% changed pixels exceeded "
                        f"{round(threshold * 100, 2)}% threshold for '{name}'."
                    )
            except Exception as exc:
                diff.update({
                    "status": "fail",
                    "mismatch_ratio": 1.0,
                    "failure_reason": f"Could not compare screenshots for '{name}': {exc}",
                })
        diffs.append(diff)
    return diffs


def compare_ui_artifacts_to_baseline(
    artifacts: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare UI evidence to a named visual baseline set.

    Pixel comparison can be supplied by a browser/screenshot tool through
    artifacts["visual_diffs"]. This helper makes the harness deterministic by
    enforcing screen coverage and recording explicit diff failures.
    """
    data = artifacts or {}
    base = baseline or {}
    screens = [screen for screen in base.get("screens", []) if isinstance(screen, dict)]
    candidates = _screenshot_candidates(data)
    diff_results = _visual_diff_results(data)
    if not diff_results and candidates and screens:
        generated = generate_visual_diffs(
            baseline=base,
            candidates=candidates,
            threshold=float(data.get("visual_diff_threshold") or 0.03),
        )
        diff_results = _visual_diff_results({"visual_diffs": generated})
    screen_results: list[dict[str, Any]] = []
    failures: list[str] = []
    for screen in screens:
        name = str(screen.get("screen_name") or "").strip()
        key = name.lower()
        candidate_path = candidates.get(key)
        diff = diff_results.get(key) or {}
        diff_status = str(diff.get("status") or diff.get("verdict") or "").strip().lower()
        failure_reason = str(
            diff.get("failure_reason") or diff.get("reason") or diff.get("explanation") or ""
        ).strip()
        if not candidate_path and _present(diff.get("candidate_screenshot")):
            candidate_path = str(diff.get("candidate_screenshot")).strip()
        baseline_path = str(screen.get("screenshot_path") or "").strip()
        if not baseline_path or not Path(baseline_path).exists():
            failures.append(f"Baseline screenshot is missing for '{name}'.")
            status = "missing_baseline"
        elif not candidate_path:
            failures.append(f"Missing candidate screenshot for baseline screen '{name}'.")
            status = "missing"
        elif diff_status in {"fail", "failed", "regressed"}:
            failures.append(failure_reason or f"Visual regression detected for '{name}'.")
            status = "fail"
        else:
            status = "pass"
        screen_results.append({
            "screen_name": name,
            "baseline_screenshot": screen.get("screenshot_path"),
            "candidate_screenshot": candidate_path,
            "status": status,
            "diff": diff,
        })
    if not screens:
        failures.append("Visual baseline has no reference screens.")
    return {
        "verdict": "pass" if not failures else "fail",
        "baseline_set_id": base.get("id"),
        "baseline_name": base.get("name"),
        "screen_results": screen_results,
        "failures": failures,
        "explanation": " ".join(failures) if failures else "Candidate UI evidence covers the selected visual baseline.",
    }


def normalize_feedback_label(label: str) -> str:
    """Normalize a human approval/rejection label into the harness taxonomy."""
    raw = (label or "").strip().lower().replace("-", "_")
    raw = LABEL_ALIASES.get(raw.replace("_", " "), raw)
    return raw if raw in FEEDBACK_LABELS else "rejected_other"


def build_feedback_summary(label: str, reason: str = "") -> str:
    """Build a short Hermes event summary for UI feedback."""
    normalized = normalize_feedback_label(label)
    if normalized == "approved":
        return "UI outcome approved."
    readable = normalized.replace("_", " ")
    if reason.strip():
        return f"UI outcome rejected: {readable}. {reason.strip()}"
    return f"UI outcome rejected: {readable}."
