"""Step configuration validation service.

Validates step configuration against type-specific rules before execution,
returning structured errors with field names and human-readable messages.
"""

from dataclasses import dataclass
from typing import List

from distr.core.workflow_engine.step_types import StepType


@dataclass
class ValidationError:
    """A structured validation error with the offending field and a message."""
    field: str
    message: str


# Valid HTTP methods for HTTP Request validation
_VALID_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class StepValidator:
    """Validates step configuration before execution."""

    def validate(self, step_type: str, config: dict) -> List[ValidationError]:
        """Validate step config for the given step type.

        Returns an empty list if the configuration is valid.
        """
        validators = {
            StepType.RUN_COMMAND.value: self.validate_run_command,
            StepType.PLAY_RECORDING.value: self.validate_play_recording,
            StepType.HTTP_REQUEST.value: self.validate_http_request,
            StepType.EXECUTE_CODE.value: self.validate_execute_code,
            StepType.PLAYWRIGHT.value: self.validate_playwright,
            StepType.SEND_TO_PROJECT_CLI.value: self.validate_send_to_project_cli,
            StepType.COMPUTER_USE.value: self.validate_computer_use,
        }

        validator = validators.get(step_type)
        if validator is None:
            return [ValidationError(
                field="step_type",
                message=f"Unknown step type: {step_type}",
            )]

        return validator(config)

    def validate_run_command(self, config: dict) -> List[ValidationError]:
        """Require a non-empty command string."""
        errors: List[ValidationError] = []
        command = config.get("command", "")
        if not command or not command.strip():
            errors.append(ValidationError(
                field="command",
                message="Command is required and cannot be empty",
            ))
        return errors

    def validate_play_recording(self, config: dict) -> List[ValidationError]:
        """Require either a recording_id or a recording_name."""
        errors: List[ValidationError] = []
        recording_id = config.get("recording_id")
        recording_name = config.get("recording_name")

        has_id = recording_id is not None
        has_name = recording_name is not None and str(recording_name).strip() != ""

        if not has_id and not has_name:
            errors.append(ValidationError(
                field="recording",
                message="Either a recording ID or recording name is required",
            ))
        return errors

    def validate_http_request(self, config: dict) -> List[ValidationError]:
        """Require a valid URL (non-empty, starts with http:// or https://)
        and a valid HTTP method if provided."""
        errors: List[ValidationError] = []

        url = config.get("url", "")
        if not url or not url.strip():
            errors.append(ValidationError(
                field="url",
                message="URL is required and cannot be empty",
            ))
        elif not url.strip().startswith(("http://", "https://")):
            errors.append(ValidationError(
                field="url",
                message='URL must start with "http://" or "https://"',
            ))

        method = config.get("method")
        if method is not None:
            method_upper = str(method).upper()
            if method_upper not in _VALID_HTTP_METHODS:
                errors.append(ValidationError(
                    field="method",
                    message=f"Invalid HTTP method: {method}. Must be one of {', '.join(sorted(_VALID_HTTP_METHODS))}",
                ))

        return errors

    def validate_execute_code(self, config: dict) -> List[ValidationError]:
        """Require either a non-empty instruction or non-empty code."""
        errors: List[ValidationError] = []
        instruction = config.get("instruction", "")
        code = config.get("code", "")

        has_instruction = bool(instruction and instruction.strip())
        has_code = bool(code and code.strip())

        if not has_instruction and not has_code:
            errors.append(ValidationError(
                field="instruction",
                message="Either an instruction or code is required",
            ))
        return errors

    def validate_playwright(self, config: dict) -> List[ValidationError]:
        """Require either a non-empty instruction or non-empty code."""
        errors: List[ValidationError] = []
        instruction = config.get("instruction", "")
        code = config.get("code", "")

        has_instruction = bool(instruction and instruction.strip())
        has_code = bool(code and code.strip())

        if not has_instruction and not has_code:
            errors.append(ValidationError(
                field="instruction",
                message="Either an instruction or Playwright code is required",
            ))
        return errors

    def validate_send_to_project_cli(self, config: dict) -> List[ValidationError]:
        """Require a non-empty instruction to send to project CLI."""
        errors: List[ValidationError] = []
        instruction = config.get("instruction", "")
        if not instruction or not str(instruction).strip():
            errors.append(ValidationError(
                field="instruction",
                message="Instruction is required for Send to Project CLI",
            ))
        return errors

    def validate_computer_use(self, config: dict) -> List[ValidationError]:
        """Require a non-empty goal/instruction and sane loop limits."""
        errors: List[ValidationError] = []
        goal = config.get("goal") or config.get("instruction") or ""
        if not str(goal).strip():
            errors.append(ValidationError(
                field="goal",
                message="Goal or instruction is required for Computer Use",
            ))

        for field, min_value, max_value in (
            ("max_iterations", 1, 25),
            ("stuck_threshold", 1, 10),
            ("screenshot_resize_width", 320, 4096),
        ):
            if field not in config or config.get(field) in (None, ""):
                continue
            try:
                value = int(config.get(field))
            except (TypeError, ValueError):
                errors.append(ValidationError(
                    field=field,
                    message=f"{field} must be an integer",
                ))
                continue
            if value < min_value or value > max_value:
                errors.append(ValidationError(
                    field=field,
                    message=f"{field} must be between {min_value} and {max_value}",
                ))
        return errors
