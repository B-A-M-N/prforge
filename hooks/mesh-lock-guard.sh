#!/usr/bin/env bash
# PRForge Mesh Lock Guard — PreToolUse hook for distributed-local mode.
# Thin wrapper that calls the Python guard for Bash/Write/Edit/MultiEdit.
# Exits 0 (allow) or 1 (block with redirect message).

set +e

CONFIG="$HOME/.prforge-mesh/config.json"

# Skip if mesh mode is not active
if [[ ! -f "$CONFIG" ]]; then
  exit 0
fi

# Check if mode is local or distributed
MODE=$(python3 -c "
import json
try:
    d = json.load(open('$CONFIG'))
    print(d.get('mode', ''))
except:
    print('')
" 2>/dev/null)

if [[ "$MODE" != "local" && "$MODE" != "distributed" ]]; then
  exit 0
fi

# Check if this session is a worker
WORKER_ID=$(python3 -c "
import json
try:
    d = json.load(open('$CONFIG'))
    print(d.get('worker_id', ''))
except:
    print('')
" 2>/dev/null)

if [[ -z "$WORKER_ID" ]]; then
  exit 0  # Not a worker, skip
fi

# Read tool name from hook input
HOOK_JSON=$(cat)
TOOL_NAME=""
if command -v jq >/dev/null 2>&1; then
  TOOL_NAME=$(echo "$HOOK_JSON" | jq -r '.tool_name // empty' 2>/dev/null)
else
  TOOL_NAME=$(echo "$HOOK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
fi

# Only enforce for relevant tools
case "$TOOL_NAME" in
  Bash|Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

# Run Python guard
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [[ -z "$PLUGIN_ROOT" ]]; then
  # Try to find plugin root from config or filesystem
  PLUGIN_ROOT=$(find "$HOME" -path "*/prforge/scripts/mesh" -type d 2>/dev/null | head -1 | xargs dirname)
fi

GUARD_SCRIPT="$PLUGIN_ROOT/mesh_lock_guard.py"

if [[ ! -f "$GUARD_SCRIPT" ]]; then
  # Guard script not found, allow (don't block on missing infra)
  exit 0
fi

# Pass hook JSON to Python guard via stdin
echo "$HOOK_JSON" | python3 "$GUARD_SCRIPT"
exit $?
