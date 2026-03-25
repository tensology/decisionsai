"""
Step Runner database models.

Stores sessions and steps for breaking down big instructions into
executable sub-steps with validation and approval.

Two session types:
- instruction: One-off, user-triggered (plan from instruction, execute manually)
- scheduled: Recurring, runs on schedule (e.g. "every day check my calendar")
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from . import Base
from datetime import datetime


class StepRunnerSession(Base):
    """A session for a multi-step workflow derived from a single instruction."""
    __tablename__ = 'step_runner_sessions'

    id = Column(Integer, primary_key=True)
    instruction = Column(Text, nullable=False)  # Original user instruction
    status = Column(String, default='planned')  # planned, in_progress, completed, failed, cancelled
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=True)  # Optional link to chat
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Session type: "instruction" (one-off, user-triggered) or "scheduled" (recurring)
    session_type = Column(String, default='instruction')
    # Schedule: preset ("daily", "hourly", "weekly") or cron ("0 9 * * *" = 9am daily)
    schedule = Column(String, nullable=True)
    # For scheduled sessions: when to run next
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    # Scheduled sessions can be enabled/disabled
    enabled = Column(Boolean, default=True)
    # Timezone for schedule (e.g. "America/New_York") - None = UTC
    timezone = Column(String, nullable=True)
    # Custom time for daily/weekly: "08:00" = 8am
    schedule_time = Column(String, nullable=True)
    # For weekly: comma-separated cron weekdays "1,3,5" = Mon, Wed, Fri (0=Sun, 1=Mon, ..., 6=Sat)
    schedule_days = Column(String, nullable=True)

    # Context and Rules (renamed from Variables) — free-form text for AI Agent pre-context
    context_rules = Column(Text, nullable=True)
    # Workflow Input — JSON-serialized WorkflowInput trigger data (source type, text, title, images, etc.)
    workflow_input = Column(Text, nullable=True)

    steps = relationship(
        "StepRunnerStep",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="StepRunnerStep.position"
    )


class StepRunnerStep(Base):
    """A single step within a Step Runner session."""
    __tablename__ = 'step_runner_steps'

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('step_runner_sessions.id'), nullable=False)
    position = Column(Integer, default=0)  # Order within session
    title = Column(String, nullable=False)  # Short label
    instruction = Column(Text, nullable=False)  # What to do for this step
    verification = Column(Text, nullable=True)  # Optional post-step verification criteria
    status = Column(String, default='pending')  # pending, approved, running, waiting, completed, failed, skipped
    result = Column(Text)  # Output or error from execution
    tool_used = Column(String)  # Tool name if executed via agent
    step_type = Column(String, default='run_command')  # run_command, play_recording, http_request, execute_code, playwright
    config = Column(Text, nullable=True)  # JSON-serialized type-specific configuration
    code = Column(Text, nullable=True)  # Generated or hand-written code for Execute Code / Playwright steps
    created_date = Column(DateTime, default=datetime.utcnow)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("StepRunnerSession", back_populates="steps")


class StepRunnerRun(Base):
    """Record of a scheduled session run (for history)."""
    __tablename__ = 'step_runner_runs'

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey('step_runner_sessions.id'), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default='running')  # running, completed, failed
    step_results = Column(Text)  # JSON: [{step_id, status, result}, ...]

    session = relationship("StepRunnerSession", backref="runs")
