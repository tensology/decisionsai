"""Video generator tool — Pixazo text-to-video when configured in Settings → LLMs."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class VideoGeneratorInput(BaseModel):
    prompt: str = Field(description="Description of the video clip to generate")
    output_path: Optional[str] = Field(default=None, description="Optional save path (folder or file). Defaults to desktop.")
    duration_sec: Optional[int] = Field(default=None, description="Optional target duration in seconds when the model supports it")


class VideoGeneratorTool(BaseTool):
    name: str = "video_generator"
    description: str = (
        "Generate a short video clip from a text prompt using the configured video model "
        "(Settings → LLMs → Video generation, Pixazo)."
    )
    args_schema: type[BaseModel] = VideoGeneratorInput

    def _config(self) -> tuple[str, str]:
        from distr.core.settings import load_settings_from_db
        from distr.core.chat import provider_slug

        settings = load_settings_from_db()
        provider = provider_slug(settings.get("video_llm_provider"))
        model = (settings.get("video_llm_model") or "").strip()
        return provider, model

    def _run(
        self,
        prompt: str = "",
        output_path: Optional[str] = None,
        duration_sec: Optional[int] = None,
        **kwargs,
    ) -> str:
        if not (prompt or "").strip():
            return "Error: No prompt provided."

        provider, model = self._config()
        if not provider or not model:
            return "Error: Video generation not configured. Set Provider and Model under Settings → LLMs → Video generation."

        if provider != "pixazo":
            return f"Error: Unsupported video provider '{provider}'. Only Pixazo is supported for native video generation."

        try:
            from distr.core.third_party_keys import pixazo_api_key
            from distr.core.pixazo_client import download_url_to_bytes, pixazo_generate_media_urls
            from distr.core.settings import resolve_folder_path

            api_key = pixazo_api_key()
            if not api_key:
                return "Error: Pixazo API key not configured. Add it in Settings → API Keys."

            extra = {}
            if duration_sec:
                extra["duration"] = duration_sec
            urls = pixazo_generate_media_urls(api_key, model, prompt.strip(), extra=extra, timeout_sec=600)
            if not urls:
                return "Error: Pixazo returned no video URL."

            if output_path and output_path.lower() in {"desktop", "my desktop"}:
                folder = resolve_folder_path("desktop")
            elif output_path and os.path.isdir(output_path):
                folder = output_path
            elif output_path:
                final_path = output_path
                folder = None
            else:
                folder = resolve_folder_path("desktop")
                final_path = None

            if folder is not None:
                safe = "".join(c for c in prompt[:40] if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_") or "video"
                final_path = os.path.join(folder, f"{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")

            data = download_url_to_bytes(urls[0], timeout=300)
            Path(final_path).parent.mkdir(parents=True, exist_ok=True)
            with open(final_path, "wb") as fh:
                fh.write(data)
            return f"Video saved to: {final_path}\n\nPrompt: {prompt.strip()}"
        except Exception as exc:
            logger.error("VideoGeneratorTool failed: %s", exc, exc_info=True)
            return f"Error: {exc}"
