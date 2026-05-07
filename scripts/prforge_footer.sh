#!/usr/bin/env bash
# PRForge CLI Footer — Mode & Worker Status Indicator
#
# Outputs a single-line status footer for the terminal.
# Designed to be called from Stop hook or shell prompt.
#
# Usage:
#   bash scripts/prforge_footer.sh
#
# Output examples:
#   ◆ PRForge │ standalone │ INTAKE
#   ◆ PRForge │ local-mesh │ worker │ idle
#   ◆ PRForge │ local-mesh │ worker │ active │ job_9f42a │ org/repo │ VALIDATE
#   ◆ PRForge │ lan-mesh   │ coordinator │ online │ 3 nodes │ 2 jobs queued
#   ◆ PRForge │ lan-mesh   │ auditor │ scanning │ 12 PRs tracked
#   ◆ PRForge │ disabled

set -euo pipefail

# ── Config resolution ──────────────────────────────────────────────────────

PRFORGE_MESH_CONFIG="${PRFORGE_MESH_CONFIG:-}"
PRFORGE_MESH_MODE="${PRFORGE_MESH_MODE:-}"
PRFORGE_MESH_ACTIVE="${PRFORGE_MESH_ACTIVE:-0}"
PRFORGE_WORKER_ID="${PRFORGE_WORKER_ID:-}"
PRFORGE_HOME="${PRFORGE_HOME:-$HOME/.prforge}"

# ── Symbols ─────────────────────────────────────────────────────────────────

SYMBOL_PRFORGE="◆"
SYMBOL_STANDALONE="●"
SYMBOL_WORKER="▸"
SYMBOL_COORDINATOR="◆"
SYMBOL_AUDITOR="◇"
SYMBOL_MANAGER="★"
SYMBOL_SEPARATOR="│"
SYMBOL_ONLINE="●"
SYMBOL_OFFLINE="○"
SYMBOL_IDLE="◌"
SYMBOL_ACTIVE="◉"
SYMBOL_BLOCKED="✗"

# ── Detect mode ─────────────────────────────────────────────────────────────

