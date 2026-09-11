"""
Automation Workflow database models.

Workflow engine data model. An AutoWorkflow is a reusable, schedulable
sequence of steps. Each step IS a single action with validation and routing.

Named "Auto" to avoid conflict with the existing Workflow model (template/job card system).
"""
from sqlalchemy import Column, Index, Integer, String, Text, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from . import Base
from .time import utc_now_naive


class AutoWorkflow(Base):
    """A workflow definition — a named, reusable sequence of steps."""
    __tablename__ = 'auto_workflows'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, default='Untitled Workflow')
    description = Column(Text, nullable=True)
    status = Column(String, default='draft')  # draft, active, paused, archived

    # Workflow type (replaces StepRunnerSession.session_type)
    workflow_type = Column(String, default='manual')  # manual, instruction, scheduled, audit, retro, review, deploy
    # Chat link (replaces StepRunnerSession.chat_id)
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=True)
    # Context rules (replaces StepRunnerSession.context_rules)
    context_rules = Column(Text, nullable=True)
    # Workflow input (replaces StepRunnerSession.workflow_input)
    workflow_input = Column(Text, nullable=True)
    # JSON settings for queued ticket execution: sequencing, branching, concurrency.
    run_settings = Column(Text, nullable=True)

    # Safety mode — auto-activated when workflow starts
    safety_mode = Column(String, nullable=True)  # null (off), careful, freeze, guard
    safety_frozen_scope = Column(String, nullable=True)  # directory path for freeze/guard

    # Skill chaining — pre/post execution skills
    pre_chain = Column(Text, nullable=True)  # JSON: ["ceo-scope-review", "pre-flight-review"]
    post_chain = Column(Text, nullable=True)  # JSON: ["session-retro"]

    # Structured verification — replaces free-text verification field
    verification_template = Column(String, nullable=True)  # named template: "web_app", "api", "cli", "security"

    # Scheduling
    schedule_enabled = Column(Boolean, default=False)
    schedule_preset = Column(String, nullable=True)  # hourly, daily, weekly, custom
    schedule_cron = Column(String, nullable=True)
    schedule_time = Column(String, nullable=True)  # HH:MM
    schedule_days = Column(String, nullable=True)  # comma-separated weekday numbers
    schedule_timezone = Column(String, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)

    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    start_step_position = Column(Integer, default=0)

    steps = relationship(
        "AutoWorkflowStep", back_populates="workflow",
        cascade="all, delete-orphan", order_by="AutoWorkflowStep.position"
    )
    variables = relationship(
        "AutoWorkflowVariable", back_populates="workflow",
        cascade="all, delete-orphan",
    )
    runs = relationship(
        "AutoWorkflowRun", back_populates="workflow",
        cascade="all, delete-orphan", order_by="AutoWorkflowRun.started_at.desc()"
    )


class AutoWorkflowStep(Base):
    """A step within a workflow — one action with validation and routing."""
    __tablename__ = 'auto_workflow_steps'

    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey('auto_workflows.id'), nullable=False)
    position = Column(Integer, default=0)
    name = Column(String, nullable=False, default='New Step')
    description = Column(Text, nullable=True)

    # The action itself (one action per step)
    action_type = Column(String, default='agent_instruction')  # agent_instruction, run_command, set_variable, http_request
    instruction = Column(Text, nullable=True)  # The main instruction / action config

    # Step type for typed execution (replaces StepRunnerStep.step_type)
    step_type = Column(String, default='agent_instruction')
    # Type-specific config JSON (replaces StepRunnerStep.config)
    config = Column(Text, nullable=True)
    # Verification criteria (replaces StepRunnerStep.verification)
    verification = Column(Text, nullable=True)
    # Tool name from agent execution (replaces StepRunnerStep.tool_used)
    tool_used = Column(String, nullable=True)
    # Routing telemetry for audit steps
    routing_path = Column(Text, nullable=True)

    # Validation
    validation_type = Column(String, default='none')  # none, text_match, screenshot_compare, llm_judgment, rule_based
    validation_prompt = Column(Text, nullable=True)  # What passes validation (instruction for the validator)
    screenshot_path = Column(String, nullable=True)  # Path to reference screenshot for screenshot_compare

    # Recording (shared with Actions infrastructure)
    recording_filename = Column(String, nullable=True)
    action_id = Column(Integer, ForeignKey('actions.id'), nullable=True)  # Linked Action entity

    linked_action = relationship("Action", foreign_keys=[action_id])

    # Routing: null=end (default), -1=end (explicit), N=go to step id N
    routing_mode = Column(String, default='static')  # static | agent_decision
    routing_prompt = Column(Text, nullable=True)  # Instructions for agent when routing_mode=agent_decision
    on_pass_goto = Column(Integer, nullable=True)
    on_fail_goto = Column(Integer, nullable=True)
    wait_before_next = Column(Integer, default=0)  # seconds to wait before moving to next step

    # Code storage for execute_code/playwright steps
    code = Column(Text, nullable=True)  # Generated/edited code for execute_code/playwright
    validation_code = Column(Text, nullable=True)  # Playwright validation script
    linked_project_id = Column(Integer, nullable=True)  # Optional project link for context
    wait_for_continue = Column(Boolean, default=False)  # When True, step enters 'waiting' after action completes

    # Execution controls
    max_retries = Column(Integer, default=0)
    timeout_seconds = Column(Integer, default=300)
    require_approval = Column(Boolean, default=False)

    # Runtime state
    status = Column(String, default='pending')  # pending, running, passed, failed, cancelled, skipped, waiting
    result = Column(Text, nullable=True)  # LLM response / execution result
    created_date = Column(DateTime, default=utc_now_naive)
    modified_date = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    workflow = relationship("AutoWorkflow", back_populates="steps")


