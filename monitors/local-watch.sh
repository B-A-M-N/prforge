#!/usr/bin/env bash
# PRForge Local Watch Monitor — Consistency Sentinel
#
# NOT an auto-dispatcher. NOT a job finder. NOT a background PR loop.
#
# Purpose: keep the active PRForge session honest against repo/state reality.
# The monitor is a consistency layer that surfaces contradictions between
# what the session claims and what the repo actually shows.
#
# Runs as a persistent background monitor for the lifetime of the session.
# Each stdout line is delivered to Claude as a PRFORGE_EVENT notification.
#
# Self-gates: runs in both local mode and inside distributed workers.
# Same binary, different output surface. In local mode, events go to the
# solo session. In distributed worker mode, events go to the worker session.
#
# Monitors:
#   local-state-consistency-watch
#   local-diff-fingerprint-watch
#   local-evidence-watch
#   local-approval-integrity-watch
#   local-phase-contract-watch
#   local-review-context-watch

set -euo pipefail

MONITOR_NAME="prforge-local-watch"
PID_DIR="${PRFORGE_MONITOR_PID_DIR:-$HOME/.prforge/monitors}"
PID_FILE="$PID_DIR/$MONITOR_NAME.pid"
LOCK_FILE="$PID_DIR/$MONITOR_NAME.lock"

setup_monitor_lifecycle() {
  mkdir -p "$PID_DIR" 2>/dev/null || true
  exec 9>"$LOCK_FILE"
  if command -v flock >/dev/null 2>&1; then
    if ! flock -n 9; then
      exit 0
    fi
  elif [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
    exit 0
  fi
  echo "$$" > "$PID_FILE" 2>/dev/null || true
  trap 'rm -f "$PID_FILE"; exit 0' INT TERM EXIT
}

# --- Resolve paths -----------------------------------------------------------
REPO_ROOT=""
ARTIFACT_DIR=""
STATE_FILE=""
INBOX_DIR=""
OUTBOX_DIR=""
DIST_FILE=""

# --- State tracking (in-memory, per monitor lifetime) ------------------------
declare -A LAST
LAST[head_sha]=""
LAST[diff_fingerprint]=""
LAST[dirty_count]="0"
LAST[phase]=""
LAST[approval_hash]=""
LAST[contract_hash]=""
LAST[dod_hash]=""
LAST[validation_hash]=""
LAST[state_json_hash]=""
LAST[evidence_count]="0"
LAST[review_cursor]=""
LAST[untracked_count]="0"
LAST[unclassified_count]="0"
LAST[upstream_behind]="0"

# --- Helpers -----------------------------------------------------------------

emit() {
  echo "PRFORGE_EVENT $1"
}

now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

find_context() {
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
  DIST_FILE="$REPO_ROOT/.prforge/distributed.json"

  # Check .prforge-run pointer
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

  # Fallback legacy
  if [ -d "$REPO_ROOT/.prforge" ]; then
    ARTIFACT_DIR="$REPO_ROOT/.prforge"
    STATE_FILE="$ARTIFACT_DIR/state.json"
    INBOX_DIR="$ARTIFACT_DIR/inbox"
    OUTBOX_DIR="$ARTIFACT_DIR/outbox"
    return 0
  fi

  return 1
}

sha() {
  local f="$1"
  if [ -f "$f" ]; then
    sha256sum "$f" 2>/dev/null | cut -d' ' -f1
  else
    echo "absent"
  fi
}

# --- local-state-consistency-watch -------------------------------------------
# Detects when state.json no longer matches the actual repo state.

watch_state_consistency() {
  if [ -z "$STATE_FILE" ] || [ ! -f "$STATE_FILE" ]; then return; fi

  local current_hash
  current_hash=$(sha "$STATE_FILE")
  local last="${LAST[state_json_hash]}"
  LAST[state_json_hash]="$current_hash"
  [ -z "$last" ] && return

  if [ "$current_hash" = "$last" ]; then return; fi

  # State changed — check for contradictions
  local reported_phase
  reported_phase=$(jq -r '.phase // "unknown"' "$STATE_FILE" 2>/dev/null)

  # Phase claims vs evidence existence
  case "$reported_phase" in
    VALIDATE|SELF_REVIEW|PACKAGE|APPROVAL|SHIPPED)
      if [ -n "$ARTIFACT_DIR" ]; then
        if [ ! -f "$ARTIFACT_DIR/validation_ledger.md" ]; then
          emit "evidence_missing phase=$reported_phase required=validation_ledger.md file=validation_ledger.md"
        fi
      fi
      ;;
  esac

  case "$reported_phase" in
    PACKAGE|APPROVAL|SHIPPED)
      if [ -n "$ARTIFACT_DIR" ]; then
        if [ ! -f "$ARTIFACT_DIR/dod.md" ]; then
          emit "evidence_missing phase=$reported_phase required=dod.md file=dod.md"
        fi
        if [ ! -f "$ARTIFACT_DIR/hostile_review.md" ]; then
          emit "evidence_missing phase=$reported_phase required=hostile_review.md file=hostile_review.md"
        fi
      fi
      ;;
  esac

  case "$reported_phase" in
    APPROVAL|SHIPPED)
      if [ -n "$ARTIFACT_DIR" ]; then
        if [ ! -f "$ARTIFACT_DIR/approval.md" ]; then
          emit "evidence_missing phase=$reported_phase required=approval.md file=approval.md"
        fi
      fi
      ;;
  esac
}