detect_mode() {
    # Explicit mesh config takes priority
    if [[ -n "$PRFORGE_MESH_CONFIG" ]] && [[ -f "$PRFORGE_MESH_CONFIG" ]]; then
        local mode
        mode=$(python3 -c "
import json
try:
    d = json.load(open('$PRFORGE_MESH_CONFIG'))
    print(d.get('mode', ''))
except:
    print('')
" 2>/dev/null || echo "")
        if [[ -n "$mode" ]]; then
            echo "$mode"
            return
        fi
    fi

    # Mode env var
    if [[ -n "$PRFORGE_MESH_MODE" ]]; then
        echo "$PRFORGE_MESH_MODE"
        return
    fi

    # Check for local config
    if [[ -f "$HOME/.prforge-mesh/config.json" ]]; then
        local mode
        mode=$(python3 -c "
import json
try:
    d = json.load(open('$HOME/.prforge-mesh/config.json'))
    print(d.get('mode', ''))
except:
    print('')
" 2>/dev/null || echo "")
        if [[ -n "$mode" ]]; then
            echo "$mode"
            return
        fi
    fi

    echo "standalone"
}

# ── Read distributed state ──────────────────────────────────────────────────

read_distributed_state() {
    local config_path="$1"

    python3 -c "
import json, os, sys
from pathlib import Path

config_path = '$config_path'
try:
    config = json.load(open(config_path))
except:
    print('roles=')
    print('cluster=')
    print('node_id=')
    print('manager_mode=off')
    sys.exit(0)

roles = config.get('roles', [])
if isinstance(roles, str):
    roles = [r.strip() for r in roles.split(',')]

cluster = config.get('cluster', 'default')
node_id = config.get('mesh', {}).get('node_id', '')
mgr = config.get('manager_mode', {})
mgr_enabled = mgr.get('enabled', False)
mgr_authority = mgr.get('authority', 'off')

print('roles=' + ','.join(roles))
print('cluster=' + cluster)
print('node_id=' + node_id)
print('manager_enabled=' + str(mgr_enabled))
print('manager_authority=' + mgr_authority)
" 2>/dev/null || echo "roles="
}

# ── Read worker state from Redis ────────────────────────────────────────────

read_worker_state() {
    local config_path="$1"

    python3 -c "
import json, os, sys, subprocess
from pathlib import Path

config_path = '$config_path'
try:
    config = json.load(open(config_path))
except:
    print('status=unknown')
    print('active_job=')
    print('repo=')
    print('phase=')
    sys.exit(0)

redis_url = config.get('redis', {}).get('url', os.environ.get('PRFORGE_MESH_REDIS', ''))
cluster = config.get('cluster', 'default')
node_id = config.get('mesh', {}).get('node_id', '')

if not redis_url or not node_id:
    print('status=unknown')
    print('active_job=')
    print('repo=')
    print('phase=')
    sys.exit(0)

try:
    import redis
    r = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    r.ping()
except:
    print('status=redis_down')
    print('active_job=')
    print('repo=')
    print('phase=')
    sys.exit(0)

# Read node state
node_key = f'Workflow:{cluster}:node:{node_id}'
node_data = r.hgetall(node_key)

status = node_data.get('status', 'unknown')
active_job = node_data.get('active_job', '')

print('status=' + status)
print('active_job=' + active_job)

# Read job details if active
if active_job:
    job_key = f'Workflow:{cluster}:job:{active_job}'
    job_data = r.hgetall(job_key)
    if job_data:
        print('repo=' + job_data.get('repo', ''))
        # Phase comes from the run's state.json, not Redis
        print('phase=')
    else:
        print('repo=')
        print('phase=')
else:
    print('repo=')
    print('phase=')
" 2>/dev/null || echo "status=unknown"
}

# ── Read coordinator state from Redis ───────────────────────────────────────

read_coordinator_state() {
    local config_path="$1"

    python3 -c "
import json, os, sys
from pathlib import Path

config_path = '$config_path'
try:
    config = json.load(open(config_path))
except:
    print('nodes=0')
    print('pending=0')
    print('active_jobs=0')
    sys.exit(0)

redis_url = config.get('redis', {}).get('url', os.environ.get('PRFORGE_MESH_REDIS', ''))
cluster = config.get('cluster', 'default')

if not redis_url:
    print('nodes=0')
    print('pending=0')
    print('active_jobs=0')
    sys.exit(0)

try:
    import redis
    r = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    r.ping()
except:
    print('nodes=0')
    print('pending=0')
    print('active_jobs=redis_down')
    sys.exit(0)

# Count nodes
nodes_key = f'Workflow:{cluster}:nodes'
node_ids = r.smembers(nodes_key)
node_count = len(node_ids) if node_ids else 0

# Count pending jobs
pending_key = f'Workflow:{cluster}:stream:jobs:pending'
pending = r.xlen(pending_key) if pending_key else 0

# Count active worker jobs
active = 0
for nid in (node_ids or []):
    n = r.hgetall(f'Workflow:{cluster}:node:{nid}')
    if n.get('status') == 'active':
        active += 1

print('nodes=' + str(node_count))
print('pending=' + str(pending))
print('active_jobs=' + str(active))
" 2>/dev/null || echo "nodes=0"
}

# ── Read standalone state ───────────────────────────────────────────────────

read_standalone_state() {
    # Check for active run state in repo
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || true

    if [[ -z "$repo_root" ]]; then
        echo ""
        return
    fi

    # Resolve artifact dir via pointer
    local state_file=""
    if [[ -f "$repo_root/.prforge-run" ]] && [[ ! -L "$repo_root/.prforge-run" ]]; then
        local artifact_dir
        artifact_dir=$(awk -F= '$1=="artifact_dir"{print $2}' "$repo_root/.prforge-run" 2>/dev/null | tail -1)
        if [[ -n "$artifact_dir" ]] && [[ -f "$artifact_dir/state.json" ]]; then
            state_file="$artifact_dir/state.json"
        fi
    fi
    if [[ -z "$state_file" ]] && [[ -f "$repo_root/.prforge/state.json" ]]; then
        state_file="$repo_root/.prforge/state.json"
    fi

    if [[ -n "$state_file" ]]; then
        python3 -c "
import json
try:
    d = json.load(open('$state_file'))
    print(d.get('phase', ''))
except:
    print('')
" 2>/dev/null || echo ""
    return
    fi

    echo ""
}

# ── Format role badge ───────────────────────────────────────────────────────

format_role_badge() {
    local roles="$1"
    local badge=""

    if [[ "$roles" == *"coordinator"* ]] && [[ "$roles" == *"auditor"* ]]; then
        badge="${SYMBOL_COORDINATOR} coord+audit"
    elif [[ "$roles" == *"coordinator"* ]]; then
        badge="${SYMBOL_COORDINATOR} coordinator"
    elif [[ "$roles" == *"auditor"* ]]; then
        badge="${SYMBOL_AUDITOR} auditor"
    elif [[ "$roles" == *"worker"* ]]; then
        badge="${SYMBOL_WORKER} worker"
    else
        badge="${SYMBOL_STANDALONE} node"
    fi

    echo "$badge"
}

# ── Format status indicator ─────────────────────────────────────────────────

format_status_indicator() {
    local status="$1"
    case "$status" in
        online|idle)    echo "${SYMBOL_IDLE} idle" ;;
        active)         echo "${SYMBOL_ACTIVE} active" ;;
        offline)        echo "${SYMBOL_OFFLINE} offline" ;;
        blocked)        echo "${SYMBOL_BLOCKED} blocked" ;;
        redis_down)     echo "${SYMBOL_OFFLINE} redis ↓" ;;
        *)              echo "${SYMBOL_OFFLINE} ${status:-unknown}" ;;
    esac
}

