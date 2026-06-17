"""yt-dlp CLI helpers for workflows and harness bootstrap."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def ytdlp_command() -> list[str]:
    """Return argv prefix to invoke yt-dlp (binary or python -m)."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    try:
        import yt_dlp  # noqa: F401

        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        return ["yt-dlp"]


def is_ytdlp_available() -> bool:
    if shutil.which("yt-dlp"):
        return True
    try:
        import yt_dlp  # noqa: F401

        return True
    except ImportError:
        return False


def ensure_ytdlp_package() -> dict[str, Any]:
    if is_ytdlp_available():
        return {"installed": True, "method": "existing"}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        ok = result.returncode == 0 and is_ytdlp_available()
        return {
            "installed": ok,
            "method": "pip",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:400],
        }
    except Exception as exc:
        return {"installed": False, "reason": str(exc)}


def ytdlp_version() -> str:
    try:
        result = subprocess.run(
            [*ytdlp_command(), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except Exception:
        pass
    return ""


def fetch_metadata(url: str) -> dict[str, Any]:
    cmd = [*ytdlp_command(), "--dump-single-json", "--no-download", url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout or "yt-dlp failed")[:500]}
    try:
        payload = json.loads(result.stdout)
        return {
            "ok": True,
            "id": payload.get("id"),
            "title": payload.get("title"),
            "description": (payload.get("description") or "")[:2000],
            "duration": payload.get("duration"),
            "uploader": payload.get("uploader"),
            "webpage_url": payload.get("webpage_url") or url,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def fetch_subtitles(url: str, *, lang: str = "en") -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="decisions-ytdlp-") as tmp:
        out = Path(tmp) / "%(id)s"
        cmd = [
            *ytdlp_command(),
            "--write-sub",
            "--write-auto-sub",
            "--sub-lang",
            lang,
            "--skip-download",
            "-o",
            str(out),
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or result.stdout or "subtitle fetch failed")[:500]}
        vtt_files = list(Path(tmp).glob("*.vtt")) + list(Path(tmp).glob("*.srt"))
        if not vtt_files:
            return {"ok": False, "error": "no subtitle file produced"}
        text = vtt_files[0].read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "path": str(vtt_files[0]), "text": text[:8000], "format": vtt_files[0].suffix}


def search_youtube(query: str, *, limit: int = 5) -> dict[str, Any]:
    cmd = [
        *ytdlp_command(),
        "--dump-single-json",
        "--flat-playlist",
        f"ytsearch{max(1, min(limit, 20))}:{query}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout or "search failed")[:500]}
    items: list[dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            items.append(
                {
                    "id": row.get("id"),
                    "title": row.get("title"),
                    "url": row.get("url") or row.get("webpage_url"),
                }
            )
        except Exception:
            continue
    return {"ok": True, "items": items}


def run_ytdlp_step(config: dict[str, Any]) -> dict[str, Any]:
    """Execute a workflow ytdlp step from step config."""
    if not is_ytdlp_available():
        installed = ensure_ytdlp_package()
        if not installed.get("installed"):
            return {
                "output": "yt-dlp not installed. Run bin/setup.py or pip install yt-dlp.",
                "passed": False,
            }

    mode = str(config.get("mode") or "metadata").strip().lower()
    url = str(config.get("url") or "").strip()
    query = str(config.get("query") or config.get("search") or "").strip()
    lang = str(config.get("sub_lang") or config.get("lang") or "en").strip()

    if mode == "search":
        if not query and not url:
            return {"output": "ytdlp search requires query", "passed": False}
        payload = search_youtube(query or url, limit=int(config.get("limit") or 5))
    elif mode in {"subtitles", "subs", "transcript"}:
        if not url:
            return {"output": "ytdlp subtitles requires url", "passed": False}
        payload = fetch_subtitles(url, lang=lang)
    elif mode in {"download", "video"}:
        if not url and not query:
            urls = config.get("urls") or []
            if isinstance(urls, str):
                urls = [u.strip() for u in urls.splitlines() if u.strip()]
        else:
            urls = [url] if url else [query]
        if not urls:
            return {"output": "ytdlp download requires url or urls", "passed": False}
        from distr.core import download_jobs

        job_id = download_jobs.create_job(
            urls,
            title=str(config.get("title") or ""),
            output_dir=str(config.get("output_dir") or "") or None,
        )
        download_jobs.start_ytdlp_job(job_id)
        payload = {
            "ok": True,
            "job_id": job_id,
            "manager_path": "/downloads/",
            "message": "Download started — open Download Manager for progress.",
        }
    else:
        if not url:
            return {"output": "ytdlp metadata requires url", "passed": False}
        payload = fetch_metadata(url)

    if mode not in {"download", "video"}:
        if not payload.get("ok"):
            return {"output": str(payload.get("error") or "yt-dlp failed"), "passed": False}
        return {"output": json.dumps(payload, indent=2)[:2000], "passed": True}

    if not payload.get("ok"):
        return {"output": str(payload.get("error") or "yt-dlp failed"), "passed": False}
    return {"output": json.dumps(payload, indent=2)[:2000], "passed": True}