# --- local-diff-fingerprint-watch -------------------------------------------
# Detects when working tree or staged changes differ from last known fingerprint.

watch_diff_fingerprint() {
  if [ -z "$REPO_ROOT" ]; then return; fi

  local fp
  fp=$(git status --porcelain 2>/dev/null | sha256sum | cut -d' ' -f1)
  # Include staged diff
  local staged
  staged=$(git diff --cached --stat 2>/dev/null | sha256sum | cut -d' ' -f1)
  fp="$fp$staged"

  local last="${LAST[diff_fingerprint]}"
  LAST[diff_fingerprint]="$fp"
  [ -z "$last" ] && return

  if [ "$fp" != "$last" ]; then
    local changed
    changed=$(git diff --cached --name-only 2>/dev/null | wc -l)
    local unstaged
    unstaged=$(git diff --name-only 2>/dev/null | wc -l)
    local total=$((changed + unstaged))
    local phase="${LAST[phase]:-unknown}"
    emit "diff_changed files=$total staged=$changed unstaged=$unstaged phase=$phase since_last_state_report=true time=$(now)"
  fi
}

# --- local-evidence-watch ---------------------------------------------------
# Detects missing or stale artifacts required by the current phase.

watch_evidence() {
  if [ -z "$ARTIFACT_DIR" ]; then return; fi

  local phase="${LAST[phase]:-unknown}"

  # Count evidence files
  local count=0
  for f in contract.md dod.md validation_ledger.md hostile_review.md approval.md pr_body.md; do
    [ -f "$ARTIFACT_DIR/$f" ] && count=$((count + 1))
  done

  local last_count="${LAST[evidence_count]}"
  LAST[evidence_count]="$count"

  # Check stale validation
  if [ "$phase" = "VALIDATE" ] && [ -n "$last_count" ] && [ "$count" -eq "$last_count" ]; then
    if [ -f "$ARTIFACT_DIR/state.json" ]; then
      local state_age
      state_age=$(($(date +%s) - $(stat -c %Y "$ARTIFACT_DIR/state.json" 2>/dev/null || echo 0)))
      # If state hasn't changed in 5+ minutes during VALIDATE, possible stall
      if [ "$state_age" -gt 300 ]; then
        emit "phase_stalled phase=VALIDATE state_age_seconds=$state_age time=$(now)"
      fi
    fi
  fi

  # Detect missing validation ledger
  if [ "$phase" = "VALIDATE" ] && [ ! -f "$ARTIFACT_DIR/validation_ledger.md" ]; then
    # Only warn once per state
    local marker="${LAST[validation_warning]:-}"
    if [ -z "$marker" ]; then
      emit "evidence_missing phase=VALIDATE required=validation_ledger.md reason=no_file_detected"
      LAST[validation_warning]="sent"
    fi
  fi
}

# --- local-approval-integrity-watch -----------------------------------------
# Detects when diff changes after approval.md was already written.

watch_approval_integrity() {
  if [ -z "$ARTIFACT_DIR" ]; then return; fi

  local approval="$ARTIFACT_DIR/approval.md"
  [ -f "$approval" ] || return

  local current_hash
  current_hash=$(sha "$approval")
  local last="${LAST[approval_hash]}"
  LAST[approval_hash]="$current_hash"
  [ -z "$last" ] && return

  if [ "$current_hash" != "$last" ]; then
    local aid
    aid=$(echo "$current_hash" | cut -c1-8)
    emit "approval_modified approval_id=$aid require_revalidation=true time=$(now)"
  fi

  # Check if diff changed SINCE approval was written
  if [ -n "$REPO_ROOT" ]; then
    local approval_mtime
    approval_mtime=$(stat -c %Y "$approval" 2>/dev/null || echo 0)
    local latest_file_mtime
    latest_file_mtime=$(find "$REPO_ROOT" -maxdepth 3 -newer "$approval" -not -path "*/.git/*" -not -path "*/.prforge*" 2>/dev/null | head -1 | xargs stat -c %Y 2>/dev/null || echo 0)

    if [ "$latest_file_mtime" -gt "$approval_mtime" ] 2>/dev/null; then
      emit "approval_stale approval_id=$(echo "$current_hash" | cut -c1-8) reason=diff_changed_after_approval_draft time=$(now)"
    fi
  fi
}

