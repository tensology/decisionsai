#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_DIR="$ROOT_DIR/db"
BACKUP_ROOT="$DB_DIR/backups"
STAMP="$(date +"%Y%m%d-%H%M%S")"
DEST="$BACKUP_ROOT/db-$STAMP"

mkdir -p "$DEST"

echo "Creating DecisionsAI database backup..."
echo "Source: $DB_DIR"
echo "Backup: $DEST"

backup_sqlite() {
    local src="$1"
    local dst="$2"

    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$src" ".backup '$dst'"
    else
        cp -p "$src" "$dst"
    fi
}

for src in "$DB_DIR"/*.db; do
    [ -e "$src" ] || continue
    name="$(basename "$src")"
    backup_sqlite "$src" "$DEST/$name"
done

for src in "$DB_DIR"/*.json "$DB_DIR"/*.txt; do
    [ -e "$src" ] || continue
    cp -p "$src" "$DEST/$(basename "$src")"
done

{
    echo "backup_name=db-$STAMP"
    echo "created_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "source=$DB_DIR"
    echo ""
    echo "files:"
    find "$DEST" -maxdepth 1 -type f -print | sort | while read -r file; do
        bytes="$(wc -c < "$file" | tr -d ' ')"
        echo "  $(basename "$file") $bytes bytes"
    done
} > "$DEST/MANIFEST.txt"

echo ""
echo "Backup complete:"
echo "  $DEST"
echo ""
echo "Restore with:"
echo "  $ROOT_DIR/bin/restore.sh db-$STAMP"
