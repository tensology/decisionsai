"""
Property tests for Masko skin generation.

Validates the 7 correctness properties from the design document using Hypothesis.

Feature: masko-skin-generation
"""

import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.skin_config import parse, to_json, validate, EVENT_HOOKS
from distr.core.skin_discovery import discover_skins
from distr.core.integrations.masko.models import (
    sanitize_skin_name,
    GenerationStatus,
    Style,
    JobStatus,
    CanvasNode,
)
from distr.core.integrations.masko.skin_builder import (
    build_skin_json,
    write_skin,
    AVATAR_RENDERING,
)
from distr.core.integrations.masko.client import MaskoClient, MaskoError


# ---------------------------------------------------------------------------
# Property 3: Skin name sanitization produces filesystem-safe output
# ---------------------------------------------------------------------------


@given(name=st.text())
@settings(max_examples=100)
def test_sanitize_name_produces_filesystem_safe_output(name):
    """Property 3: sanitize_skin_name produces filesystem-safe output."""
    result = sanitize_skin_name(name)
    if result:  # MAY be empty — caller rejects empty
        # Only contains lowercase ASCII letters, digits, and hyphens
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in result), f"Invalid chars in: {result!r}"
        # Does not start or end with hyphen
        assert not result.startswith("-"), f"Starts with hyphen: {result!r}"
        assert not result.endswith("-"), f"Ends with hyphen: {result!r}"


# ---------------------------------------------------------------------------
# Property 4: Whitespace-only inputs are rejected
# ---------------------------------------------------------------------------


@given(name=st.from_regex(r"^\s+$"))
@settings(max_examples=50)
def test_whitespace_only_names_produce_empty_sanitized(name):
    """Property 4: sanitize_skin_name returns empty string for whitespace-only input."""
    result = sanitize_skin_name(name)
    assert result == "", f"Expected empty string for whitespace-only input {name!r}, got {result!r}"


# ---------------------------------------------------------------------------
# Property 1: Generated SkinConfig structural validity
# ---------------------------------------------------------------------------


SKIN_NAMES = st.text(min_size=1, max_size=50)
MODES = st.sampled_from(["static", "animated"])
FILE_EXTENSIONS = st.sampled_from([".webp", ".webm"])
FILENAMES = st.sampled_from(EVENT_HOOKS)


@given(name=SKIN_NAMES, mode=MODES)
@settings(max_examples=100)
def test_generated_skin_config_structural_validity(name, mode):
    """Property 1: build_skin_json produces structurally valid SkinConfig."""
    assume(sanitize_skin_name(name) != "")  # Skip names that sanitize to empty

    ext = ".webp" if mode == "static" else ".webm"
    hook_to_file = {hook: f"{hook}{ext}" for hook in EVENT_HOOKS}

    if mode == "animated":
        transitions = {"idle-thinking": "idle-thinking.webm"}
    else:
        transitions = None

    config = build_skin_json(name=name, mode=mode, hook_to_file=hook_to_file, transitions=transitions)

    # Type must be "avatar"
    assert config.type == "avatar"

    # Rendering constraints
    assert config.rendering.shape == "square"
    assert config.rendering.border is False
    assert config.rendering.shadow is False
    assert config.rendering.glow_on_hold is False
    assert config.rendering.chroma_key is None
    assert config.rendering.chroma_threshold == 30

    # Must contain all 12 event hooks
    assert len(config.events) == 12
    for hook in EVENT_HOOKS:
        assert hook in config.events, f"Missing hook: {hook}"
        assert config.events[hook].playback == "loop"

    # Validate with no errors
    errors = validate(config)
    assert errors == [], f"Validation errors: {errors}"


# ---------------------------------------------------------------------------
# Property 2: SkinConfig serialization round-trip
# ---------------------------------------------------------------------------


@given(name=SKIN_NAMES, mode=MODES)
@settings(max_examples=100)
def test_skin_config_round_trip(name, mode):
    """Property 2: parse(to_json(config)) produces equivalent SkinConfig."""
    assume(sanitize_skin_name(name) != "")

    ext = ".webp" if mode == "static" else ".webm"
    hook_to_file = {hook: f"{hook}{ext}" for hook in EVENT_HOOKS}
    transitions = {"idle-thinking": "idle-thinking.webm"} if mode == "animated" else None

    config = build_skin_json(name=name, mode=mode, hook_to_file=hook_to_file, transitions=transitions)

    json_str = to_json(config)
    config_rt = parse(json_str)

    assert config_rt.type == config.type
    assert config_rt.name == config.name
    assert config_rt.rendering.shape == config.rendering.shape
    assert config_rt.rendering.border == config.rendering.border
    assert config_rt.rendering.shadow == config.rendering.shadow
    assert config_rt.rendering.glow_on_hold == config.rendering.glow_on_hold
    assert config_rt.rendering.chroma_key == config.rendering.chroma_key
    assert config_rt.rendering.chroma_threshold == config.rendering.chroma_threshold
    assert len(config_rt.events) == len(config.events)
    assert config_rt.transitions == config.transitions

    for hook in EVENT_HOOKS:
        assert config_rt.events[hook].animation == config.events[hook].animation
        assert config_rt.events[hook].playback == config.events[hook].playback


# ---------------------------------------------------------------------------
# Property 5: Generated skin is discoverable
# ---------------------------------------------------------------------------


