#!/usr/bin/env bash
# PRForge Distributed Coordinator Watch Monitor
#
# Runs on coordinator/auditor nodes in distributed mode.
# Combines local consistency monitoring with mesh-wide coordination monitoring.
#
# Self-gates: only emits mesh events when distributed.json exists with
# role=coordinator or role=auditor.
#
# Monitors:
#   local consistency (inline)
#   queue-watch                  — depth/pending job count changes
#   worker-heartbeat-watch       — worker node health
#   stale-lease-watch            — jobs stuck beyond lease TTL
#   signoff-watch                — coordinator/auditor/manager verdict state
#   reviewer-update-dispatch-watch — review-triggered job lifecycle

set -euo pipefail

# --- Resolve paths -----------------------------------------------------------
REPO_ROOT=""
ARTIFACT_DIR=""
STATE_FILE=""
INBOX_DIR=""
OUTBOX_DIR=""
DIST_FILE=""

# --- State tracking ----------------------------------------------------------
declare -A LAST
LAST[last_queue_depth]="0"
LAST[pending_jobs]="0"
LAST[active_jobs]="0"
LAST[worker_count]="0"
LAST[verdict_state]=""
LAST[stale_lease_count]="0"
LAST[submission_count]="0"
LAST[job_lifecycle]=""
LAST[dispatch_round]="0"

emit() {
  echo "PRFORGE_EVENT $1"
}

now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

# --- Redis CLI wrapper (via prforge_mesh.py or direct redis-cli) --------------
redis_cmd() {
  redis-cli "$@" 2>/dev/null || true
}

find_context() {
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || true
  if [ -n "$REPO_ROOT" ]; then
    DIST_FILE="$REPO_ROOT/.prforge/distributed.json"

    if [ -f "$REPO_ROOT/.prforge-run" ]; then
      local ad
      ad=$(awk -F= '$1=="artifact_dir"{print $2}' "$REPO_ROOT/.prforge-run" 2>/dev/null)
      if [ -n "$ad" ] && [ -d "$ad" ]; then
        ARTIFACT_DIR="$ad"
        STATE_FILE="$ARTIFACT_DIR/state.json"
        INBOX_DIR="$ARTIFACT_DIR/inbox"
        OUTBOX_DIR="$ARTIFACT_DIR/outbox"
        return 0
      fi
    fi

    if [ -d "$REPO_ROOT/.prforge" ]; then
      ARTIFACT_DIR="$REPO_ROOT/.prforge"
      STATE_FILE="$ARTIFACT_DIR/state.json"
      INBOX_DIR="$ARTIFACT_DIR/inbox"
      OUTBOX_DIR="$ARTIFACT_DIR/outbox"
      return 0
    fi
  fi

  # Coordinator may not have a local repo context
  return 0
}

is_distributed_coordinator() {
  [ -f "$DIST_FILE" ] || return 1
  local roles
  roles=$(jq -r '.roles // [] | join(",")' "$DIST_FILE" 2>/dev/null)
  echo "$roles" | grep -qE "(coordinator|auditor)"
}

get_cluster_name() {
  if [ -f "$DIST_FILE" ]; then
    jq -r '.cluster_name // "default"' "$DIST_FILE" 2>/dev/null
  else
    echo "default"
  fi
}

# --- queue-watch -------------------------------------------------------------
# Monitors pending and active job counts in Redis.

watch_queue() {
  local cluster
  cluster=$(get_cluster_name)
  local prefix="Workflow:${cluster}"

  # Pending jobs from stream
  local pending
  pending=$(redis_cmd XLEN "${prefix}:stream:jobs:pending" 2>/dev/null) || pending="0"

  # Active worker jobs (scan node hashes for status=active)
  local active=0
  local nodes
  nodes=$(redis_cmd KEYS "${prefix}:node:*" 2>/dev/null) || nodes=""
  for node_key in $nodes; do
    local status
    status=$(redis_cmd HGET "$node_key" status 2>/dev/null) || status=""
    local roles
    roles=$(redis_cmd HGET "$node_key" roles 2>/dev/null) || roles=""
    if [ "$status" = "active" ] && echo "$roles" | grep -q "worker"; then
      active=$((active + 1))
    fi
  done

  local last_pending="${LAST[pending_jobs]}"
  local last_active="${LAST[active_jobs]}"

  if [ "$pending" != "$last_pending" ]; then
    emit "queue_depth changed=$((pending - last_pending)) pending=$pending active=$active time=$(now)"
    LAST[pending_jobs]="$pending"
  fi

  if [ "$active" != "$last_active" ]; then
    emit "active_jobs_changed from=$last_active to=$active time=$(now)"
    LAST[active_jobs]="$active"
  fi
}

