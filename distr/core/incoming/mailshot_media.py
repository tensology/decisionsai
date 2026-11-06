"""Mailshot attachment bytes for Incoming previews (local tunnel only)."""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote

logger = logging.getLogger(__name__)

_MAILSHOT_ENV_CANDIDATES = (
    Path.home() / "TensologyTeam/secrets/mailshot.env",
    Path.home() / "TensologyTeam/secrets/mailshot-finance.env",
)


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _mailshot_tunnel_config() -> tuple[str, str]:
    """Return (base_url, api_key) for the local Mailshot tunnel only."""
    for path in _MAILSHOT_ENV_CANDIDATES:
        env = _read_env(path)
        base = str(env.get("MAILSHOT_INTERNAL_BASE_URL") or "").rstrip("/")
        key = str(env.get("MAILSHOT_TENSOLOGY_API_KEY") or "").strip()
        if not base or not key:
            continue
        if not base.startswith(("http://127.0.0.1", "http://localhost")):
            raise RuntimeError("Mailshot base is not the tunnel; refusing public host")
        return base, key
    raise FileNotFoundError("Mailshot tunnel credentials are not configured.")


def _filename_from_disposition(header: str | None, fallback: str) -> str:
    value = str(header or "")
    if "filename*=" in value.lower():
        try:
            part = value.split("filename*=", 1)[1].split(";", 1)[0].strip().strip('"')
            if "''" in part:
                part = part.split("''", 1)[1]
            name = unquote(part)
            if name:
                return Path(name).name
        except Exception:
            pass
    if "filename=" in value.lower():
        try:
            part = value.split("filename=", 1)[1].split(";", 1)[0].strip().strip('"')
            if part:
                return Path(part).name
        except Exception:
            pass
    return Path(fallback or "attachment").name


def fetch_mailshot_attachment_bytes(
    message_id: str,
    attachment_id: str,
    *,
    filename_hint: str = "",
) -> tuple[bytes, str, str]:
    """Download one Mailshot attachment via the local integrations tunnel.

    Tensology's public mail proxy does not expose attachment bytes; Incoming
    previews the same tunnel path Tensology uses server-side for MailShot.
    """
    mid = str(message_id or "").strip()
    aid = str(attachment_id or "").strip()
    if not mid or not aid:
        raise FileNotFoundError("Mailshot message_id and attachment_id are required.")
    base, key = _mailshot_tunnel_config()
    url = (
        f"{base}/api/integrations/v1/messages/{quote(mid, safe='')}"
        f"/attachments/{quote(aid, safe='')}"
        f"?disposition=inline"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "X-Tensology-Source": "decisionsai-incoming-mailshot",
            "Accept": "*/*",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            if not data:
                raise FileNotFoundError("Mailshot attachment was empty.")
            mime = (response.headers.get("Content-Type") or "application/octet-stream").split(";", 1)[0].strip()
            filename = _filename_from_disposition(
                response.headers.get("Content-Disposition"),
                filename_hint or aid,
            )
            return data, mime or "application/octet-stream", filename
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FileNotFoundError("Mailshot attachment could not be found.") from exc
        raise RuntimeError(f"Mailshot attachment download failed (HTTP {exc.code}).") from exc
    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.info("Mailshot attachment download failed", exc_info=True)
        raise RuntimeError("Mailshot attachment download failed.") from exc
