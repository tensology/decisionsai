from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INFO_SCRIPT = ROOT / "distr/gui/web/static/shared/js/provider_model_info.js"
POPUP_STYLES = ROOT / "distr/gui/web/static/shared/css/model_popups.css"


def test_provider_info_control_stretches_to_the_live_select_height() -> None:
    script = INFO_SCRIPT.read_text(encoding="utf-8")
    styles = POPUP_STYLES.read_text(encoding="utf-8")

    assert "wrapper.className = 'model-info-control'" in script
    assert ".model-info-control" in styles
    assert "align-items: stretch" in styles
    assert "height: auto" in styles
    assert "align-self: stretch" in styles
    assert "\n    height: 34px;" not in styles

    # Hidden chat modals and inactive settings panels have no measurable height
    # at injection time, so the component must not cache a pixel measurement.
    assert "sel.offsetHeight" not in script
    assert "sel.getBoundingClientRect()" not in script


def test_shared_provider_info_control_covers_chat_and_llm_settings() -> None:
    script = INFO_SCRIPT.read_text(encoding="utf-8")
    provider_ids = (
        "conversational_provider",
        "coding_provider",
        "vision_provider",
        "computer_use_provider",
        "image_provider",
        "video_provider",
        "workflow_provider",
        "emptyStateLlmProvider",
        "llmProvider",
        "chatConfigLlmProvider",
    )

    for provider_id in provider_ids:
        assert provider_id in script