# --- worker-heartbeat-watch --------------------------------------------------
# Monitors worker node health via Redis heartbeats.

watch_worker_heartbeat() {
  local cluster
  cluster=$(get_cluster_name)
  local prefix="Workflow:${cluster}"

  local workers=0
  local stale_workers=0
  local now_epoch
  now_epoch=$(date +%s)
  local stale_threshold=60  # seconds without heartbeat = stale

  local nodes
  nodes=$(redis_cmd KEYS "${prefix}:node:*" 2>/dev/null) || nodes=""
  for node_key in $nodes; do
    local roles
    roles=$(redis_cmd HGET "$node_key" roles 2>/dev/null) || roles=""
    echo "$roles" | grep -q "worker" || continue

    workers=$((workers + 1))

    local last_seen
    last_seen=$(redis_cmd HGET "$node_key" last_seen 2>/dev/null) || last_seen="0"
    local node_status
    node_status=$(redis_cmd HGET "$node_key" status 2>/dev/null) || node_status=""

    # Check heartbeat age
    if [ "$node_status" != "offline" ] && [ "$last_seen" != "0" ]; then
      local seen_epoch
      seen_epoch=$(date -d "$last_seen" +%s 2>/dev/null) || seen_epoch=0
      local age=$((now_epoch - seen_epoch))
      if [ "$age" -gt "$stale_threshold" ]; then
        local node_id
        node_id=$(redis_cmd HGET "$node_key" node_id 2>/dev/null) || node_id="unknown"
        emit "worker_heartbeat_stale worker=$node_id age_seconds=$age last_seen=$last_seen time=$(now)"
        stale_workers=$((stale_workers + 1))
      fi
    fi
  done

  local last_count="${LAST[worker_count]}"
  if [ "$workers" != "$last_count" ]; then
    emit "worker_pool_changed from=$last_count to=$workers time=$(now)"
    LAST[worker_count]="$workers"
  fi

  if [ "$stale_workers" -gt 0 ]; then
    local last_stale="${LAST[stale_worker_count]:-0}"
    if [ "$stale_workers" != "$last_stale" ]; then
      emit "stale_workers_detected count=$stale_workers time=$(now)"
      LAST[stale_worker_count]="$stale_workers"
    fi
  fi
}

# --- stale-lease-watch -------------------------------------------------------
# Detects jobs stuck in assigned/active beyond lease TTL.

watch_stale_leases() {
  local cluster
  cluster=$(get_cluster_name)
  local prefix="Workflow:${cluster}"

  local lease_ttl=1800
  if [ -f "$DIST_FILE" ]; then
    lease_ttl=$(jq -r '.lease_ttl_seconds // 1800' "$DIST_FILE" 2>/dev/null)
  fi

  local now_epoch
  now_epoch=$(date +%s)
  local stale_count=0

  # Scan job keys
  local jobs
  jobs=$(redis_cmd KEYS "${prefix}:job:*" 2>/dev/null) || jobs=""
  for job_key in $jobs; do
    local status
    status=$(redis_cmd HGET "$job_key" status 2>/dev/null) || status=""
    [ "$status" = "assigned" ] || [ "$status" = "active" ] || continue

    local assigned_at
    assigned_at=$(redis_cmd HGET "$job_key" assigned_at 2>/dev/null) || assigned_at=""
    [ -z "$assigned_at" ] && continue

    local assigned_epoch
    assigned_epoch=$(date -d "$assigned_at" +%s 2>/dev/null) || assigned_epoch=0
    local age=$((now_epoch - assigned_epoch))

    if [ "$age" -gt "$lease_ttl" ]; then
      local job_id
      job_id=$(redis_cmd HGET "$job_key" job_id 2>/dev/null) || job_id="unknown"
      local assigned_node
      assigned_node=$(redis_cmd HGET "$job_key" assigned_node 2>/dev/null) || assigned_node="unknown"
      emit "stale_lease job_id=$job_id node=$assigned_node age_seconds=$age ttl=$lease_ttl time=$(now)"
      stale_count=$((stale_count + 1))
    fi
  done

  local last_stale="${LAST[stale_lease_count]}"
  if [ "$stale_count" != "$last_stale" ]; then
    LAST[stale_lease_count]="$stale_count"
    [ "$stale_count" -gt 0 ] && emit "stale_leases_detected count=$stale_count time=$(now)"
  fi
}

