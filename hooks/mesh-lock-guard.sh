#!/usr/bin/env bash
# PRForge Mesh Lock Guard — PreToolUse hook for distributed mode.
# Resolves config path deterministically, then delegates to the Python guard.
# Exits 0 (allow) or 1 (block with redirect message).
#
# The Python guard (scripts/mesh/mesh_lock_guard.py) handles all JSON parsing,
# Redis checks, lease verification, and phase-aware enforcement.
# This Bash wrapper ONLY resolves config and validates env identity.

set +e

# ---------------------------------------------------------------------------
# Config resolution with mode-aware priority
# ---------------------------------------------------------------------------
resolve_mesh_config() {
  # 1. Explicit override from worker runtime — always wins
  if [[ -n "${PRFORGE_MESH_CONFIG:-}" ]]; then
    if [[ -f "$PRFORGE_MESH_CONFIG" ]]; then
      echo "$PRFORGE_MESH_CONFIG"
      return 0
    fi
    # Explicit path set but file missing/invalid — fail closed
    echo "PRForge lock guard: PRFORGE_MESH_CONFIG set but not found: $PRFORGE_MESH_CONFIG" >&2
    return 2
  fi

  # 2. Mode-specific default
  case "${PRFORGE_MESH_MODE:-}" in
    local)
      local p="$HOME/.prforge-mesh/config.json"
      if [[ -f "$p" ]]; then
        echo "$p"
        return 0
      fi
      return 1
      ;;
    lan)
      # LAN glob fallback — only when mesh is active
      if [[ "${PRFORGE_MESH_ACTIVE:-}" == "1" ]]; then
        shopt -s nullglob
        local configs=( "$HOME"/.prforge-mesh/lan/*/config.json )
        shopt -u nullglob

        if [[ "${#configs[@]}" -eq 1 ]]; then
          echo "${configs[0]}"
          return 0
        fi

        if [[ "${#configs[@]}" -gt 1 ]]; then
          echo "PRForge lock guard: multiple LAN configs found; set PRFORGE_MESH_CONFIG explicitly" >&2
          return 2
        fi
      fi
      return 1
      ;;
  esac

  # 3. No mode set — safe default: check local config only
  local default="$HOME/.prforge-mesh/config.json"
  if [[ -f "$default" ]]; then
    echo "$default"
    return 0
  fi

  # 4. No mesh context at all
  return 1
}

CONFIG_PATH="$(resolve_mesh_config)"
code=$?

if [[ "$code" -eq 2 ]]; then
  # Ambiguous or broken config — always block
  exit 1
fi

if [[ "$code" -eq 1 ]]; then
  if [[ "${PRFORGE_MESH_ACTIVE:-}" == "1" ]]; then
    # Active worker but no config found — fail closed
    echo "PRForge lock guard: active mesh worker but no config found" >&2
    exit 1
  fi
  # Not running under mesh — allow
  exit 0
fi

# ---------------------------------------------------------------------------
# Fail-closed: active mesh worker must have PRFORGE_WORKER_ID
# ---------------------------------------------------------------------------
if [[ -z "${PRFORGE_WORKER_ID:-}" ]]; then
  if [[ -n "${PRFORGE_MESH_ACTIVE:-}" || -n "${PRFORGE_MESH_CONFIG:-}" ]]; then
    echo "PRForge lock guard: active mesh worker missing PRFORGE_WORKER_ID" >&2
    exit 1
  fi
  # No mesh worker context — allow
  exit 0
fi

# ---------------------------------------------------------------------------
# Read tool name from hook input
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Locate Python guard script
# ---------------------------------------------------------------------------
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
MESH_SCRIPTS=""
if [[ -z "$PLUGIN_ROOT" ]]; then
  MESH_SCRIPTS=$(find "$HOME" -path "*/prforge/scripts/mesh" -type d 2>/dev/null | head -1)
else
  MESH_SCRIPTS="$PLUGIN_ROOT/scripts/mesh"
fi

if [[ -z "$MESH_SCRIPTS" || ! -d "$MESH_SCRIPTS" ]]; then
  echo "PRForge lock guard: mesh scripts directory not found — refusing unsafe write" >&2
  exit 1
fi

GUARD_SCRIPT="$MESH_SCRIPTS/mesh_lock_guard.py"

if [[ ! -f "$GUARD_SCRIPT" ]]; then
  echo "PRForge lock guard: Python guard script not found at $GUARD_SCRIPT — refusing unsafe write" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Delegate to Python guard for all JSON/Redis/phase logic
# ---------------------------------------------------------------------------
echo "$HOOK_JSON" | python3 "$GUARD_SCRIPT" \
  --config "$CONFIG_PATH" \
  --worker-id "$PRFORGE_WORKER_ID"
exit $?
