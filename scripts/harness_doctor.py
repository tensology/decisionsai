#!/usr/bin/env python3
"""Print the Decisions harness doctor report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Assess Decisions harness, pack, projection, and CLI setup.")
    parser.add_argument("--home", type=Path, default=None, help="User home directory to assess.")
    parser.add_argument("--root", type=Path, default=None, help="DecisionsAI repository root.")
    parser.add_argument("--json", action="store_true", help="Emit full JSON report.")
    args = parser.parse_args()

    repo_root = args.root or _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from distr.core.harness_doctor import assess_harness_stack

    report = assess_harness_stack(home=args.home, project_root=repo_root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report.get("summary") or {}
        print(
            "Decisions harness doctor: "
            f"{summary.get('ready', 0)} ready, "
            f"{summary.get('missing', 0)} missing, "
            f"{summary.get('stale', 0)} stale"
        )
        for action in report.get("repair_actions") or []:
            print(f"- {action.get('name')}: {action.get('command')}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