# --- signoff-watch -----------------------------------------------------------
# Monitors the verdict chain: coordinator → auditor → manager.

watch_signoff() {
  if [ -z "$ARTIFACT_DIR" ]; then return; fi

  local mesh_dir="$ARTIFACT_DIR/mesh"
  [ -d "$mesh_dir" ] && [ -d "$ARTIFACT_DIR/outbox" ] || return

  local state=""

  # Build signoff state string
  local coord="absent" audit="absent" mgr="absent" submission="absent"

  [ -f "$ARTIFACT_DIR/outbox/submission.json" ] && submission="present"
  [ -f "$mesh_dir/coordinator_verdict.json" ] && coord=$(jq -r '.decision // "present"' "$mesh_dir/coordinator_verdict.json" 2>/dev/null)
  [ -f "$mesh_dir/auditor_verdict.json" ] && audit=$(jq -r '.decision // "present"' "$mesh_dir/auditor_verdict.json" 2>/dev/null)
  [ -f "$mesh_dir/manager_verdict.json" ] && mgr=$(jq -r '.decision // "present"' "$mesh_dir/manager_verdict.json" 2>/dev/null)

  state="${submission}:${coord}:${audit}:${mgr}"
  local last_state="${LAST[verdict_state]}"
  LAST[verdict_state]="$state"

  [ "$state" = "$last_state" ] && return
  [ -z "$last_state" ] && return

  # Detect specific transitions
  if [ "$submission" = "present" ] && echo "$last_state" | grep -q "^absent:"; then
    emit "worker_submission_ready time=$(now)"
  fi

  if [ "$coord" = "coordinator_pass" ] && echo "$last_state" | grep -q ":coordinator_fail\|:absent:"; then
    emit "coordinator_passed time=$(now)"
  elif [ "$coord" = "coordinator_fail" ]; then
    emit "coordinator_failed time=$(now)"
  fi

  if [ "$audit" = "auditor_pass" ] && echo "$last_state" | grep -q ":auditor_fail\|:absent:"; then
    emit "auditor_passed time=$(now)"
  elif [ "$audit" = "auditor_fail" ]; then
    local rev_instr_count ac_pass
    rev_instr_count=$(jq '.revision_instructions | length' "$mesh_dir/auditor_verdict.json" 2>/dev/null) || rev_instr_count=0
    ac_pass=$(jq -r '.checks.acceptance_criteria_met.pass // "true"' "$mesh_dir/auditor_verdict.json" 2>/dev/null)
    emit "auditor_failed revision_instructions=$rev_instr_count acceptance_criteria_met=$ac_pass time=$(now)"
  fi

  if [ "$mgr" = "manager_pass" ] && echo "$last_state" | grep -q ":manager_fail\|:absent:"; then
    emit "manager_passed auto_ship_eligible=true time=$(now)"
  elif [ "$mgr" = "manager_fail" ]; then
    emit "manager_failed time=$(now)"
  fi
}

# --- reviewer-update-dispatch-watch ------------------------------------------
# Monitors the lifecycle of review-triggered jobs.

