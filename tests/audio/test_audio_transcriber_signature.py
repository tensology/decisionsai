"""Signature checks for AudioTranscriberTool (skip if optional media deps missing)."""

from __future__ import annotations

import inspect

import pytest

try:
    from distr.core.agent.tools.media.audio_transcriber import AudioTranscriberTool
except ImportError as e:
    pytest.skip(f"AudioTranscriberTool unavailable: {e}", allow_module_level=True)


def test_run_signature_accepts_last_user_message_and_var_kw() -> None:
    sig_run = inspect.signature(AudioTranscriberTool._run)
    assert "last_user_message" in sig_run.parameters
    assert any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_run.parameters.values()
    )


def test_arun_signature_accepts_last_user_message_and_var_kw() -> None:
    sig_arun = inspect.signature(AudioTranscriberTool._arun)
    assert "last_user_message" in sig_arun.parameters
    assert any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_arun.parameters.values()
    )
