"""Command-line entry point for deterministic UI contract compilation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler import compile_screen_contract
from .benchmark import run_template_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile or benchmark DecisionsAI UI contracts")
    parser.add_argument("contract", nargs="?", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.benchmark:
        print(json.dumps(run_template_benchmark(args.output), indent=2))
        return 0
    if args.contract is None or args.output is None:
        parser.error("contract and --output are required unless --benchmark is used")
    payload = json.loads(args.contract.read_text(encoding="utf-8"))
    result = compile_screen_contract(payload, args.output, overwrite=args.overwrite)
    print(json.dumps(result.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
