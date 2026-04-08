"""
Skins routes — /skins, /skins/select, /skins/{name}/config,
                /skins/{name}/files, /skins/{name}/preview/{filename}

Requirements: 11.1-11.10
"""
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ._shared import logger, route_handler, SkinSelectRequest


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_routes(router, templates):

    @router.get("/skins")
    @route_handler("list skins")
    async def list_skins():
        """List all valid skins discovered under AVATARS_DIR."""
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_discovery import discover_skins
        from distr.core.settings import load_settings_from_db

        results = discover_skins(AVATARS_DIR)
        skins = []
        for folder_name, config in results:
            idle_anim = config.events.get("idle")
            skins.append({
                "folder_name": folder_name,
                "name": config.name,
                "type": config.type,
                "idle_animation": idle_anim.animation if idle_anim else None,
                "idle_playback": idle_anim.playback if idle_anim else "loop",
            })

        settings = load_settings_from_db()
        selected = settings.get("selected_oracle", "oracle") or "oracle"
        sphere_size = settings.get("sphere_size", 180)

        return JSONResponse({
            "skins": skins,
            "selected_skin": selected,
            "sphere_size": sphere_size,
        })

    @router.post("/skins/select")
    @route_handler("select skin")
    async def select_skin(data: SkinSelectRequest):
        """Persist skin selection and emit direct_oracle_change signal."""
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_discovery import get_skin_by_name

        result = get_skin_by_name(AVATARS_DIR, data.skin_name)
        if result is None:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid skin: '{data.skin_name}' not found or has invalid config",
            )

        from distr.core.services.settings_service import update_oracle_skin
        update_oracle_skin(data.skin_name)
        return JSONResponse({"success": True, "selected_skin": data.skin_name})

    @router.get("/skins/{name}/config")
    @route_handler("get skin config")
    async def get_skin_config(name: str):
        """Return the full SkinConfig as JSON for a specific skin."""
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_discovery import get_skin_by_name
        from distr.core.skin_config import to_json
        import json

        result = get_skin_by_name(AVATARS_DIR, name)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Skin '{name}' not found or has invalid config",
            )

        _folder, config = result
        # Return the config as a parsed JSON object (not a string)
        return JSONResponse(json.loads(to_json(config)))

    @router.put("/skins/{name}/config")
    @route_handler("update skin config")
    async def update_skin_config(name: str, body: dict):
        """Validate and write updated SkinConfig to disk."""
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_config import parse, validate, to_json
        import json

        skin_dir = Path(AVATARS_DIR) / name
        skin_json_path = skin_dir / "skin.json"

        if not skin_dir.is_dir():
            raise HTTPException(
                status_code=404,
                detail=f"Skin folder '{name}' does not exist",
            )

        # Parse the incoming JSON to validate structure
        try:
            config = parse(json.dumps(body))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        errors = validate(config)
        if errors:
            raise HTTPException(
                status_code=400,
                detail=f"Validation errors: {'; '.join(errors)}",
            )

        # Write validated config to disk
        skin_json_path.write_text(to_json(config), encoding="utf-8")
        return JSONResponse({"success": True, "message": f"Skin '{name}' config updated"})

    @router.get("/skins/{name}/files")
    @route_handler("list skin files")
    async def list_skin_files(name: str):
        """List .webm and .gif animation files in the skin folder."""
        from distr.core.paths import AVATARS_DIR

        skin_dir = Path(AVATARS_DIR) / name
        if not skin_dir.is_dir():
            raise HTTPException(
                status_code=404,
                detail=f"Skin folder '{name}' does not exist",
            )

        files = sorted(
            f.name
            for f in skin_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".webm", ".gif", ".webp", ".png", ".jpg", ".jpeg")
        )
        return JSONResponse({"files": files})

    @router.get("/skins/{name}/preview/{filename}")
    @route_handler("serve skin preview file")
    async def preview_skin_file(name: str, filename: str):
        """Serve an animation file (WebM or GIF) for live preview."""
        from distr.core.paths import AVATARS_DIR

        skin_dir = Path(AVATARS_DIR) / name
        file_path = skin_dir / filename

        # Security: ensure the resolved path stays inside the skin dir
        try:
            file_path = file_path.resolve()
            skin_dir_resolved = skin_dir.resolve()
            if not str(file_path).startswith(str(skin_dir_resolved)):
                raise HTTPException(status_code=400, detail="Invalid filename")
        except (OSError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid filename")

        if not file_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"File '{filename}' not found in skin '{name}'",
            )

        suffix = file_path.suffix.lower()
        media_types = {
            ".webm": "video/webm",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        if suffix not in media_types:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: '{suffix}'",
            )

        media_type = media_types[suffix]
        return FileResponse(str(file_path), media_type=media_type)
