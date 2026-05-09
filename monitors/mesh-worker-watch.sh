#!/usr/bin/env bash
# mesh-worker-watch.sh — watches Redis for jobs assigned to THIS worker instance.
# Emits a directive only when a new job appears and the instance is not mid-task.
# Runs continuously; self-terminates if no worker daemon is registered.
set -euo pipefail

MESH_DIR="$HOME/.prforge-mesh"
NODE_ID_FILE="$MESH_DIR/my-node-id"
LOCK_FILE="$MESH_DIR/mesh-worker-watch.lock"

# Single-instance guard
exec 9>"$LOCK_FILE"
flock -n 9 2>/dev/null || exit 0
trap 'rm -f "$LOCK_FILE"; exit 0' INT TERM EXIT

emit() { echo "$*"; }

get_node_id() {
  [ -f "$NODE_ID_FILE" ] || return 1
  cat "$NODE_ID_FILE"
}

redis_hget() {
  local url="$1" key="$2" field="$3"
  python3 -c "
import redis, sys
r = redis.Redis.from_url('$url', decode_responses=True, socket_connect_timeout=2)
v = r.hget('$key', '$field')
print(v or '')
" 2>/dev/null
}

redis_hgetall() {
  local url="$1" key="$2"
  python3 -c "
import redis, json, sys
r = redis.Redis.from_url('$url', decode_responses=True, socket_connect_timeout=2)
print(json.dumps(r.hgetall('$key')))
" 2>/dev/null
}

LAST_JOB_ID=""
INTERVAL="${MESH_WORKER_WATCH_INTERVAL:-10}"

while true; do
  sleep "$INTERVAL"

  # Get this instance's node_id
  NODE_ID=$(get_node_id 2>/dev/null) || continue
  [ -z "$NODE_ID" ] && continue

  # Get mesh config
  CFG="$MESH_DIR/config.json"
  [ -f "$CFG" ] || continue
  REDIS_URL=$(python3 -c "import json; print(json.load(open('$CFG'))['mesh']['redis_url'])" 2>/dev/null) || continue
  CLUSTER=$(python3 -c "import json; print(json.load(open('$CFG'))['mesh']['cluster_name'])" 2>/dev/null) || continue

  # Read node state from Redis
  NODE_JSON=$(redis_hgetall "$REDIS_URL" "Workflow:${CLUSTER}:node:${NODE_ID}") || continue
  [ "$NODE_JSON" = "{}" ] && continue

  ACTIVE_JOB=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('active_job',''))" <<< "$NODE_JSON" 2>/dev/null) || continue
  NODE_STATUS=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('status',''))" <<< "$NODE_JSON" 2>/dev/null) || continue

  # No job assigned
  [ -z "$ACTIVE_JOB" ] && { LAST_JOB_ID=""; continue; }

  # Already handled this job
  [ "$ACTIVE_JOB" = "$LAST_JOB_ID" ] && continue

  # Fetch job details
  JOB_JSON=$(redis_hgetall "$REDIS_URL" "Workflow:${CLUSTER}:job:${ACTIVE_JOB}") || continue
  [ "$JOB_JSON" = "{}" ] && continue

  JOB_TYPE=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('type',''))" <<< "$JOB_JSON")
  REPO=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('repo',''))" <<< "$JOB_JSON")
  PR_NUM=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('pr_number',''))" <<< "$JOB_JSON")
  HEAD_BRANCH=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('head_branch',''))" <<< "$JOB_JSON")
  BASE_BRANCH=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('base_branch','main'))" <<< "$JOB_JSON")
  SOURCE_URL=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('source_url',''))" <<< "$JOB_JSON")
  PEER_REVIEW=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('peer_review','false'))" <<< "$JOB_JSON")

  LAST_JOB_ID="$ACTIVE_JOB"

  if [ "$PEER_REVIEW" = "true" ]; then
    # Reviewer role
    PRIMARY_JOB=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('primary_job_id',''))" <<< "$JOB_JSON")
    emit "You have been assigned a PEER REVIEW job."
    emit ""
    emit "Job ID:      $ACTIVE_JOB"
    emit "Reviewing:   $REPO PR #${PR_NUM} — branch ${HEAD_BRANCH}"
    emit "Primary job: $PRIMARY_JOB"
    emit "PR URL:      $SOURCE_URL"
    emit ""
    emit "Run /prforge:pr-continue to load the review context and begin your review."
    emit "Your verdict will be submitted to the coordinator for final approval."
  else
    # Primary worker role
    emit "You have been assigned a job."
    emit ""
    emit "Job ID:   $ACTIVE_JOB"
    emit "Type:     $JOB_TYPE"
    emit "Repo:     $REPO"
    emit "PR:       #${PR_NUM} — ${HEAD_BRANCH} → ${BASE_BRANCH}"
    emit "URL:      $SOURCE_URL"
    emit ""
    emit "Run /prforge:pr-continue to begin working this PR."
    emit "When complete, your output goes to the reviewer before coordinator approval."
  fi
done
