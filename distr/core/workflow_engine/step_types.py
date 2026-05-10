"""Step type definitions and Pydantic configuration models for workflows.

Defines the supported step types (Run Command, Play Recording, HTTP Request,
Execute Code, Playwright, Computer Use, Send to Project CLI) and their typed
configuration schemas. The 'Set Variable' type has been removed.
"""

from enum import Enum
from typing import Optional, Dict

from pydantic import BaseModel


class StepType(str, Enum):
    """Supported step types for workflows."""
    RUN_COMMAND = "run_command"
    PLAY_RECORDING = "play_recording"
    HTTP_REQUEST = "http_request"
    EXECUTE_CODE = "execute_code"
    PLAYWRIGHT = "playwright"
    SEND_TO_PROJECT_CLI = "send_to_project_cli"
    COMPUTER_USE = "computer_use"


class RunCommandConfig(BaseModel):
    """Configuration for a Run Command step."""
    command: str = ""
    working_directory: Optional[str] = None
    timeout_seconds: int = 60


class PlayRecordingConfig(BaseModel):
    """Configuration for a Play Recording step."""
    recording_id: Optional[int] = None
    recording_name: Optional[str] = None


class HttpMethod(str, Enum):
    """Supported HTTP methods for HTTP Request steps."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class HttpRequestConfig(BaseModel):
    """Configuration for an HTTP Request step."""
    url: str = ""
    method: HttpMethod = HttpMethod.GET
    headers: Dict[str, str] = {}
    body: Optional[str] = None
    variables: Dict[str, str] = {}
    timeout_seconds: int = 30


class ExecuteCodeConfig(BaseModel):
    """Configuration for an Execute Code step."""
    instruction: str = ""
    code: str = ""
    language: str = "python"


class PlaywrightConfig(BaseModel):
    """Configuration for a Playwright step."""
    instruction: str = ""
    code: str = ""
    headless: bool = True


class ComputerUseConfig(BaseModel):
    """Configuration for a Computer Use step.

    This step type owns its own vision-action loop. It captures a screenshot,
    asks a vision model what action to take next, executes it via the sidecar,
    then repeats — without burning orchestration LLM tokens on micro-decisions.

    The orchestration model is only called when the loop escalates (stuck_threshold
    consecutive failed observations, or the vision model explicitly asks for help).
    """
    goal: str = ""                    # Natural language goal for this step
    max_iterations: int = 15          # Hard cap on vision-action cycles
    stuck_threshold: int = 3          # Consecutive "no progress" turns before escalating
    escalate_on_ambiguity: bool = True # Call orchestration model when stuck
    screenshot_resize_width: int = 1280  # Max width before sending to vision model
