#!/bin/bash
# MemPalace shared-brain hub for the Decisions palace (loopback only).
# One `mempalace serve` owns the palace; every agent/harness talks to it.
# A per-palace bearer token is kept 0600 under ~/.mempalace/server/<key>/token
# (MemPalace's own location, so local stdio forwarders find it) and passed via
# the environment only — never argv, never logged.
set -euo pipefail

PALACE="${DECISIONS_MEMPALACE_PALACE_PATH:-$HOME/.decisions/mempalace/palace}"
PY="${DECISIONS_MEMPALACE_PYTHON:-$HOME/.virtualenvs/decisions/bin/python}"
PORT="${DECISIONS_MEMPALACE_HUB_PORT:-8766}"
HOST="127.0.0.1"   # never widen this; remote access is a separate decision

TOKFILE="$("$PY" - "$PALACE" <<'PY'
import os, secrets, sys
from mempalace.server_registry import server_token_path
p = server_token_path(sys.argv[1])
p.parent.mkdir(parents=True, exist_ok=True)
os.chmod(p.parent, 0o700)
if not p.exists() or not p.read_text().strip():
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, (secrets.token_urlsafe(32) + "\n").encode())
    os.close(fd)
print(p)
PY
)"
MEMPALACE_MCP_HTTP_TOKEN="$(tr -d '\n' < "$TOKFILE")"
export MEMPALACE_MCP_HTTP_TOKEN
export MEMPALACE_PALACE_PATH="$PALACE"
exec "$PY" -m mempalace --palace "$PALACE" serve --host "$HOST" --port "$PORT"
