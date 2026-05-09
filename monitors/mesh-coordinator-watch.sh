#!/usr/bin/env bash
# mesh-coordinator-watch.sh — watches Redis for jobs needing coordinator final approval.
# Emits a directive only when a submission_ready job arrives.
set -euo pipefail

MESH_DIR="$HOME/.prforge-mesh"
LOCK_FILE="$MESH_DIR/mesh-coordinator-watch.lock"

exec 9>"$LOCK_FILE"
flock -n 9 2>/dev/null || exit 0
trap 'rm -f "$LOCK_FILE"; exit 0' INT TERM EXIT

emit() { echo "$*"; }

INTERVAL="${MESH_COORDINATOR_WATCH_INTERVAL:-10}"
LAST_SEEN=""

while true; do
  sleep "$INTERVAL"

  CFG="$MESH_DIR/config.json"
  [ -f "$CFG" ] || continue

  REDIS_URL=$(python3 -c "import json; print(json.load(open('$CFG'))['mesh']['redis_url'])" 2>/dev/null) || continue
  CLUSTER=$(python3 -c "import json; print(json.load(open('$CFG'))['mesh']['cluster_name'])" 2>/dev/null) || continue

  # Find jobs with status=submission_ready
  READY=$(python3 -c "
import redis, json
r = redis.Redis.from_url('$REDIS_URL', decode_responses=True, socket_connect_timeout=2)
cluster = '$CLUSTER'
keys = r.keys(f'Workflow:{cluster}:job:*')
ready = []
for k in keys:
    j = r.hgetall(k)
    if j.get('status') == 'submission_ready':
        ready.append(j)
print(json.dumps(ready))
" 2>/dev/null) || continue

  COUNT=$(python3 -c "import json,sys; print(len(json.loads(sys.stdin.read())))" <<< "$READY")
  [ "$COUNT" = "0" ] && continue

  # Emit one directive per new submission_ready job
  python3 -c "
import json, sys
jobs = json.loads(sys.stdin.read())
for j in jobs:
    jid = j.get('job_id','?')
    repo = j.get('repo','?')
    pr = j.get('pr_number','?')
    branch = j.get('head_branch','?')
    url = j.get('source_url','?')
    print(f'COORDINATOR APPROVAL NEEDED')
    print(f'')
    print(f'Job ID:  {jid}')
    print(f'Repo:    {repo}  PR #{pr}  branch {branch}')
    print(f'URL:     {url}')
    print(f'')
    print(f'Worker 1 completed. Worker 2 peer review passed.')
    print(f'Run /prforge:pr-approve to review the output and give final approval or rejection.')
" <<< "$READY" 2>/dev/null || continue

done
