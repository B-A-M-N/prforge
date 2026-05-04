#!/usr/bin/env bash
# PRForge Distributed Worker Watch Monitor
#
# Runs on worker nodes in distributed mode.
# Combines local consistency monitoring with mesh-specific inbox listening.
#
# Self-gates: only emits mesh events when distributed.json exists with role=worker.
# Falls back to local-only mode when not in distributed context.
#
# Monitors:
#   local consistency (delegated to local-watch logic inline)
#   inbox-watch          — detects new jobs and revision jobs in inbox/
#   lease-renewal-watch  — warns if lease TTL is approaching expiry
#   coordinator-directive-watch — detects coordinator verdicts and instructions

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
LAST[current_job_id]=""
LAST[current_job_type]=""
LAST[last_job_status]=""
LAST[lease_expiry]=""
LAST[coordinator_verdict]=""
LAST[last_revision_count]="0"
LAST[inbox_count]="0"

emit() {
  echo "PRFORGE_EVENT $1"
}

now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

find_context() {
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
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

  return 1
}

is_distributed_worker() {
  [ -f "$DIST_FILE" ] || return 1
  jq -e '.roles // [] | contains(["worker"])' "$DIST_FILE" >/dev/null 2>&1
}

# --- inbox-watch -------------------------------------------------------------
# Detects new jobs and revision jobs written to inbox/ by coordinator.

watch_inbox() {
  if [ -z "$INBOX_DIR" ] || [ ! -d "$INBOX_DIR" ]; then return; fi

  local job_file="$INBOX_DIR/job.json"
  [ -f "$job_file" ] || return

  local job_id job_type job_status
  job_id=$(jq -r '.job.job_id // empty' "$job_file" 2>/dev/null)
  job_type=$(jq -r '.job.type // empty' "$job_file" 2>/dev/null)
  job_status=$(jq -r '.job.status // "assigned"' "$job_file" 2>/dev/null)

  local last_id="${LAST[current_job_id]}"
  local last_status="${LAST[last_job_status]}"

  # New job detected
  if [ -n "$job_id" ] && [ "$job_id" != "$last_id" ]; then
    local mesh_enabled
    mesh_enabled=$(jq -r '.mesh.enabled // false' "$job_file" 2>/dev/null)
    if [ "$mesh_enabled" = "true" ]; then
      emit "mesh_job_assigned job_id=$job_id type=$job_type time=$(now)"
    fi
    LAST[current_job_id]="$job_id"
    LAST[current_job_type]="$job_type"
    LAST[last_job_status]="$job_status"
    return
  fi

  # Status change on existing job
  if [ -n "$job_id" ] && [ "$job_id" = "$last_id" ] && [ "$job_status" != "$last_status" ]; then
    emit "mesh_job_status_changed job_id=$job_id from=$last_status to=$job_status time=$(now)"
    LAST[last_job_status]="$job_status"
  fi

  # Check for revision jobs (coordinator returning job with required_changes)
  local revision_file="$INBOX_DIR/revision.json"
  if [ -f "$revision_file" ]; then
    local rev_cycle changes_count
    rev_cycle=$(jq -r '.revision_count // 0' "$revision_file" 2>/dev/null) || rev_cycle=0
    changes_count=$(jq '.required_changes | length' "$revision_file" 2>/dev/null) || changes_count=0
    local last_rev="${LAST[last_revision_count]}"
    if [ "$rev_cycle" -gt "${last_rev:-0}" ] 2>/dev/null; then
      emit "revision_job_received job_id=$job_id revision_cycle=$rev_cycle changes_required=$changes_count time=$(now)"
      LAST[last_revision_count]="$rev_cycle"
    fi
  fi
}

# --- lease-renewal-watch -----------------------------------------------------
# Warns if the worker's job lease is approaching expiry.

watch_lease() {
  if ! is_distributed_worker; then return; fi
  [ -f "$DIST_FILE" ] || return

  local lease_ttl
  lease_ttl=$(jq -r '.lease_ttl_seconds // 1800' "$DIST_FILE" 2>/dev/null)
  local assigned_at
  assigned_at=$(jq -r '.assigned_at // empty' "$DIST_FILE" 2>/dev/null)
  [ -z "$assigned_at" ] && return

  local now_epoch
  now_epoch=$(date +%s)
  local assigned_epoch
  assigned_epoch=$(date -d "$assigned_at" +%s 2>/dev/null) || return
  local elapsed=$((now_epoch - assigned_epoch))
  local remaining=$((lease_ttl - elapsed))

  local last_remaining="${LAST[lease_expiry]}"

  # Warn at 50%, 25%, 10% remaining
  if [ "$remaining" -le 0 ]; then
    if [ "${last_remaining:-1}" -gt 0 ] 2>/dev/null; then
      emit "lease_expired job_id=${LAST[current_job_id]:-unknown} time=$(now)"
    fi
    LAST[lease_expiry]="0"
  elif [ "$remaining" -le $((lease_ttl / 10)) ]; then
    if [ "${last_remaining:-$lease_ttl}" -gt $((lease_ttl / 10)) ] 2>/dev/null; then
      emit "lease_critical remaining_seconds=$remaining total=$lease_ttl time=$(now)"
    fi
    LAST[lease_expiry]="$remaining"
  elif [ "$remaining" -le $((lease_ttl / 4)) ]; then
    if [ "${last_remaining:-$lease_ttl}" -gt $((lease_ttl / 4)) ] 2>/dev/null; then
      emit "lease_warning remaining_seconds=$remaining total=$lease_ttl time=$(now)"
    fi
    LAST[lease_expiry]="$remaining"
  fi
}

