"""Hermes orchestration ledger models."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text

from . import Base


class HermesEvent(Base):
    """Canonical event across tickets, workflows, executors, and channels."""

    __tablename__ = "hermes_events"

    id = Column(Integer, primary_key=True)
    event_uid = Column(String, nullable=False, unique=True)
    source = Column(String, nullable=False, default="workflow")
    event_type = Column(String, nullable=False, default="event")
    status = Column(String, nullable=True)

    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("auto_workflow_runs.id"), nullable=True)
    step_id = Column(Integer, ForeignKey("auto_workflow_steps.id"), nullable=True)
    ticket_id = Column(Integer, ForeignKey("kanban_tickets.id"), nullable=True)
    board_id = Column(Integer, ForeignKey("kanban_boards.id"), nullable=True)
    project_id = Column(Integer, nullable=True)
    execution_session_id = Column(Integer, ForeignKey("project_execution_sessions.id"), nullable=True)
    parent_event_id = Column(Integer, ForeignKey("hermes_events.id"), nullable=True)

    summary = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class HermesUserMemory(Base):
    """Durable user preference/style memory extracted from conversations."""

    __tablename__ = "hermes_user_memories"

    id = Column(Integer, primary_key=True)
    memory_uid = Column(String, nullable=False, unique=True)
    content = Column(Text, nullable=False, default="")
    normalized_content = Column(Text, nullable=False, default="")
    content_hash = Column(String, nullable=False)
    category = Column(String, nullable=False, default="preference")
    tags = Column(Text, nullable=True)
    visibility = Column(String, nullable=False, default="private")
    scope = Column(String, nullable=False, default="global")
    scope_id = Column(Integer, nullable=True)
    project_id = Column(Integer, nullable=True)
    source_type = Column(String, nullable=False, default="")
    source_id = Column(String, nullable=False, default="")
    source_chat_id = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=False, default=0.6)
    evidence_count = Column(Integer, nullable=False, default=1)
    payload_json = Column(Text, nullable=True)
    enabled = Column(Integer, nullable=False, default=1)
    manually_added = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HermesMachineActivity(Base):
    """Quiet local activity samples that help Hermes infer active work context."""

    __tablename__ = "hermes_machine_activity"

    id = Column(Integer, primary_key=True)
    activity_uid = Column(String, nullable=False, unique=True)
    surface = Column(String, nullable=False, default="desktop")
    app_name = Column(String, nullable=False, default="")
    window_title = Column(Text, nullable=False, default="")
    workspace_path = Column(Text, nullable=False, default="")
    project_id = Column(Integer, nullable=True)
    summary = Column(Text, nullable=False, default="")
    metadata_json = Column(Text, nullable=True)
    content_hash = Column(String, nullable=False)
    evidence_count = Column(Integer, nullable=False, default=1)
    compacted = Column(Integer, nullable=False, default=0)
    captured_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HermesMaintenanceState(Base):
    """Small idempotency ledger for background Hermes maintenance jobs."""

    __tablename__ = "hermes_maintenance_state"

    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False, unique=True)
    value_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProjectRuntimeSession(Base):
    """Durable view of Decisions-owned project runtime terminals."""

    __tablename__ = "project_runtime_sessions"

    id = Column(Integer, primary_key=True)
    terminal_id = Column(String, nullable=False, unique=True)
    project_id = Column(Integer, nullable=False)
    pid = Column(Integer, nullable=True)
    command = Column(Text, nullable=True)
    cwd = Column(Text, nullable=True)
    purpose = Column(String, nullable=False, default="startup")
    owner = Column(String, nullable=False, default="decisions_project_runtime")
    status = Column(String, nullable=False, default="running")
    urls = Column(Text, nullable=True)
    last_buffer_preview = Column(Text, nullable=True)
    safe_restart_policy = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    created_at_epoch = Column(Float, nullable=True)


class HermesValidationRecord(Base):
    """Durable validation evidence and correction hints for workflow learning."""

    __tablename__ = "hermes_validation_records"

    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("auto_workflow_runs.id"), nullable=True)
    step_id = Column(Integer, ForeignKey("auto_workflow_steps.id"), nullable=True)
    step_result_id = Column(Integer, ForeignKey("auto_workflow_step_results.id"), nullable=True)
    ticket_id = Column(Integer, ForeignKey("kanban_tickets.id"), nullable=True)
    board_id = Column(Integer, ForeignKey("kanban_boards.id"), nullable=True)
    project_id = Column(Integer, nullable=True)
    execution_session_id = Column(Integer, ForeignKey("project_execution_sessions.id"), nullable=True)

    validation_type = Column(String, nullable=False, default="none")
    expected = Column(Text, nullable=True)
    observed = Column(Text, nullable=True)
    standards_context = Column(Text, nullable=True)
    caller_passed = Column(String, nullable=True)
    verified_passed = Column(String, nullable=True)
    verdict = Column(String, nullable=False, default="unknown")
    correction_hint = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class HermesVisualBaselineSet(Base):
    """Named reference set of gold-standard UI screens for visual validation."""

    __tablename__ = "hermes_visual_baseline_sets"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    scope = Column(String, nullable=False, default="global")  # global | board | project
    scope_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    version = Column(String, nullable=False, default="v1")
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HermesVisualBaselineScreen(Base):
    """One reference screen inside a Hermes visual baseline set."""

    __tablename__ = "hermes_visual_baseline_screens"

    id = Column(Integer, primary_key=True)
    baseline_set_id = Column(Integer, ForeignKey("hermes_visual_baseline_sets.id"), nullable=False)
    screen_name = Column(String, nullable=False)
    screenshot_path = Column(Text, nullable=False)
    flow_name = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class HermesCorrectionAttempt(Base):
    """A bounded correction packet created after failed validation."""

    __tablename__ = "hermes_correction_attempts"

    id = Column(Integer, primary_key=True)
    validation_record_id = Column(Integer, ForeignKey("hermes_validation_records.id"), nullable=True)
    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("auto_workflow_runs.id"), nullable=True)
    step_id = Column(Integer, ForeignKey("auto_workflow_steps.id"), nullable=True)
    ticket_id = Column(Integer, ForeignKey("kanban_tickets.id"), nullable=True)
    board_id = Column(Integer, ForeignKey("kanban_boards.id"), nullable=True)
    project_id = Column(Integer, nullable=True)
    execution_session_id = Column(Integer, ForeignKey("project_execution_sessions.id"), nullable=True)

    status = Column(String, nullable=False, default="queued")
    attempt_number = Column(Integer, nullable=False, default=1)
    target_backend = Column(String, nullable=True)
    target_model = Column(String, nullable=True)
    correction_packet = Column(Text, nullable=True)
    dispatch_result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    dispatched_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


Index("ix_hermes_events_workflow_run", HermesEvent.workflow_id, HermesEvent.run_id)
Index("ix_hermes_events_ticket_id", HermesEvent.ticket_id)
Index("ix_hermes_events_execution_session_id", HermesEvent.execution_session_id)
Index("ix_hermes_events_created_at", HermesEvent.created_at)
Index("ix_hermes_events_type_status", HermesEvent.event_type, HermesEvent.status)
Index("ix_hermes_user_memories_hash", HermesUserMemory.content_hash)
Index("ix_hermes_user_memories_category", HermesUserMemory.category)
Index("ix_hermes_user_memories_scope", HermesUserMemory.scope, HermesUserMemory.scope_id)
Index("ix_hermes_user_memories_chat", HermesUserMemory.source_chat_id)
Index("ix_hermes_machine_activity_surface_seen", HermesMachineActivity.surface, HermesMachineActivity.last_seen_at)
Index("ix_hermes_machine_activity_hash", HermesMachineActivity.content_hash)
Index("ix_hermes_machine_activity_compacted", HermesMachineActivity.compacted, HermesMachineActivity.last_seen_at)
Index("ix_hermes_maintenance_state_key", HermesMaintenanceState.key)
Index("ix_project_runtime_sessions_project_status", ProjectRuntimeSession.project_id, ProjectRuntimeSession.status)
Index("ix_project_runtime_sessions_terminal_id", ProjectRuntimeSession.terminal_id)
Index("ix_hermes_validation_records_workflow_run", HermesValidationRecord.workflow_id, HermesValidationRecord.run_id)
Index("ix_hermes_validation_records_ticket_id", HermesValidationRecord.ticket_id)
Index("ix_hermes_validation_records_verdict", HermesValidationRecord.verdict)
Index("ix_hermes_visual_baseline_sets_scope", HermesVisualBaselineSet.scope, HermesVisualBaselineSet.scope_id)
Index("ix_hermes_visual_baseline_sets_name", HermesVisualBaselineSet.name)
Index("ix_hermes_visual_baseline_screens_set", HermesVisualBaselineScreen.baseline_set_id)
Index("ix_hermes_correction_attempts_validation", HermesCorrectionAttempt.validation_record_id)
Index("ix_hermes_correction_attempts_workflow_run", HermesCorrectionAttempt.workflow_id, HermesCorrectionAttempt.run_id)
Index("ix_hermes_correction_attempts_status", HermesCorrectionAttempt.status)


class HermesLearnedRule(Base):
    """Board/project-scoped rules captured from validation and IDE iteration."""

    __tablename__ = "hermes_learned_rules"

    id = Column(Integer, primary_key=True)
    scope = Column(String, nullable=False, default="board")  # global | board | project
    scope_id = Column(Integer, nullable=True)
    rule_type = Column(String, nullable=False, default="validation")
    summary = Column(Text, nullable=False, default="")
    payload = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.5)
    evidence_count = Column(Integer, nullable=False, default=1)
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


Index("ix_hermes_events_board_id", HermesEvent.board_id)
Index("ix_hermes_learned_rules_scope", HermesLearnedRule.scope, HermesLearnedRule.scope_id)
Index("ix_hermes_learned_rules_type", HermesLearnedRule.rule_type)
