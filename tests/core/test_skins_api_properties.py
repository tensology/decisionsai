# Feature: oracle-skins-system — Skins API property tests
# Properties 8, 9, 10, 11, 13
"""Property-based tests for the Skins API routes.

Uses Hypothesis with FastAPI TestClient to verify round-trip persistence,
error handling, validation, and file listing across generated inputs.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.skin_config import (
    EVENT_HOOKS,
    GLOW_STYLES,
    EventResponse,
    RenderingConfig,
    SkinConfig,
    to_json,
    parse,
    validate,
)
from distr.gui.web.routes.settings.skins import register_routes


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_app_and_client(avatars_dir: Path) -> TestClient:
    """Build a FastAPI app with the skins router, patching AVATARS_DIR."""
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)
    # We return the client; callers must use the AVATARS_DIR patch context
    return TestClient(app)


def _minimal_event_response_dict(**overrides) -> dict:
    base = {
        "animation": "idle.webm",
        "show_player": False,
        "show_chat_bubble": False,
        "glow": False,
        "glow_color": [0, 0, 0],
        "glow_speed": 1000,
        "glow_style": "breathing",
        "tray_icon": "default",
    }
    base.update(overrides)
    return base


def _write_valid_skin(avatars_dir: Path, folder_name: str, skin_type: str = "avatar",
                      display_name: str = "Test") -> Path:
    """Write a valid skin folder with skin.json and a dummy animation file."""
    skin_dir = avatars_dir / folder_name
    skin_dir.mkdir(parents=True, exist_ok=True)

    if skin_type == "oracle":
        rendering = {"shape": "round", "border": True, "shadow": True, "glow_on_hold": True}
    else:
        rendering = {"shape": "square", "border": False, "shadow": False, "glow_on_hold": False}

    config = {
        "type": skin_type,
        "name": display_name,
        "rendering": rendering,
        "events": {"idle": _minimal_event_response_dict()},
        "transitions": {},
    }
    (skin_dir / "skin.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (skin_dir / "idle.webm").write_bytes(b"\x1a\x45\xdf\xa3")
    return skin_dir


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_safe_folder_name = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789"),
    min_size=1,
    max_size=12,
)

_animation_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_."),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "")

_tray_icon_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_."),
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip() != "")


def event_response_strategy() -> st.SearchStrategy[EventResponse]:
    return st.builds(
        EventResponse,
        animation=_animation_strategy,
        show_player=st.booleans(),
        show_chat_bubble=st.booleans(),
        glow=st.booleans(),
        glow_color=st.tuples(
            st.integers(min_value=0, max_value=255),
            st.integers(min_value=0, max_value=255),
            st.integers(min_value=0, max_value=255),
        ),
        glow_speed=st.integers(min_value=1, max_value=10000),
        glow_style=st.sampled_from(GLOW_STYLES),
        tray_icon=_tray_icon_strategy,
    )


@st.composite
def valid_skin_config_strategy(draw):
    """Generate a valid SkinConfig with matching rendering constraints."""
    skin_type = draw(st.sampled_from(["oracle", "avatar"]))
    name = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"),
        min_size=1,
        max_size=30,
    ).filter(lambda s: s.strip() != ""))

    if skin_type == "oracle":
        rendering = RenderingConfig(shape="round", border=True, shadow=True, glow_on_hold=True)
    else:
        rendering = RenderingConfig(shape="square", border=False, shadow=False, glow_on_hold=False)

    other_hooks = [h for h in EVENT_HOOKS if h != "idle"]
    events_dict = {"idle": draw(event_response_strategy())}
    extra_hooks = draw(st.lists(st.sampled_from(other_hooks), max_size=4, unique=True))
    for h in extra_hooks:
        events_dict[h] = draw(event_response_strategy())

    transitions = {}
    num_transitions = draw(st.integers(min_value=0, max_value=3))
    for _ in range(num_transitions):
        k1 = draw(st.sampled_from(EVENT_HOOKS))
        k2 = draw(st.sampled_from(EVENT_HOOKS))
        transitions[f"{k1}-{k2}"] = draw(_animation_strategy)

    return SkinConfig(
        type=skin_type,
        name=name,
        rendering=rendering,
        events=events_dict,
        transitions=transitions,
    )


# ---------------------------------------------------------------------------
# Property 8: Skin selection persistence round-trip
# Feature: oracle-skins-system, Property 8: Skin selection persistence round-trip
# Validates: Requirements 11.2, 11.8
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(folder_name=_safe_folder_name)
def test_skin_selection_persistence_round_trip(folder_name: str, tmp_path_factory) -> None:
    """**Validates: Requirements 11.2, 11.8**

    For any valid skin folder name, calling the skin select endpoint and then
    reading the selected_oracle setting should return that same skin folder name.
    """
    tmp_path = tmp_path_factory.mktemp("avatars")
    _write_valid_skin(tmp_path, folder_name, skin_type="avatar", display_name=folder_name.capitalize())

    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)

    captured_skin = {}

    def fake_update_oracle_skin(skin: str) -> None:
        captured_skin["value"] = skin

    with patch("distr.core.paths.AVATARS_DIR", str(tmp_path)), \
         patch("distr.core.services.settings_service.update_oracle_skin", side_effect=fake_update_oracle_skin):
        resp = client.post("/skins/select", json={"skin_name": folder_name})

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json()["selected_skin"] == folder_name
    # The update_oracle_skin function was called with the folder name
    assert captured_skin["value"] == folder_name


# ---------------------------------------------------------------------------
# Property 10: Invalid skin selection produces error
# Feature: oracle-skins-system, Property 10: Invalid skin selection produces error
# Validates: Requirements 11.9
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(invalid_name=st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != ""))
def test_invalid_skin_selection_produces_error(invalid_name: str, tmp_path_factory) -> None:
    """**Validates: Requirements 11.9**

    For any string that does not correspond to a valid skin folder name,
    the skin select endpoint should return HTTP 400.
    """
    tmp_path = tmp_path_factory.mktemp("avatars")
    # Write one valid skin with a known name that won't collide
    _write_valid_skin(tmp_path, "known-valid-skin", skin_type="avatar", display_name="Valid")

    # Ensure the generated name doesn't match the valid skin
    assume(invalid_name != "known-valid-skin")
    # Also ensure it's not a folder that exists with a valid skin.json
    assume(not (tmp_path / invalid_name / "skin.json").exists())

    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)

    with patch("distr.core.paths.AVATARS_DIR", str(tmp_path)):
        resp = client.post("/skins/select", json={"skin_name": invalid_name})

    assert resp.status_code == 400, (
        f"Expected 400 for invalid skin '{invalid_name}', got {resp.status_code}: {resp.text}"
    )
    assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# Property 9: Skin config PUT/GET round-trip
# Feature: oracle-skins-system, Property 9: Skin config PUT/GET round-trip
# Validates: Requirements 11.4, 11.3, 9.7
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(config=valid_skin_config_strategy())
def test_skin_config_put_get_round_trip(config: SkinConfig, tmp_path_factory) -> None:
    """**Validates: Requirements 11.4, 11.3, 9.7**

    For any valid skin name and valid updated SkinConfig, writing the config
    via PUT and then reading it via GET should return an equivalent config.
    """
    tmp_path = tmp_path_factory.mktemp("avatars")
    folder_name = "roundtrip-skin"

    # Create the skin folder with an initial valid config
    _write_valid_skin(tmp_path, folder_name, skin_type=config.type, display_name="Initial")

    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)

    # Serialize the generated config to a dict for the PUT body
    config_json_str = to_json(config)
    config_dict = json.loads(config_json_str)

    with patch("distr.core.paths.AVATARS_DIR", str(tmp_path)):
        # PUT the config
        put_resp = client.put(f"/skins/{folder_name}/config", json=config_dict)
        assert put_resp.status_code == 200, (
            f"PUT failed: {put_resp.status_code}: {put_resp.text}"
        )

        # GET the config back
        get_resp = client.get(f"/skins/{folder_name}/config")
        assert get_resp.status_code == 200, (
            f"GET failed: {get_resp.status_code}: {get_resp.text}"
        )

    returned = get_resp.json()

    # Verify top-level fields
    assert returned["type"] == config.type
    assert returned["name"] == config.name
    assert returned["transitions"] == config.transitions

    # Verify rendering
    assert returned["rendering"]["shape"] == config.rendering.shape
    assert returned["rendering"]["border"] == config.rendering.border
    assert returned["rendering"]["shadow"] == config.rendering.shadow
    assert returned["rendering"]["glow_on_hold"] == config.rendering.glow_on_hold

    # Verify events — same keys
    assert set(returned["events"].keys()) == set(config.events.keys())

    for hook in config.events:
        orig = config.events[hook]
        ret = returned["events"][hook]
        assert ret["animation"] == orig.animation, f"{hook}.animation mismatch"
        assert ret["show_player"] == orig.show_player, f"{hook}.show_player mismatch"
        assert ret["show_chat_bubble"] == orig.show_chat_bubble, f"{hook}.show_chat_bubble mismatch"
        assert ret["glow"] == orig.glow, f"{hook}.glow mismatch"
        assert list(ret["glow_color"]) == list(orig.glow_color), f"{hook}.glow_color mismatch"
        assert ret["glow_speed"] == orig.glow_speed, f"{hook}.glow_speed mismatch"
        assert ret["glow_style"] == orig.glow_style, f"{hook}.glow_style mismatch"
        assert ret["tray_icon"] == orig.tray_icon, f"{hook}.tray_icon mismatch"


# ---------------------------------------------------------------------------
# Property 11: Skin config validation on write
# Feature: oracle-skins-system, Property 11: Skin config validation on write
# Validates: Requirements 11.10
# ---------------------------------------------------------------------------


@st.composite
def invalid_skin_config_dict_strategy(draw):
    """Generate a dict that looks like a SkinConfig but has at least one
    validation-breaking flaw that parse() or validate() will reject."""
    kind = draw(st.sampled_from([
        "missing_type",
        "missing_name",
        "missing_rendering",
        "missing_events",
        "missing_idle",
        "empty_name",
        "wrong_rendering_for_type",
        "bad_glow_color",
        "bad_glow_speed",
        "bad_glow_style",
        "missing_animation",
    ]))

    base = {
        "type": "avatar",
        "name": "Test",
        "rendering": {"shape": "square", "border": False, "shadow": False, "glow_on_hold": False},
        "events": {"idle": _minimal_event_response_dict()},
        "transitions": {},
    }

    if kind == "missing_type":
        del base["type"]
    elif kind == "missing_name":
        del base["name"]
    elif kind == "missing_rendering":
        del base["rendering"]
    elif kind == "missing_events":
        del base["events"]
    elif kind == "missing_idle":
        base["events"] = {"thinking": _minimal_event_response_dict()}
    elif kind == "empty_name":
        base["name"] = ""
    elif kind == "wrong_rendering_for_type":
        # oracle type with avatar rendering
        base["type"] = "oracle"
        # rendering stays square/false — invalid for oracle
    elif kind == "bad_glow_color":
        bad_len = draw(st.integers(min_value=0, max_value=6).filter(lambda n: n != 3))
        base["events"]["idle"]["glow_color"] = [0] * bad_len
    elif kind == "bad_glow_speed":
        base["events"]["idle"]["glow_speed"] = draw(st.one_of(
            st.integers(max_value=0),
            st.just(-100),
        ))
    elif kind == "bad_glow_style":
        base["events"]["idle"]["glow_style"] = draw(st.text(min_size=1, max_size=10).filter(
            lambda s: s not in GLOW_STYLES
        ))
    elif kind == "missing_animation":
        del base["events"]["idle"]["animation"]

    return base


@settings(max_examples=100, deadline=None)
@given(invalid_config=invalid_skin_config_dict_strategy())
def test_skin_config_validation_on_write(invalid_config: dict, tmp_path_factory) -> None:
    """**Validates: Requirements 11.10**

    For any skin name and any invalid SkinConfig, the PUT endpoint should
    reject the update (HTTP 400) and not modify the existing config on disk.
    """
    tmp_path = tmp_path_factory.mktemp("avatars")
    folder_name = "validation-skin"

    # Write a valid initial config
    _write_valid_skin(tmp_path, folder_name, skin_type="avatar", display_name="Original")
    original_content = (tmp_path / folder_name / "skin.json").read_text(encoding="utf-8")

    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)

    with patch("distr.core.paths.AVATARS_DIR", str(tmp_path)):
        resp = client.put(f"/skins/{folder_name}/config", json=invalid_config)

    assert resp.status_code == 400, (
        f"Expected 400 for invalid config, got {resp.status_code}: {resp.text}"
    )

    # Verify the file on disk was NOT modified
    current_content = (tmp_path / folder_name / "skin.json").read_text(encoding="utf-8")
    assert current_content == original_content, "Config on disk should not be modified on invalid PUT"


# ---------------------------------------------------------------------------
# Property 13: Skin files endpoint returns only animation files
# Feature: oracle-skins-system, Property 13: Skin files endpoint returns only animation files
# Validates: Requirements 11.5
# ---------------------------------------------------------------------------


@st.composite
def mixed_files_strategy(draw):
    """Generate a list of filenames: some .webm, some .gif, some other extensions."""
    animation_exts = [".webm", ".gif", ".webp", ".png", ".jpg", ".jpeg"]
    other_exts = [".txt", ".json", ".mp4", ".py", ".md", ".DS_Store"]

    base_name = st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_"),
        min_size=1,
        max_size=15,
    ).filter(lambda s: s.strip() != "")

    # Generate animation files
    num_anim = draw(st.integers(min_value=0, max_value=5))
    anim_files = []
    for _ in range(num_anim):
        name = draw(base_name)
        ext = draw(st.sampled_from(animation_exts))
        anim_files.append(name + ext)

    # Generate non-animation files
    num_other = draw(st.integers(min_value=0, max_value=5))
    other_files = []
    for _ in range(num_other):
        name = draw(base_name)
        ext = draw(st.sampled_from(other_exts))
        other_files.append(name + ext)

    # Deduplicate and also exclude "skin.json" from other_files
    all_files = list(set(anim_files + other_files))
    return all_files


@settings(max_examples=100, deadline=None)
@given(files=mixed_files_strategy())
def test_skin_files_endpoint_returns_only_animation_files(files: list, tmp_path_factory) -> None:
    """**Validates: Requirements 11.5**

    For any valid skin folder containing a mix of .webm, .gif, and other files,
    the files listing endpoint should return only files with .webm or .gif extensions.
    """
    tmp_path = tmp_path_factory.mktemp("avatars")
    folder_name = "files-skin"

    # Create the skin folder with a valid skin.json
    skin_dir = _write_valid_skin(tmp_path, folder_name, skin_type="avatar", display_name="FileTest")

    # Write all generated files into the skin folder
    for fname in files:
        # Don't overwrite skin.json
        if fname == "skin.json":
            continue
        (skin_dir / fname).write_bytes(b"dummy content")

    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)

    with patch("distr.core.paths.AVATARS_DIR", str(tmp_path)):
        resp = client.get(f"/skins/{folder_name}/files")

    assert resp.status_code == 200
    returned_files = set(resp.json()["files"])

    # Compute expected: only .webm and .gif files that actually exist in the folder
    expected_anim = set()
    for fname in files:
        if fname == "skin.json":
            continue
        if fname.lower().endswith((".webm", ".gif", ".webp", ".png", ".jpg", ".jpeg")):
            expected_anim.add(fname)
    # Also include idle.webm written by _write_valid_skin
    expected_anim.add("idle.webm")

    # Property: returned files are exactly the animation files
    assert returned_files == expected_anim, (
        f"Expected animation files {expected_anim}, got {returned_files}"
    )

    # Property: no non-animation files are returned
    for fname in returned_files:
        assert fname.lower().endswith((".webm", ".gif", ".webp", ".png", ".jpg", ".jpeg")), (
            f"Non-animation file returned: {fname}"
        )
