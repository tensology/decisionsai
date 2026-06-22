#!/usr/bin/env python3
"""Score frontier-prep packet JSON files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


LEVEL = {"low": 0.34, "med": 0.67, "medium": 0.67, "high": 1.0}


def level(value: object) -> float:
    return LEVEL.get(str(value or "").strip().lower(), 0.5)


def score_packet(packet: dict) -> tuple[float, float]:
    rounds = min(float(packet.get("rounds_saved") or 0) / 5.0, 1.0)
    qualify = (0.5 * rounds) + (0.5 * level(packet.get("ceiling_lift")))
    score = (0.5 * qualify) + (0.3 * level(packet.get("value"))) + (0.2 * level(packet.get("risk")))
    return round(score, 3), round(qualify, 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packets", type=Path, help="JSON file containing a packet list")
    parser.add_argument("--drop-below", type=float, default=0.15)
    args = parser.parse_args()

    data = json.loads(args.packets.read_text(encoding="utf-8"))
    kept = []
    for packet in data:
        score, qualify = score_packet(packet)
        if qualify < args.drop_below:
            continue
        packet["score"] = score
        packet["_qualify"] = qualify
        kept.append(packet)
    kept.sort(key=lambda item: item["score"], reverse=True)
    print(json.dumps(kept, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
