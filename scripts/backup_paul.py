#!/usr/bin/env python3
"""Safely add missing files from a selected drive to Paul Hoft Backup."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from tqdm import tqdm


MOUNT_ROOT = Path("/Volumes")
BACKUP_FOLDER_NAME = "Paul Hoft Backup"
EXCLUDED_ROOT_NAMES = frozenset(
    {
        "SCRAPING",
        "._SCRAPING",
        ".Trashes",
        ".Spotlight-V100",
        ".TemporaryItems",
        ".fseventsd",
        "$RECYCLE.BIN",
        "System Volume Information",
    }
)
DEFAULT_RESERVE_BYTES = 1 * 1024**3
COPY_CHUNK_BYTES = 8 * 1024**2


class BackupError(RuntimeError):
    """A safety check prevented the backup from running."""


@dataclass(frozen=True)
class PlannedFile:
    source: Path
    destination: Path
    size: int


@dataclass(frozen=True)
class PlannedSymlink:
    source: Path
    destination: Path
    target: str


@dataclass
class BackupPlan:
    source_root: Path
    destination_root: Path
    directories: list[Path] = field(default_factory=list)
    files: list[PlannedFile] = field(default_factory=list)
    symlinks: list[PlannedSymlink] = field(default_factory=list)
    skipped_existing: int = 0
    skipped_special: list[Path] = field(default_factory=list)
    conflicts: list[Path] = field(default_factory=list)
    scan_errors: list[str] = field(default_factory=list)

    @property
    def required_bytes(self) -> int:
        return sum(item.size for item in self.files)


@dataclass(frozen=True)
class BackupResult:
    copied_files: int
    copied_symlinks: int
    copied_bytes: int
    skipped_races: int


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    raise AssertionError("unreachable")


def path_exists(path: Path) -> bool:
    """Return True for normal paths and broken symlinks."""
    return os.path.lexists(path)


def mounted_volumes(mount_root: Path = MOUNT_ROOT) -> list[Path]:
    try:
        volumes = [path for path in mount_root.iterdir() if path.is_dir()]
    except OSError as exc:
        raise BackupError(f"Cannot read mounted drives in {mount_root}: {exc}") from exc
    return sorted(volumes, key=lambda path: path.name.casefold())


def describe_volume(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
        return f"{path.name} ({format_bytes(usage.free)} free of {format_bytes(usage.total)})"
    except OSError:
        return path.name


def choose_path(prompt: str, choices: list[Path]) -> Path:
    if not choices:
        raise BackupError(f"No choices are available for {prompt.lower()}.")
    if not sys.stdin.isatty():
        raise BackupError(
            f"{prompt} requires an interactive terminal. Use --source and --destination instead."
        )
    print(f"\n{prompt}")
    for index, path in enumerate(choices, start=1):
        print(f"  {index}. {describe_volume(path)}")
    while True:
        answer = input("Enter a number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        print(f"Please enter a number from 1 to {len(choices)}.")


def resolve_source(value: str | None, mount_root: Path = MOUNT_ROOT) -> Path:
    if value:
        source = Path(value).expanduser().resolve()
    else:
        candidates = [path for path in mounted_volumes(mount_root) if path.name != "Macintosh HD"]
        source = choose_path("Choose the drive to back up", candidates).resolve()
    if not source.is_dir():
        raise BackupError(f"Source drive is not available: {source}")
    return source


def discover_backup_folders(
    source: Path, mount_root: Path = MOUNT_ROOT
) -> list[Path]:
    found: list[Path] = []
    for volume in mounted_volumes(mount_root):
        if volume.resolve() == source.resolve():
            continue
        backup_folder = volume / BACKUP_FOLDER_NAME
        if backup_folder.is_dir():
            found.append(backup_folder)
    return found


def resolve_destination(
    value: str | None, source: Path, mount_root: Path = MOUNT_ROOT
) -> Path:
    if value:
        destination = Path(value).expanduser().resolve()
    else:
        candidates = discover_backup_folders(source, mount_root)
        if len(candidates) == 1:
            destination = candidates[0].resolve()
            print(f"Using existing backup folder: {destination}")
        elif len(candidates) > 1:
            selected_volume = choose_path(
                f"Choose the drive containing {BACKUP_FOLDER_NAME}",
                [path.parent for path in candidates],
            )
            destination = (selected_volume / BACKUP_FOLDER_NAME).resolve()
        else:
            raise BackupError(
                f'No mounted drive contains an existing folder named "{BACKUP_FOLDER_NAME}". '
                "Nothing was created or copied."
            )
    if not destination.is_dir():
        raise BackupError(f"Backup folder is not available: {destination}")
    if destination == source or source in destination.parents:
        raise BackupError("The backup folder cannot be inside the source drive.")
    return destination


def build_plan(
    source_root: Path, destination_root: Path, *, show_progress: bool = False
) -> BackupPlan:
    plan = BackupPlan(source_root=source_root, destination_root=destination_root)
    scan_progress = tqdm(
        desc="Scanning items",
        unit="item",
        dynamic_ncols=True,
        disable=not show_progress,
    )

    def on_walk_error(exc: OSError) -> None:
        plan.scan_errors.append(str(exc))

    try:
        for current_text, directory_names, file_names in os.walk(
            source_root, topdown=True, followlinks=False, onerror=on_walk_error
        ):
            current = Path(current_text)
            relative_current = current.relative_to(source_root)
            scan_progress.set_postfix_str(str(relative_current)[:80], refresh=False)
            scan_progress.update(1 + len(directory_names) + len(file_names))

            if relative_current == Path("."):
                directory_names[:] = [
                    name for name in directory_names if name not in EXCLUDED_ROOT_NAMES
                ]
                file_names = [name for name in file_names if name not in EXCLUDED_ROOT_NAMES]

            destination_directory = destination_root / relative_current
            if path_exists(destination_directory) and not destination_directory.is_dir():
                plan.conflicts.append(destination_directory)
                directory_names[:] = []
                continue
            if not path_exists(destination_directory):
                plan.directories.append(destination_directory)

            traversable_directories: list[str] = []
            for name in directory_names:
                source_path = current / name
                destination_path = destination_directory / name
                if source_path.is_symlink():
                    if path_exists(destination_path):
                        plan.skipped_existing += 1
                    else:
                        try:
                            target = os.readlink(source_path)
                        except OSError as exc:
                            plan.scan_errors.append(f"{source_path}: {exc}")
                        else:
                            plan.symlinks.append(
                                PlannedSymlink(source_path, destination_path, target)
                            )
                    continue
                if path_exists(destination_path) and not destination_path.is_dir():
                    plan.conflicts.append(destination_path)
                    continue
                traversable_directories.append(name)
            directory_names[:] = traversable_directories

            for name in file_names:
                source_path = current / name
                destination_path = destination_directory / name
                if path_exists(destination_path):
                    plan.skipped_existing += 1
                    continue
                try:
                    source_stat = source_path.lstat()
                except OSError as exc:
                    plan.scan_errors.append(f"{source_path}: {exc}")
                    continue
                if stat.S_ISLNK(source_stat.st_mode):
                    try:
                        target = os.readlink(source_path)
                    except OSError as exc:
                        plan.scan_errors.append(f"{source_path}: {exc}")
                    else:
                        plan.symlinks.append(
                            PlannedSymlink(source_path, destination_path, target)
                        )
                elif stat.S_ISREG(source_stat.st_mode):
                    plan.files.append(
                        PlannedFile(source_path, destination_path, source_stat.st_size)
                    )
                else:
                    plan.skipped_special.append(source_path)
    finally:
        scan_progress.close()
    return plan


def validate_plan(plan: BackupPlan, reserve_bytes: int) -> tuple[int, int]:
    if plan.scan_errors:
        examples = "\n".join(f"  - {message}" for message in plan.scan_errors[:10])
        suffix = "" if len(plan.scan_errors) <= 10 else "\n  - ..."
        raise BackupError(
            f"The source could not be scanned completely ({len(plan.scan_errors)} errors):\n"
            f"{examples}{suffix}\nNo files were copied."
        )
    try:
        free_bytes = shutil.disk_usage(plan.destination_root).free
    except OSError as exc:
        raise BackupError(f"Cannot check free space on {plan.destination_root}: {exc}") from exc
    needed_with_reserve = plan.required_bytes + reserve_bytes
    if needed_with_reserve > free_bytes:
        raise BackupError(
            "Not enough free space. "
            f"Missing files need {format_bytes(plan.required_bytes)}, the safety reserve is "
            f"{format_bytes(reserve_bytes)}, and only {format_bytes(free_bytes)} is free. "
            "Nothing was copied."
        )
    return free_bytes, needed_with_reserve


def print_plan(plan: BackupPlan, free_bytes: int, reserve_bytes: int) -> None:
    print("\nBackup preflight")
    print(f"  Source:      {plan.source_root}")
    print(f"  Destination: {plan.destination_root}")
    print(f"  Excluded:    {plan.source_root / 'SCRAPING'}")
    print("               system-managed trash, indexes, and filesystem metadata")
    print(f"  New files:   {len(plan.files):,}")
    print(f"  New links:   {len(plan.symlinks):,}")
    print(f"  New data:    {format_bytes(plan.required_bytes)}")
    print(f"  Already in backup and skipped: {plan.skipped_existing:,}")
    print(f"  Free space:  {format_bytes(free_bytes)}")
    print(f"  Kept free after backup: at least {format_bytes(reserve_bytes)}")
    print("  Existing files will never be overwritten. No files will be deleted.")
    if plan.conflicts:
        print(f"  Path conflicts skipped: {len(plan.conflicts):,}")
    if plan.skipped_special:
        print(f"  Special files skipped: {len(plan.skipped_special):,}")


def confirm_backup() -> bool:
    if not sys.stdin.isatty():
        raise BackupError("Confirmation requires an interactive terminal. Use --yes to continue.")
    answer = input("\nStart this additive backup? Type YES to continue: ").strip()
    return answer == "YES"


def create_directories(directories: Iterable[Path]) -> None:
    for directory in sorted(directories, key=lambda path: len(path.parts)):
        if path_exists(directory):
            continue
        directory.mkdir(exist_ok=False)


def copy_file_exclusive(item: PlannedFile, byte_progress: tqdm) -> bool:
    """Copy one file without any operation that can replace an existing path."""
    destination_created = False
    try:
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        source_stat = item.source.stat()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(item.destination, flags, stat.S_IMODE(source_stat.st_mode))
        destination_created = True
        with item.source.open("rb") as source_handle, os.fdopen(descriptor, "wb") as dest_handle:
            while True:
                chunk = source_handle.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                dest_handle.write(chunk)
                byte_progress.update(len(chunk))
            dest_handle.flush()
            os.fsync(dest_handle.fileno())
        try:
            shutil.copystat(item.source, item.destination, follow_symlinks=False)
        except OSError:
            try:
                os.utime(
                    item.destination,
                    ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                    follow_symlinks=False,
                )
            except OSError:
                pass
        return True
    except FileExistsError:
        return False
    except BaseException:
        if destination_created:
            try:
                item.destination.unlink()
            except OSError:
                pass
        raise


def execute_plan(plan: BackupPlan) -> BackupResult:
    create_directories(plan.directories)
    copied_files = 0
    copied_symlinks = 0
    copied_bytes = 0
    skipped_races = 0

    with tqdm(
        total=plan.required_bytes,
        desc="Backup data",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        dynamic_ncols=True,
    ) as byte_progress, tqdm(
        total=len(plan.files) + len(plan.symlinks),
        desc="Backup items",
        unit="item",
        dynamic_ncols=True,
    ) as item_progress:
        for item in plan.files:
            item_progress.set_postfix_str(item.source.name[:40], refresh=False)
            if copy_file_exclusive(item, byte_progress):
                copied_files += 1
                copied_bytes += item.size
            else:
                skipped_races += 1
                byte_progress.update(item.size)
            item_progress.update(1)

        for item in plan.symlinks:
            item_progress.set_postfix_str(item.source.name[:40], refresh=False)
            try:
                item.destination.parent.mkdir(parents=True, exist_ok=True)
                item.destination.symlink_to(item.target)
                copied_symlinks += 1
            except FileExistsError:
                skipped_races += 1
            item_progress.update(1)

    return BackupResult(copied_files, copied_symlinks, copied_bytes, skipped_races)


def verify_result(plan: BackupPlan, result: BackupResult) -> None:
    problems: list[str] = []
    for item in plan.files:
        if not item.destination.is_file():
            problems.append(f"missing: {item.destination}")
            continue
        try:
            destination_size = item.destination.stat().st_size
        except OSError as exc:
            problems.append(f"cannot inspect {item.destination}: {exc}")
            continue
        if destination_size != item.size:
            problems.append(
                f"size mismatch: {item.destination} ({destination_size} != {item.size})"
            )
    for item in plan.symlinks:
        if not path_exists(item.destination):
            problems.append(f"missing link: {item.destination}")
    if problems:
        examples = "\n".join(f"  - {problem}" for problem in problems[:10])
        raise BackupError(
            f"Post-copy verification found {len(problems)} problem(s):\n{examples}"
        )
    print(
        f"\nVerified {result.copied_files:,} copied files and "
        f"{result.copied_symlinks:,} copied links by destination presence and file size."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            f"Copy only missing items into an existing {BACKUP_FOLDER_NAME} folder. "
            "Existing backup content is never overwritten or deleted."
        )
    )
    parser.add_argument("--source", help="Source drive or folder (otherwise choose interactively)")
    parser.add_argument(
        "--destination",
        help=(
            f"Existing {BACKUP_FOLDER_NAME} folder (otherwise discover it on mounted drives)"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without copying")
    parser.add_argument("--yes", action="store_true", help="Skip the typed confirmation")
    parser.add_argument(
        "--reserve-gb",
        type=float,
        default=DEFAULT_RESERVE_BYTES / 1024**3,
        help="Free space to keep after the backup (default: 1 GiB)",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.reserve_gb < 0:
        raise BackupError("--reserve-gb cannot be negative.")
    source = resolve_source(args.source)
    destination = resolve_destination(args.destination, source)
    if source.stat().st_dev == destination.stat().st_dev:
        raise BackupError("Source and destination must be on different drives.")

    print("Scanning the source and checking which items are missing from the backup...")
    plan = build_plan(source, destination, show_progress=True)
    reserve_bytes = int(args.reserve_gb * 1024**3)
    free_bytes, _ = validate_plan(plan, reserve_bytes)
    print_plan(plan, free_bytes, reserve_bytes)

    if args.dry_run:
        print("\nDry run complete. Nothing was copied.")
        return 0
    if not args.yes and not confirm_backup():
        print("Backup cancelled. Nothing was copied.")
        return 0
    if not plan.files and not plan.symlinks and not plan.directories:
        print("\nThe backup already contains every copyable item. Nothing to do.")
        return 0

    result = execute_plan(plan)
    verify_result(plan, result)
    print(f"Backup complete. Added {format_bytes(result.copied_bytes)} without overwriting anything.")
    return 0


def main() -> int:
    try:
        return run()
    except BackupError as exc:
        print(f"\nSAFETY STOP: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nBackup interrupted. The current partial file was removed.", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"\nSAFETY STOP: A drive operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
