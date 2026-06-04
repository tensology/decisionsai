from distr.core.harness.ui_quality import (
    compare_ui_artifacts_to_baseline,
    evaluate_ui_artifacts,
    generate_visual_diffs,
    normalize_feedback_label,
)


def _write_png(path, color, size=(8, 8), patch=None):
    from PIL import Image

    image = Image.new("RGB", size, color)
    if patch:
        x0, y0, x1, y1, patch_color = patch
        for x in range(x0, x1):
            for y in range(y0, y1):
                image.putpixel((x, y), patch_color)
    image.save(path)


def test_ui_artifacts_pass_when_required_evidence_present():
    result = evaluate_ui_artifacts(
        {
            "before_screenshot": "/tmp/before.png",
            "after_screenshot": "/tmp/after.png",
            "flow_summary": "Open settings, change theme, save.",
            "happy_path_steps": ["Open settings", "Change theme", "Save"],
            "click_count": 3,
            "layout_notes": "Kept hierarchy consistent.",
        }
    )

    assert result["verdict"] == "pass"
    assert result["missing"] == []


def test_ui_artifacts_fail_without_after_screenshot_and_flow():
    result = evaluate_ui_artifacts({"before_screenshot": "/tmp/before.png"})

    assert result["verdict"] == "fail"
    assert "after_screenshot" in result["missing"]
    assert "flow_summary" in result["missing"]
    assert "happy_path_steps" in result["missing"]
    assert "click_count" in result["missing"]
    assert "layout_hierarchy_notes" in result["missing"]


def test_ui_artifacts_fail_without_layout_hierarchy_notes():
    result = evaluate_ui_artifacts(
        {
            "before_screenshot": "/tmp/before.png",
            "after_screenshot": "/tmp/after.png",
            "flow_summary": "Open settings, change theme, save.",
            "happy_path_steps": ["Open settings", "Change theme", "Save"],
            "click_count": 3,
        }
    )

    assert result["verdict"] == "fail"
    assert result["missing"] == ["layout_hierarchy_notes"]


def test_ui_artifacts_require_taste_checks_for_common_rejections():
    taste_summary = {
        "labels": {
            "spacing_off": {
                "approved": False,
                "count": 2,
                "recent_reasons": ["Toolbar padding was loose."],
            }
        }
    }

    result = evaluate_ui_artifacts(
        {
            "before_screenshot": "/tmp/before.png",
            "after_screenshot": "/tmp/after.png",
            "flow_summary": "Open settings, change theme, save.",
            "happy_path_steps": ["Open settings", "Change theme", "Save"],
            "click_count": 3,
            "layout_hierarchy_notes": "Kept hierarchy consistent.",
        },
        taste_summary=taste_summary,
    )

    assert result["verdict"] == "fail"
    assert "taste_check:spacing_off" in result["missing"]

    addressed = evaluate_ui_artifacts(
        {
            "before_screenshot": "/tmp/before.png",
            "after_screenshot": "/tmp/after.png",
            "flow_summary": "Open settings, change theme, save.",
            "happy_path_steps": ["Open settings", "Change theme", "Save"],
            "click_count": 3,
            "layout_hierarchy_notes": "Kept hierarchy consistent.",
            "taste_checks": {"spacing_off": "Reduced vertical padding and aligned toolbar gaps."},
        },
        taste_summary=taste_summary,
    )

    assert addressed["verdict"] == "pass"
    assert addressed["taste_requirements"][0]["label"] == "spacing_off"


def test_feedback_label_normalization():
    assert normalize_feedback_label("spacing off") == "spacing_off"
    assert normalize_feedback_label("Approved") == "approved"
    assert normalize_feedback_label("weird") == "rejected_other"


def test_visual_baseline_comparison_requires_named_candidate_screens(tmp_path):
    dashboard_path = tmp_path / "dashboard.png"
    settings_path = tmp_path / "settings.png"
    _write_png(dashboard_path, (24, 36, 48))
    _write_png(settings_path, (24, 36, 48))
    baseline = {
        "id": 4,
        "name": "Gold Admin",
        "screens": [
            {"screen_name": "Dashboard", "screenshot_path": str(dashboard_path)},
            {"screen_name": "Settings", "screenshot_path": str(settings_path)},
        ],
    }

    result = compare_ui_artifacts_to_baseline(
        {
            "screenshots_by_screen": {"Dashboard": "/tmp/dashboard-after.png"},
            "visual_diffs": [{"screen_name": "Dashboard", "status": "pass"}],
        },
        baseline,
    )

    assert result["verdict"] == "fail"
    assert result["baseline_name"] == "Gold Admin"
    assert "Settings" in result["failures"][0]


