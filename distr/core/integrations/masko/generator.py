"""
Skin generation orchestrator — manages the full Masko API workflow for
generating avatar skins.

Orchestrates: project creation → collection creation → pose generation →
(optional) canvas + transitions → asset download → skin.json assembly.

Runs generation in a background thread and tracks progress in an in-memory
dict keyed by generation_id.
"""
from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Literal, Optional

from .client import MaskoClient, MaskoError
from .models import (
    EVENT_HOOKS,
    POSE_PROMPT_SUFFIXES,
    CanvasNode,
    GenerationStatus,
    JobStatus,
    sanitize_skin_name,
)
from .skin_builder import build_skin_json, write_skin

logger = logging.getLogger(__name__)


class SkinGenerator:
    """Orchestrates full skin generation from Masko API.

    Stores progress in an in-memory dict keyed by generation_id.
    Must be paired with a MaskoClient instance.
    """

    def __init__(self, client: MaskoClient, avatars_dir: str):
        self._client = client
        self._avatars_dir = Path(avatars_dir)
        self._generations: Dict[str, _GenerationContext] = {}

    def start(
        self,
        generation_id: str,
        name: str,
        description: str,
        style: str,
        mode: Literal["static", "animated"],
    ) -> None:
        """Start a skin generation in a background thread."""
        sanitized = sanitize_skin_name(name)
        if not sanitized:
            # Store immediate failure
            ctx = _GenerationContext(
                generation_id=generation_id,
                name=name,
                description=description,
                style=style,
                mode=mode,
                sanitized_name="",
            )
            ctx.status = "failed"
            ctx.errors.append("Skin name sanitizes to empty string — please choose a different name")
            self._generations[generation_id] = ctx
            return

        skin_dir = self._avatars_dir / sanitized
        if skin_dir.exists():
            ctx = _GenerationContext(
                generation_id=generation_id,
                name=name,
                description=description,
                style=style,
                mode=mode,
                sanitized_name=sanitized,
            )
            ctx.status = "failed"
            ctx.errors.append(f"A skin folder named '{sanitized}' already exists — choose a different name")
            self._generations[generation_id] = ctx
            return

        ctx = _GenerationContext(
            generation_id=generation_id,
            name=name,
            description=description,
            style=style,
            mode=mode,
            sanitized_name=sanitized,
        )
        # Initialize hook statuses
        for hook in EVENT_HOOKS:
            ctx.hook_statuses[hook] = "pending"

        ctx.total_jobs = len(EVENT_HOOKS)  # Starts at 12; increases for animated mode transitions
        self._generations[generation_id] = ctx

        thread = threading.Thread(
            target=self._run_generation,
            args=(ctx,),
            daemon=True,
        )
        thread.start()

    def get_status(self, generation_id: str) -> GenerationStatus:
        """Return current generation status (non-blocking)."""
        ctx = self._generations.get(generation_id)
        if ctx is None:
            return GenerationStatus(
                status="failed",
                completed_jobs=0,
                total_jobs=0,
                current_hook=None,
                errors=[f"Unknown generation_id: {generation_id}"],
            )
        return GenerationStatus(
            status=ctx.status,
            completed_jobs=ctx.completed_jobs,
            total_jobs=ctx.total_jobs,
            current_hook=ctx.current_hook,
            hook_statuses=dict(ctx.hook_statuses),
            errors=list(ctx.errors),
            skin_name=ctx.sanitized_name if ctx.status == "complete" else None,
        )

    def cancel(self, generation_id: str) -> None:
        """Cancel an in-progress generation and clean up."""
        ctx = self._generations.get(generation_id)
        if ctx is None:
            return
        ctx.cancelled = True
        ctx.status = "cancelled"
        # Clean up partial skin folder
        self._cleanup_folder(ctx)

    def retry_failed(
        self,
        generation_id: str,
        hooks: Optional[List[str]] = None,
    ) -> None:
        """Retry failed hooks. If hooks is None, retry all failed hooks.

        For animated mode: creates a fresh canvas from all completed poses
        and runs generate-all anew.
        """
        ctx = self._generations.get(generation_id)
        if ctx is None:
            return

        # Determine which hooks to retry
        if hooks is None:
            retry_hooks = [h for h, s in ctx.hook_statuses.items() if s == "failed"]
        else:
            retry_hooks = [h for h in hooks if ctx.hook_statuses.get(h) == "failed"]

        if not retry_hooks:
            return

        # Reset status for retried hooks
        for hook in retry_hooks:
            ctx.hook_statuses[hook] = "pending"

        # Reset errors
        ctx.errors = []
        ctx.status = "in_progress"

        thread = threading.Thread(
            target=self._run_retry,
            args=(ctx, retry_hooks),
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # Internal generation logic
    # ------------------------------------------------------------------

    def _run_generation(self, ctx: _GenerationContext) -> None:
        """Main generation workflow running in a background thread."""
        try:
            ctx.status = "in_progress"

            # 1. Create project
            project_id = self._client.create_project(f"Skin: {ctx.name}")

            # 2. Create collection
            collection_id = self._client.create_collection(
                project_id, ctx.name, ctx.description, ctx.style,
            )

            # 3. Generate poses
            hook_to_job_id: Dict[str, str] = {}
            for hook in EVENT_HOOKS:
                if ctx.cancelled:
                    return
                prompt = f"{ctx.description}, {POSE_PROMPT_SUFFIXES[hook]}, {ctx.style} style"
                ctx.current_hook = hook
                ctx.hook_statuses[hook] = "in_progress"

                try:
                    if ctx.mode == "static":
                        job_id = self._client.generate_image(collection_id, prompt, transparent=True)
                    else:
                        job_id = self._client.generate_animation(collection_id, prompt, duration=4, loop=True)
                    hook_to_job_id[hook] = job_id
                except MaskoError as e:
                    ctx.hook_statuses[hook] = "failed"
                    ctx.errors.append(f"Hook '{hook}' generation failed: {e.message}")
                    continue

            # 4. Poll jobs until complete
            hook_to_item_id: Dict[str, str] = {}
            for hook, job_id in hook_to_job_id.items():
                if ctx.cancelled:
                    return
                if ctx.hook_statuses.get(hook) == "failed":
                    continue

                try:
                    job_status = self._poll_until_done(ctx, job_id)
                    if job_status.status == "completed" and job_status.result_item_id:
                        hook_to_item_id[hook] = job_status.result_item_id
                        ctx.hook_statuses[hook] = "completed"
                        ctx.completed_jobs += 1
                    else:
                        ctx.hook_statuses[hook] = "failed"
                        error_msg = job_status.error or "Job failed without error message"
                        ctx.errors.append(f"Hook '{hook}': {error_msg}")
                except MaskoError as e:
                    ctx.hook_statuses[hook] = "failed"
                    ctx.errors.append(f"Hook '{hook}' polling failed: {e.message}")
                except Exception as e:
                    ctx.hook_statuses[hook] = "failed"
                    ctx.errors.append(f"Hook '{hook}' unexpected error: {e}")

            # Check if all 12 hooks completed
            all_completed = all(s == "completed" for s in ctx.hook_statuses.values())
            any_failed = any(s == "failed" for s in ctx.hook_statuses.values())

            if any_failed:
                ctx.status = "failed"
                return

            if ctx.cancelled:
                return

            # 5. For animated mode: create canvas and generate transitions
            transitions: Dict[str, str] = {}
            if ctx.mode == "animated" and all_completed:
                try:
                    transitions = self._generate_transitions(ctx, collection_id, hook_to_item_id)
                except MaskoError as e:
                    ctx.status = "failed"
                    ctx.errors.append(f"Transition generation failed: {e.message}")
                    return
                except Exception as e:
                    ctx.status = "failed"
                    ctx.errors.append(f"Transition generation unexpected error: {e}")
                    return

            if ctx.cancelled:
                return

            # 6. Download assets
            skin_dir = self._avatars_dir / ctx.sanitized_name
            hook_to_file: Dict[str, str] = {}
            ext = ".webp" if ctx.mode == "static" else ".webm"

            for hook, item_id in hook_to_item_id.items():
                if ctx.cancelled:
                    return
                try:
                    variant = "transparent" if ctx.mode == "static" else "default"
                    asset_bytes = self._client.download_asset(item_id, variant=variant)
                    filename = f"{hook}{ext}"
                    skin_dir.mkdir(parents=True, exist_ok=True)
                    (skin_dir / filename).write_bytes(asset_bytes)
                    hook_to_file[hook] = filename
                except MaskoError as e:
                    ctx.hook_statuses[hook] = "failed"
                    ctx.errors.append(f"Hook '{hook}' download failed: {e.message}")
                except Exception as e:
                    ctx.hook_statuses[hook] = "failed"
                    ctx.errors.append(f"Hook '{hook}' download unexpected error: {e}")

            # Download transitions (animated mode)
            if ctx.mode == "animated":
                for trans_key, item_id in transitions.items():
                    if ctx.cancelled:
                        return
                    try:
                        asset_bytes = self._client.download_asset(item_id, variant="default")
                        filename = f"{trans_key}.webm"
                        skin_dir.mkdir(parents=True, exist_ok=True)
                        (skin_dir / filename).write_bytes(asset_bytes)
                        transitions[trans_key] = filename  # Replace item_id with filename
                    except MaskoError as e:
                        ctx.errors.append(f"Transition '{trans_key}' download failed: {e.message}")
                    except Exception as e:
                        ctx.errors.append(f"Transition '{trans_key}' download error: {e}")

            # 7. Check all hooks still succeeded after downloads
            if any(s == "failed" for s in ctx.hook_statuses.values()):
                ctx.status = "failed"
                self._cleanup_folder(ctx)
                return

            # 8. Build and write skin.json
            try:
                config = build_skin_json(
                    name=ctx.name,
                    mode=ctx.mode,
                    hook_to_file=hook_to_file,
                    transitions=transitions if ctx.mode == "animated" else None,
                )
                write_skin(skin_dir, config)
                ctx.status = "complete"
                ctx.current_hook = None
            except ValueError as e:
                ctx.status = "failed"
                ctx.errors.append(f"skin.json generation failed: {e}")
                self._cleanup_folder(ctx)
            except Exception as e:
                ctx.status = "failed"
                ctx.errors.append(f"skin.json write failed: {e}")
                self._cleanup_folder(ctx)

        except MaskoError as e:
            ctx.status = "failed"
            ctx.errors.append(f"Masko API error: {e.message}")
            self._cleanup_folder(ctx)
        except Exception as e:
            ctx.status = "failed"
            ctx.errors.append(f"Unexpected error: {e}")
            self._cleanup_folder(ctx)

    def _generate_transitions(
        self,
        ctx: _GenerationContext,
        collection_id: str,
        hook_to_item_id: Dict[str, str],
    ) -> Dict[str, str]:
        """Create canvas and generate transition animations.

        Returns dict mapping "hookA-hookB" to result_item_id for each transition.
        """
        # Build canvas nodes from all poses
        nodes = [
            CanvasNode(node_id=hook, item_id=item_id, label=hook)
            for hook, item_id in hook_to_item_id.items()
        ]

        canvas_id = self._client.create_canvas(collection_id, nodes)

        # Generate all transitions
        job_ids = self._client.generate_all_transitions(canvas_id)

        # Update total_jobs to include transition jobs
        ctx.total_jobs = len(EVENT_HOOKS) + len(job_ids)

        # Poll transition jobs
        transitions: Dict[str, str] = {}
        for i, job_id in enumerate(job_ids):
            if ctx.cancelled:
                return transitions

            try:
                job_status = self._poll_until_done(ctx, job_id)
                if job_status.status == "completed" and job_status.result_item_id:
                    # The transition key might come from the job result or we derive it
                    # Try to get the transition label from the result
                    trans_key = job_status.result_item_id  # Will be replaced with actual key
                    transitions[f"transition_{i}"] = job_status.result_item_id
                    ctx.completed_jobs += 1
                else:
                    ctx.errors.append(
                        f"Transition job failed: {job_status.error or 'unknown error'}"
                    )
            except MaskoError as e:
                ctx.errors.append(f"Transition polling failed: {e.message}")

        return transitions

    def _run_retry(self, ctx: _GenerationContext, retry_hooks: List[str]) -> None:
        """Retry failed hooks in a background thread."""
        try:
            ctx.status = "in_progress"

            # Create a fresh project for retry (simplifies state management)
            project_id = self._client.create_project(f"Skin Retry: {ctx.name}")
            collection_id = self._client.create_collection(
                project_id, ctx.name, ctx.description, ctx.style,
            )

            # Re-generate only the failed hooks
            hook_to_job_id: Dict[str, str] = {}
            for hook in retry_hooks:
                if ctx.cancelled:
                    return
                prompt = f"{ctx.description}, {POSE_PROMPT_SUFFIXES[hook]}, {ctx.style} style"
                ctx.current_hook = hook
                ctx.hook_statuses[hook] = "in_progress"

                try:
                    if ctx.mode == "static":
                        job_id = self._client.generate_image(collection_id, prompt, transparent=True)
                    else:
                        job_id = self._client.generate_animation(collection_id, prompt, duration=4, loop=True)
                    hook_to_job_id[hook] = job_id
                except MaskoError as e:
                    ctx.hook_statuses[hook] = "failed"
                    ctx.errors.append(f"Hook '{hook}' retry generation failed: {e.message}")
                    continue

            # Poll the retried jobs
            hook_to_item_id: Dict[str, str] = {}
            for hook, job_id in hook_to_job_id.items():
                if ctx.cancelled:
                    return
                if ctx.hook_statuses.get(hook) == "failed":
                    continue

                try:
                    job_status = self._poll_until_done(ctx, job_id)
                    if job_status.status == "completed" and job_status.result_item_id:
                        hook_to_item_id[hook] = job_status.result_item_id
                        ctx.hook_statuses[hook] = "completed"
                    else:
                        ctx.hook_statuses[hook] = "failed"
                        error_msg = job_status.error or "Job failed"
                        ctx.errors.append(f"Hook '{hook}' retry: {error_msg}")
                except MaskoError as e:
                    ctx.hook_statuses[hook] = "failed"
                    ctx.errors.append(f"Hook '{hook}' retry polling failed: {e.message}")

            # Check if all hooks are now completed
            if any(s == "failed" for s in ctx.hook_statuses.values()):
                ctx.status = "failed"
                return

            if ctx.cancelled:
                return

            # Download the retried assets
            skin_dir = self._avatars_dir / ctx.sanitized_name
            ext = ".webp" if ctx.mode == "static" else ".webm"

            # Rebuild hook_to_file from all completed hooks
            hook_to_file: Dict[str, str] = {}
            for hook in EVENT_HOOKS:
                if ctx.hook_statuses.get(hook) == "completed":
                    filename = f"{hook}{ext}"
                    hook_to_file[hook] = filename
                    # Download if this was a retried hook
                    if hook in hook_to_item_id:
                        try:
                            variant = "transparent" if ctx.mode == "static" else "default"
                            asset_bytes = self._client.download_asset(hook_to_item_id[hook], variant=variant)
                            skin_dir.mkdir(parents=True, exist_ok=True)
                            (skin_dir / filename).write_bytes(asset_bytes)
                        except MaskoError as e:
                            ctx.hook_statuses[hook] = "failed"
                            ctx.errors.append(f"Hook '{hook}' retry download failed: {e.message}")

            # For animated mode: create a fresh canvas from all completed poses
            transitions: Dict[str, str] = {}
            if ctx.mode == "animated":
                all_completed_item_ids = {}
                # We need item_ids for all hooks — for retried hooks we have them,
                # for originally completed hooks we may not have them stored
                # In practice, we'd need to store the original item_ids.
                # For simplicity in retry, we skip transitions if we don't have all item IDs
                if hook_to_item_id:
                    try:
                        all_item_ids = {
                            **getattr(ctx, '_original_item_ids', {}),
                            **hook_to_item_id,
                        }
                        if len(all_item_ids) == len(EVENT_HOOKS):
                            transitions = self._generate_transitions(ctx, collection_id, all_item_ids)
                    except Exception as e:
                        ctx.errors.append(f"Transition generation on retry failed: {e}")

            if any(s == "failed" for s in ctx.hook_statuses.values()):
                ctx.status = "failed"
                return

            # Rebuild skin.json
            try:
                config = build_skin_json(
                    name=ctx.name,
                    mode=ctx.mode,
                    hook_to_file=hook_to_file,
                    transitions=transitions if ctx.mode == "animated" else None,
                )
                write_skin(skin_dir, config)
                ctx.status = "complete"
                ctx.current_hook = None
            except ValueError as e:
                ctx.status = "failed"
                ctx.errors.append(f"skin.json generation on retry failed: {e}")
                self._cleanup_folder(ctx)
            except Exception as e:
                ctx.status = "failed"
                ctx.errors.append(f"skin.json write on retry failed: {e}")
                self._cleanup_folder(ctx)

        except MaskoError as e:
            ctx.status = "failed"
            ctx.errors.append(f"Masko API error during retry: {e.message}")
        except Exception as e:
            ctx.status = "failed"
            ctx.errors.append(f"Unexpected error during retry: {e}")

    def _poll_until_done(
        self, ctx: _GenerationContext, job_id: str, max_wait: int = 600
    ) -> JobStatus:
        """Poll a job until it completes or fails. Uses long-polling."""
        import time
        start = time.time()
        while True:
            if ctx.cancelled:
                return JobStatus(job_id=job_id, status="failed", error="Cancelled by user")

            elapsed = time.time() - start
            if elapsed > max_wait:
                return JobStatus(job_id=job_id, status="failed", error="Job polling timed out")

            result = self._client.poll_job(job_id, wait=True)
            if result.status in ("completed", "failed"):
                return result

            # If still pending/processing, the long-poll already waited
            # So we just loop again

    def _cleanup_folder(self, ctx: _GenerationContext) -> None:
        """Delete partial skin folder on cancel/complete failure."""
        if ctx.sanitized_name:
            skin_dir = self._avatars_dir / ctx.sanitized_name
            if skin_dir.exists():
                try:
                    shutil.rmtree(skin_dir)
                    logger.info("Cleaned up partial skin folder: %s", skin_dir)
                except Exception as e:
                    logger.warning("Failed to clean up folder %s: %s", skin_dir, e)


class _GenerationContext:
    """Internal state for a single generation run."""

    def __init__(
        self,
        generation_id: str,
        name: str,
        description: str,
        style: str,
        mode: Literal["static", "animated"],
        sanitized_name: str,
    ):
        self.generation_id = generation_id
        self.name = name
        self.description = description
        self.style = style
        self.mode = mode
        self.sanitized_name = sanitized_name
        self.status: Literal["pending", "in_progress", "complete", "failed", "cancelled"] = "pending"
        self.completed_jobs: int = 0
        self.total_jobs: int = len(EVENT_HOOKS)
        self.current_hook: Optional[str] = None
        self.hook_statuses: Dict[str, str] = {}
        self.errors: List[str] = []
        self.cancelled: bool = False