"""
Queue YouTube/video downloads via yt-dlp and open the Download Manager.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import webbrowser
from typing import Any, List, Optional, Union

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.web_runtime import internal_api_headers, resolve_local_web_base_url
from distr.gui.web.security import INTERNAL_AUTH_HEADER

logger = logging.getLogger(__name__)


class YtdlpDownloadInput(BaseModel):
    urls: Union[str, List[str]] = Field(
        ...,
        description="One URL or list of video URLs to download with yt-dlp",
    )
    title: str = Field("", description="Optional label shown in Download Manager")
    output_dir: str = Field(
        "",
        description="Optional output folder (default ~/Downloads/DecisionsAI)",
    )


class YtdlpDownloadTool(BaseTool):
    """Download videos with yt-dlp and show progress in Download Manager."""

    name: str = "ytdlp_download"
    description: str = (
        "Download YouTube or other video URLs using yt-dlp. Opens the Download Manager "
        "so the user can watch live progress, speed, and ETA. Use when the user asks to "
        "download videos, save YouTube links, or batch-fetch media. Pass one URL or many."
    )
    args_schema: type[BaseModel] = YtdlpDownloadInput
    chat_manager: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager

    def _run(
        self,
        urls: Union[str, List[str]] = "",
        title: str = "",
        output_dir: str = "",
        **kwargs,
    ) -> str:
        return self._download(urls, title, output_dir)

    async def _arun(
        self,
        urls: Union[str, List[str]] = "",
        title: str = "",
        output_dir: str = "",
        **kwargs,
    ) -> str:
        return self._download(urls, title, output_dir)

    def _normalize_urls(self, urls: Union[str, List[str]]) -> List[str]:
        if isinstance(urls, list):
            return [u.strip() for u in urls if (u or "").strip()]
        text = (urls or "").strip()
        if not text:
            return []
        if "\n" in text:
            return [u.strip() for u in text.splitlines() if u.strip()]
        if "," in text and "http" in text:
            return [u.strip() for u in text.split(",") if u.strip()]
        return [text]

    def _download(self, urls: Union[str, List[str]], title: str, output_dir: str) -> str:
        clean = self._normalize_urls(urls)
        if not clean:
            return "Error: No URLs provided."

        base_url = resolve_local_web_base_url()
        if not base_url:
            return "Error: Web server is not ready. Try again in a moment."

        headers = internal_api_headers()
        if not headers.get(INTERNAL_AUTH_HEADER):
            return "Error: Could not authenticate with the local web server."

        payload = {"urls": clean, "title": title or ""}
        if (output_dir or "").strip():
            payload["output_dir"] = output_dir.strip()

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/api/downloads",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.error("YtdlpDownloadTool: API failed: %s", exc, exc_info=True)
            return f"Error starting download: {exc}"

        job_id = (result or {}).get("id")
        manager = f"{base_url}/downloads/"
        try:
            webbrowser.open(manager)
        except Exception as exc:
            logger.warning("YtdlpDownloadTool: could not open browser: %s", exc)
            return f"Download started (job {job_id}). Open Download Manager: {manager}"

        count = len(clean)
        return (
            f"Started downloading {count} video{'s' if count != 1 else ''}. "
            "Opened Download Manager — you can watch progress, speed, and ETA there."
        )