def test_visual_baseline_comparison_records_explicit_diff_failures(tmp_path):
    dashboard_path = tmp_path / "dashboard.png"
    _write_png(dashboard_path, (24, 36, 48))
    baseline = {
        "id": 4,
        "name": "Gold Admin",
        "screens": [{"screen_name": "Dashboard", "screenshot_path": str(dashboard_path)}],
    }

    result = compare_ui_artifacts_to_baseline(
        {
            "screenshots_by_screen": {"Dashboard": "/tmp/dashboard-after.png"},
            "visual_diffs": [
                {
                    "screen_name": "Dashboard",
                    "status": "fail",
                    "failure_reason": "Toolbar spacing drifted from the reference.",
                }
            ],
        },
        baseline,
    )

    assert result["verdict"] == "fail"
    assert "Toolbar spacing" in result["explanation"]


def test_visual_baseline_comparison_fails_when_reference_file_is_missing_despite_pass_diff():
    baseline = {
        "id": 4,
        "name": "Gold Admin",
        "screens": [{"screen_name": "Dashboard", "screenshot_path": "/missing/dashboard.png"}],
    }

    result = compare_ui_artifacts_to_baseline(
        {
            "screenshots_by_screen": {"Dashboard": "/tmp/dashboard-after.png"},
            "visual_diffs": [{"screen_name": "Dashboard", "status": "pass"}],
        },
        baseline,
    )

    assert result["verdict"] == "fail"
    assert "Baseline screenshot is missing" in result["explanation"]


def test_generate_visual_diffs_passes_identical_screenshots(tmp_path):
    baseline_path = tmp_path / "baseline.png"
    candidate_path = tmp_path / "candidate.png"
    _write_png(baseline_path, (24, 36, 48))
    _write_png(candidate_path, (24, 36, 48))

    diffs = generate_visual_diffs(
        baseline={
            "id": 4,
            "name": "Gold Admin",
            "screens": [{"screen_name": "Dashboard", "screenshot_path": str(baseline_path)}],
        },
        candidates={"Dashboard": str(candidate_path)},
    )

    assert diffs[0]["status"] == "pass"
    assert diffs[0]["mismatch_ratio"] == 0


def test_generate_visual_diffs_fails_when_changed_region_exceeds_threshold(tmp_path):
    baseline_path = tmp_path / "baseline.png"
    candidate_path = tmp_path / "candidate.png"
    _write_png(baseline_path, (24, 36, 48))
    _write_png(candidate_path, (24, 36, 48), patch=(0, 0, 4, 4, (240, 240, 240)))

    diffs = generate_visual_diffs(
        baseline={
            "id": 4,
            "name": "Gold Admin",
            "screens": [{"screen_name": "Dashboard", "screenshot_path": str(baseline_path)}],
        },
        candidates={"Dashboard": str(candidate_path)},
        threshold=0.1,
    )

    assert diffs[0]["status"] == "fail"
    assert diffs[0]["mismatch_ratio"] > 0.1
    assert "changed pixels" in diffs[0]["failure_reason"]


def test_baseline_comparison_generates_visual_diffs_from_screenshot_paths(tmp_path):
    baseline_path = tmp_path / "baseline.png"
    candidate_path = tmp_path / "candidate.png"
    _write_png(baseline_path, (24, 36, 48))
    _write_png(candidate_path, (24, 36, 48), patch=(0, 0, 4, 4, (240, 240, 240)))

    result = compare_ui_artifacts_to_baseline(
        {
            "screenshots_by_screen": {"Dashboard": str(candidate_path)},
            "visual_diff_threshold": 0.1,
        },
        {
            "id": 4,
            "name": "Gold Admin",
            "screens": [{"screen_name": "Dashboard", "screenshot_path": str(baseline_path)}],
        },
    )

    assert result["verdict"] == "fail"
    assert "changed pixels" in result["explanation"]
    assert result["screen_results"][0]["diff"]["status"] == "fail"
