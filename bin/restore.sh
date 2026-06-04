#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_DIR="$ROOT_DIR/db"
BACKUP_ROOT="$DB_DIR/backups"
FORCE="false"
REQUESTED=""

for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE="true"
            ;;
        *)
            REQUESTED="$arg"
            ;;
    esac
done

list_backups() {
    if [ ! -d "$BACKUP_ROOT" ]; then
        echo "No backup folder exists yet: $BACKUP_ROOT"
        exit 1
    fi

    backup_list="$(mktemp)"
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'db-*' | sort -r > "$backup_list"
    backup_count="$(wc -l < "$backup_list" | tr -d ' ')"
    if [ "$backup_count" -eq 0 ]; then
        rm -f "$backup_list"
        echo "No database backups found in $BACKUP_ROOT"
        exit 1
    fi

    echo "Available database backups:"
    local i=1
    while IFS= read -r path; do
        name="$(basename "$path")"
        created=""
        if [ -f "$path/MANIFEST.txt" ]; then
            created="$(awk -F= '/^created_at=/{print $2}' "$path/MANIFEST.txt" | head -1)"
        fi
        printf "  %2d. %s" "$i" "$name"
        [ -n "$created" ] && printf " (%s)" "$created"
        printf "\n"
        i=$((i + 1))
    done < "$backup_list"

    echo ""
    read -r -p "Restore which backup? Enter number or backup name: " choice
    if [[ "$choice" =~ ^[0-9]+$ ]]; then
        idx=$((choice - 1))
        if [ "$idx" -lt 0 ] || [ "$idx" -ge "$backup_count" ]; then
            rm -f "$backup_list"
            echo "Invalid backup number: $choice" >&2
            exit 1
        fi
        REQUESTED="$(sed -n "$((idx + 1))p" "$backup_list")"
    else
        REQUESTED="$choice"
    fi
    rm -f "$backup_list"
}

resolve_backup() {
    local requested="$1"
    if [ -z "$requested" ]; then
        list_backups
        requested="$REQUESTED"
    fi

    if [ -d "$requested" ]; then
        BACKUP_DIR="$(cd "$requested" && pwd)"
    elif [ -d "$BACKUP_ROOT/$requested" ]; then
        BACKUP_DIR="$BACKUP_ROOT/$requested"
    else
        echo "Backup not found: $requested" >&2
        echo "Run without arguments to list available backups." >&2
        exit 1
    fi
}

resolve_backup "$REQUESTED"

echo "Selected backup: $BACKUP_DIR"
if [ -f "$BACKUP_DIR/MANIFEST.txt" ]; then
    echo ""
    sed -n '1,80p' "$BACKUP_DIR/MANIFEST.txt"
    echo ""
fi

if [ "$FORCE" != "true" ]; then
    echo "Restore will overwrite top-level database/config files in:"
    echo "  $DB_DIR"
    echo ""
    echo "Close DecisionsAI before restoring. Restoring while it is running can lose live writes."
    read -r -p "Type RESTORE to continue: " confirm
    if [ "$confirm" != "RESTORE" ]; then
        echo "Restore cancelled."
        exit 0
    fi
fi

if [ "${SKIP_PRE_RESTORE_BACKUP:-}" != "1" ]; then
    echo ""
    echo "Creating safety backup before restore..."
    "$ROOT_DIR/bin/backup.sh" >/dev/null
fi

mkdir -p "$DB_DIR"

for src in "$BACKUP_DIR"/*; do
    [ -f "$src" ] || continue
    name="$(basename "$src")"
    [ "$name" = "MANIFEST.txt" ] && continue
    cp -p "$src" "$DB_DIR/$name"
    case "$name" in
        *.db)
            rm -f "$DB_DIR/$name-wal" "$DB_DIR/$name-shm"
            ;;
    esac
done

echo ""
echo "Restore complete from:"
echo "  $BACKUP_DIR"
echo ""
echo "You can now restart DecisionsAI."