class AutoWorkflowVariable(Base):
    """A variable scoped to a workflow, persists across steps within a run."""
    __tablename__ = 'auto_workflow_variables'

    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey('auto_workflows.id'), nullable=False)
    name = Column(String, nullable=False)
    default_value = Column(Text, nullable=True, default='')
    description = Column(Text, nullable=True)

    workflow = relationship("AutoWorkflow", back_populates="variables")


class AutoWorkflowStepResult(Base):
    """History of step execution results — one row per step execution."""
    __tablename__ = 'auto_workflow_step_results'

    id = Column(Integer, primary_key=True)
    step_id = Column(Integer, ForeignKey('auto_workflow_steps.id'), nullable=False)
    run_id = Column(Integer, ForeignKey('auto_workflow_runs.id'), nullable=True)
    agent_response = Column(Text, nullable=True)
    status = Column(String, default='pending')  # pending, passed, failed, cancelled
    created_at = Column(DateTime, default=utc_now_naive)

    step = relationship("AutoWorkflowStep", backref="results")
    run = relationship("AutoWorkflowRun", backref="step_result_records")


class AutoWorkflowRun(Base):
    """Record of a workflow execution."""
    __tablename__ = 'auto_workflow_runs'

    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey('auto_workflows.id'), nullable=False)
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=True)
    board_id = Column(Integer, ForeignKey('kanban_boards.id'), nullable=True)
    ticket_id = Column(Integer, ForeignKey('kanban_tickets.id'), nullable=True)
    parent_run_id = Column(Integer, ForeignKey('auto_workflow_runs.id'), nullable=True)  # Subagent hierarchy
    started_at = Column(DateTime, default=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default='running')  # running, completed, failed, cancelled, waiting
    current_step_id = Column(Integer, nullable=True)  # Which step is currently executing
    run_data = Column(Text, nullable=True)  # JSON: step results
    step_results = Column(Text, nullable=True)  # JSON: [{step_id, status, result}, ...]
    variable_values = Column(Text, nullable=True)  # JSON: variable values at end

    workflow = relationship("AutoWorkflow", back_populates="runs")


Index('ix_auto_workflow_runs_chat_status', AutoWorkflowRun.chat_id, AutoWorkflowRun.status)


class StudioArtifact(Base):
    """A durable visual or structured planning artifact attached to a Studio task."""

    __tablename__ = "studio_artifacts"

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("auto_workflow_runs.id"), nullable=True)
    artifact_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    content_format = Column(String, nullable=False, default="markdown")
    uri = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="planned")
    sort_order = Column(Integer, nullable=False, default=0)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    modified_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)


class DevelopmentPlanRevision(Base):
    """Immutable per-thread plan snapshot derived from a reusable workflow."""

    __tablename__ = "development_plan_revisions"
    __table_args__ = (
        UniqueConstraint("chat_id", "revision", name="uq_development_plan_chat_revision"),
    )

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=False)
    revision = Column(Integer, nullable=False)
    mode = Column(String, nullable=False, default="develop")
    status = Column(String, nullable=False, default="draft")
    instruction = Column(Text, nullable=False, default="")
    snapshot_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


Index(
    "ix_development_plan_revisions_chat_status",
    DevelopmentPlanRevision.chat_id,
    DevelopmentPlanRevision.status,
)


class DevelopmentCommand(Base):
    """A durable, ordered instruction sent to a Development thread.

    Commands are intentionally separate from chat messages. They can arrive
    from the browser, Telegram, or another approved control surface and remain
    visible until the workflow harness acknowledges or cancels them.
    """

    __tablename__ = "development_commands"

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=True)
    run_id = Column(Integer, ForeignKey("auto_workflow_runs.id"), nullable=True)
    source = Column(String, nullable=False, default="web")
    source_ref = Column(String, nullable=True)
    command_type = Column(String, nullable=False, default="instruction")
    content = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="queued")
    position = Column(Integer, nullable=False, default=0)
    result_summary = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    modified_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)


