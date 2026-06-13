"""Orchestrator ledger SQLAlchemy models (orchestrator_* tables)."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text

from . import Base


class OrchestratorEvent(Base):
    """Canonical event across tickets, workflows, executors, and channels."""

    __tablename__ = "orchestrator_events"

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
    parent_event_id = Column(Integer, ForeignKey("orchestrator_events.id"), nullable=True)

    summary = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OrchestratorUserMemory(Base):
    """Durable user preference/style memory extracted from conversations."""

    __tablename__ = "orchestrator_user_memories"

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


class OrchestratorMachineActivity(Base):
    """Quiet local activity samples that help Hermes infer active work context."""

    __tablename__ = "orchestrator_machine_activity"

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


class OrchestratorMaintenanceState(Base):
    """Small idempotency ledger for background Hermes maintenance jobs."""

    __tablename__ = "orchestrator_maintenance_state"

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


class OrchestratorValidationRecord(Base):
    """Durable validation evidence and correction hints for workflow learning."""

    __tablename__ = "orchestrator_validation_records"

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


class OrchestratorVisualBaselineSet(Base):
    """Named reference set of gold-standard UI screens for visual validation."""

    __tablename__ = "orchestrator_visual_baseline_sets"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    scope = Column(String, nullable=False, default="global")  # global | board | project
    scope_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    version = Column(String, nullable=False, default="v1")
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrchestratorVisualBaselineScreen(Base):
    """One reference screen inside a Hermes visual baseline set."""

    __tablename__ = "orchestrator_visual_baseline_screens"

    id = Column(Integer, primary_key=True)
    baseline_set_id = Column(Integer, ForeignKey("orchestrator_visual_baseline_sets.id"), nullable=False)
    screen_name = Column(String, nullable=False)
    screenshot_path = Column(Text, nullable=False)
    flow_name = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OrchestratorCorrectionAttempt(Base):
    """A bounded correction packet created after failed validation."""

    __tablename__ = "orchestrator_correction_attempts"

    id = Column(Integer, primary_key=True)
    validation_record_id = Column(Integer, ForeignKey("orchestrator_validation_records.id"), nullable=True)
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


Index("ix_orchestrator_events_workflow_run", OrchestratorEvent.workflow_id, OrchestratorEvent.run_id)
Index("ix_orchestrator_events_ticket_id", OrchestratorEvent.ticket_id)
Index("ix_orchestrator_events_execution_session_id", OrchestratorEvent.execution_session_id)
Index("ix_orchestrator_events_created_at", OrchestratorEvent.created_at)
Index("ix_orchestrator_events_type_status", OrchestratorEvent.event_type, OrchestratorEvent.status)
Index("ix_orchestrator_user_memories_hash", OrchestratorUserMemory.content_hash)
Index("ix_orchestrator_user_memories_category", OrchestratorUserMemory.category)
Index("ix_orchestrator_user_memories_scope", OrchestratorUserMemory.scope, OrchestratorUserMemory.scope_id)
Index("ix_orchestrator_user_memories_chat", OrchestratorUserMemory.source_chat_id)
Index("ix_orchestrator_machine_activity_surface_seen", OrchestratorMachineActivity.surface, OrchestratorMachineActivity.last_seen_at)
Index("ix_orchestrator_machine_activity_hash", OrchestratorMachineActivity.content_hash)
Index("ix_orchestrator_machine_activity_compacted", OrchestratorMachineActivity.compacted, OrchestratorMachineActivity.last_seen_at)
Index("ix_orchestrator_maintenance_state_key", OrchestratorMaintenanceState.key)
Index("ix_project_runtime_sessions_project_status", ProjectRuntimeSession.project_id, ProjectRuntimeSession.status)
Index("ix_project_runtime_sessions_terminal_id", ProjectRuntimeSession.terminal_id)
Index("ix_orchestrator_validation_records_workflow_run", OrchestratorValidationRecord.workflow_id, OrchestratorValidationRecord.run_id)
Index("ix_orchestrator_validation_records_ticket_id", OrchestratorValidationRecord.ticket_id)
Index("ix_orchestrator_validation_records_verdict", OrchestratorValidationRecord.verdict)
Index("ix_orchestrator_visual_baseline_sets_scope", OrchestratorVisualBaselineSet.scope, OrchestratorVisualBaselineSet.scope_id)
Index("ix_orchestrator_visual_baseline_sets_name", OrchestratorVisualBaselineSet.name)
Index("ix_orchestrator_visual_baseline_screens_set", OrchestratorVisualBaselineScreen.baseline_set_id)
Index("ix_orchestrator_correction_attempts_validation", OrchestratorCorrectionAttempt.validation_record_id)
Index("ix_orchestrator_correction_attempts_workflow_run", OrchestratorCorrectionAttempt.workflow_id, OrchestratorCorrectionAttempt.run_id)
Index("ix_orchestrator_correction_attempts_status", OrchestratorCorrectionAttempt.status)


class OrchestratorLearnedRule(Base):
    """Orchestrator learned rules — board/project memory from validation and IDE feedback.

    Not Nous Hermes Agent memory (``~/.hermes``). Consumed by routing via
    ``build_learned_rules_context()``.
    """

    __tablename__ = "orchestrator_learned_rules"

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


Index("ix_orchestrator_events_board_id", OrchestratorEvent.board_id)
Index("ix_orchestrator_learned_rules_scope", OrchestratorLearnedRule.scope, OrchestratorLearnedRule.scope_id)
Index("ix_orchestrator_learned_rules_type", OrchestratorLearnedRule.rule_type)
