#!/usr/bin/env bash
# PRForge mesh worker startup — runs everything, no AI interpretation needed.
# Usage: bash start-worker.sh
set -euo pipefail

MESH_DIR="$HOME/.prforge-mesh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── prereqs ──────────────────────────────────────────────────────────────────
if [ ! -f "$MESH_DIR/config.json" ]; then
  echo "ERROR: Coordinator config not found. Run start-coordinator.sh first."
  exit 1
fi
python3 -c "import redis" 2>/dev/null || { echo "ERROR: redis-py missing. Run: pip install redis>=4.6.0"; exit 1; }

# ── write worker template (all workers share this; daemon generates its own UUID) ──
REDIS_URL=$(python3 -c "import json; print(json.load(open('$MESH_DIR/config.json'))['mesh']['redis_url'])")
CLUSTER=$(python3 -c "import json; print(json.load(open('$MESH_DIR/config.json'))['mesh']['cluster_name'])")

cat > "$MESH_DIR/worker-template.json" <<EOF
{
  "mesh": {
    "cluster_name": "${CLUSTER}",
    "node_id": "auto",
    "roles": ["worker"],
    "redis_url": "${REDIS_URL}"
  },
  "worker": {
    "repo_roots": ["${HOME}"],
    "capacity": 1
  },
  "limits": {
    "lease_ttl_seconds": 1800,
    "heartbeat_interval_seconds": 15
  },
  "notifications": {"desktop": false, "pubsub": true}
}
EOF

# ── enforce max 2 workers ────────────────────────────────────────────────────
MAX_WORKERS=2
live=0
for pf in "$MESH_DIR"/worker-*.pid; do
  [ -f "$pf" ] || continue
  pid=$(cat "$pf")
  if kill -0 "$pid" 2>/dev/null; then
    live=$((live + 1))
  else
    rm -f "$pf"  # clean up stale pid file
  fi
done
if [ "$live" -ge "$MAX_WORKERS" ]; then
  echo "ERROR: already $live worker(s) running (max $MAX_WORKERS). Run mesh-off.sh first."
  exit 1
fi

# ── start daemon ─────────────────────────────────────────────────────────────
mkdir -p "$MESH_DIR/logs"
cd "$SCRIPT_DIR"

nohup python3 prforge_mesh.py --config "$MESH_DIR/worker-template.json" worker \
  > "$MESH_DIR/logs/worker-$$.log" 2>&1 &
DAEMON_PID=$!

# Move log to PID-named file now that we have the daemon PID
mv "$MESH_DIR/logs/worker-$$.log" "$MESH_DIR/logs/worker-$DAEMON_PID.log" 2>/dev/null || true
echo "$DAEMON_PID" > "$MESH_DIR/worker-$DAEMON_PID.pid"
sleep 2

if kill -0 "$DAEMON_PID" 2>/dev/null; then
  WORKER_ID=$(grep -m1 "worker node_id:" "$MESH_DIR/logs/worker-$DAEMON_PID.log" 2>/dev/null | awk '{print $NF}' || echo "<see log>")
  echo "✓ worker started  PID=$DAEMON_PID  node_id=$WORKER_ID"
  echo "  log: $MESH_DIR/logs/worker-$DAEMON_PID.log"
  tail -5 "$MESH_DIR/logs/worker-$DAEMON_PID.log"
else
  echo "ERROR: worker daemon died at startup"
  cat "$MESH_DIR/logs/worker-$DAEMON_PID.log" 2>/dev/null
  exit 1
fi
