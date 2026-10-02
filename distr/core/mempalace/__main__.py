"""python -m distr.core.mempalace — LOCAL seed setup / status / mine.

Examples::

    python -m distr.core.mempalace --status
    python -m distr.core.mempalace              # ensure deps + mine (blocking)
    python -m distr.core.mempalace --force
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Ensure DecisionsAI root on path when run as module from elsewhere
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

REF = _ROOT.parent / "reference" / "mempalace"


def _ensure_importable() -> tuple[bool, str]:
    from distr.core.mempalace.bootstrap import ensure_mempalace_on_path, try_import_mempalace

    ensure_mempalace_on_path()
    ok, err = try_import_mempalace()
    if ok:
        try:
            import chromadb  # noqa: F401
        except Exception as exc:
            return False, f"chromadb missing: {exc}"
        return True, ""
    return False, err or "import failed"


def _try_pip_editable() -> int:
    if not (REF / "pyproject.toml").is_file() and not (REF / "setup.py").is_file():
        print(f"Reference clone missing at {REF}", file=sys.stderr)
        return 1
    print(f"pip install -e {REF}")
    return subprocess.call([sys.executable, "-m", "pip", "install", "-e", str(REF)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-pip", action="store_true", help="Do not attempt pip install -e")
    args = ap.parse_args(argv)

    if args.status:
        from distr.core.mempalace.wiring import backend_status
        from distr.core.mempalace.seed import last_seed_status

        print(
            json.dumps(
                {"backend": backend_status(), "seed": last_seed_status()},
                indent=2,
                default=str,
            )
        )
        return 0

    ok, err = _ensure_importable()
    if not ok and not args.no_pip:
        print(f"mempalace/chromadb not ready ({err}); attempting local editable install…")
        rc = _try_pip_editable()
        if rc != 0:
            print("pip install failed — install manually then re-run.", file=sys.stderr)
            return rc
        ok, err = _ensure_importable()
    if not ok:
        print(
            f"Still unavailable: {err}\n"
            f"  pip install -e {REF}\n"
            "Then re-run: python -m distr.core.mempalace",
            file=sys.stderr,
        )
        return 1

    from distr.core.mempalace.seed import mine_seed_wing_sync

    result = mine_seed_wing_sync(force=args.force)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("state") in {"done", "mining"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
