"""Request contracts retained by the Development HTTP API."""
from pydantic import BaseModel
from typing import Optional, List
class WorkflowCreateRequest(BaseModel):
    name: str = "Untitled Workflow"
    description: str = ""
    workflow_type: Optional[str] = None


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_preset: Optional[str] = None
    schedule_cron: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    schedule_timezone: Optional[str] = None
    start_step_position: Optional[int] = None
    workflow_type: Optional[str] = None
    context_rules: Optional[str] = None
    workflow_input: Optional[str] = None
    run_settings: Optional[dict] = None
    pre_chain: Optional[List[str]] = None
    post_chain: Optional[List[str]] = None


class WorkflowOrderRequest(BaseModel):
    workflow_ids: List[int]


class CodexBridgeEventRequest(BaseModel):
    event_type: str = "codex_event"
    status: Optional[str] = None
    message: str = ""
    input: str = ""
    output: str = ""
    execution_session_id: Optional[int] = None
    step_id: Optional[int] = None
    ticket_id: Optional[int] = None
    project_id: Optional[int] = None
    mistake_label: Optional[str] = None
    payload: Optional[dict] = None
    evidence: Optional[dict] = None


class UiFeedbackRequest(BaseModel):
    label: str
    reason: str = ""
    step_id: Optional[int] = None
    ticket_id: Optional[int] = None
    board_id: Optional[int] = None
    project_id: Optional[int] = None
    execution_session_id: Optional[int] = None
    screenshot_paths: Optional[List[str]] = None
    save_as_visual_baseline: bool = False
    visual_baseline_name: Optional[str] = None
    baseline_screen_name: Optional[str] = None


class VisualBaselineScreenRequest(BaseModel):
    screen_name: str
    screenshot_path: str
    flow_name: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[dict] = None


class VisualBaselineRequest(BaseModel):
    name: str
    screens: List[VisualBaselineScreenRequest]
    board_id: Optional[int] = None
    project_id: Optional[int] = None
    description: str = ""
    version: str = "v1"
    store_copy: bool = False


class StepCreateRequest(BaseModel):
    name: str = "New Step"
    action_type: str = "agent_instruction"
    position: Optional[int] = None
    instruction: str = ""
    config: Optional[dict] = None
    validation_type: str = "none"
    validation_prompt: str = ""
    wait_for_continue: bool = False


class LoopPresetApplyRequest(BaseModel):
    preset_name: str
    mode: str = "replace"


class LoopPresetSaveRequest(BaseModel):
    name: str


class StepHarnessSuggestRequest(BaseModel):
    instruction: str = ""
    action_type: str = ""
    archetype: str = ""
    loop_contract: Optional[dict] = None
    step_role: str = ""


class StepHarnessLlmSuggestRequest(BaseModel):
    instruction: str = ""
    guardrail: str = ""
    validation_prompt: str = ""
    loop_contract: Optional[dict] = None


class StepReorderRequest(BaseModel):
    step_ids: List[int]


class WorkflowGenerateRequest(BaseModel):
    description: str


class WorkflowPlanRequest(BaseModel):
    instruction: str
    chat_id: Optional[int] = None
    name: Optional[str] = None


class StudioTaskCreateRequest(BaseModel):
    prompt: str
    title: Optional[str] = None
    project_id: Optional[int] = None
    workflow_id: Optional[int] = None
    ticket_id: Optional[int] = None
    board_key: Optional[str] = None
    board_provider: Optional[str] = None
    board_ticket_key: Optional[str] = None
    board_ticket_title: Optional[str] = None
    board_ticket_lane: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    route_mode: str = "auto"
    execution_profile: str = "code"
    autonomy_level: str = "full"
    permission_mode: str = "standard"
    reasoning_effort: str = "medium"
    service_tier: str = "standard"
    routing_assessment: Optional[dict] = None
    attachments: Optional[List[dict]] = None
    skill_ids: Optional[List[str]] = None
    use_playwright: bool = False


class StudioRoutingAssessmentRequest(BaseModel):
    instruction: str
    ticket_title: str = ""
    ticket_description: str = ""
    has_images: bool = False
    recent_messages: Optional[List[str]] = None


class StudioThreadMessageRequest(BaseModel):
    message: str
    routing_assessment: Optional[dict] = None
    attachments: Optional[List[dict]] = None
    skill_ids: Optional[List[str]] = None
    use_playwright: bool = False


class StudioCommandRequest(BaseModel):
    content: str
    source: str = "web"
    source_ref: str = ""
    command_type: str = "instruction"
    metadata: Optional[dict] = None


