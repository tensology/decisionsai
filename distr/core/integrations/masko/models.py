"""
Data models and utilities for the Masko AI integration.

Defines dataclasses for API communication, generation status tracking,
and skin name sanitization.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Skin name sanitization
# ---------------------------------------------------------------------------

def sanitize_skin_name(name: str) -> str:
    """Convert a display name to a filesystem-safe folder name.

    - Lowercase
    - Replace spaces with hyphens
    - Strip special characters (keep a-z, 0-9, hyphens)
    - Strip leading/trailing hyphens

    Returns empty string if input sanitizes to nothing — caller must reject.
    """
    safe = name.strip().lower()
    safe = re.sub(r'[^a-z0-9\s-]', '', safe)
    safe = re.sub(r'[\s]+', '-', safe)
    safe = safe.strip('-')
    return safe


# ---------------------------------------------------------------------------
# Event hook → pose prompt mapping
# ---------------------------------------------------------------------------

EVENT_HOOKS: List[str] = [
    "idle",
    "hands_free_listening",
    "ptt_active",
    "dictation",
    "recording_action",
    "file_drop_success",
    "tts_response",
    "running_action",
    "running_step_runner",
    "snippet_copied",
    "thinking",
    "needs_attention",
]

POSE_PROMPT_SUFFIXES: Dict[str, str] = {
    "idle": "standing relaxed, neutral pose",
    "hands_free_listening": "listening attentively, head slightly tilted",
    "ptt_active": "alert and ready, leaning forward",
    "dictation": "writing or taking notes",
    "recording_action": "holding a microphone, recording",
    "file_drop_success": "celebrating, happy gesture",
    "tts_response": "talking, mouth open, gesturing",
    "running_action": "working busily, focused",
    "running_step_runner": "running or moving quickly",
    "snippet_copied": "thumbs up, approval gesture",
    "thinking": "thinking, hand on chin",
    "needs_attention": "waving, trying to get attention",
}

# Estimated forward transitions for animated mode cost calculation
ESTIMATED_FORWARD_TRANSITIONS = [
    ("idle", "thinking"),
    ("idle", "tts_response"),
    ("thinking", "running_action"),
    ("idle", "hands_free_listening"),
    ("idle", "needs_attention"),
    ("thinking", "running_step_runner"),
]

CREDITS_PER_IMAGE = 1
CREDITS_PER_ANIMATION = 21  # 4-second animation at ~5 credits/sec
CREDITS_PER_TRANSITION_SECOND = 5
TRANSITION_DURATION_SECONDS = 4


# ---------------------------------------------------------------------------
# API response dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Style:
    id: str
    name: str
    preview_url: str


@dataclass
class JobStatus:
    job_id: str
    status: Literal["pending", "processing", "completed", "failed"]
    result_item_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CanvasNode:
    node_id: str
    item_id: str
    label: str  # event hook name


@dataclass
class GenerationStatus:
    status: Literal["pending", "in_progress", "complete", "failed", "cancelled"]
    completed_jobs: int
    total_jobs: int  # Mutable: starts at 12 for animated mode (poses), increases when transition jobs are queued
    current_hook: Optional[str]
    hook_statuses: Dict[str, str] = field(default_factory=dict)  # hook_name -> "pending"|"in_progress"|"completed"|"failed"
    errors: List[str] = field(default_factory=list)
    skin_name: Optional[str] = None