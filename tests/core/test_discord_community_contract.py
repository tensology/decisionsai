from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISCORD_INVITE = "https://discord.gg/X83gVz8G"
RETIRED_ROOM_NAME = "".join(("i", "r", "c"))


def test_header_links_to_discord_community_without_retired_room_surface() -> None:
    header = (ROOT / "distr/gui/web/templates/base.html").read_text(encoding="utf-8")
    server = (ROOT / "distr/gui/web/server.py").read_text(encoding="utf-8")

    assert DISCORD_INVITE in header
    assert 'title="Discord Community"' in header
    retired_path = f"/{RETIRED_ROOM_NAME}"
    assert f'href="{retired_path}/' not in header
    assert f'"{retired_path}' not in server
    assert not (ROOT / f"distr/gui/web/routes/{RETIRED_ROOM_NAME}.py").exists()
    assert not (ROOT / f"distr/gui/web/templates/{RETIRED_ROOM_NAME}/{RETIRED_ROOM_NAME}.html").exists()
    assert not (ROOT / f"distr/gui/web/static/{RETIRED_ROOM_NAME}/js/{RETIRED_ROOM_NAME}.js").exists()
    assert not (ROOT / f"distr/gui/web/static/{RETIRED_ROOM_NAME}/css/{RETIRED_ROOM_NAME}.css").exists()


def test_retired_room_hotkey_is_removed() -> None:
    from distr.core.hotkeys import DEFAULTS

    assert f"web_hotkey_{RETIRED_ROOM_NAME}_modifier" not in DEFAULTS
    assert f"web_hotkey_{RETIRED_ROOM_NAME}_key" not in DEFAULTS
