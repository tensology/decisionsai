"""Blocking VibeVoice-Realtime TTS inference (used by descriptor previews and VibeVoiceRealtimeTTSService).

Loads ``microsoft/VibeVoice-Realtime-0.5B`` (or ``DECISIONSAI_VIBEVOICE_MODEL``) once per process.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_processor: Any = None
_model: Any = None
_device_str: str = ""


def _pick_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _model_id() -> str:
    return (os.environ.get("DECISIONSAI_VIBEVOICE_MODEL") or "microsoft/VibeVoice-Realtime-0.5B").strip()


def get_streaming_model() -> Tuple[Any, Any, str]:
    """Return (processor, model, device_str) singleton."""
    global _processor, _model, _device_str
    with _lock:
        if _model is not None and _processor is not None:
            return _processor, _model, _device_str
        import torch
        from vibevoice.modular.modeling_vibevoice_streaming_inference import (
            VibeVoiceStreamingForConditionalGenerationInference,
        )
        from vibevoice.processor.vibevoice_streaming_processor import VibeVoiceStreamingProcessor

        mid = _model_id()
        device = _pick_device()
        logger.info("VibeVoice Realtime: loading %s on %s (one-time)...", mid, device)

        if device == "mps":
            load_dtype = torch.float32
            attn_impl_primary = "sdpa"
        elif device == "cuda":
            load_dtype = torch.bfloat16
            attn_impl_primary = "flash_attention_2"
        else:
            load_dtype = torch.float32
            attn_impl_primary = "sdpa"

        processor = VibeVoiceStreamingProcessor.from_pretrained(mid)
        try:
            if device == "mps":
                model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                    mid,
                    torch_dtype=load_dtype,
                    attn_implementation=attn_impl_primary,
                    device_map=None,
                )
                model.to("mps")
            elif device == "cuda":
                model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                    mid,
                    torch_dtype=load_dtype,
                    device_map="cuda",
                    attn_implementation=attn_impl_primary,
                )
            else:
                model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                    mid,
                    torch_dtype=load_dtype,
                    device_map="cpu",
                    attn_implementation=attn_impl_primary,
                )
        except Exception as first_err:
            if attn_impl_primary == "flash_attention_2":
                logger.warning("VibeVoice Realtime: flash_attention_2 load failed (%s); retrying sdpa", first_err)
                model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                    mid,
                    torch_dtype=load_dtype,
                    device_map=(device if device in ("cuda", "cpu") else None),
                    attn_implementation="sdpa",
                )
                if device == "mps":
                    model.to("mps")
            else:
                raise
        model.eval()
        model.set_ddpm_inference_steps(num_steps=5)
        _processor = processor
        _model = model
        _device_str = device
        logger.info("VibeVoice Realtime: loaded %s", mid)
        return _processor, _model, _device_str


def synthesize_streaming_wav(
    text: str,
    voice_pt_path: str,
    out_wav_path: str,
    *,
    cfg_scale: float = 1.5,
) -> str:
    """Synthesize *text* to *out_wav_path* using cached prompt at *voice_pt_path*. Returns out path."""
    import torch

    text = (text or "").strip()
    if not text:
        raise ValueError("VibeVoice Realtime: empty text")
    if not voice_pt_path or not os.path.isfile(voice_pt_path):
        raise FileNotFoundError(f"VibeVoice Realtime: voice preset missing: {voice_pt_path}")

    processor, model, device = get_streaming_model()
    target_device = device if device != "cpu" else "cpu"
    all_prefilled_outputs = torch.load(voice_pt_path, map_location=target_device, weights_only=False)

    inputs = processor.process_input_with_cached_prompt(
        text=text.replace("’", "'").replace("“", '"').replace("”", '"'),
        cached_prompt=all_prefilled_outputs,
        padding=True,
        return_tensors="pt",
        return_attention_mask=True,
    )
    for k, v in list(inputs.items()):
        if torch.is_tensor(v):
            inputs[k] = v.to(target_device)

    outputs = model.generate(
        **inputs,
        max_new_tokens=None,
        cfg_scale=float(cfg_scale),
        tokenizer=processor.tokenizer,
        generation_config={"do_sample": False},
        verbose=False,
        all_prefilled_outputs=copy.deepcopy(all_prefilled_outputs) if all_prefilled_outputs is not None else None,
    )
    if not outputs.speech_outputs or outputs.speech_outputs[0] is None:
        raise RuntimeError("VibeVoice Realtime: model returned no audio")
    _parent = os.path.dirname(os.path.abspath(out_wav_path))
    if _parent:
        os.makedirs(_parent, exist_ok=True)
    processor.save_audio(outputs.speech_outputs[0], output_path=out_wav_path)
    return out_wav_path
