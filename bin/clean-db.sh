#!/usr/bin/env bash
# Prune local runtime clutter under db/ (never touches settings.db data rows).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_DIR="$ROOT_DIR/db"
DRY_RUN="false"
KEEP_BACKUPS=0
VACUUM="false"

usage() {
    cat <<'EOF'
Usage: bin/clean-db.sh [options]

Removes safe-to-delete runtime clutter from db/:
  - empty legacy *.db files (database.db, decisions.db)
  - manual settings.db.backup-* snapshots in db/
  - old db/backups/* folders (see --keep-backups)
  - whatsapp_media files not referenced by whatsapp_messages
  - rotated log files and .DS_Store junk

Options:
  --dry-run          Show what would be removed, do not delete
  --keep-backups N   Keep the N newest backup folders (default: 0)
  --vacuum           Run sqlite3 VACUUM on settings.db (stop DecisionsAI first)
  -h, --help         Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN="true" ;;
        --keep-backups)
            shift
            KEEP_BACKUPS="${1:-0}"
            ;;
        --vacuum) VACUUM="true" ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

bytes_before="$(du -sk "$DB_DIR" 2>/dev/null | awk '{print $1}')"

remove_path() {
    local path="$1"
    local reason="$2"
    if [ ! -e "$path" ]; then
        return 0
    fi
    if [ "$DRY_RUN" = "true" ]; then
        echo "would remove: $path ($reason)"
    else
        rm -rf "$path"
        echo "removed: $path ($reason)"
    fi
}

truncate_file() {
    local path="$1"
    local reason="$2"
    if [ ! -f "$path" ]; then
        return 0
    fi
    if [ "$DRY_RUN" = "true" ]; then
        echo "would truncate: $path ($reason)"
    else
        : >"$path"
        echo "truncated: $path ($reason)"
    fi
}

echo "Cleaning db/: $DB_DIR"
if [ "$DRY_RUN" = "true" ]; then
    echo "(dry run — nothing deleted)"
fi

# Legacy empty database files.
for legacy in "$DB_DIR/database.db" "$DB_DIR/decisions.db"; do
    if [ -f "$legacy" ] && [ ! -s "$legacy" ]; then
        remove_path "$legacy" "empty legacy database file"
    fi
done

# Ad-hoc sqlite snapshots left in db/ root.
while IFS= read -r backup; do
    remove_path "$backup" "manual settings.db backup snapshot"
done < <(find "$DB_DIR" -maxdepth 1 -type f -name 'settings.db.backup-*' 2>/dev/null | sort)

# Timestamped backup folders from bin/backup.sh.
BACKUP_ROOT="$DB_DIR/backups"
if [ -d "$BACKUP_ROOT" ]; then
    backup_list="$(mktemp)"
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'db-*' | sort -r >"$backup_list"
    skip="$KEEP_BACKUPS"
    while IFS= read -r path; do
        if [ "$skip" -gt 0 ]; then
            skip=$((skip - 1))
            continue
        fi
        remove_path "$path" "old db backup folder"
    done <"$backup_list"
    rm -f "$backup_list"
fi

# Orphan WhatsApp media (files on disk with no whatsapp_messages.media_local_path).
if [ -f "$DB_DIR/settings.db" ] && [ -d "$DB_DIR/whatsapp_media" ]; then
    export DB_DIR DRY_RUN
    PYTHON_BIN="${PYTHON_BIN:-python3}"
    if [ -x "$HOME/.virtualenvs/decisions/bin/python" ]; then
        PYTHON_BIN="$HOME/.virtualenvs/decisions/bin/python"
    fi
    "$PYTHON_BIN" - <<'PY'
import os
import sqlite3
from pathlib import Path

db_dir = Path(os.environ["DB_DIR"])
media_dir = db_dir / "whatsapp_media"
settings_db = db_dir / "settings.db"
dry_run = os.environ.get("DRY_RUN", "false") == "true"

conn = sqlite3.connect(settings_db)
rows = conn.execute(
    "SELECT media_local_path FROM whatsapp_messages "
    "WHERE media_local_path IS NOT NULL AND media_local_path != ''"
).fetchall()
referenced = {os.path.basename(stored) for (stored,) in rows}

removed = 0
freed = 0
for path in sorted(media_dir.iterdir()):
    if not path.is_file():
        continue
    if path.name in referenced:
        continue
    size = path.stat().st_size
    if dry_run:
        print(f"would remove: {path} (orphan whatsapp media)")
    else:
        path.unlink()
        print(f"removed: {path} (orphan whatsapp media)")
    removed += 1
    freed += size

print(f"whatsapp_media orphans: {removed} files ({freed / 1024 / 1024:.1f} MB)")
PY
fi

# Logs live under db/logs; app may also log to ~/.decisions/logs.
if [ -d "$DB_DIR/logs" ]; then
    while IFS= read -r logfile; do
        truncate_file "$logfile" "runtime log"
    done < <(find "$DB_DIR/logs" -type f -name '*.log' 2>/dev/null)
fi

while IFS= read -r ds; do
    remove_path "$ds" "macOS metadata"
done < <(find "$DB_DIR" -name '.DS_Store' 2>/dev/null)

if [ "$VACUUM" = "true" ]; then
    if pgrep -f "[Pp]ython.*${ROOT_DIR}/bin/start.py" >/dev/null 2>&1; then
        echo "Skipping VACUUM: DecisionsAI is still running. Stop the app and re-run with --vacuum." >&2
    elif [ -f "$DB_DIR/settings.db" ]; then
        if [ "$DRY_RUN" = "true" ]; then
            echo "would vacuum: $DB_DIR/settings.db"
        else
            sqlite3 "$DB_DIR/settings.db" "VACUUM;"
            echo "vacuumed: $DB_DIR/settings.db"
        fi
    fi
fi

bytes_after="$(du -sk "$DB_DIR" 2>/dev/null | awk '{print $1}')"
saved_kb=$((bytes_before - bytes_after))
if [ "$saved_kb" -lt 0 ]; then
    saved_kb=0
fi
echo ""
echo "Done. db/ size: ${bytes_before} KB -> ${bytes_after} KB (freed ~$((saved_kb / 1024)) MB)"