# ── Main ────────────────────────────────────────────────────────────────────

main() {
    local mode
    mode=$(detect_mode)

    case "$mode" in
        standalone)
            local phase
            phase=$(read_standalone_state)
            if [[ -n "$phase" ]]; then
                echo "${SYMBOL_PRFORGE} PRForge ${SYMBOL_SEPARATOR} ${SYMBOL_STANDALONE} standalone ${SYMBOL_SEPARATOR} ${phase}"
            else
                echo "${SYMBOL_PRFORGE} PRForge ${SYMBOL_SEPARATOR} ${SYMBOL_STANDALONE} standalone"
            fi
            ;;

        local|lan)
            local config_path="$PRFORGE_MESH_CONFIG"
            if [[ -z "$config_path" ]] || [[ ! -f "$config_path" ]]; then
                config_path="$HOME/.prforge-mesh/config.json"
            fi

            if [[ ! -f "$config_path" ]]; then
                echo "${SYMBOL_PRFORGE} PRForge ${SYMBOL_SEPARATOR} ${mode}-mesh ${SYMBOL_SEPARATOR} ${SYMBOL_OFFLINE} no config"
                return
            fi

            # Read distributed state
            local dist_state
            dist_state=$(read_distributed_state "$config_path")
            local roles node_id cluster
            roles=$(echo "$dist_state" | grep '^roles=' | cut -d= -f2)
            node_id=$(echo "$dist_state" | grep '^node_id=' | cut -d= -f2)
            cluster=$(echo "$dist_state" | grep '^cluster=' | cut -d= -f2)

            local role_badge
            role_badge=$(format_role_badge "$roles")

            # Role-specific output
            if [[ "$roles" == *"worker"* ]]; then
                local worker_state
                worker_state=$(read_worker_state "$config_path")
                local status active_job repo phase
                status=$(echo "$worker_state" | grep '^status=' | cut -d= -f2)
                active_job=$(echo "$worker_state" | grep '^active_job=' | cut -d= -f2)
                repo=$(echo "$worker_state" | grep '^repo=' | cut -d= -f2)
                phase=$(echo "$worker_state" | grep '^phase=' | cut -d= -f2)

                local status_str
                status_str=$(format_status_indicator "$status")

                local footer="${SYMBOL_PRFORGE} PRForge ${SYMBOL_SEPARATOR} ${mode}-mesh ${SYMBOL_SEPARATOR} ${role_badge} ${SYMBOL_SEPARATOR} ${status_str}"

                if [[ -n "$active_job" ]] && [[ "$status" == "active" ]]; then
                    footer="${footer} ${SYMBOL_SEPARATOR} ${active_job}"
                    if [[ -n "$repo" ]]; then
                        footer="${footer} ${SYMBOL_SEPARATOR} ${repo}"
                    fi
                    if [[ -n "$phase" ]]; then
                        footer="${footer} ${SYMBOL_SEPARATOR} ${phase}"
                    fi
                fi

                echo "$footer"

            elif [[ "$roles" == *"coordinator"* ]] || [[ "$roles" == *"auditor"* ]]; then
                local coord_state
                coord_state=$(read_coordinator_state "$config_path")
                local nodes pending active_jobs
                nodes=$(echo "$coord_state" | grep '^nodes=' | cut -d= -f2)
                pending=$(echo "$coord_state" | grep '^pending=' | cut -d= -f2)
                active_jobs=$(echo "$coord_state" | grep '^active_jobs=' | cut -d= -f2)

                local status_sym="${SYMBOL_ONLINE}"
                if [[ "$active_jobs" == "redis_down" ]]; then
                    status_sym="${SYMBOL_OFFLINE}"
                fi

                echo "${SYMBOL_PRFORGE} PRForge ${SYMBOL_SEPARATOR} ${mode}-mesh ${SYMBOL_SEPARATOR} ${role_badge} ${SYMBOL_SEPARATOR} ${status_sym} online ${SYMBOL_SEPARATOR} ${nodes} nodes ${SYMBOL_SEPARATOR} ${pending} queued ${SYMBOL_SEPARATOR} ${active_jobs} active"

            else
                echo "${SYMBOL_PRFORGE} PRForge ${SYMBOL_SEPARATOR} ${mode}-mesh ${SYMBOL_SEPARATOR} ${role_badge}"
            fi
            ;;

        *)
            echo "${SYMBOL_PRFORGE} PRForge ${SYMBOL_SEPARATOR} unknown mode: ${mode}"
            ;;
    esac
}

main
