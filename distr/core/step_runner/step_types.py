"""Step type definitions and Pydantic configuration models for the Step Runner.

Defines the five supported step types (Run Command, Play Recording, HTTP Request,
Execute Code, Playwright) and their typed configuration schemas. The 'Set Variable'
type has been removed.
"""

from enum import Enum
from typing import Optional, Dict

from pydantic import BaseModel


class StepType(str, Enum):
    """Supported step types for the Step Runner."""
    RUN_COMMAND = "run_command"
    PLAY_RECORDING = "play_recording"
    HTTP_REQUEST = "http_request"
    EXECUTE_CODE = "execute_code"
    PLAYWRIGHT = "playwright"


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
