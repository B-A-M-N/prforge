#!/usr/bin/env bash
# mesh-watch.sh — unified PRForge mesh monitor.
#
# Silently tracks node state, queue state, and time. Emits ONLY when:
#   WORKER:      assigned job is new AND node has been idle+completed >= 3 minutes
#   COORDINATOR: submission_ready job exists AND coordinator has been idle >= 3 minutes
#
# "Idle" means: Redis node status=idle AND active_job is empty or last job=completed.
# "Idle+stuck" (active_job present but incomplete, status=idle) → never emits.
# Re-emits are suppressed: the same job_id is only emitted once per session.
#
# Role is determined by ~/.prforge-mesh/my-node-id:
#   exists → worker mode (watches that specific node)
#   absent → coordinator mode (watches for submission_ready jobs)

set -euo pipefail

MESH_DIR="$HOME/.prforge-mesh"
LOCK_FILE="$MESH_DIR/mesh-watch.lock"
STATE_DIR="$MESH_DIR/monitor-state"
mkdir -p "$STATE_DIR"

# Single-instance guard
exec 9>"$LOCK_FILE"
flock -n 9 2>/dev/null || exit 0
trap 'rm -f "$LOCK_FILE"; exit 0' INT TERM EXIT

IDLE_THRESHOLD=180   # 3 minutes
INTERVAL="${MESH_WATCH_INTERVAL:-15}"
EMITTED_FILE="$STATE_DIR/emitted-jobs"
IDLE_SINCE_FILE="$STATE_DIR/idle-since"
touch "$EMITTED_FILE"

emit() { echo "$*"; }

py() { python3 -c "$@" 2>/dev/null; }

already_emitted() {
  grep -qxF "$1" "$EMITTED_FILE" 2>/dev/null
}

mark_emitted() {
  echo "$1" >> "$EMITTED_FILE"
}

idle_seconds() {
  # Returns how many seconds this node has been continuously idle.
  # Resets the idle-since timestamp when node becomes active.
  local status="$1" active_job="$2" last_job_status="$3"

  local is_idle=false
  if [ "$status" = "idle" ] && [ -z "$active_job" ]; then
    is_idle=true
  elif [ "$status" = "idle" ] && [ -n "$active_job" ] && [ "$last_job_status" = "completed" ]; then
    is_idle=true
  fi

  if [ "$is_idle" = "false" ]; then
    # Active or stuck — reset idle timer
    rm -f "$IDLE_SINCE_FILE"
    echo "0"
    return
  fi

  if [ ! -f "$IDLE_SINCE_FILE" ]; then
    date +%s > "$IDLE_SINCE_FILE"
    echo "0"
    return
  fi

  local since now
  since=$(cat "$IDLE_SINCE_FILE")
  now=$(date +%s)
  echo $((now - since))
}

get_config() {
  [ -f "$MESH_DIR/config.json" ] || return 1
  REDIS_URL=$(py "import json; print(json.load(open('$MESH_DIR/config.json'))['mesh']['redis_url'])")
  CLUSTER=$(py "import json; print(json.load(open('$MESH_DIR/config.json'))['mesh']['cluster_name'])")
}

redis_node() {
  local node_id="$1"
  py "
import redis, json
r = redis.Redis.from_url('$REDIS_URL', decode_responses=True, socket_connect_timeout=2)
d = r.hgetall('Workflow:${CLUSTER}:node:$node_id')
print(json.dumps(d))
"
}

redis_job() {
  local job_id="$1"
  py "
import redis, json
r = redis.Redis.from_url('$REDIS_URL', decode_responses=True, socket_connect_timeout=2)
d = r.hgetall('Workflow:${CLUSTER}:job:$job_id')
print(json.dumps(d))
"
}

redis_submission_ready() {
  py "
import redis, json
r = redis.Redis.from_url('$REDIS_URL', decode_responses=True, socket_connect_timeout=2)
keys = r.keys('Workflow:${CLUSTER}:job:*')
ready = [r.hgetall(k) for k in keys if r.hgetall(k).get('status') == 'submission_ready']
print(json.dumps(ready))
"
}

# ── Worker mode ──────────────────────────────────────────────────────────────

