"""Canonical, channel-neutral work intake pipeline."""

from .contracts import (
    WorkIntake,
    WorkIntakeAction,
    WorkIntakeAttachment,
    WorkIntakeDecision,
    WorkIntakeSource,
)
from .service import OrchestratorIntakeService, get_work_intake_service

__all__ = [
    "WorkIntake",
    "WorkIntakeAction",
    "WorkIntakeAttachment",
    "WorkIntakeDecision",
    "WorkIntakeSource",
    "OrchestratorIntakeService",
    "get_work_intake_service",
]
