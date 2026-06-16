"""First-class automation definitions — separate from workflow engine rows."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from . import Base
from .time import utc_now_naive


class Automation(Base):
    """A scheduled automation (instruction or tool-bound), not a multi-step workflow."""

    __tablename__ = "automations"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, default="New Automation")
    description = Column(Text, nullable=True)
    status = Column(String, default="active")  # active, paused

    automation_type = Column(String, default="scheduled_instruction")
    preset_id = Column(String, default="")
    instruction = Column(Text, default="")
    action_config = Column(Text, default="{}")  # JSON

    schedule_enabled = Column(Boolean, default=False)
    schedule_preset = Column(String, nullable=True)
    schedule_time = Column(String, nullable=True)
    schedule_days = Column(String, nullable=True)
    schedule_timezone = Column(String, nullable=True)
    schedule_config = Column(Text, default="{}")  # JSON round-trip for Automations UI
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)

    legacy_workflow_id = Column(Integer, nullable=True, index=True)

    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    runs = relationship(
        "AutomationRun",
        back_populates="automation",
        cascade="all, delete-orphan",
        order_by="AutomationRun.started_at.desc()",
    )


class AutomationRun(Base):
    """Run history for an automation."""

    __tablename__ = "automation_runs"

    id = Column(Integer, primary_key=True)
    automation_id = Column(Integer, ForeignKey("automations.id"), nullable=False, index=True)
    status = Column(String, default="running")
    run_data = Column(Text, default="{}")
    started_at = Column(DateTime, default=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)

    automation = relationship("Automation", back_populates="runs")