class DevelopmentWorkItem(Base):
    """Authoritative board/ticket identity for one Development thread.

    ``Chat.params.development`` remains a compatibility projection while the
    Development UI and older integrations are migrated. Identity and reuse
    decisions must use this row instead of inferring ownership from a project
    or workflow id.
    """

    __tablename__ = "development_work_items"
    __table_args__ = (
        UniqueConstraint("chat_id", name="uq_development_work_items_chat"),
        UniqueConstraint("identity_key", name="uq_development_work_items_identity"),
    )

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    identity_key = Column(String, nullable=False)
    source_type = Column(String, nullable=False, default="prompt")
    board_provider = Column(String, nullable=True)
    board_key = Column(String, nullable=True)
    ticket_key = Column(String, nullable=True)
    local_ticket_id = Column(Integer, ForeignKey("kanban_tickets.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    workflow_id = Column(Integer, ForeignKey("auto_workflows.id"), nullable=True)
    ticket_title = Column(Text, nullable=True)
    ticket_lane = Column(String, nullable=True)
    time_accumulated_seconds = Column(Integer, nullable=False, default=0)
    time_started_at = Column(DateTime, nullable=True)
    time_last_activity_at = Column(DateTime, nullable=True)
    time_paused = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    modified_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)


class PlanWorkspace(Base):
    """One durable planning workspace for a Development board."""

    __tablename__ = "plan_workspaces"
    __table_args__ = (
        UniqueConstraint("board_key", name="uq_plan_workspaces_board_key"),
    )

    id = Column(Integer, primary_key=True)
    board_key = Column(String, nullable=False)
    board_provider = Column(String, nullable=False, default="decisions")
    board_name = Column(String, nullable=False, default="Untitled board")
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    root_path = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="draft")
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    modified_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)


class PlanItem(Base):
    """A board-scoped source document, diagram, design, or delivery item."""

    __tablename__ = "plan_items"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("plan_workspaces.id"), nullable=False)
    item_type = Column(String, nullable=False, default="document")
    title = Column(String, nullable=False, default="Untitled")
    content = Column(Text, nullable=False, default="")
    content_format = Column(String, nullable=False, default="markdown")
    status = Column(String, nullable=False, default="draft")
    sort_order = Column(Integer, nullable=False, default=0)
    file_path = Column(Text, nullable=True)
    content_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    modified_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)


class PlanItemRevision(Base):
    """Immutable history for a planning item."""

    __tablename__ = "plan_item_revisions"
    __table_args__ = (
        UniqueConstraint("item_id", "revision", name="uq_plan_item_revision"),
    )

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("plan_items.id"), nullable=False)
    revision = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False, default="draft")
    source = Column(String, nullable=False, default="user")
    instruction = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)


class PlanFileWrite(Base):
    """A file projection to finish after its item revision commits."""
    __tablename__ = "plan_file_writes"
    item_id = Column(Integer, ForeignKey("plan_items.id", ondelete="CASCADE"), primary_key=True)
    target_path = Column(Text, nullable=False)
    previous_hash = Column(String, nullable=True)
    desired_hash = Column(String, nullable=False)


# Indexes for high-frequency query patterns (must appear after class definitions)
Index('ix_autoworkflowrun_workflow_id', AutoWorkflowRun.workflow_id)
Index('ix_autoworkflowrun_ticket_id', AutoWorkflowRun.ticket_id)
Index('ix_autoworkflowrun_board_id', AutoWorkflowRun.board_id)
Index('ix_autoworkflowrun_status', AutoWorkflowRun.status)
Index('ix_studio_artifacts_chat_order', StudioArtifact.chat_id, StudioArtifact.sort_order)
Index('ix_studio_artifacts_workflow', StudioArtifact.workflow_id)
Index('ix_studio_artifacts_run', StudioArtifact.run_id)
Index('ix_development_commands_chat_position', DevelopmentCommand.chat_id, DevelopmentCommand.position)
Index('ix_development_commands_status', DevelopmentCommand.status)
Index('ix_development_commands_run', DevelopmentCommand.run_id)
Index('ix_development_work_items_board', DevelopmentWorkItem.board_provider, DevelopmentWorkItem.board_key)
Index('ix_development_work_items_ticket', DevelopmentWorkItem.local_ticket_id)
Index('ix_development_work_items_project', DevelopmentWorkItem.project_id)
Index('ix_plan_workspaces_project', PlanWorkspace.project_id)
Index('ix_plan_items_workspace_order', PlanItem.workspace_id, PlanItem.sort_order)
Index('ix_plan_item_revisions_item', PlanItemRevision.item_id, PlanItemRevision.revision)
