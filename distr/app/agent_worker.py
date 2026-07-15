"""
agent_worker.py - Agent subprocess entry point.

This module is intentionally kept free of PyQt6 GUI imports so that
multiprocessing spawn workers can import it without triggering Qt
initialisation in a headless subprocess.
"""

import os

# Agent subprocess: set before importing distr (which pulls sentence_transformers / HF stacks).
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

if os.name == "posix" and __import__("sys").platform == "darwin":
    os.environ.setdefault("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", "1")
    from distr.core.macos_background import hide_process_from_dock

    hide_process_from_dock()

from distr.core.rubicon_arm64_fix import apply_rubicon_arm64_fix

apply_rubicon_arm64_fix()

import gc
import sys
import time


def run_agent_session(settings, input_device=None, output_device=None,
                      command_queue=None, event_queue=None,
                      confirmation_results_dict=None, skip_welcome=False,
                      screen_info_cache=None, agent_current_chat_id=None):
    """Runs the agent session in a separate process with proper error handling."""
    import warnings
    import logging

    # Suppress macOS memory logging noise
    if sys.platform == 'darwin':
        os.environ.pop("MallocStackLogging", None)
        os.environ.pop("MallocStackLoggingDirectory", None)

    warnings.filterwarnings("ignore", message=r".*coroutine.*was never awaited", category=RuntimeWarning)
    warnings.filterwarnings("ignore", message=r".*CUDA is not available.*", category=UserWarning)
    warnings.filterwarnings("ignore", message=r".*FlashAttention.*", category=UserWarning)
    os.environ.setdefault("WHISPER_LOG_LEVEL", "3")

    # Set up logging (don't clear — main process already cleared at startup)
    from distr.app.worker_logging import setup_worker_logging
    setup_worker_logging()
    logger = logging.getLogger(__name__)

    # Initialise screen cache
    if screen_info_cache:
        from distr.core.screen_utils import init_screen_cache_manager
        init_screen_cache_manager(screen_info_cache)

    # Create a QCoreApplication (no GUI) so Qt signals work in this process
    from PyQt6.QtCore import QCoreApplication
    if not QCoreApplication.instance():
        QCoreApplication(sys.argv)

    import sounddevice as sd
    from distr.core.agent.session import AgentSession

    agent_session = None
    try:
        def _exc_handler(exc_type, exc_value, exc_tb):
            if exc_type is sd.PortAudioError and "PortAudio not initialized" in str(exc_value):
                return
            if exc_type is RuntimeError and "Event loop is closed" in str(exc_value):
                return
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        sys.excepthook = _exc_handler

        agent_session = AgentSession(
            input_device=input_device,
            output_device=output_device,
            settings=settings,
            command_queue=command_queue,
            event_queue=event_queue,
            confirmation_results_dict=confirmation_results_dict,
            skip_welcome=skip_welcome,
            agent_current_chat_id=agent_current_chat_id,
        )
        agent_session.start()
    except Exception as e:
        logger.error("Error in agent session: %s", e, exc_info=True)
    finally:
        if agent_session:
            try:
                agent_session.stop()
            except Exception as e:
                if "Event loop is closed" not in str(e) and "coroutine" not in str(e).lower():
                    logger.debug("Error stopping agent session: %s", e)
        logger.info("Agent session process exiting")
        try:
            time.sleep(0.5)
        except (RuntimeError, KeyboardInterrupt):
            pass
        gc.collect()
        os._exit(0)
