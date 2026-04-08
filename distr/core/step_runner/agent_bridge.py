"""Workflow Agent Bridge — separates workflow execution from the Voice Agent.

Handles workflow completion notifications, queues run history reports to the
agent LLM via a thread-safe queue, and emits the ``workflow_finished`` signal
so the Voice Agent can react.
"""

import logging
import queue
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Module-level thread-safe queue for agent reports.
# Reports are drained by the agent when it is ready to process them.
_agent_report_queue: queue.Queue = queue.Queue()


class WorkflowAgentBridge:
    """Bridge between the Step Runner workflow engine and the Voice Agent."""

    def on_workflow_completed(self, session_id: int, run_result: dict) -> None:
        """Finalize a workflow run and notify the Voice Agent.

        Generates a human-readable summary from *run_result*, queues it for
        the agent LLM, and emits the ``workflow_finished`` signal.
        """
        try:
            report = self._generate_report(run_result)
            self.queue_report_to_agent(session_id, report)
            self.notify_voice_agent(session_id, report)
            logger.info(
                "WorkflowAgentBridge: completed notification for session %d",
                session_id,
            )
        except Exception as e:
            logger.error(
                "WorkflowAgentBridge: on_workflow_completed failed for session %d: %s",
                session_id,
                e,
                exc_info=True,
            )

    def queue_report_to_agent(self, session_id: int, report: str) -> None:
        """Put *report* on the thread-safe agent report queue."""
        _agent_report_queue.put({"session_id": session_id, "report": report})
        logger.debug(
            "WorkflowAgentBridge: queued report for session %d (queue size: %d)",
            session_id,
            _agent_report_queue.qsize(),
        )

    def notify_voice_agent(self, session_id: int, summary: str) -> None:
        """Emit ``workflow_finished`` signal for the Voice Agent."""
        from distr.core.signals import signal_manager

        signal_manager.workflow_finished.emit(session_id, summary)
        logger.debug(
            "WorkflowAgentBridge: emitted workflow_finished for session %d",
            session_id,
        )

    @staticmethod
    def get_pending_reports() -> List[Dict[str, Any]]:
        """Drain the queue and return all pending reports."""
        reports: List[Dict[str, Any]] = []
        while True:
            try:
                reports.append(_agent_report_queue.get_nowait())
            except queue.Empty:
                break
        return reports

    @staticmethod
    def _generate_report(run_result: dict) -> str:
        """Generate a human-readable summary from *run_result*."""
        session_id = run_result.get("session_id", "?")
        run_id = run_result.get("run_id", "?")
        success = run_result.get("success", False)
        cancelled = run_result.get("cancelled", False)

        steps_summary = run_result.get("steps_summary", [])
        total = len(steps_summary)

        if cancelled:
            status_label = "Cancelled"
        elif success:
            status_label = "Completed successfully"
        else:
            status_label = "Failed"

        step_lines = []
        for i, s in enumerate(steps_summary, 1):
            title = s.get("title") or f"Step {i}"
            result = (s.get("result") or "").strip()
            status = s.get("status", "")
            status_tag = f" [{status}]" if status and status not in ("completed", "passed") else ""
            if result:
                # Truncate long results for readability
                short = result[:200] + ("..." if len(result) > 200 else "")
                step_lines.append(f"  {i}. {title}{status_tag}: {short}")
            else:
                step_lines.append(f"  {i}. {title}{status_tag}")

        steps_block = "\n".join(step_lines) if step_lines else "  (no steps)"

        if total == 1:
            speak_instruction = "Give a brief spoken response about what this single step did."
        else:
            speak_instruction = "Give a brief spoken overview of the entire workflow run — what was accomplished across all steps."

        return (
            f"Workflow run {status_label} "
            f"(session {session_id}, run {run_id})\n"
            f"Steps ({total}):\n{steps_block}\n\n"
            f"[Instruction: {speak_instruction}]"
        )
