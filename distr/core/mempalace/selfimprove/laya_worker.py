"""Laya classifier worker. Runs inside the Laya venv and never imports Decisions code.

Two modes:
- one-shot (legacy): one JSON ``{"states": [...], "questions": {...}}`` on stdin, one JSON list on stdout;
- ``--serve``: the model is loaded ONCE. The worker prints ``{"ready": true, ...}``, then answers one
  JSON request per stdin line with one JSON line per request until stdin closes.

Memory safety: each request is classified with an explicit ``batch_size``, never an
unbounded single pass. States are clipped to ``LAYA_MAX_CHARS``. On MPS the per-process
allocation cap is set from ``DECISIONS_CAPTURE_LAYA_MAX_MB``, so an oversized batch raises
an out-of-memory error, which is returned as ``{"error": ...}``, instead of wiring the
machine's RAM. The MPS cache is emptied after every request.
"""
import gc
import json
import os
import sys


def _answers(res):
    out = []
    for r in res:
        answers = r.get("answers", r) if isinstance(r, dict) else {}
        f = answers.get("frustrated") or {}
        s = answers.get("severity") or {}
        k = answers.get("kind") or {}
        out.append({"frustrated": float(f.get("noul", 0.0)), "severity": float(s.get("score", 0.0)),
                    "kind": k.get("choice")})
    return out


def _load():
    os.environ.setdefault("LAYA_REVISION", "reviewed")  # same pinned checkpoint Decisions uses
    import laya  # noqa: WPS433

    device = os.environ.get("LAYA_DEVICE") or "cpu"  # MPS measured heavier (3.8 GB loaded) for this job
    info = {"device": device}
    if device == "cpu":
        try:
            import torch

            torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS") or 4))
        except Exception:
            pass
    if device == "mps":
        try:
            import torch

            cap_mb = float(os.environ.get("DECISIONS_CAPTURE_LAYA_MAX_MB") or 0)
            rec = float(torch.mps.recommended_max_memory()) if hasattr(torch.mps, "recommended_max_memory") else 0.0
            if cap_mb > 0 and rec > 0:
                frac = max(0.01, min(1.0, cap_mb * 2**20 / rec))
                torch.mps.set_per_process_memory_fraction(frac)
                info["mps_fraction"] = round(frac, 4)
        except Exception as exc:  # pragma: no cover - depends on torch build
            info["mps_fraction_error"] = type(exc).__name__
    return laya.load("convaiinnovations/laya", device=device), info


def _empty_cache():
    gc.collect()
    try:
        import torch

        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


def _classify(agent, req, max_chars, batch_size):
    states = [str(s)[:max_chars] for s in req["states"]]
    res = agent.predict_batch(states, req["questions"], batch_size=min(batch_size, max(1, len(states))))
    return _answers(res)


def _protocol_stream():
    """Keep the protocol on the real stdout. Anything libraries print (Laya prints warnings to stdout)
    goes to stderr instead, so it can never corrupt a reply."""
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return proto


def main() -> int:
    out_stream = _protocol_stream()
    max_chars = int(os.environ.get("LAYA_MAX_CHARS") or 600)
    batch_size = int(os.environ.get("LAYA_BATCH_SIZE") or 4)
    if "--serve" not in sys.argv:
        req = json.load(sys.stdin)
        agent, _ = _load()
        json.dump(_classify(agent, req, max_chars, batch_size), out_stream)
        out_stream.flush()
        return 0
    agent, info = _load()
    print(json.dumps({"ready": True, **info}), file=out_stream, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            out = _classify(agent, json.loads(line), max_chars, batch_size)
        except Exception as exc:  # OOM from the MPS cap lands here
            out = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        print(json.dumps(out), file=out_stream, flush=True)
        _empty_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())
