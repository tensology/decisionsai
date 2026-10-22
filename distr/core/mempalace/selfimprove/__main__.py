"""CLI: python -m distr.core.mempalace.selfimprove {capture,nightly,pending,approve,reject} [id] [--dry-run] [--force] [--llm]"""

import argparse
import json
import sys

from distr.core.mempalace.selfimprove import approvals, capture, nightly


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m distr.core.mempalace.selfimprove")
    p.add_argument("cmd", choices=["capture", "nightly", "pending", "approve", "reject"])
    p.add_argument("id", nargs="?", help="approve/reject: proposal id")
    p.add_argument("--allow-dirty", action="store_true", help="approve even if the target has uncommitted edits")
    p.add_argument("--note", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="nightly: run even on an odd night")
    p.add_argument("--llm", action="store_true", help="nightly --dry-run: still call the reviewer CLI")
    p.add_argument("--top", type=int, default=nightly.TOP_N)
    a = p.parse_args(argv)
    if a.cmd in ("approve", "reject") and not a.id:
        p.error(f"{a.cmd} needs a proposal id (see: pending)")
    if a.cmd == "pending":
        out = [{k: r.get(k) for k in ("id", "risk", "risk_reason", "target", "scope", "reason", "created")}
               for r in approvals.list_proposals("pending")]
    elif a.cmd == "approve":
        out = approvals.approve(a.id, allow_dirty=a.allow_dirty)
    elif a.cmd == "reject":
        out = approvals.reject(a.id, note=a.note)
    elif a.cmd == "capture":
        out = capture.run(dry_run=a.dry_run)
    else:
        out = nightly.run(dry_run=a.dry_run, force=a.force, use_llm=True if a.llm else None, top_n=a.top)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
