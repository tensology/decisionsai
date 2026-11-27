"""
Skins routes — /skins, /skins/select, /skins/{name}/config,
                /skins/{name}/files, /skins/{name}/preview/{filename}

Requirements: 11.1-11.10
"""
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from distr.core.skin_config import MAX_SKIN_SIZE_SCALE, MIN_SKIN_SIZE_SCALE

from ._shared import route_handler, SkinSelectRequest


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class SkinSizeUpdateRequest(BaseModel):
    sphere_size: int = Field(ge=MIN_SKIN_SIZE_SCALE, le=MAX_SKIN_SIZE_SCALE)


class OracleBackgroundRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


def _skin_media_version(skin_dir: Path, idle_file, preview_file):
    """Return (version, still_is_current) for a skin folder's media files."""
    import hashlib

    digest = hashlib.sha1()
    try:
        entries = sorted(skin_dir.iterdir(), key=lambda item: item.name)
    except OSError:
        entries = []
    mtimes = {}
    for entry in entries:
        try:
            info = entry.stat()
        except OSError:
            continue
        if not entry.is_file():
            continue
        mtimes[entry.name] = info.st_mtime_ns
        digest.update(f"{entry.name}:{info.st_size}:{info.st_mtime_ns};".encode("utf-8"))
    idle_name = Path(idle_file).name if idle_file else None
    still_current = bool(preview_file) and (
        idle_name not in mtimes or mtimes.get(preview_file, 0) >= mtimes[idle_name]
    )
    return digest.hexdigest()[:12], still_current


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
            idle_file = idle_anim.animation if idle_anim else None
            preview_file = Path(idle_file).with_suffix(".webp").name if idle_file else None
            if preview_file and not (Path(AVATARS_DIR) / folder_name / preview_file).is_file():
                preview_file = None
            media_version, preview_current = _skin_media_version(Path(AVATARS_DIR) / folder_name, idle_file, preview_file)
            skins.append({
                "folder_name": folder_name,
                "name": config.name,
                "type": config.type,
                "idle_animation": idle_file,
                "preview_image": preview_file,
                # Remote clients key cached clips by media_version, so a regenerated
                # skin shows up; a still older than the idle clip shows a superseded look.
                "media_version": media_version,
                "preview_image_current": preview_current,
                "idle_playback": idle_anim.playback if idle_anim else "loop",
                "shape": config.rendering.shape,
                "image_scale": config.rendering.image_scale,
                "image_offset_x": config.rendering.image_offset_x,
                "image_offset_y": config.rendering.image_offset_y,
                "chroma_key": list(config.rendering.chroma_key) if config.rendering.chroma_key else None,
                "chroma_threshold": config.rendering.chroma_threshold,
            })

        settings = load_settings_from_db()
        from distr.core.services.settings_service import active_oracle_background
        selected = settings.get("selected_oracle", "oracle") or "oracle"
        sphere_size = settings.get("sphere_size", 180)

        return JSONResponse({
            "skins": skins,
            "selected_skin": selected,
            "selected_oracle_background": active_oracle_background(),
            "sphere_size": sphere_size,
        })

    @router.get("/skins/oracle/background")
    async def get_oracle_background():
        from distr.core.services.settings_service import active_oracle_background

        return {"filename": active_oracle_background()}

    @router.put("/skins/oracle/background")
    async def set_oracle_background(data: OracleBackgroundRequest):
        from distr.core.services.settings_service import update_oracle_background

        try:
            update_oracle_background(data.filename)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"filename": data.filename}

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

    @router.post("/skins/import", status_code=201)
    async def import_skin_archive(request: Request):
        from distr.core.paths import AVATARS_DIR
        from distr.core.services.settings_service import update_oracle_skin
        from distr.core.skin_archive import MAX_UPLOAD, import_skin

        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/zip":
            raise HTTPException(415, "Upload a skin ZIP file.")
        with tempfile.TemporaryFile() as upload:
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_UPLOAD:
                    raise HTTPException(413, "The skin ZIP must be 128 MB or smaller.")
                if chunk:
                    await run_in_threadpool(upload.write, chunk)
            upload.seek(0)
            try:
                folder, name = await run_in_threadpool(import_skin, upload, AVATARS_DIR)
            except FileExistsError as exc:
                raise HTTPException(409, "A skin with this name is already imported.") from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        update_oracle_skin(folder)
        return {"folder_name": folder, "name": name}

    @router.get("/skins/{name}/export")
    async def export_skin_archive(name: str):
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_archive import export_skin
        from distr.core.skin_discovery import get_skin_by_name

        if name in (".", "..") or "\\" in name or "/" in name:
            raise HTTPException(404, "Skin not found.")
        skin = get_skin_by_name(AVATARS_DIR, name)
        if skin is None:
            raise HTTPException(404, "Skin not found.")
        source = Path(AVATARS_DIR) / name
        if source.is_symlink():
            raise HTTPException(422, "Export requires a skin folder stored in DecisionsAI.")
        output = tempfile.TemporaryFile()
        try:
            await run_in_threadpool(export_skin, source, skin[1], output)
            size = output.tell()
            output.seek(0)
        except ValueError as exc:
            output.close()
            raise HTTPException(422, str(exc)) from exc
        except BaseException:
            output.close()
            raise
        filename = quote(f"{name}.zip", safe="")
        return StreamingResponse(
            iter(lambda: output.read(64 * 1024), b""), media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}", "Content-Length": str(size)},
            background=BackgroundTask(output.close),
        )

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

    @router.get("/skins/{name}/size")
    @route_handler("get skin size")
    async def get_skin_size_route(name: str):
        """Return the saved per-skin size as scale and pixels."""
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_discovery import get_skin_by_name
        from distr.core.db.skin_sizes import get_skin_size

        result = get_skin_by_name(AVATARS_DIR, name)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Skin '{name}' not found or has invalid config",
            )

        size_px = get_skin_size(name, result[1].rendering.default_size_px)
        scale = max(MIN_SKIN_SIZE_SCALE, min(MAX_SKIN_SIZE_SCALE, round(size_px / 20)))
        return JSONResponse({"skin_name": name, "sphere_size": scale, "pixels": size_px})

    @router.post("/skins/{name}/size")
    @route_handler("update skin size")
    async def update_skin_size_route(name: str, data: SkinSizeUpdateRequest):
        """Persist per-skin size without forcing a live skin switch."""
        from distr.core.paths import AVATARS_DIR
        from distr.core.skin_discovery import get_skin_by_name
        from distr.core.db.skin_sizes import set_skin_size

        result = get_skin_by_name(AVATARS_DIR, name)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Skin '{name}' not found or has invalid config",
            )

        size_px = int(data.sphere_size) * 20
        set_skin_size(name, size_px)
        return JSONResponse({"success": True, "skin_name": name, "sphere_size": data.sphere_size, "pixels": size_px})

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
