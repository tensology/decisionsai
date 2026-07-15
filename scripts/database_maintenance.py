#!/usr/bin/env python3
"""Backup, verify, or restore the DecisionsAI SQLite database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from distr.core.database_backup import (
    create_database_backup,
    restore_database_backup,
    validate_database_backup,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup")
    backup.add_argument("destination", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("backup", type=Path)

    restore = subparsers.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument(
        "--yes",
        action="store_true",
        help="Confirm replacement of the live database after creating a rollback copy.",
    )

    args = parser.parse_args()
    if args.command == "backup":
        result = create_database_backup(args.destination)
    elif args.command == "verify":
        result = validate_database_backup(args.backup)
    else:
        if not args.yes:
            parser.error("restore requires --yes")
        result = restore_database_backup(args.backup)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
