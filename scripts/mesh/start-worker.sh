#!/usr/bin/env bash
# PRForge mesh worker startup — runs everything, no AI interpretation needed.
# Usage: bash start-worker.sh [SLOT]
#   SLOT — optional 1 or 2 (default: auto-detect next free slot)
#   Each slot writes its node-id to ~/.prforge-mesh/my-node-id-<SLOT>
#   After this script runs, start Claude Code with:
#     source ~/.prforge-mesh/worker-slot<SLOT>.env && claude
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

# ── determine slot ────────────────────────────────────────────────────────────
MAX_WORKERS=2
SLOT="${1:-}"
if [ -z "$SLOT" ]; then
  for s in 1 2; do
    nid_file="$MESH_DIR/my-node-id-$s"
    slot_pid=$(ls "$MESH_DIR"/worker-slot${s}-*.pid 2>/dev/null | head -1 || true)
    if [ -z "$slot_pid" ] || ! kill -0 "$(cat "$slot_pid" 2>/dev/null)" 2>/dev/null; then
      SLOT="$s"; break
    fi
  done
  [ -z "$SLOT" ] && { echo "ERROR: both slots occupied. Run mesh-off.sh first."; exit 1; }
fi
[ "$SLOT" = "1" ] || [ "$SLOT" = "2" ] || { echo "ERROR: SLOT must be 1 or 2"; exit 1; }
NODE_ID_FILE="$MESH_DIR/my-node-id-${SLOT}"

# Kill any existing daemon for this slot
for pf in "$MESH_DIR"/worker-slot${SLOT}-*.pid; do
  [ -f "$pf" ] || continue
  pid=$(cat "$pf")
  kill "$pid" 2>/dev/null && echo "Stopped old slot-$SLOT daemon PID=$pid" || true
  rm -f "$pf"
done

# ── enforce global max ────────────────────────────────────────────────────────
live=0
for pf in "$MESH_DIR"/worker-slot*.pid; do
  [ -f "$pf" ] || continue
  pid=$(cat "$pf")
  if kill -0 "$pid" 2>/dev/null; then live=$((live + 1)); else rm -f "$pf"; fi
done
if [ "$live" -ge "$MAX_WORKERS" ]; then
  echo "ERROR: $live worker(s) already running (max $MAX_WORKERS). Run mesh-off.sh first."
  exit 1
fi

# ── start daemon ─────────────────────────────────────────────────────────────
mkdir -p "$MESH_DIR/logs"
cd "$SCRIPT_DIR"

nohup python3 prforge_mesh.py --config "$MESH_DIR/worker-template.json" worker \
  > "$MESH_DIR/logs/worker-slot${SLOT}-$$.log" 2>&1 &
DAEMON_PID=$!

mv "$MESH_DIR/logs/worker-slot${SLOT}-$$.log" "$MESH_DIR/logs/worker-slot${SLOT}-$DAEMON_PID.log" 2>/dev/null || true
echo "$DAEMON_PID" > "$MESH_DIR/worker-slot${SLOT}-$DAEMON_PID.pid"
sleep 2

if kill -0 "$DAEMON_PID" 2>/dev/null; then
  WORKER_ID=$(grep -m1 "worker node_id:" "$MESH_DIR/logs/worker-slot${SLOT}-$DAEMON_PID.log" 2>/dev/null | awk '{print $NF}' || echo "<see log>")
  echo "$WORKER_ID" > "$NODE_ID_FILE"
  # Write env file so the session can source it before launching Claude Code
  cat > "$MESH_DIR/worker-slot${SLOT}.env" <<ENVEOF
export PRFORGE_WORKER_SLOT=${SLOT}
export PRFORGE_WORKER_ID=${WORKER_ID}
ENVEOF
  echo "✓ worker slot $SLOT started  PID=$DAEMON_PID  node_id=$WORKER_ID"
  echo "  log: $MESH_DIR/logs/worker-slot${SLOT}-$DAEMON_PID.log"
  echo ""
  echo "  Now launch Claude Code for this slot:"
  echo "    source $MESH_DIR/worker-slot${SLOT}.env && claude"
else
  echo "ERROR: worker daemon died at startup"
  cat "$MESH_DIR/logs/worker-slot${SLOT}-$DAEMON_PID.log" 2>/dev/null
  exit 1
fi