watch_worker() {
  local node_id="$1"

  local node_json status active_job
  node_json=$(redis_node "$node_id") || return
  [ "$node_json" = "{}" ] && return  # not registered yet

  status=$(py "import json,sys; print(json.loads('''$node_json''').get('status',''))")
  active_job=$(py "import json,sys; print(json.loads('''$node_json''').get('active_job',''))")

  # Determine last job completion status
  local last_job_status=""
  if [ -n "$active_job" ]; then
    local job_json
    job_json=$(redis_job "$active_job") || true
    last_job_status=$(py "import json,sys; print(json.loads('''$job_json''').get('status',''))" 2>/dev/null || echo "")
  fi

  local secs
  secs=$(idle_seconds "$status" "$active_job" "$last_job_status")

  # Guard: only emit if idle for >= threshold
  [ "$secs" -lt "$IDLE_THRESHOLD" ] && return

  # Guard: must have no unfinished job (either no job, or last job completed)
  if [ -n "$active_job" ] && [ "$last_job_status" != "completed" ]; then
    return  # idle but stuck — do not emit
  fi

  # Now look for a NEW assigned job
  # Re-read node in case assignment just happened
  node_json=$(redis_node "$node_id") || return
  active_job=$(py "import json,sys; print(json.loads('''$node_json''').get('active_job',''))")
  status=$(py "import json,sys; print(json.loads('''$node_json''').get('status',''))")

  [ -z "$active_job" ] && return  # still nothing assigned
  [ "$status" = "active" ] && return  # already working on it

  already_emitted "$active_job" && return

  # Fetch job details
  local job_json
  job_json=$(redis_job "$active_job") || return
  [ "$job_json" = "{}" ] && return

  local job_type repo pr_num head_branch base_branch source_url peer_review primary_job_id
  job_type=$(py "import json; print(json.loads('''$job_json''').get('type',''))")
  repo=$(py "import json; print(json.loads('''$job_json''').get('repo',''))")
  pr_num=$(py "import json; print(json.loads('''$job_json''').get('pr_number',''))")
  head_branch=$(py "import json; print(json.loads('''$job_json''').get('head_branch',''))")
  base_branch=$(py "import json; print(json.loads('''$job_json''').get('base_branch','main'))")
  source_url=$(py "import json; print(json.loads('''$job_json''').get('source_url',''))")
  peer_review=$(py "import json; print(json.loads('''$job_json''').get('peer_review','false'))")

  mark_emitted "$active_job"

  if [ "$peer_review" = "true" ]; then
    primary_job_id=$(py "import json; print(json.loads('''$job_json''').get('primary_job_id',''))")
    emit "=== PRFORGE MESH: PEER REVIEW JOB ASSIGNED ==="
    emit "You are the reviewer for this PR. The primary worker has completed their work."
    emit ""
    emit "Job ID:      $active_job"
    emit "Repo:        $repo"
    emit "PR:          #${pr_num}  ${head_branch} → ${base_branch}"
    emit "URL:         $source_url"
    emit "Primary job: $primary_job_id"
    emit ""
    emit "Review the primary worker's output thoroughly. Check that all acceptance criteria"
    emit "are met, nothing was overlooked, and the implementation is complete."
    emit "Run /prforge:pr-continue to load the review context and begin."
  else
    emit "=== PRFORGE MESH: JOB ASSIGNED ==="
    emit ""
    emit "Job ID:  $active_job"
    emit "Type:    $job_type"
    emit "Repo:    $repo"
    emit "PR:      #${pr_num}  ${head_branch} → ${base_branch}"
    emit "URL:     $source_url"
    emit ""
    emit "Run /prforge:pr-continue to begin working this PR."
    emit "When complete, a peer reviewer will audit your work before coordinator approval."
  fi
}

# ── Coordinator mode ─────────────────────────────────────────────────────────

watch_coordinator() {
  # Coordinator idle state: track separately using a dedicated idle-since file
  local coord_idle_file="$STATE_DIR/coord-idle-since"
  local coord_active_file="$STATE_DIR/coord-active"

  # Check if coordinator instance is currently running a workflow
  # Heuristic: look for a recent state.json modification or active prforge process
  local coord_busy=false
  if [ -f "$coord_active_file" ]; then
    local active_since now elapsed
    active_since=$(cat "$coord_active_file" 2>/dev/null || echo 0)
    now=$(date +%s)
    elapsed=$((now - active_since))
    # Consider coordinator busy if marked active in last 5 minutes
    [ "$elapsed" -lt 300 ] && coord_busy=true
  fi

  if [ "$coord_busy" = "true" ]; then
    return
  fi

  # Track idle time
  if [ ! -f "$coord_idle_file" ]; then
    date +%s > "$coord_idle_file"
    return
  fi
  local since now secs
  since=$(cat "$coord_idle_file")
  now=$(date +%s)
  secs=$((now - since))
  [ "$secs" -lt "$IDLE_THRESHOLD" ] && return

  # Look for submission_ready jobs
  local ready_json
  ready_json=$(redis_submission_ready) || return
  local count
  count=$(py "import json; print(len(json.loads('''$ready_json''')))")
  [ "$count" = "0" ] && return

  # Emit one directive per new submission_ready job
  py "
import json, sys
jobs = json.loads('''$ready_json''')
emitted = open('$EMITTED_FILE').read().splitlines()
for j in jobs:
    jid = j.get('job_id','?')
    if jid in emitted:
        continue
    repo = j.get('repo','?')
    pr = j.get('pr_number','?')
    branch = j.get('head_branch','?')
    url = j.get('source_url','?')
    print('=== PRFORGE MESH: COORDINATOR APPROVAL NEEDED ===')
    print('')
    print(f'Job ID:  {jid}')
    print(f'Repo:    {repo}  PR #{pr}  branch {branch}')
    print(f'URL:     {url}')
    print('')
    print('Worker 1 completed. Worker 2 peer review passed.')
    print('Run /prforge:pr-approve to review the output and give final approval or rejection.')
    print(jid)  # last line written to emitted file below
" | while IFS= read -r line; do
    case "$line" in
      job_*) mark_emitted "$line" ;;
      *) emit "$line" ;;
    esac
  done
}

# ── Main loop ────────────────────────────────────────────────────────────────

main() {
  get_config 2>/dev/null || return

  if [ -f "$MESH_DIR/my-node-id" ]; then
    local node_id
    node_id=$(cat "$MESH_DIR/my-node-id")
    watch_worker "$node_id"
  else
    watch_coordinator
  fi
}

while true; do
  main 2>/dev/null || true
  sleep "$INTERVAL"
done