@given(name=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"))))
@settings(max_examples=50)
def test_generated_skin_is_discoverable(name):
    """Property 5: discover_skins finds newly generated skin."""
    assume(sanitize_skin_name(name) != "")

    mode = "static"
    ext = ".webp"
    hook_to_file = {hook: f"{hook}{ext}" for hook in EVENT_HOOKS}

    config = build_skin_json(name=name, mode=mode, hook_to_file=hook_to_file)

    tmpdir = tempfile.mkdtemp()
    try:
        sanitized = sanitize_skin_name(name)
        skin_dir = Path(tmpdir) / sanitized
        skin_dir.mkdir(parents=True, exist_ok=True)
        write_skin(skin_dir, config)

        results = discover_skins(tmpdir)
        assert len(results) >= 1, f"Expected at least 1 skin, got {len(results)}"
        found = False
        for folder_name, found_config in results:
            if folder_name == sanitized:
                assert found_config.name == config.name
                found = True
                break
        assert found, f"Skin '{sanitized}' not found in discovered skins: {[r[0] for r in results]}"
    finally:
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Property 6: Cancellation and failure cleanup
# ---------------------------------------------------------------------------


def test_cancellation_cleans_up_folder():
    """Property 6: Cancelled generation deletes partial skin folder."""
    from distr.core.integrations.masko.generator import SkinGenerator

    tmpdir = tempfile.mkdtemp()
    try:
        client = MaskoClient("test_key")
        generator = SkinGenerator(client, tmpdir)

        # Create a partial folder
        skin_dir = Path(tmpdir) / "cancel-test"
        skin_dir.mkdir(parents=True, exist_ok=True)
        (skin_dir / "idle.webp").write_bytes(b"fake")

        # Register a context and cancel it
        gen_id = "test-cancel-gen"
        from distr.core.integrations.masko.generator import _GenerationContext
        ctx = _GenerationContext(
            generation_id=gen_id,
            name="Cancel Test",
            description="test",
            style="test-style",
            mode="static",
            sanitized_name="cancel-test",
        )
        generator._generations[gen_id] = ctx

        # Calling cancel should set status and clean up
        generator.cancel(gen_id)

        assert ctx.cancelled is True
        assert ctx.status == "cancelled"
        # Skin folder should be cleaned up by cancel
        assert not skin_dir.exists(), "Folder should be deleted after cancel"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property 7: Bearer token authentication on all requests
# ---------------------------------------------------------------------------


@given(api_key=st.text(min_size=1, max_size=80, alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"))))
@settings(max_examples=50)
def test_bearer_token_on_all_requests(api_key):
    """Property 7: Every MaskoClient request includes correct Authorization header."""
    client = MaskoClient(api_key)
    headers = client._headers()
    assert headers["Authorization"] == f"Bearer {api_key}"


# ---------------------------------------------------------------------------
# Unit tests: Cost calculation
# ---------------------------------------------------------------------------


def test_static_cost_is_12():
    """Static mode cost = 12 credits."""
    from distr.core.integrations.masko.models import CREDITS_PER_IMAGE
    assert CREDITS_PER_IMAGE * 12 == 12


def test_animated_cost_estimation():
    """Animated mode estimated cost breakdown."""
    from distr.core.integrations.masko.models import (
        CREDITS_PER_ANIMATION, CREDITS_PER_TRANSITION_SECOND,
        TRANSITION_DURATION_SECONDS, ESTIMATED_FORWARD_TRANSITIONS,
    )
    pose_credits = CREDITS_PER_ANIMATION * 12  # 21 * 12 = 252
    transition_credits = (
        CREDITS_PER_TRANSITION_SECOND
        * TRANSITION_DURATION_SECONDS
        * len(ESTIMATED_FORWARD_TRANSITIONS)
    )  # 5 * 4 * 6 = 120
    total = pose_credits + transition_credits  # 372
    assert total == 372


# ---------------------------------------------------------------------------
# Unit tests: Event hook mapping
# ---------------------------------------------------------------------------


def test_all_12_hooks_have_pose_prompt():
    """All 12 event hooks have a pose prompt suffix."""
    from distr.core.integrations.masko.models import EVENT_HOOKS, POSE_PROMPT_SUFFIXES
    assert len(EVENT_HOOKS) == 12
    for hook in EVENT_HOOKS:
        assert hook in POSE_PROMPT_SUFFIXES, f"Missing pose prompt for hook: {hook}"


# ---------------------------------------------------------------------------
# Unit tests: API validation
# ---------------------------------------------------------------------------


def test_masko_validation_registered():
    """Masko validator is registered in validate_provider."""
    from distr.core.api_validation import validate_provider
    # Should not raise "Unknown provider"
    try:
        validate_provider("masko", "test_key")
    except Exception as e:
        # We expect it to fail with an API call error for invalid key, not "Unknown provider"
        assert "Unknown provider" not in str(e)


def test_nvidia_validation_registered():
    """NVIDIA validator is registered in validate_provider."""
    from distr.core.api_validation import validate_provider

    is_valid, err = validate_provider("nvidia", "test_key")
    assert "Unknown provider" not in err


# ---------------------------------------------------------------------------
# Unit tests: MaskoClient error handling
# ---------------------------------------------------------------------------


def test_masko_client_base_url():
    """MaskoClient has correct base URL."""
    assert MaskoClient.BASE_URL == "https://api.masko.ai/v1"


def test_masko_client_timeout():
    """MaskoClient has 30s timeout."""
    assert MaskoClient.TIMEOUT == 30


def test_masko_error():
    """MaskoError stores message and status code."""
    err = MaskoError("test error", status_code=401)
    assert err.message == "test error"
    assert err.status_code == 401
    assert str(err) == "test error"