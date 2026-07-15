#!/usr/bin/env python3
"""Expose pytest JUnit failures in GitHub annotations and the step summary."""

from __future__ import annotations

import argparse
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape_command(value: str) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _failure_path(classname: str) -> str:
    clean = re.sub(r"\[[^]]*]", "", classname or "")
    return clean.replace(".", "/") + ".py" if clean else ".github"


def summarize(path: Path) -> tuple[list[str], str]:
    root = ET.parse(path).getroot()
    annotations: list[str] = []
    sections: list[str] = ["## Pytest default-suite failures", ""]
    for case in root.iter("testcase"):
        failures = list(case.findall("failure")) + list(case.findall("error"))
        for failure in failures:
            name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
            detail = (failure.text or failure.get("message") or "pytest failure").strip()
            concise = detail[-4000:]
            annotations.append(
                f"::error file={_failure_path(case.get('classname', ''))},title="
                f"{_escape_command(name)}::{_escape_command(concise)}"
            )
            sections.extend([f"### `{name}`", "", "```text", concise, "```", ""])
    if not annotations:
        sections.extend(["No failing testcases were present in the JUnit file.", ""])
    return annotations, "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    parser.add_argument("--github-annotations", action="store_true")
    args = parser.parse_args()
    annotations, markdown = summarize(args.junit)
    if args.github_annotations:
        for annotation in annotations:
            print(annotation)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as handle:
                handle.write(markdown + "\n")
    else:
        print(markdown)
    return 0 if annotations else 1


if __name__ == "__main__":
    raise SystemExit(main())