watch_reviewer_dispatch() {
  local cluster
  cluster=$(get_cluster_name)
  local prefix="Workflow:${cluster}"

  # Count jobs by type and status
  local review_response_queued=0
  local review_response_active=0
  local review_response_approval=0

  local jobs
  jobs=$(redis_cmd KEYS "${prefix}:job:*" 2>/dev/null) || jobs=""
  for job_key in $jobs; do
    local jtype
    jtype=$(redis_cmd HGET "$job_key" type 2>/dev/null) || jtype=""
    local jstatus
    jstatus=$(redis_cmd HGET "$job_key" status 2>/dev/null) || jstatus=""

    if [ "$jtype" = "review_response" ]; then
      case "$jstatus" in
        queued) review_response_queued=$((review_response_queued + 1)) ;;
        assigned|active) review_response_active=$((review_response_active + 1)) ;;
        approval_ready) review_response_approval=$((review_response_approval + 1)) ;;
      esac
    fi
  done

  local lifecycle="${review_response_queued}:${review_response_active}:${review_response_approval}"
  local last_lifecycle="${LAST[job_lifecycle]}"
  LAST[job_lifecycle]="$lifecycle"

  [ "$lifecycle" = "$last_lifecycle" ] && return
  [ -z "$last_lifecycle" ] && return

  if [ "$review_response_queued" -gt 0 ]; then
    emit "review_response_pending count=$review_response_queued time=$(now)"
  fi

  if [ "$review_response_approval" -gt 0 ]; then
    emit "review_response_approval_ready count=$review_response_approval time=$(now)"
  fi
}

# --- redis-audit-pending-watch -----------------------------------------------
# Detects audit_pending Redis keys written by coordinator after coordinator_pass.
# Emits worker_submission_ready to trigger the auditor Claude session.

watch_redis_audit_pending() {
  local cluster
  cluster=$(get_cluster_name)
  local prefix="Workflow:${cluster}"

  local audit_keys
  audit_keys=$(redis_cmd KEYS "${prefix}:audit_pending:*" 2>/dev/null) || audit_keys=""

  for akey in $audit_keys; do
    local last_seen="${LAST[$akey]:-}"
    [ "$last_seen" = "seen" ] && continue
    local data jid repo pr_number
    data=$(redis_cmd GET "$akey" 2>/dev/null) || data=""
    [ -z "$data" ] && continue
    jid=$(echo "$data" | jq -r '.job_id // "unknown"' 2>/dev/null)
    repo=$(echo "$data" | jq -r '.repo // ""' 2>/dev/null)
    pr_number=$(echo "$data" | jq -r '.pr_number // ""' 2>/dev/null)
    emit "worker_submission_ready job_id=$jid repo=$repo pr_number=$pr_number time=$(now)"
    LAST[$akey]="seen"
  done
}

# --- Local consistency (inline subset for coordinator context) ----------------

watch_local_consistency() {
  if [ -z "$STATE_FILE" ] || [ ! -f "$STATE_FILE" ]; then return; fi

  local phase
  phase=$(jq -r '.phase // "unknown"' "$STATE_FILE" 2>/dev/null)
  local last_phase="${LAST[local_phase]:-}"
  LAST[local_phase]="$phase"

  [ "$phase" = "$last_phase" ] && return
  [ -z "$last_phase" ] && return

  emit "phase_transition from=$last_phase to=$phase time=$(now)"
}

# --- Main loop ---------------------------------------------------------------

main() {
  find_context 2>/dev/null || true

  # Always run local consistency if we have a repo context
  watch_local_consistency

  # Mesh-specific monitors only in distributed coordinator/auditor context
  if is_distributed_coordinator 2>/dev/null; then
    watch_queue
    watch_worker_heartbeat
    watch_stale_leases
    watch_signoff
    watch_reviewer_dispatch
    watch_redis_audit_pending
  fi

  return 0
}

INTERVAL="${PRFORGE_COORDINATOR_WATCH_INTERVAL:-10}"
while true; do
  main 2>/dev/null || true
  sleep "$INTERVAL"
done
