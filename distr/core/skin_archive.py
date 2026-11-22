"""Validate and install portable skin ZIPs made from the existing skin format."""

import re
import shutil
import stat
import struct
import tempfile
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from PIL import Image

from distr.core.skin_config import parse, to_json, validate


MAX_UPLOAD = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILES = 256
MAX_CONFIG = 256 * 1024
MEDIA_SIGNATURES = {
    ".webm": (b"\x1a\x45\xdf\xa3",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}


def _plain_name(name):
    reserved = re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name.split(".")[0], re.I)
    return not reserved and bool(re.fullmatch(r"[\w-][\w .-]{0,159}", name)) and not name.endswith((" ", "."))


def _validate_assets(config, names):
    errors = validate(config)
    if errors:
        raise ValueError("Invalid skin.json. " + " ".join(errors))
    if not config.name.strip() or len(config.name) > 160:
        raise ValueError("The skin name must contain 1 to 160 characters.")
    for name in [event.animation for event in config.events.values()] + list(config.transitions.values()):
        if not isinstance(name, str) or Path(name).suffix.lower() not in MEDIA_SIGNATURES or not _plain_name(name):
            raise ValueError("Skin animations and transitions must reference supported media files by filename.")
        if name not in names:
            raise ValueError(f"The skin is missing '{name}'.")


def _validate_rendering(config):
    for field, minimum, maximum in (
        ("image_scale", 0.05, 8), ("image_offset_x", -4096, 4096),
        ("image_offset_y", -4096, 4096), ("chroma_threshold", 0, 255),
        ("unfocused_opacity", 0, 1),
    ):
        value = getattr(config.rendering, field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError(f"Invalid rendering value for '{field}' in skin.json.")
    key = config.rendering.chroma_key
    if key is not None and (len(key) != 3 or any(type(value) is not int or not 0 <= value <= 255 for value in key)):
        raise ValueError("Invalid chroma key in skin.json.")


def _archive_files(archive):
    members = archive.infolist()
    if len(members) > MAX_FILES or sum(member.file_size for member in members) > MAX_EXPANDED:
        raise ValueError("The ZIP exceeds the skin package size or file-count limit.")
    entries = []
    for member in members:
        parts = member.orig_filename.rstrip("/").split("/")
        if any(not part or part in (".", "..") or "\\" in part or ":" in part or "\x00" in part for part in parts):
            raise ValueError("The ZIP contains an invalid file path.")
        kind = stat.S_IFMT(member.external_attr >> 16)
        if kind not in (0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG):
            raise ValueError("The ZIP must contain regular files and folders.")
        if member.is_dir() or parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
            continue
        entries.append((parts, member))

    manifests = [parts for parts, _ in entries if parts[-1] == "skin.json"]
    if len(manifests) != 1 or len(manifests[0]) > 2:
        raise ValueError("The ZIP must contain one skin.json at its root or inside one skin folder.")
    prefix = manifests[0][:-1]
    files = {}
    used_names = set()
    for parts, member in entries:
        name = parts[-1]
        if parts[:-1] != prefix or not _plain_name(name) or name.casefold() in used_names:
            raise ValueError("The ZIP contains nested, duplicate, or invalid filenames.")
        if name != "skin.json" and Path(name).suffix.lower() not in MEDIA_SIGNATURES:
            raise ValueError(f"Unsupported file '{name}'. Use WebM, GIF, WebP, PNG, or JPEG media.")
        used_names.add(name.casefold())
        files[name] = member
    return files


def _check_media(file, name):
    header = file.read(64)
    suffix = Path(name).suffix.lower()
    if (not header.startswith(MEDIA_SIGNATURES[suffix])
            or (suffix == ".webp" and header[8:12] != b"WEBP")
            or (suffix == ".webm" and b"webm" not in header)):
        raise ValueError(f"'{name}' does not match its media format.")
    file.seek(0)
    try:
        if suffix == ".webm":
            import av

            try:
                with av.open(file, format="webm") as video:
                    if not video.streams.video:
                        raise ValueError("Missing video stream")
                    width, height = video.streams.video[0].width, video.streams.video[0].height
            except av.FFmpegError as exc:
                raise ValueError("Invalid WebM") from exc
        else:
            with Image.open(file) as image:
                width, height = image.size
                image.verify()
        if not (0 < width <= 4096 and 0 < height <= 4096):
            raise ValueError("Unsupported dimensions")
    except (OSError, ValueError, EOFError, SyntaxError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError(f"Could not read '{name}'. Use valid media up to 4096 × 4096 pixels.") from exc


def import_skin(upload, avatars_dir):
    """Install a validated ZIP, publishing skin.json after all media is in place."""
    avatars_dir = Path(avatars_dir)
    try:
        with ZipFile(upload) as archive:
            files = _archive_files(archive)
            if files["skin.json"].file_size > MAX_CONFIG:
                raise ValueError("skin.json is too large.")
            config = parse(archive.read(files.pop("skin.json")).decode("utf-8"))
            _validate_assets(config, files)
            _validate_rendering(config)
            slug = re.sub(r"[^a-z0-9]+", "-", config.name.lower()).strip("-")[:72] or "skin"
            destination = avatars_dir / f"imported-{slug}"
            avatars_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".skin-import-", dir=avatars_dir.parent) as temp:
                staging = Path(temp)
                for name, member in files.items():
                    with archive.open(member) as media:
                        _check_media(media, name)
                        media.seek(0)
                        with (staging / name).open("wb") as output:
                            shutil.copyfileobj(media, output)
                (staging / "skin.json").write_text(to_json(config), encoding="utf-8")
                destination.mkdir()
                try:
                    for name in files:
                        (staging / name).replace(destination / name)
                    (staging / "skin.json").replace(destination / "skin.json")
                except OSError:
                    shutil.rmtree(destination)
                    raise
            return destination.name, config.name
    except (BadZipFile, NotImplementedError, RuntimeError, zlib.error) as exc:
        raise ValueError("Could not read the skin ZIP. Use an unencrypted ZIP file.") from exc
    except (TypeError, OverflowError) as exc:
        raise ValueError("Invalid value in skin.json.") from exc


def export_skin(source, config, output):
    """Write the skin config and supported media to a portable ZIP."""
    files = {
        path.name: path for path in source.iterdir()
        if path.is_file() and not path.is_symlink() and _plain_name(path.name)
        and path.suffix.lower() in MEDIA_SIGNATURES
    }
    _validate_assets(config, files)
    _validate_rendering(config)
    if len(files) >= MAX_FILES or sum(path.stat().st_size for path in files.values()) > MAX_UPLOAD:
        raise ValueError("The skin exceeds the size or file-count limit for a portable ZIP.")
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("skin.json", to_json(config))
        for name, path in sorted(files.items()):
            with path.open("rb") as media:
                _check_media(media, name)
            archive.write(path, name)
    if output.tell() > MAX_UPLOAD:
        raise ValueError("The exported skin ZIP exceeds 128 MB.")