# --- local-phase-contract-watch ---------------------------------------------
# Detects phase-transition safety: is it safe to advance?

watch_phase_contract() {
  if [ -z "$STATE_FILE" ] || [ ! -f "$STATE_FILE" ]; then return; fi

  local phase
  phase=$(jq -r '.phase // "unknown"' "$STATE_FILE" 2>/dev/null)
  local last_phase="${LAST[phase]}"
  LAST[phase]="$phase"

  [ "$phase" = "$last_phase" ] && return
  [ -z "$last_phase" ] && return

  # Phase just changed — check safety
  emit "phase_transition from=$last_phase to=$phase time=$(now)"

  # Validate: IMPLEMENT -> VALIDATE should have changed files
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

  # Validate: PACKAGE -> APPROVAL should have approval.md
  if [ "$last_phase" = "PACKAGE" ] && [ "$phase" = "APPROVAL" ]; then
    if [ -n "$ARTIFACT_DIR" ] && [ ! -f "$ARTIFACT_DIR/approval.md" ]; then
      emit "phase_exit_blocked phase=PACKAGE reason=approval.md_missing time=$(now)"
    fi
  fi
}

# --- local-review-context-watch ----------------------------------------------
# Surfaces reviewer comments relevant to current file/line context.

watch_review_context() {
  if [ -z "$REPO_ROOT" ] || ! command -v gh &>/dev/null; then return; fi

  # Try to find PR number
  local pr_number=""
  if [ -f "$STATE_FILE" ]; then
    pr_number=$(jq -r '.pr_number // empty' "$STATE_FILE" 2>/dev/null)
  fi
  [ -z "$pr_number" ] && return

  local repo
  repo=$(timeout 5s gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null) || return
  local me
  me=$(timeout 5s gh api user -q '.login' 2>/dev/null) || return

  # Fetch only latest review cursor (lightweight)
  local reviews_json
  reviews_json=$(timeout 5s gh pr view "$pr_number" --repo "$repo" --json reviews 2>/dev/null) || return

  local latest_ts
  latest_ts=$(echo "$reviews_json" | jq -r '
    [.reviews[] | select(.author.login != "'"$me'"' and .author.login != "") |
     .submittedAt] | max // ""
  ' 2>/dev/null)

  local last_ts="${LAST[review_cursor]}"
  LAST[review_cursor]="$latest_ts"
  [ -z "$last_ts" ] && { [ -n "$latest_ts" ] && emit "review_context_initialized pr=$pr_number cursor=${latest_ts:0:10} time=$(now)"; return; }

  if [ -n "$latest_ts" ] && [ "$latest_ts" != "$last_ts" ]; then
    emit "review_update pr=$pr_number new_since=${last_ts:0:10} cursor=${latest_ts:0:10} time=$(now)"
  fi
}

# --- Untracked files check ---------------------------------------------------

watch_untracked() {
  if [ -z "$REPO_ROOT" ]; then return; fi

  local count
  count=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l)
  local last="${LAST[untracked_count]}"
  LAST[untracked_count]="$count"

  if [ -n "$last" ] && [ "$count" -gt "$last" ]; then
    emit "untracked_files count=$count new=$((count - last)) requires_classification=true time=$(now)"
  fi
}

# --- Branch mismatch check ---------------------------------------------------

watch_branch_mismatch() {
  if [ -z "$STATE_FILE" ] || [ ! -f "$STATE_FILE" ] || [ -z "$REPO_ROOT" ]; then return; fi

  local expected_branch
  expected_branch=$(jq -r '.head_branch // .branch // empty' "$STATE_FILE" 2>/dev/null)
  [ -z "$expected_branch" ] && return

  local actual_branch
  actual_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || return

  if [ "$expected_branch" != "$actual_branch" ]; then
    local marker="${LAST[branch_mismatch]:-}"
    if [ "$marker" != "$expected_branch:$actual_branch" ]; then
      emit "branch_mismatch expected=$expected_branch actual=$actual_branch time=$(now)"
      LAST[branch_mismatch]="$expected_branch:$actual_branch"
    fi
  fi
}

# --- Main loop ---------------------------------------------------------------

main() {
  if ! find_context 2>/dev/null; then
    return 0
  fi

  watch_state_consistency
  watch_diff_fingerprint
  watch_evidence
  watch_approval_integrity
  watch_phase_contract
  watch_review_context
  watch_untracked
  watch_branch_mismatch

  return 0
}

setup_monitor_lifecycle
INTERVAL="${PRFORGE_LOCAL_WATCH_INTERVAL:-30}"
if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]]; then INTERVAL=30; fi
[ "$INTERVAL" -lt 10 ] && INTERVAL=10
while true; do
  main 2>/dev/null || true
  [ "${PRFORGE_MONITOR_ONCE:-}" = "1" ] && break
  sleep "$INTERVAL"
done