# --- coordinator-directive-watch ---------------------------------------------
# Detects coordinator_verdict.json and audit results written to mesh/ dir.

watch_coordinator_directive() {
  if [ -z "$ARTIFACT_DIR" ]; then return; fi

  local mesh_dir="$ARTIFACT_DIR/mesh"
  [ -d "$mesh_dir" ] || return

  # Check coordinator_verdict.json
  local verdict_file="$mesh_dir/coordinator_verdict.json"
  if [ -f "$verdict_file" ]; then
    local vhash
    vhash=$(sha256sum "$verdict_file" 2>/dev/null | cut -d' ' -f1)
    local last_vhash="${LAST[coordinator_verdict]}"
    if [ "$vhash" != "$last_vhash" ]; then
      local decision
      decision=$(jq -r '.decision // "unknown"' "$verdict_file" 2>/dev/null)
      emit "coordinator_verdict decision=$decision time=$(now)"
      LAST[coordinator_verdict]="$vhash"
    fi
  fi

  # Check auditor_verdict.json
  local auditor_file="$mesh_dir/auditor_verdict.json"
  if [ -f "$auditor_file" ]; then
    local ahash
    ahash=$(sha256sum "$auditor_file" 2>/dev/null | cut -d' ' -f1)
    local last_ahash="${LAST[auditor_verdict]:-}"
    if [ "$ahash" != "$last_ahash" ]; then
      local decision
      decision=$(jq -r '.decision // "unknown"' "$auditor_file" 2>/dev/null)
      emit "auditor_verdict decision=$decision time=$(now)"
      if [ "$decision" = "auditor_fail" ]; then
        local ac_pass rev_instr_count
        ac_pass=$(jq -r '.checks.acceptance_criteria_met.pass // "true"' "$auditor_file" 2>/dev/null)
        rev_instr_count=$(jq '.revision_instructions | length' "$auditor_file" 2>/dev/null) || rev_instr_count=0
        emit "auditor_fail_detail acceptance_criteria_met=$ac_pass revision_instructions=$rev_instr_count time=$(now)"
        if [ "$ac_pass" = "false" ]; then
          local ac_reason
          ac_reason=$(jq -r '.checks.acceptance_criteria_met.reason // "unspecified"' "$auditor_file" 2>/dev/null)
          emit "acceptance_criteria_not_met reason=\"$ac_reason\" time=$(now)"
        fi
      fi
      LAST[auditor_verdict]="$ahash"
    fi
  fi

  # Check manager_verdict.json
  local manager_file="$mesh_dir/manager_verdict.json"
  if [ -f "$manager_file" ]; then
    local mhash
    mhash=$(sha256sum "$manager_file" 2>/dev/null | cut -d' ' -f1)
    local last_mhash="${LAST[manager_verdict]:-}"
    if [ "$mhash" != "$last_mhash" ]; then
      local decision
      decision=$(jq -r '.decision // "unknown"' "$manager_file" 2>/dev/null)
      emit "manager_verdict decision=$decision time=$(now)"
      LAST[manager_verdict]="$mhash"
    fi
  fi
}

# --- Local consistency (inline subset for worker context) ---------------------
# Workers still need local consistency monitoring.

watch_local_consistency() {
  if [ -z "$STATE_FILE" ] || [ ! -f "$STATE_FILE" ]; then return; fi

  local phase
  phase=$(jq -r '.phase // "unknown"' "$STATE_FILE" 2>/dev/null)
  local last_phase="${LAST[local_phase]:-}"
  LAST[local_phase]="$phase"

  [ "$phase" = "$last_phase" ] && return
  [ -z "$last_phase" ] && return

  emit "phase_transition from=$last_phase to=$phase time=$(now)"

  # Phase exit safety checks
  if [ "$last_phase" = "IMPLEMENT" ] && [ "$phase" = "VALIDATE" ]; then
    if [ -n "$REPO_ROOT" ]; then
      local changed
      changed=$(git diff --name-only 2>/dev/null | wc -l)
      local staged
      staged=$(git diff --cached --name-only 2>/dev/null | wc -l)
      if [ "$changed" -eq 0 ] && [ "$staged" -eq 0 ]; then
        emit "phase_exit_blocked phase=IMPLEMENT reason=no_changes_detected time=$(now)"
      fi
    fi
  fi

  if [ "$last_phase" = "PACKAGE" ] && [ "$phase" = "APPROVAL" ]; then
    if [ -n "$ARTIFACT_DIR" ] && [ ! -f "$ARTIFACT_DIR/approval.md" ]; then
      emit "phase_exit_blocked phase=PACKAGE reason=approval.md_missing time=$(now)"
    fi
  fi
}

# --- Main loop ---------------------------------------------------------------

main() {
  if ! find_context 2>/dev/null; then
    return 0
  fi

  # Always run local consistency
  watch_local_consistency

  # Mesh-specific monitors only in distributed worker context
  if is_distributed_worker 2>/dev/null; then
    watch_inbox
    watch_lease
    watch_coordinator_directive
  fi

  return 0
}

INTERVAL="${PRFORGE_WORKER_WATCH_INTERVAL:-15}"
while true; do
  main 2>/dev/null || true
  sleep "$INTERVAL"
done
