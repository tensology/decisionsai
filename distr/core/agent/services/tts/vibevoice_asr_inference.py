"""Lazy singleton for Microsoft VibeVoice-ASR (local, long-form capable).

Uses the same ``vibevoice`` package as Realtime TTS. First run downloads weights
(Hugging Face). See https://github.com/microsoft/VibeVoice and ``vibevoice_runtime.py``.

Environment:

- ``DECISIONSAI_VIBEVOICE_ASR_MODEL``: Hugging Face repo for weights (default ``microsoft/VibeVoice-ASR``).
- ``DECISIONSAI_VIBEVOICE_ASR_LM``: language model id passed to ``VibeVoiceASRProcessor.from_pretrained`` (default ``Qwen/Qwen2.5-7B``). Smaller models may work if the processor supports them; large downloads apply.
- ``DECISIONSAI_VIBEVOICE_ASR_DEVICE``: force ``cuda``, ``mps``, or ``cpu`` (otherwise auto-detect).
- ``DECISIONSAI_VIBEVOICE_ASR_FLASH``: set to ``1`` to prefer ``flash_attention_2`` on CUDA when installed; default uses ``sdpa`` on CUDA so ASR works without the optional ``flash-attn`` wheel.
- ``DECISIONSAI_VIBEVOICE_ASR_HF_LOCAL_ONLY``: set to ``1`` to pass ``local_files_only=True`` to Hugging Face loads (offline / no new downloads).

**Why it “keeps downloading” after a crash:** The first successful ``Processor.from_pretrained`` can pull the LM repo (default **Qwen2.5-7B**) while the acoustic ``model`` load pulls **microsoft/VibeVoice-ASR** — that is two large phases, not necessarily a bug. If the process dies *after* the processor is built but *before* ``_asr`` is assigned, older code threw away the processor and repeated the heavy HF work on retry; we now **reuse a cached processor** when the model step fails so you do not re-walk the LM download path in the same Python process.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_asr: Any = None
# If acoustic model load fails (OOM, MPS bug), keep the processor so the next attempt
# does not re-run Processor.from_pretrained (which touches the LM repo again).
_processor_cache: Any = None
_processor_cache_key: Tuple[str, str] | None = None


def _model_id() -> str:
    return (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_MODEL") or "microsoft/VibeVoice-ASR").strip()


def _lm_pretrained_name() -> str:
    return (
        os.environ.get("DECISIONSAI_VIBEVOICE_ASR_LM") or "Qwen/Qwen2.5-7B"
    ).strip() or "Qwen/Qwen2.5-7B"


def _pick_device() -> str:
    forced = (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_DEVICE") or "").strip().lower()
    if forced in ("cuda", "mps", "cpu"):
        return forced
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _hf_local_only() -> bool:
    return (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_HF_LOCAL_ONLY") or "").strip() == "1"


def get_asr_batch() -> Any:
    """Return a cached ``VibeVoiceASRBatchInference``-compatible wrapper."""
    global _asr, _processor_cache, _processor_cache_key
    with _lock:
        if _asr is not None:
            return _asr
        # Avoid fork + tokenizers deadlock warnings; silence HF fork spam in logs.
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        import torch
        from vibevoice.modular.modeling_vibevoice_asr import VibeVoiceASRForConditionalGeneration
        from vibevoice.processor.vibevoice_asr_processor import VibeVoiceASRProcessor

        mid = _model_id()
        device = _pick_device()
        lm_name = _lm_pretrained_name()
        local_only = _hf_local_only()
        proc_key = (mid, lm_name)
        if device == "mps":
            torch_dtype = torch.float32
            attn = "sdpa"
        elif device == "cuda":
            torch_dtype = torch.bfloat16
            # flash-attn is optional; sdpa works out of the box on most CUDA setups.
            use_flash = (os.environ.get("DECISIONSAI_VIBEVOICE_ASR_FLASH") or "").strip() == "1"
            attn = "flash_attention_2" if use_flash else "sdpa"
        else:
            torch_dtype = torch.float32
            attn = "sdpa"

        logger.info(
            "VibeVoice ASR: loading %s on %s (LM=%s, attn=%s, hf_local_only=%s)...",
            mid,
            device,
            lm_name,
            attn,
            local_only,
        )

        hf_kw = {"local_files_only": True} if local_only else {}

        if _processor_cache is not None and _processor_cache_key == proc_key:
            processor = _processor_cache
            logger.info("VibeVoice ASR: reusing cached processor (model load will retry)")
        else:
            try:
                processor = VibeVoiceASRProcessor.from_pretrained(
                    mid,
                    language_model_pretrained_name=lm_name,
                    **hf_kw,
                )
            except Exception as e:
                logger.exception(
                    "VibeVoice ASR: processor load failed (check HF access, disk space, and DECISIONSAI_VIBEVOICE_ASR_LM): %s",
                    e,
                )
                raise
            _processor_cache = processor
            _processor_cache_key = proc_key

        logger.info(
            "VibeVoice ASR: processor ready; loading acoustic weights next (first run can take many minutes "
            "while Hugging Face files download — watch disk and network; if MPS hangs, try "
            "DECISIONSAI_VIBEVOICE_ASR_DEVICE=cpu)."
        )

        # Transformers expects ``torch_dtype``; passing ``dtype`` can leak into JSON metadata and raise
        # "Object of type dtype is not JSON serializable".
        try:
            model = VibeVoiceASRForConditionalGeneration.from_pretrained(
                mid,
                torch_dtype=torch_dtype,
                device_map=None,
                attn_implementation=attn,
                trust_remote_code=True,
                **hf_kw,
            )
            logger.info("VibeVoice ASR: from_pretrained returned; moving model to %s", device)
            model = model.to(device)
        except Exception as first_err:
            if attn == "flash_attention_2":
                logger.warning("VibeVoice ASR: flash_attention_2 failed (%s); retrying sdpa", first_err)
                model = VibeVoiceASRForConditionalGeneration.from_pretrained(
                    mid,
                    torch_dtype=torch_dtype,
                    device_map=None,
                    attn_implementation="sdpa",
                    trust_remote_code=True,
                    **hf_kw,
                )
                model = model.to(device)
            else:
                logger.exception(
                    "VibeVoice ASR: model load failed on %s (OOM, unsupported ops on MPS, or bad install). "
                    "Try DECISIONSAI_VIBEVOICE_ASR_DEVICE=cpu or install CUDA build of torch.",
                    device,
                )
                raise
        model.eval()
        _asr = _VibeVoiceASRWrapper(processor, model, device, torch_dtype)
        logger.info("VibeVoice ASR: loaded %s", mid)
        return _asr


class _VibeVoiceASRWrapper:
    """Minimal batch transcribe (one file / path) mirroring upstream demo."""

    def __init__(self, processor, model, device: str, torch_dtype) -> None:
        self.processor = processor
        self.model = model
        self.device = device
        self.torch_dtype = torch_dtype

    def _generation_config(self, max_new_tokens: int) -> dict:
        return {
            "max_new_tokens": max_new_tokens,
            "pad_token_id": self.processor.pad_id,
            "eos_token_id": self.processor.tokenizer.eos_token_id,
            "do_sample": False,
        }

    def transcribe_paths(self, paths: List[str], *, max_new_tokens: int = 8192) -> str:
        """Transcribe one or more audio files; returns flattened plain text (first file only if one)."""
        import torch

        if not paths:
            return ""
        inputs = self.processor(
            audio=paths,
            sampling_rate=None,
            return_tensors="pt",
            padding=True,
            add_generation_prompt=True,
        )
        inputs = {k: v.to(self.device) if torch.is_tensor(v) else v for k, v in inputs.items()}
        gen_cfg = self._generation_config(max_new_tokens)
        input_len = inputs["input_ids"].shape[1]
        with torch.no_grad():
            output_ids = self.model.generate(**inputs, **gen_cfg)
        texts_out = []
        for i in range(len(paths)):
            generated_ids = output_ids[i, input_len:]
            eos_positions = (generated_ids == self.processor.tokenizer.eos_token_id).nonzero(
                as_tuple=True
            )[0]
            if len(eos_positions) > 0:
                generated_ids = generated_ids[: eos_positions[0] + 1]
            raw = self.processor.decode(generated_ids, skip_special_tokens=True)
            segments: list = []
            try:
                segments = self.processor.post_process_transcription(raw) or []
            except Exception as ex:
                logger.warning("VibeVoice ASR: post_process failed (%s); using raw decode", ex)
            flat = _flatten_segments(segments)
            texts_out.append(flat or raw.strip())
        return " ".join(t for t in texts_out if t).strip()


def _flatten_segments(segments: list) -> str:
    parts: list[str] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        t = (seg.get("text") or "").strip()
        if t:
            parts.append(t)
    return " ".join(parts).strip()


def transcribe_audio_file(path: str, *, max_new_tokens: int = 8192) -> str:
    """Transcribe a single audio file (wav/mp3/… supported by upstream processor)."""
    asr = get_asr_batch()
    return asr.transcribe_paths([os.path.abspath(path)], max_new_tokens=max_new_tokens)
