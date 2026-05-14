# Feature: oracle-skins-system, Task 2.5: Unit tests for default skin configs
# Validates: Requirements 12.1, 12.2, 12.11
"""Unit tests verifying each shipped skin.json parses correctly, validates
with no errors, and contains the expected field values."""

from pathlib import Path

import pytest

from distr.core.skin_config import parse, validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATARS_DIR = PROJECT_ROOT / "assets" / "avatars"

SHIPPED_SKINS = [
    "oracle",
    "clippy",
    "nugget",
    "rusty",
    "masko",
    "madame-patate",
]

AVATAR_SKINS = [s for s in SHIPPED_SKINS if s != "oracle"]


def _load_skin(name: str):
    """Load and parse a skin.json by avatar folder name."""
    path = AVATARS_DIR / name / "skin.json"
    return parse(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1 & 2 & 3: Every shipped skin parses and validates without errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skin_name", SHIPPED_SKINS)
def test_shipped_skin_parses(skin_name: str) -> None:
    """Each shipped skin.json parses without errors."""
    config = _load_skin(skin_name)
    assert config is not None


@pytest.mark.parametrize("skin_name", SHIPPED_SKINS)
def test_shipped_skin_validates(skin_name: str) -> None:
    """Each shipped skin.json validates with no errors."""
    config = _load_skin(skin_name)
    errors = validate(config)
    assert errors == [], f"Validation errors for {skin_name}: {errors}"


# ---------------------------------------------------------------------------
# 4: Oracle skin specific assertions
# ---------------------------------------------------------------------------


class TestOracleSkin:
    """Validates: Requirement 12.1"""

    @pytest.fixture()
    def oracle(self):
        return _load_skin("oracle")

    def test_type(self, oracle) -> None:
        assert oracle.type == "oracle"

    def test_name(self, oracle) -> None:
        assert oracle.name == "Oracle"

    def test_tts_response_show_player(self, oracle) -> None:
        assert oracle.events["tts_response"].show_player is True

    def test_tts_response_show_chat_bubble(self, oracle) -> None:
        assert oracle.events["tts_response"].show_chat_bubble is False

    def test_hands_free_listening_glow_color(self, oracle) -> None:
        assert tuple(oracle.events["hands_free_listening"].glow_color) == (0, 170, 255)

    def test_hands_free_listening_glow_style(self, oracle) -> None:
        assert oracle.events["hands_free_listening"].glow_style == "breathing"

    def test_hands_free_listening_glow_speed(self, oracle) -> None:
        assert oracle.events["hands_free_listening"].glow_speed == 875

    def test_dictation_glow_style(self, oracle) -> None:
        assert oracle.events["dictation"].glow_style == "fade"

    def test_dictation_glow_color(self, oracle) -> None:
        assert tuple(oracle.events["dictation"].glow_color) == (76, 175, 80)

    def test_ptt_active_glow_style(self, oracle) -> None:
        assert oracle.events["ptt_active"].glow_style == "pulse"

    def test_file_drop_success_glow_style(self, oracle) -> None:
        assert oracle.events["file_drop_success"].glow_style == "fade"

    def test_recording_action_tray_icon(self, oracle) -> None:
        assert oracle.events["recording_action"].tray_icon == "recording"

    def test_rendering_shape(self, oracle) -> None:
        assert oracle.rendering.shape == "round"

    def test_rendering_border(self, oracle) -> None:
        assert oracle.rendering.border is True

    def test_rendering_shadow(self, oracle) -> None:
        assert oracle.rendering.shadow is True

    def test_rendering_glow_on_hold(self, oracle) -> None:
        assert oracle.rendering.glow_on_hold is True


# ---------------------------------------------------------------------------
# 5: Clippy skin specific assertions
# ---------------------------------------------------------------------------


class TestClippySkin:
    """Validates: Requirement 12.2"""

    @pytest.fixture()
    def clippy(self):
        return _load_skin("clippy")

    def test_type(self, clippy) -> None:
        assert clippy.type == "avatar"

    def test_name(self, clippy) -> None:
        assert clippy.name == "Clippy"

    def test_tts_response_show_chat_bubble(self, clippy) -> None:
        assert clippy.events["tts_response"].show_chat_bubble is True

    def test_tts_response_show_player(self, clippy) -> None:
        assert clippy.events["tts_response"].show_player is False

    def test_rendering_shape(self, clippy) -> None:
        assert clippy.rendering.shape == "square"

    def test_rendering_border(self, clippy) -> None:
        assert clippy.rendering.border is False

    def test_rendering_shadow(self, clippy) -> None:
        assert clippy.rendering.shadow is False

    def test_rendering_glow_on_hold(self, clippy) -> None:
        assert clippy.rendering.glow_on_hold is False

    def test_has_transitions(self, clippy) -> None:
        assert isinstance(clippy.transitions, dict)
        assert "idle-thinking" in clippy.transitions


# ---------------------------------------------------------------------------
# 6: All avatar skins have chat bubble on TTS, no player
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skin_name", AVATAR_SKINS)
def test_avatar_tts_show_chat_bubble(skin_name: str) -> None:
    """All avatar skins must have tts_response.show_chat_bubble == True."""
    config = _load_skin(skin_name)
    assert config.events["tts_response"].show_chat_bubble is True


@pytest.mark.parametrize("skin_name", AVATAR_SKINS)
def test_avatar_tts_show_player_false(skin_name: str) -> None:
    """All avatar skins must have tts_response.show_player == False."""
    config = _load_skin(skin_name)
    assert config.events["tts_response"].show_player is False