class StudioCommandUpdateRequest(BaseModel):
    content: Optional[str] = None
    cancel: bool = False


class StudioThreadControlsRequest(BaseModel):
    pinned: Optional[bool] = None
    permission_profile: Optional[dict] = None
    remote_continuation: Optional[bool] = None
    shared_with_telegram: Optional[bool] = None
    project_id: Optional[int] = None
    workflow_id: Optional[int] = None
    autonomy_level: Optional[str] = None
    execution_profile: Optional[str] = None


class StudioModelRouteRequest(BaseModel):
    route_mode: str = "auto"
    provider: Optional[str] = None
    model_name: Optional[str] = None
    reasoning_effort: Optional[str] = None
    service_tier: Optional[str] = None


class StudioThreadUpdateRequest(BaseModel):
    title: str
    project_id: Optional[int] = None
    board_key: Optional[str] = None
    board_provider: Optional[str] = None
    ticket_id: Optional[int] = None
    board_ticket_key: Optional[str] = None
    board_ticket_title: Optional[str] = None
    board_ticket_lane: Optional[str] = None
    permission_profile: Optional[dict] = None
    remote_continuation: Optional[bool] = None


class StudioThreadArchiveRequest(BaseModel):
    archived: bool = True


class StudioThreadForkRequest(BaseModel):
    title: Optional[str] = None


class StudioTimeUpdateRequest(BaseModel):
    seconds: int = 0


class StudioSkillCaptureRequest(BaseModel):
    execution_session_id: int
    name: str = ""


class StudioInteractionResolveRequest(BaseModel):
    action: str
    response_text: str = ""


class StudioArtifactCreateRequest(BaseModel):
    artifact_type: str
    title: str
    workflow_id: Optional[int] = None
    run_id: Optional[int] = None
    summary: str = ""
    content: str = ""
    content_format: str = "markdown"
    uri: str = ""
    status: str = "planned"
    sort_order: int = 0
    metadata: Optional[dict] = None


class StudioArtifactUpdateRequest(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    content_format: Optional[str] = None
    uri: Optional[str] = None
    status: Optional[str] = None
    sort_order: Optional[int] = None
    run_id: Optional[int] = None
    metadata: Optional[dict] = None


class StudioPlanTransitionRequest(BaseModel):
    status: str


class PlanWorkspaceEnsureRequest(BaseModel):
    board_key: str
    board_provider: str = "decisions"
    board_name: str
    project_id: Optional[int] = None


class PlanItemCreateRequest(BaseModel):
    item_type: str
    title: str = ""
    content: str = ""
    content_format: str = ""


class PlanItemUpdateRequest(BaseModel):
    expected_revision: Optional[int] = None
    title: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None


class PlanInstructionRequest(BaseModel):
    instruction: str
    item_id: Optional[int] = None


class ProjectOpsPlanRequest(BaseModel):
    instruction: str
    board_id: Optional[int] = None


class ProjectOpsExecuteRequest(BaseModel):
    instruction: str
    route: str
    board_id: Optional[int] = None
    ticket_id: Optional[int] = None
    approved: bool = True


class WorkflowGenerateStepsRequest(BaseModel):
    instruction: str


class WorkflowTicketGroupRunRequest(BaseModel):
    ticket_ids: List[int]


class WorkflowGenerateCodeRequest(BaseModel):
    instruction: str
    step_type: str


class WorkflowTestCodeRequest(BaseModel):
    code: str
    step_type: str
    headless: bool = True


class WorkflowValidateStepRequest(BaseModel):
    step_type: str
    config: dict


class WorkflowScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    timezone: Optional[str] = None


class WorkflowSeedFixturesRequest(BaseModel):
    force_reset: bool = False
    workflow_names: Optional[List[str]] = None


class WorkflowPurgeAllRequest(BaseModel):
    confirm: bool = False
    include_audit: bool = False


class ScheduledActionRequest(BaseModel):
    title: str = "Scheduled action"
    schedule: dict
    action: dict
    target_context: Optional[dict] = None
    safety: Optional[dict] = None


class ScheduledActionUpdateRequest(BaseModel):
    title: Optional[str] = None
    enabled: Optional[bool] = None
    schedule: Optional[dict] = None
    action: Optional[dict] = None
    target_context: Optional[dict] = None
    safety: Optional[dict] = None


class ContextItemCreateRequest(BaseModel):
    title: str
    content: str = ""
    notes: str = ""


class ContextItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    notes: Optional[str] = None


class PlanFileReconcileRequest(BaseModel):
    expected_revision: int
    file_hash: str | None = None
    action: str
