"""Test Loop Service for Execute Code and Playwright step types.

Executes generated code in an isolated subprocess, captures results, and
auto-fixes failing code via the CodeGeneratorService up to MAX_FIX_ATTEMPTS
times.
"""

import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List

from distr.core.workflow_engine.code_generator import CodeGeneratorService
from distr.core.workflow_engine.step_types import StepType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """Raw result from a single subprocess execution."""
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class TestResult:
    """Aggregated result of the test-fix loop."""
    success: bool
    code: str
    attempts: List[Dict[str, Any]]
    output: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TestLoopService:
    """Execute code and auto-fix on failure using the coding LLM."""

    MAX_FIX_ATTEMPTS = 3

    def __init__(self, code_generator: CodeGeneratorService = None):
        self.code_generator = code_generator or CodeGeneratorService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_test(
        self,
        code: str,
        step_type: StepType,
        config: dict,
    ) -> TestResult:
        """Execute *code* and auto-fix up to :attr:`MAX_FIX_ATTEMPTS` times.

        Parameters
        ----------
        code:
            The Python source code to execute.
        step_type:
            ``StepType.EXECUTE_CODE`` or ``StepType.PLAYWRIGHT``.
        config:
            Step-specific configuration dict.  For Playwright steps the
            ``headless`` key controls browser mode (defaults to ``True``).

        Returns
        -------
        TestResult
            Contains the final code, success flag, all attempt logs, and
            the last execution output.
        """
        instruction = config.get("instruction", "")
        headless = config.get("headless", True)

        current_code = code
        attempts: List[Dict[str, Any]] = []
        exec_result: ExecutionResult | None = None

        for attempt_num in range(1, self.MAX_FIX_ATTEMPTS + 1):
            # --- execute ---------------------------------------------------
            if step_type == StepType.PLAYWRIGHT:
                exec_result = self._execute_playwright(
                    current_code, headless=headless
                )
            else:
                exec_result = self._execute_python(current_code)

            attempts.append({
                "attempt": attempt_num,
                "code": current_code,
                "exit_code": exec_result.exit_code,
                "stdout": exec_result.stdout,
                "stderr": exec_result.stderr,
            })

            # --- success? --------------------------------------------------
            if exec_result.exit_code == 0:
                return TestResult(
                    success=True,
                    code=current_code,
                    attempts=attempts,
                    output=exec_result.stdout,
                )

            # --- fix if more attempts remain -------------------------------
            if attempt_num < self.MAX_FIX_ATTEMPTS:
                error_context = (
                    f"stdout:\n{exec_result.stdout}\n"
                    f"stderr:\n{exec_result.stderr}"
                )
                try:
                    current_code = self.code_generator.fix_code(
                        code=current_code,
                        error=error_context,
                        instruction=instruction,
                        step_type=step_type,
                    )
                except RuntimeError as exc:
                    logger.error("Code fix LLM call failed: %s", exc)
                    # Cannot fix — break out and return failure
                    break

        # All attempts exhausted (or fix call failed)
        return TestResult(
            success=False,
            code=current_code,
            attempts=attempts,
            output=exec_result.stderr if exec_result else "",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_python(self, code: str, timeout: int = 60, cwd: str | None = None) -> ExecutionResult:
        """Run *code* as a Python script in an isolated subprocess.

        The code is written to a temporary file and executed with the
        current Python interpreter (``sys.executable``).  stdout and stderr
        are captured.  A *timeout* (seconds) is enforced.
        """
        return self._run_in_subprocess(code, timeout=timeout, env_extra=None, cwd=cwd)

    def _execute_playwright(
        self,
        code: str,
        headless: bool = True,
        timeout: int = 120,
        base_url: str | None = None,
    ) -> ExecutionResult:
        """Run Playwright *code* in a subprocess.

        If *headless* is ``True`` the ``PLAYWRIGHT_HEADLESS`` environment
        variable is set to ``"1"`` so that the script can honour it.  The
        default timeout is 120 s (longer than plain Python to allow for
        browser startup).
        """
        bridge_session = None
        try:
            from distr.core.browser_bridge import create_browser_artifact_session, write_browser_session_manifest

            bridge_session = create_browser_artifact_session(surface="workflow_playwright")
            write_browser_session_manifest(bridge_session, {"base_url": base_url or ""})
        except Exception:
            bridge_session = None

        env_extra = {"PLAYWRIGHT_HEADLESS": "1" if headless else "0"}
        if base_url:
            env_extra["PLAYWRIGHT_BASE_URL"] = str(base_url).strip()
        if bridge_session:
            env_extra["DECISIONS_BROWSER_SESSION_ID"] = bridge_session.session_id
            env_extra["PW_SCREENSHOT_DIR"] = bridge_session.artifact_dir
            env_extra["PW_CONSOLE_LOG"] = bridge_session.console_log_path

        # Inject a headless helper at the top of the code so scripts that
        # use ``launch(headless=...)`` can read the env var automatically.
        headless_preamble = (
            "import os as _os\n"
            "_HEADLESS = _os.environ.get('PLAYWRIGHT_HEADLESS', '1') == '1'\n"
            "BASE_URL = _os.environ.get('PLAYWRIGHT_BASE_URL', '')\n"
            "PW_SCREENSHOT_DIR = _os.environ.get('PW_SCREENSHOT_DIR', '')\n"
            "PW_CONSOLE_LOG = _os.environ.get('PW_CONSOLE_LOG', '')\n"
        )
        augmented_code = headless_preamble + code

        result = self._run_in_subprocess(
            augmented_code, timeout=timeout, env_extra=env_extra
        )
        if bridge_session:
            try:
                from pathlib import Path
                from distr.core.browser_bridge import record_browser_snapshot

                screenshots = sorted(
                    [
                        path
                        for path in Path(bridge_session.artifact_dir).iterdir()
                        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                    ],
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                record_browser_snapshot(
                    bridge_session,
                    status="completed" if result.exit_code == 0 else "failed",
                    summary="Workflow Playwright step captured browser state.",
                    screenshot_path=str(screenshots[0]) if screenshots else "",
                )
            except Exception:
                logger.debug("Could not record workflow Playwright browser snapshot", exc_info=True)
        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _run_in_subprocess(
        code: str,
        timeout: int,
        env_extra: dict | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Write *code* to a temp file and run it with ``sys.executable``."""
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="step_runner_")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(code)

            env = os.environ.copy()
            if env_extra:
                env.update(env_extra)

            try:
                proc = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                    cwd=cwd,
                )
                return ExecutionResult(
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                )
            except subprocess.TimeoutExpired:
                return ExecutionResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"Execution timed out after {timeout} seconds",
                )
            except Exception as exc:
                logger.error("Subprocess execution failed: %s", exc)
                return ExecutionResult(
                    exit_code=1,
                    stdout="",
                    stderr=str(exc),
                )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
