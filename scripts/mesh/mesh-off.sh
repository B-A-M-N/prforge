#!/usr/bin/env bash
# PRForge mesh shutdown — stops all daemons and cleans Redis state immediately.
set -euo pipefail

MESH_DIR="$HOME/.prforge-mesh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Collect node IDs of workers we're about to kill, then stop them
DEAD_NODES=()
for pf in "$MESH_DIR"/worker-slot*.pid "$MESH_DIR"/worker-[0-9]*.pid; do
  [ -f "$pf" ] || continue
  PID=$(cat "$pf")
  # Try to find the node_id from the matching log
  LOG=$(ls "$MESH_DIR/logs/"*"${PID}"*.log 2>/dev/null | head -1 || true)
  if [ -n "$LOG" ]; then
    NID=$(grep -m1 "worker node_id:" "$LOG" 2>/dev/null | awk '{print $NF}' || true)
    [ -n "$NID" ] && DEAD_NODES+=("$NID")
  fi
  if kill "$PID" 2>/dev/null; then
    echo "Stopped worker PID $PID ($pf)"
  else
    echo "Already gone: PID $PID ($pf)"
  fi
  rm -f "$pf"
done

# Stop coordinator if present
for pf in "$MESH_DIR"/coordinator.pid; do
  [ -f "$pf" ] || continue
  PID=$(cat "$pf")
  if kill "$PID" 2>/dev/null; then echo "Stopped coordinator PID $PID"; fi
  rm -f "$pf"
done

# Clean per-slot state files
rm -f "$MESH_DIR"/my-node-id-* "$MESH_DIR"/my-node-id "$MESH_DIR"/worker-slot*.env

# Immediately reset Redis state for dead workers — don't wait for TTL expiry
if [ ${#DEAD_NODES[@]} -gt 0 ]; then
  python3 - "${DEAD_NODES[@]}" <<'PYEOF'
import redis, json, sys

mesh_dir = __import__('os').path.expanduser("~/.prforge-mesh")
dead_set = set(sys.argv[1:])
if not dead_set:
    sys.exit(0)

try:
    cfg = json.load(open(f"{mesh_dir}/config.json"))
    r = redis.Redis.from_url(cfg["mesh"]["redis_url"], decode_responses=True, socket_connect_timeout=3)
    cluster = cfg["mesh"]["cluster_name"]
except Exception as e:
    print(f"Redis cleanup skipped: {e}", file=sys.stderr)
    sys.exit(0)

reset = []
for k in r.keys(f"Workflow:{cluster}:job:*"):
    job = r.hgetall(k)
    if job.get("status") in ("assigned", "active") and job.get("assigned_node") in dead_set:
        r.hset(k, mapping={"status": "queued"})
        r.hdel(k, "assigned_node", "lease_id", "assigned_at")
        reset.append(job.get("job_id", k))

for nid in dead_set:
    r.delete(f"Workflow:{cluster}:node:{nid}")
    r.srem(f"Workflow:{cluster}:nodes", nid)

for k in r.keys(f"Workflow:{cluster}:lease:*"):
    val = r.get(k)
    if val:
        try:
            d = json.loads(val)
            if d.get("worker_id") in dead_set or d.get("node_id") in dead_set:
                r.delete(k)
        except Exception:
            pass

print(f"Reset {len(reset)} jobs to queued")
print(f"Cleaned Redis state for nodes: {list(dead_set)}")
PYEOF
fi

echo "Done."
