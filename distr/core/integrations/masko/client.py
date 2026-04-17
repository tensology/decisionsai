"""
HTTP client for the Masko AI API v1.

Provides methods for projects, collections, image/animation generation,
canvas management, job polling, and asset downloading.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Optional

from .models import Style, JobStatus, CanvasNode

logger = logging.getLogger(__name__)


class MaskoError(Exception):
    """Error returned by the Masko API."""

    def __init__(self, message: str, status_code: int = 0):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MaskoClient:
    """Low-level HTTP client for the Masko API v1."""

    BASE_URL = "https://api.masko.ai/v1"
    TIMEOUT = 30  # seconds

    def __init__(self, api_key: str):
        self._api_key = api_key

    # ------------------------------------------------------------------
    # Internal request helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        *,
        wait: bool = False,
        timeout: Optional[int] = None,
    ) -> dict:
        """Make an HTTP request with retry logic.

        Retries on 429 (up to 3 times with exponential backoff: 2s, 4s, 8s)
        and once on 5xx (after 5s delay).
        """
        url = f"{self.BASE_URL}{path}"
        if wait:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}wait=true"

        data = json.dumps(body).encode("utf-8") if body else None
        max_retries_on_429 = 3
        retry_delay_429 = 2  # seconds, doubles each retry

        for attempt in range(max_retries_on_429 + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers=self._headers(),
                method=method,
            )
            timeout_val = timeout or self.TIMEOUT
            try:
                with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                    resp_data = resp.read().decode("utf-8")
                    try:
                        return json.loads(resp_data)
                    except json.JSONDecodeError:
                        return {"raw": resp_data}

            except urllib.error.HTTPError as e:
                status = e.code
                error_body = ""
                try:
                    error_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass

                if status == 401:
                    raise MaskoError("Invalid API key", status_code=status)
                if status == 402:
                    raise MaskoError(
                        f"Insufficient credits: {error_body}", status_code=status
                    )
                if status == 429:
                    if attempt < max_retries_on_429:
                        logger.warning(
                            "Masko API rate limited (429), retrying in %ds (attempt %d/%d)",
                            retry_delay_429, attempt + 1, max_retries_on_429,
                        )
                        time.sleep(retry_delay_429)
                        retry_delay_429 *= 2
                        continue
                    raise MaskoError(
                        f"Rate limit exceeded after {max_retries_on_429} retries",
                        status_code=status,
                    )
                if 500 <= status < 600:
                    # Retry 5xx once after a short delay
                    if attempt == 0:
                        logger.warning(
                            "Masko API server error (%d), retrying once after 5s",
                            status,
                        )
                        time.sleep(5)
                        continue
                    raise MaskoError(
                        f"Server error ({status}): {error_body}", status_code=status
                    )
                raise MaskoError(
                    f"HTTP {status}: {error_body or e.reason}",
                    status_code=status,
                )

            except urllib.error.URLError as e:
                raise MaskoError(f"Network error: {e.reason}") from e

        # Should not be reached, but just in case
        raise MaskoError("Max retries exceeded")

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_credits(self) -> int:
        """Fetch current credit balance. Returns the balance as an int."""
        data = self._request("GET", "/credits")
        # The API returns {"credits": <int>} or {"balance": <int>}
        return int(data.get("credits", data.get("balance", 0)))

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def list_styles(self) -> List[Style]:
        """Fetch available generation styles."""
        data = self._request("GET", "/styles")
        styles_raw = data if isinstance(data, list) else data.get("styles", data.get("items", []))
        result: List[Style] = []
        for item in styles_raw:
            if isinstance(item, dict):
                result.append(Style(
                    id=str(item.get("id", item.get("slug", ""))),
                    name=item.get("name", ""),
                    preview_url=item.get("preview_url", item.get("image", "")),
                ))
        return result

    # ------------------------------------------------------------------
    # Projects & Collections
    # ------------------------------------------------------------------

    def create_project(self, name: str) -> str:
        """Create a new Masko project. Returns project_id."""
        data = self._request("POST", "/projects", {"name": name})
        return str(data.get("id", data.get("project_id", "")))

    def create_collection(
        self, project_id: str, name: str, description: str, style: str
    ) -> str:
        """Create a new collection within a project. Returns collection_id."""
        data = self._request(
            "POST",
            f"/projects/{project_id}/collections",
            {"name": name, "description": description, "style": style},
        )
        return str(data.get("id", data.get("collection_id", "")))

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_image(
        self,
        collection_id: str,
        prompt: str,
        transparent: bool = True,
    ) -> str:
        """Generate a static image. Returns job_id."""
        body = {
            "collection_id": collection_id,
            "prompt": prompt,
            "transparent": transparent,
        }
        data = self._request("POST", "/generate/image", body)
        return str(data.get("id", data.get("job_id", "")))

    def generate_animation(
        self,
        collection_id: str,
        prompt: str,
        duration: int = 4,
        loop: bool = True,
    ) -> str:
        """Generate an animation. Returns job_id."""
        body = {
            "collection_id": collection_id,
            "prompt": prompt,
            "duration": duration,
            "loop": loop,
        }
        data = self._request("POST", "/generate/animation", body)
        return str(data.get("id", data.get("job_id", "")))

    # ------------------------------------------------------------------
    # Canvas (animated mode)
    # ------------------------------------------------------------------

    def create_canvas(
        self, collection_id: str, nodes: List[CanvasNode]
    ) -> str:
        """Create a canvas with nodes for transition generation. Returns canvas_id."""
        nodes_data = [
            {"id": n.node_id, "item_id": n.item_id, "label": n.label}
            for n in nodes
        ]
        data = self._request(
            "POST",
            f"/collections/{collection_id}/canvas",
            {"nodes": nodes_data},
        )
        return str(data.get("id", data.get("canvas_id", "")))

    def generate_all_transitions(self, canvas_id: str) -> List[str]:
        """Generate all transition animations for a canvas. Returns list of job_ids."""
        data = self._request("POST", f"/canvas/{canvas_id}/generate-all")
        jobs_raw = data if isinstance(data, list) else data.get("jobs", data.get("items", []))
        return [
            str(j.get("id", j.get("job_id", ""))) if isinstance(j, dict) else str(j)
            for j in jobs_raw
        ]

    # ------------------------------------------------------------------
    # Job polling
    # ------------------------------------------------------------------

    def poll_job(self, job_id: str, wait: bool = True) -> JobStatus:
        """Poll a job for status. Uses long-polling with ?wait=true by default."""
        data = self._request("GET", f"/jobs/{job_id}", wait=wait)
        status_str = data.get("status", "pending")
        # Normalize status to our Literal
        if status_str not in ("pending", "processing", "completed", "failed"):
            status_str = "pending"

        result_item_id = data.get("result_item_id", data.get("item_id", None))
        if result_item_id is not None:
            result_item_id = str(result_item_id)

        error = data.get("error", data.get("message", None))

        return JobStatus(
            job_id=job_id,
            status=status_str,  # type: ignore[arg-type]
            result_item_id=result_item_id,
            error=error,
        )

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def download_asset(
        self, item_id: str, variant: str = "default"
    ) -> bytes:
        """Download a generated asset. Returns raw bytes.

        Use variant="transparent" for static images (WebP),
        variant="default" for animated (WebM).
        """
        path = f"/items/{item_id}/download"
        if variant and variant != "default":
            path = f"/items/{item_id}/download?variant={urllib.parse.quote(variant)}"

        url = f"{self.BASE_URL}{path}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._api_key}"})

        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise MaskoError(
                f"Failed to download asset {item_id}: HTTP {e.code}",
                status_code=e.code,
            ) from e
        except urllib.error.URLError as e:
            raise MaskoError(f"Failed to download asset {item_id}: {e.reason}") from e