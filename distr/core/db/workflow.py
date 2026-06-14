"""
Automation Workflow database models.

Workflow engine data model. An AutoWorkflow is a reusable, schedulable
sequence of steps. Each step IS a single action with validation and routing.

Named "Auto" to avoid conflict with the existing Workflow model (template/job card system).
"""
from sqlalchemy import Column, Index, Integer, String, Text, DateTime, ForeignKey, Boolean
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


# Indexes for high-frequency query patterns (must appear after class definitions)
Index('ix_autoworkflowrun_workflow_id', AutoWorkflowRun.workflow_id)
Index('ix_autoworkflowrun_ticket_id', AutoWorkflowRun.ticket_id)
Index('ix_autoworkflowrun_board_id', AutoWorkflowRun.board_id)
Index('ix_autoworkflowrun_status', AutoWorkflowRun.status)
