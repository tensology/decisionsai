#!__PYTHON__
"""Pi skill client for the local MemPalace hub (search / add / status)."""
import argparse
import json
import os
import sys

sys.path.insert(0, "__REPO__")
from distr.core.mempalace import hub  # noqa: E402
from distr.core.mempalace.paths import default_palace_path  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--wing")
    s.add_argument("--room")
    s.add_argument("--limit", type=int, default=5)
    a = sub.add_parser("add")
    a.add_argument("--wing", required=True)
    a.add_argument("--room", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--project", default=os.path.basename(os.getcwd()).lower())
    sub.add_parser("status")
    args = p.parse_args()
    palace = str(default_palace_path())
    try:
        if args.cmd == "status":
            out = {"hub_up": hub.hub_is_up(palace), "journal_pending": hub.journal_pending(palace)}
            if out["hub_up"]:
                st = hub.call_tool(palace, "mempalace_status", {})
                out.update({"total_drawers": st.get("total_drawers"), "wings": st.get("wings")})
        elif args.cmd == "search":
            q = {"query": args.query, "limit": args.limit}
            if args.wing:
                q["wing"] = args.wing
            if args.room:
                q["room"] = args.room
            res = hub.call_tool(palace, "mempalace_search", q)
            out = {"results": [{k: r.get(k) for k in ("text", "wing", "room", "source_file", "similarity")}
                               for r in res.get("results", [])]}
        else:
            call = {"wing": args.wing, "room": args.room, "content": args.text,
                    "added_by": f"mac:pi:{args.project}", "source_file": "pi-skill"}
            if hub.hub_is_up(palace):
                out = hub.call_tool(palace, "mempalace_add_drawer", call)
            else:
                out = {"queued": True, "journal": str(hub.journal_append(palace, "mempalace_add_drawer", call))}
    except Exception as exc:
        out = {"error": str(exc)}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main())
